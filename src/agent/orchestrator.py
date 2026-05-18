# -*- coding: utf-8 -*-
"""
AgentOrchestrator — multi-agent pipeline coordinator.

Manages the lifecycle of specialised agents (Technical → Intel → Risk →
Specialist → Decision) for a single stock analysis run.

Modes:
- ``quick``   : Technical only → Decision (fastest, ~2 LLM calls)
- ``standard``: Technical → Intel → Decision (default)
- ``full``    : Technical → Intel → Risk → Decision
- ``specialist``: Technical → Intel → Risk → specialist evaluation → Decision

The orchestrator:
1. Seeds an :class:`AgentContext` with the user query and stock code
2. Runs agents sequentially, passing the shared context
3. Collects :class:`StageResult` from each agent
4. Produces a unified :class:`OrchestratorResult` with the final dashboard

Importantly, this class exposes the same ``run(task, context)`` and
``chat(message, session_id, ...)`` interface as ``AgentExecutor`` so it
can be a drop-in replacement via the factory.
"""

from __future__ import annotations

import json
import inspect
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from src.agent.data_confidence import build_context_quality_summary
from src.agent.decision_policy import build_decision_policy
from src.agent.hallucination_guard import apply_hallucination_guard
from src.agent.llm_adapter import LLMToolAdapter
from src.agent.protocols import (
    AgentContext,
    AgentRunStats,
    StageResult,
    StageStatus,
    normalize_decision_signal,
)
from src.agent.runner import parse_dashboard_json
from src.agent.tools.registry import ToolRegistry
from src.config import AGENT_MAX_STEPS_DEFAULT
from src.report_language import normalize_report_language

if TYPE_CHECKING:
    from src.agent.executor import AgentResult

logger = logging.getLogger(__name__)

# Valid orchestrator modes (ordered by cost/depth)
VALID_MODES = ("quick", "standard", "full", "enhanced", "specialist", "debate", "full_debate")


@dataclass
class OrchestratorResult:
    """Unified result from a multi-agent pipeline run."""

    success: bool = False
    content: str = ""
    dashboard: Optional[Dict[str, Any]] = None
    tool_calls_log: List[Dict[str, Any]] = field(default_factory=list)
    total_steps: int = 0
    total_tokens: int = 0
    provider: str = ""
    model: str = ""
    error: Optional[str] = None
    stats: Optional[AgentRunStats] = None
    agent_context_data: Optional[Dict[str, Any]] = None


class AgentOrchestrator:
    """Multi-agent pipeline coordinator.

    Drop-in replacement for ``AgentExecutor`` — exposes the same ``run()``
    and ``chat()`` interface.  The factory switches between them via
    ``AGENT_ARCH``.
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        llm_adapter: LLMToolAdapter,
        skill_instructions: str = "",
        technical_skill_policy: str = "",
        max_steps: int = AGENT_MAX_STEPS_DEFAULT,
        mode: str = "standard",
        skill_manager=None,
        config=None,
        event_bus=None,
    ):
        self.tool_registry = tool_registry
        self.llm_adapter = llm_adapter
        self.skill_instructions = skill_instructions
        self.technical_skill_policy = technical_skill_policy
        self.max_steps = max_steps
        normalized_mode = "specialist" if mode in {"strategy", "skill"} else mode
        self.mode = normalized_mode if normalized_mode in VALID_MODES else "standard"
        self.skill_manager = skill_manager
        self.config = config
        self.event_bus = event_bus

    def _get_timeout_seconds(self) -> int:
        """Return the pipeline timeout in seconds.

        ``0`` means disabled. The timeout is a cooperative budget for the
        whole pipeline rather than a hard interruption of an in-flight stage.
        """
        raw_value = getattr(self.config, "agent_orchestrator_timeout_s", 0)
        try:
            return max(0, int(raw_value or 0))
        except (TypeError, ValueError):
            return 0

    def _build_timeout_result(
        self,
        stats: AgentRunStats,
        all_tool_calls: List[Dict[str, Any]],
        models_used: List[str],
        elapsed_s: float,
        timeout_s: int,
        ctx: Optional[AgentContext] = None,
        parse_dashboard: bool = True,
    ) -> OrchestratorResult:
        """Build a standard timeout result payload."""
        stats.total_duration_s = round(elapsed_s, 2)
        stats.models_used = list(dict.fromkeys(models_used))
        error = f"Pipeline timed out after {elapsed_s:.2f}s (limit: {timeout_s}s)"
        provider = stats.models_used[0] if stats.models_used else ""
        model = ", ".join(stats.models_used)

        dashboard = None
        content = ""
        if ctx is not None:
            dashboard, content = self._resolve_final_output(ctx, parse_dashboard=parse_dashboard)
            if parse_dashboard and dashboard is not None:
                dashboard = self._mark_partial_dashboard(
                    dashboard,
                    note="多 Agent 超时，以下结论基于已完成阶段自动降级生成。",
                )
                ctx.set_data("final_dashboard", dashboard)
                content = json.dumps(dashboard, ensure_ascii=False, indent=2)

        return OrchestratorResult(
            success=bool(content) if (not parse_dashboard or dashboard is not None) else False,
            content=content,
            dashboard=dashboard,
            error=error,
            stats=stats,
            total_steps=stats.total_stages,
            total_tokens=stats.total_tokens,
            tool_calls_log=all_tool_calls,
            provider=provider,
            model=model,
        )

    def _build_budget_skip_result(
        self,
        stats: AgentRunStats,
        all_tool_calls: List[Dict[str, Any]],
        models_used: List[str],
        elapsed_s: float,
        timeout_s: int,
        stage_name: str,
        remaining_budget: float,
        min_stage_budget_s: int,
        ctx: Optional[AgentContext] = None,
        parse_dashboard: bool = True,
    ) -> OrchestratorResult:
        """Build a result for budget-insufficient stage skip (non-timeout semantics)."""
        stats.total_duration_s = round(elapsed_s, 2)
        stats.models_used = list(dict.fromkeys(models_used))
        dashboard = None
        content = ""
        if ctx is not None:
            dashboard, content = self._resolve_final_output(ctx, parse_dashboard=parse_dashboard)
            if parse_dashboard and dashboard is not None:
                dashboard = self._mark_partial_dashboard(
                    dashboard,
                    note="多 Agent 预算不足，以下结论基于已完成阶段自动降级生成。",
                )
                ctx.set_data("final_dashboard", dashboard)
                content = json.dumps(dashboard, ensure_ascii=False, indent=2)

        return OrchestratorResult(
            success=bool(content) if (not parse_dashboard or dashboard is not None) else False,
            content=content,
            dashboard=dashboard,
            error=(
                f"Pipeline skipped before stage '{stage_name}' due to insufficient budget "
                f"({remaining_budget:.1f}s remaining, minimum {min_stage_budget_s}s required)"
            ),
            stats=stats,
            total_steps=stats.total_stages,
            total_tokens=stats.total_tokens,
            tool_calls_log=all_tool_calls,
            provider=stats.models_used[0] if stats.models_used else "",
            model=", ".join(stats.models_used),
        )


    def _prepare_agent(self, agent: Any) -> Any:
        """Apply orchestrator-level runtime settings to a child agent.

        When the orchestrator-level ``max_steps`` equals the default
        (``AGENT_MAX_STEPS_DEFAULT``),
        each agent keeps its own per-agent limit — this prevents inflating
        a decision agent (designed for 3 steps) to 10 steps.

        When the user **explicitly** raises the global limit above the
        default, all agents adopt the global value so the user's intent to
        allow more steps is respected.

        When the user **lowers** the global limit below an agent's default,
        the agent is capped at the global value.
        """
        if hasattr(agent, "max_steps"):
            if self.max_steps > AGENT_MAX_STEPS_DEFAULT:
                # User explicitly raised the limit — apply to all agents.
                agent.max_steps = self.max_steps
            else:
                # Default or lowered — keep per-agent limit as ceiling.
                agent.max_steps = min(agent.max_steps, self.max_steps)
        return agent

    def _callable_accepts_timeout_kwarg(self, func: Any) -> Optional[bool]:
        """Return whether a callable accepts ``timeout_seconds`` when inspectable."""
        if not callable(func):
            return None
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            return None

        if "timeout_seconds" in signature.parameters:
            return True
        return any(
            param.kind is inspect.Parameter.VAR_KEYWORD
            for param in signature.parameters.values()
        )

    def _agent_run_accepts_timeout(self, run_callable: Any) -> bool:
        """Best-effort compatibility check for legacy test doubles / custom agents."""
        side_effect = getattr(run_callable, "side_effect", None)
        accepts_timeout = self._callable_accepts_timeout_kwarg(side_effect)
        if accepts_timeout is not None:
            return accepts_timeout

        accepts_timeout = self._callable_accepts_timeout_kwarg(run_callable)
        if accepts_timeout is not None:
            return accepts_timeout

        return True

    def _run_stage_agent(
        self,
        agent: Any,
        ctx: AgentContext,
        progress_callback: Optional[Callable] = None,
        timeout_seconds: Optional[float] = None,
    ) -> StageResult:
        """Run a stage agent while preserving compatibility with older call signatures."""
        stage_progress_callback = progress_callback
        if self.event_bus:
            stage_progress_callback = self._build_stage_progress_callback(
                agent.agent_name,
                progress_callback,
            )
        run_kwargs = {"progress_callback": stage_progress_callback}
        if (
            timeout_seconds is not None
            and timeout_seconds > 0
            and self._agent_run_accepts_timeout(agent.run)
        ):
            run_kwargs["timeout_seconds"] = timeout_seconds
        return agent.run(ctx, **run_kwargs)

    def _build_stage_progress_callback(
        self,
        agent_name: str,
        progress_callback: Optional[Callable],
    ) -> Callable[[Dict[str, Any]], None]:
        """Mirror low-level ReAct progress into the structured analysis event bus."""

        def _callback(event: Dict[str, Any]) -> None:
            if progress_callback:
                progress_callback(event)

            event_type = event.get("type")
            if event_type == "thinking":
                message = event.get("message") or event.get("display_name") or ""
                if message:
                    self.event_bus.emit_thinking(
                        agent_name=agent_name,
                        thinking=str(message),
                        step=int(event.get("step") or 0),
                    )
            elif event_type == "tool_start":
                self.event_bus.emit_tool_call(
                    agent_name=agent_name,
                    tool_name=str(event.get("tool") or ""),
                    arguments=event.get("arguments") if isinstance(event.get("arguments"), dict) else {},
                    step=int(event.get("step") or 0),
                )
            elif event_type == "tool_done":
                self.event_bus.emit_tool_result(
                    agent_name=agent_name,
                    tool_name=str(event.get("tool") or ""),
                    result_summary=(
                        "完成" if event.get("success", True) else "失败"
                    ),
                    success=bool(event.get("success", True)),
                    duration=float(event.get("duration") or 0),
                )

        return _callback

    # -----------------------------------------------------------------
    # Public interface (mirrors AgentExecutor)
    # -----------------------------------------------------------------

    def run(self, task: str, context: Optional[Dict[str, Any]] = None) -> "AgentResult":
        """Run the multi-agent pipeline for a dashboard analysis.

        Returns an ``AgentResult`` (same type as ``AgentExecutor.run``).
        """
        from src.agent.executor import AgentResult

        ctx = self._build_context(task, context)
        ctx.meta["response_mode"] = "dashboard"
        orch_result = self._execute_pipeline(ctx, parse_dashboard=True)

        return AgentResult(
            success=orch_result.success,
            content=orch_result.content,
            dashboard=orch_result.dashboard,
            tool_calls_log=orch_result.tool_calls_log,
            total_steps=orch_result.total_steps,
            total_tokens=orch_result.total_tokens,
            provider=orch_result.provider,
            model=orch_result.model,
            error=orch_result.error,
            agent_context_data=orch_result.agent_context_data,
        )

    def chat(
        self,
        message: str,
        session_id: str,
        progress_callback: Optional[Callable] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> "AgentResult":
        """Run the pipeline in chat mode (free-form answer, no dashboard parse).

        Conversation history is managed externally by the caller (via
        ``conversation_manager``); the orchestrator focuses on multi-agent
        coordination.
        """
        from src.agent.executor import AgentResult
        from src.agent.conversation import conversation_manager

        ctx = self._build_context(message, context)
        ctx.session_id = session_id
        ctx.meta["response_mode"] = "chat"

        session = conversation_manager.get_or_create(session_id)
        history = session.get_history()
        if history:
            ctx.meta["conversation_history"] = history

        # Persist user turn
        conversation_manager.add_message(session_id, "user", message)

        orch_result = self._execute_pipeline(
            ctx,
            parse_dashboard=False,
            progress_callback=progress_callback,
        )

        # Persist assistant response
        if orch_result.success:
            conversation_manager.add_message(session_id, "assistant", orch_result.content)
        else:
            conversation_manager.add_message(
                session_id, "assistant",
                f"[分析失败] {orch_result.error or '未知错误'}",
            )

        return AgentResult(
            success=orch_result.success,
            content=orch_result.content,
            dashboard=orch_result.dashboard,
            tool_calls_log=orch_result.tool_calls_log,
            total_steps=orch_result.total_steps,
            total_tokens=orch_result.total_tokens,
            provider=orch_result.provider,
            model=orch_result.model,
            error=orch_result.error,
        )

    # -----------------------------------------------------------------
    # Pipeline execution
    # -----------------------------------------------------------------

    def _execute_pipeline(
        self,
        ctx: AgentContext,
        parse_dashboard: bool = True,
        progress_callback: Optional[Callable] = None,
    ) -> OrchestratorResult:
        """Run the agent pipeline according to ``self.mode``."""
        stats = AgentRunStats()
        all_tool_calls: List[Dict[str, Any]] = []
        models_used: List[str] = []
        t0 = time.time()
        timeout_s = self._get_timeout_seconds()

        agents = self._build_agent_chain(ctx)
        specialist_agents_inserted = False
        index = 0

        if self.event_bus:
            agent_names = [a.agent_name for a in agents]
            self.event_bus.emit_pipeline_start(
                stock_code=ctx.stock_code,
                stock_name=ctx.stock_name or "",
                mode=self.mode,
                agents=agent_names,
            )

        # Minimum seconds required for a stage to do useful work.  Starting
        # a stage with less budget virtually guarantees a timeout that wastes
        # an LLM billing cycle.  Only enforced after at least one stage has
        # completed so that the first stage always gets a chance to run
        # even when the total budget is small.
        _MIN_STAGE_BUDGET_S = 15

        while index < len(agents):
            agent = agents[index]
            elapsed_s = time.time() - t0
            remaining_budget = timeout_s - elapsed_s if timeout_s else None
            stage_min_budget_s = (
                _MIN_STAGE_BUDGET_S
            )
            timeout_exhausted = (
                timeout_s
                and remaining_budget is not None
                and remaining_budget <= 0
            )
            budget_guard_triggered = (
                timeout_s
                and remaining_budget is not None
                and index > 0
                and remaining_budget < stage_min_budget_s
            )
            if timeout_exhausted:
                logger.error("[Orchestrator] pipeline timed out before stage '%s'", agent.agent_name)
                if progress_callback:
                    progress_callback({
                        "type": "pipeline_timeout",
                        "stage": agent.agent_name,
                        "elapsed": round(elapsed_s, 2),
                        "timeout": timeout_s,
                    })
                return self._build_timeout_result(
                    stats,
                    all_tool_calls,
                    models_used,
                    elapsed_s,
                    timeout_s,
                    ctx=ctx,
                    parse_dashboard=parse_dashboard,
                )

            if budget_guard_triggered:
                logger.warning(
                    "[Orchestrator] pipeline insufficient budget before stage '%s' (%.1fs remaining, min %ds)",
                    agent.agent_name,
                    remaining_budget,
                    stage_min_budget_s,
                )
                if progress_callback:
                    progress_callback({
                        "type": "pipeline_timeout",
                        "stage": agent.agent_name,
                        "elapsed": round(elapsed_s, 2),
                        "timeout": timeout_s,
                    })
                return self._build_budget_skip_result(
                    stats,
                    all_tool_calls,
                    models_used,
                    elapsed_s,
                    timeout_s,
                    agent.agent_name,
                    remaining_budget,
                    stage_min_budget_s,
                    ctx=ctx,
                    parse_dashboard=parse_dashboard,
                )

            if (
                self.mode == "specialist"
                and agent.agent_name == "decision"
                and not specialist_agents_inserted
            ):
                specialist_agents = self._build_specialist_agents(ctx)
                self._skill_agent_names = {a.agent_name for a in specialist_agents}
                specialist_agents_inserted = True
                if specialist_agents:
                    agents[index:index] = specialist_agents
                    continue

            # Aggregate skill opinions before the decision agent
            if agent.agent_name == "decision" and getattr(self, "_skill_agent_names", None):
                self._aggregate_skill_opinions(ctx)

            if agent.agent_name == "decision":
                self._refresh_decision_policy(ctx)

            if progress_callback:
                progress_callback({
                    "type": "stage_start",
                    "stage": agent.agent_name,
                    "message": f"Starting {agent.agent_name} analysis...",
                })

            if self.event_bus:
                display_names = {
                    "technical": "技术面分析", "intel": "情报分析", "risk": "风险评估",
                    "industry": "行业分析", "capital_flow": "资金流向分析",
                    "devils_advocate": "魔鬼代言人审计", "debate": "深度论证",
                    "scenario_analysis": "情景分析", "factor_scoring": "因子评分",
                    "decision": "决策综合", "fundamental": "基本面分析",
                    "sentiment": "市场情绪分析",
                }
                self.event_bus.emit_agent_start(
                    agent_name=agent.agent_name,
                    display_name=display_names.get(agent.agent_name, agent.agent_name),
                    step=index + 1,
                    total=len(agents),
                )

            remaining_timeout_s = (
                max(0.0, timeout_s - elapsed_s)
                if timeout_s
                else None
            )
            result: StageResult = self._run_stage_agent(
                agent,
                ctx,
                progress_callback=progress_callback,
                timeout_seconds=remaining_timeout_s,
            )
            stats.record_stage(result)
            all_tool_calls.extend(
                tc for tc in (result.meta.get("tool_calls_log") or [])
            )
            models_used.extend(result.meta.get("models_used", []))

            elapsed_s = time.time() - t0
            if timeout_s and elapsed_s >= timeout_s:
                logger.error("[Orchestrator] pipeline timed out after stage '%s'", agent.agent_name)
                if progress_callback:
                    progress_callback({
                        "type": "pipeline_timeout",
                        "stage": agent.agent_name,
                        "elapsed": round(elapsed_s, 2),
                        "timeout": timeout_s,
                    })
                return self._build_timeout_result(
                    stats,
                    all_tool_calls,
                    models_used,
                    elapsed_s,
                    timeout_s,
                    ctx=ctx,
                    parse_dashboard=parse_dashboard,
                )

            if progress_callback:
                progress_callback({
                    "type": "stage_done",
                    "stage": agent.agent_name,
                    "status": result.status.value,
                    "duration": result.duration_s,
                })

            if self.event_bus and result.success:
                last_opinion = None
                for op in reversed(ctx.opinions):
                    if op.agent_name == agent.agent_name:
                        last_opinion = op
                        break
                if last_opinion:
                    self.event_bus.emit_opinion(
                        agent_name=last_opinion.agent_name,
                        signal=last_opinion.signal,
                        confidence=last_opinion.confidence,
                        reasoning=last_opinion.reasoning,
                        raw_data=last_opinion.raw_data,
                    )
                if agent.agent_name == "devils_advocate":
                    audit = ctx.get_data("devils_advocate_audit")
                    if audit and isinstance(audit, dict):
                        self.event_bus.emit_challenge(
                            agent_name="devils_advocate",
                            challenges=audit.get("challenges", []),
                            weakest_links=audit.get("weakest_links", []),
                            overall_assessment=audit.get("overall_assessment", ""),
                        )
                if agent.agent_name == "debate":
                    debate_data = ctx.get_data("debate_opinion")
                    if debate_data and isinstance(debate_data, dict):
                        ds = debate_data.get("debate_summary", {})
                        self.event_bus.emit_debate_round(
                            round_num=1,
                            consensus_points=ds.get("consensus_points", []),
                            divergence_points=ds.get("divergence_points", []),
                            swing_argument=ds.get("swing_argument", ""),
                        )
                if agent.agent_name == "scenario_analysis":
                    sa = ctx.get_data("scenario_analysis")
                    if sa and isinstance(sa, dict):
                        self.event_bus.emit_scenario(
                            scenarios=sa.get("scenarios", {}),
                            expected_value=sa.get("expected_value", {}),
                            swing_factors=sa.get("swing_factors", []),
                        )

            if ctx.meta.get("response_mode") == "chat" and agent.agent_name == "decision":
                final_text = result.meta.get("raw_text")
                if isinstance(final_text, str) and final_text.strip():
                    ctx.set_data("final_response_text", final_text.strip())

            if result.success and agent.agent_name == "decision":
                self._apply_risk_override(ctx)

            # Abort pipeline on critical failure.
            # Non-critical stages that degrade gracefully:
            #   - intel / risk (standard support stages)
            #   - skill agents (specialist evaluation, optional)
            if result.status == StageStatus.FAILED:
                non_critical = (
                    agent.agent_name in ("intel", "risk", "industry", "capital_flow", "debate", "factor_scoring", "sentiment", "fundamental", "devils_advocate", "scenario_analysis")
                    or agent.agent_name in getattr(self, "_skill_agent_names", set())
                )
                if not non_critical:
                    logger.error("[Orchestrator] critical stage '%s' failed: %s", agent.agent_name, result.error)
                    return OrchestratorResult(
                        success=False,
                        error=f"Stage '{agent.agent_name}' failed: {result.error}",
                        stats=stats,
                        total_tokens=stats.total_tokens,
                        tool_calls_log=all_tool_calls,
                    )
                else:
                    logger.warning("[Orchestrator] stage '%s' failed (non-critical, degrading): %s", agent.agent_name, result.error)

            index += 1

        # Assemble final output
        total_duration = round(time.time() - t0, 2)
        stats.total_duration_s = total_duration
        stats.models_used = list(dict.fromkeys(models_used))

        dashboard, content = self._resolve_final_output(ctx, parse_dashboard=parse_dashboard)

        if self.event_bus:
            final_signal = "hold"
            final_confidence = 0.5
            if dashboard and isinstance(dashboard, dict):
                final_signal = dashboard.get("decision_type", dashboard.get("signal", "hold"))
                final_confidence = dashboard.get("confidence_level", dashboard.get("confidence", 0.5))
                if isinstance(final_confidence, str):
                    try:
                        final_confidence = float(final_confidence)
                    except (ValueError, TypeError):
                        final_confidence = 0.5
            self.event_bus.emit_pipeline_complete(
                stock_code=ctx.stock_code,
                signal=final_signal,
                confidence=final_confidence,
                duration=total_duration,
            )

        model_str = ", ".join(dict.fromkeys(m for m in models_used if m))
        provider = stats.models_used[0] if stats.models_used else ""
        agent_context_data = self._extract_agent_context_data(ctx)

        if parse_dashboard and dashboard is None:
            return OrchestratorResult(
                success=False,
                content=content,
                dashboard=None,
                tool_calls_log=all_tool_calls,
                total_steps=stats.total_stages,
                total_tokens=stats.total_tokens,
                provider=provider,
                model=model_str,
                error="Failed to parse dashboard JSON from agent response",
                stats=stats,
                agent_context_data=agent_context_data,
            )

        return OrchestratorResult(
            success=bool(content),
            content=content,
            dashboard=dashboard,
            tool_calls_log=all_tool_calls,
            total_steps=stats.total_stages,
            total_tokens=stats.total_tokens,
            provider=provider,
            model=model_str,
            stats=stats,
            agent_context_data=agent_context_data,
        )

    # -----------------------------------------------------------------
    # Agent chain construction
    # -----------------------------------------------------------------

    @staticmethod
    def _extract_agent_context_data(ctx: AgentContext) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        if ctx.opinions:
            opinions_dict = {}
            for op in ctx.opinions:
                opinions_dict[op.agent_name] = {
                    "agentName": op.agent_name,
                    "signal": op.signal,
                    "confidence": op.confidence,
                    "reasoning": op.reasoning,
                }
                if op.raw_data:
                    opinions_dict[op.agent_name]["rawData"] = op.raw_data
            result["agent_opinions"] = opinions_dict

        factor_scores = ctx.get_data("factor_scores")
        factor_score_details = ctx.get_data("factor_score_details")
        if factor_scores or factor_score_details:
            scores_payload: Dict[str, Any] = {}
            if factor_scores:
                scores_payload["scores"] = factor_scores
            if factor_score_details:
                scores_payload["scoreDetails"] = factor_score_details
            result["factor_scores"] = scores_payload

        debate_opinion = ctx.get_data("debate_opinion")
        if debate_opinion and isinstance(debate_opinion, dict):
            debate_summary = debate_opinion.get("debate_summary")
            if debate_summary:
                result["debate_summary"] = debate_summary

        trading_plan = ctx.get_data("trading_plan")
        if trading_plan and isinstance(trading_plan, dict) and trading_plan:
            result["trading_plan"] = trading_plan

        scenario_analysis = ctx.get_data("scenario_analysis")
        if scenario_analysis and isinstance(scenario_analysis, dict):
            result["scenario_analysis"] = scenario_analysis

        devils_advocate_audit = ctx.get_data("devils_advocate_audit")
        if devils_advocate_audit and isinstance(devils_advocate_audit, dict):
            result["devils_advocate_audit"] = devils_advocate_audit

        evidence_map = ctx.get_data("evidence_map")
        if evidence_map and isinstance(evidence_map, dict):
            result["evidence_map"] = evidence_map

        evidence_pool = ctx.get_data("evidence_pool")
        if evidence_pool and isinstance(evidence_pool, list):
            result["evidence_pool"] = evidence_pool[:120]

        decision_policy = ctx.get_data("decision_policy")
        if decision_policy and isinstance(decision_policy, dict):
            result["decision_policy"] = decision_policy

        data_quality_summary = ctx.get_data("data_quality_summary")
        if data_quality_summary and isinstance(data_quality_summary, dict):
            result["data_quality_summary"] = data_quality_summary

        final_dashboard = ctx.get_data("final_dashboard")
        if isinstance(final_dashboard, dict) and isinstance(final_dashboard.get("hallucination_guard"), dict):
            result["hallucination_guard"] = final_dashboard["hallucination_guard"]

        thesis_breakers = ctx.get_data("thesis_breakers")
        if thesis_breakers and isinstance(thesis_breakers, list):
            result["thesis_breakers"] = thesis_breakers

        return result

    @staticmethod
    def _refresh_decision_policy(ctx: AgentContext) -> Dict[str, Any]:
        """Refresh the rule-based pre-decision stored in the shared context."""
        policy = build_decision_policy(ctx)
        ctx.set_data("decision_policy", policy)
        return policy

    def _build_agent_chain(self, ctx: AgentContext) -> list:
        """Instantiate the ordered agent list based on ``self.mode``."""
        from src.agent.agents.technical_agent import TechnicalAgent
        from src.agent.agents.intel_agent import IntelAgent
        from src.agent.agents.decision_agent import DecisionAgent
        from src.agent.agents.risk_agent import RiskAgent
        from src.agent.agents.industry_agent import IndustryAgent
        from src.agent.agents.capital_flow_agent import CapitalFlowAgent
        from src.agent.agents.debate_agent import DebateAgent
        from src.agent.agents.factor_scoring_agent import FactorScoringAgent
        from src.agent.agents.sentiment_agent import SentimentAgent
        from src.agent.agents.fundamental_agent import FundamentalAgent
        from src.agent.agents.devils_advocate_agent import DevilsAdvocateAgent
        from src.agent.agents.scenario_analysis_agent import ScenarioAnalysisAgent

        self._skill_agent_names = set()

        common_kwargs = dict(
            tool_registry=self.tool_registry,
            llm_adapter=self.llm_adapter,
            skill_instructions=self.skill_instructions,
            technical_skill_policy=self.technical_skill_policy,
        )

        technical = self._prepare_agent(TechnicalAgent(**common_kwargs))
        intel = self._prepare_agent(IntelAgent(**common_kwargs))
        risk = self._prepare_agent(RiskAgent(**common_kwargs))
        decision = self._prepare_agent(DecisionAgent(**common_kwargs))
        industry = self._prepare_agent(IndustryAgent(**common_kwargs))
        capital_flow = self._prepare_agent(CapitalFlowAgent(**common_kwargs))
        debate = self._prepare_agent(DebateAgent(**common_kwargs))
        factor_scoring = self._prepare_agent(FactorScoringAgent(**common_kwargs))
        sentiment = self._prepare_agent(SentimentAgent(**common_kwargs))
        fundamental = self._prepare_agent(FundamentalAgent(**common_kwargs))
        devils_advocate = self._prepare_agent(DevilsAdvocateAgent(**common_kwargs))
        scenario_analysis = self._prepare_agent(ScenarioAnalysisAgent(**common_kwargs))

        from src.agent.tools.sequential_thinking import register_sequential_thinking
        from src.agent.tools.web_tools import register_web_tools
        register_sequential_thinking(self.tool_registry, event_bus=self.event_bus, agent_name="orchestrator")
        register_web_tools(self.tool_registry)

        if self.mode == "quick":
            return [technical, decision]
        elif self.mode == "standard":
            return [technical, intel, decision]
        elif self.mode == "full":
            return [technical, intel, risk, decision]
        elif self.mode == "enhanced":
            return [technical, intel, risk, industry, capital_flow, factor_scoring, decision]
        elif self.mode == "specialist":
            return [technical, intel, risk, decision]
        elif self.mode == "debate":
            return [technical, intel, risk, industry, capital_flow, devils_advocate, debate, scenario_analysis, factor_scoring, decision]
        elif self.mode == "full_debate":
            return [technical, fundamental, sentiment, intel, risk, industry, capital_flow, devils_advocate, debate, scenario_analysis, factor_scoring, decision]
        else:
            return [technical, intel, decision]

    def _build_specialist_agents(self, ctx: AgentContext) -> list:
        """Build specialist sub-agents based on requested skills.

        Uses the skill router to select applicable skills, then creates
        lightweight agent wrappers for each.
        """
        try:
            from src.agent.skills.router import SkillRouter
            common_kwargs = dict(
                tool_registry=self.tool_registry,
                llm_adapter=self.llm_adapter,
                skill_instructions=self.skill_instructions,
                technical_skill_policy=self.technical_skill_policy,
            )
            router = SkillRouter()
            selected = router.select_skills(ctx)
            if not selected:
                return []

            from src.agent.skills.skill_agent import SkillAgent
            agents = []
            for skill_id in selected[:3]:  # cap at 3 concurrent skills
                agent = self._prepare_agent(SkillAgent(
                    skill_id=skill_id,
                    **common_kwargs,
                ))
                agents.append(agent)
            return agents
        except Exception as exc:
            logger.warning("[Orchestrator] failed to build skill agents: %s", exc)
            return []

    def _build_skill_agents(self, ctx: AgentContext) -> list:
        """Compatibility wrapper for legacy imports."""
        return self._build_specialist_agents(ctx)

    def _build_strategy_agents(self, ctx: AgentContext) -> list:
        """Compatibility wrapper for legacy tests/imports."""
        return self._build_specialist_agents(ctx)

    # -----------------------------------------------------------------
    # Skill aggregation
    # -----------------------------------------------------------------

    def _aggregate_skill_opinions(self, ctx: AgentContext) -> None:
        """Run SkillAggregator to produce a consensus opinion.

        Merges individual skill-agent opinions into a single weighted
        consensus and stores it in context so the decision agent can use it.
        """
        try:
            from src.agent.skills.aggregator import SkillAggregator
            aggregator = SkillAggregator()
            consensus = aggregator.aggregate(ctx)
            if consensus:
                ctx.opinions.append(consensus)
                ctx.set_data("skill_consensus", {
                    "signal": consensus.signal,
                    "confidence": consensus.confidence,
                    "reasoning": consensus.reasoning,
                })
                logger.info(
                    "[Orchestrator] skill consensus: signal=%s confidence=%.2f",
                    consensus.signal, consensus.confidence,
                )
            else:
                logger.info("[Orchestrator] no skill opinions to aggregate")
        except Exception as exc:
            logger.warning("[Orchestrator] skill aggregation failed: %s", exc)

    def _aggregate_strategy_opinions(self, ctx: AgentContext) -> None:
        """Compatibility wrapper for legacy tests/imports."""
        self._aggregate_skill_opinions(ctx)

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def _build_context(self, task: str, context: Optional[Dict[str, Any]] = None) -> AgentContext:
        """Seed an ``AgentContext`` from the user request."""
        ctx = AgentContext(query=task)

        if context:
            ctx.stock_code = context.get("stock_code", "")
            ctx.stock_name = context.get("stock_name", "")
            requested_skills = context.get("skills")
            if requested_skills is None:
                requested_skills = context.get("strategies", [])
            ctx.meta["skills_requested"] = requested_skills or []
            ctx.meta["strategies_requested"] = requested_skills or []
            ctx.meta["report_language"] = normalize_report_language(context.get("report_language", "zh"))

            # Pre-populate data fields that the caller already has
            for data_key in ("realtime_quote", "daily_history", "chip_distribution",
                             "trend_result", "news_context"):
                if context.get(data_key):
                    ctx.set_data(data_key, context[data_key])
            if ctx.data:
                ctx.set_data("data_quality_summary", build_context_quality_summary(ctx.data))

        # Try to extract stock code from the query text
        if not ctx.stock_code:
            ctx.stock_code = _extract_stock_code(task)

        if "report_language" not in ctx.meta:
            ctx.meta["report_language"] = "zh"

        return ctx

    @staticmethod
    def _fallback_summary(ctx: AgentContext) -> str:
        """Build a plaintext summary when dashboard JSON is unavailable."""
        lines = [f"# Analysis Summary: {ctx.stock_code} ({ctx.stock_name})", ""]
        for op in ctx.opinions:
            lines.append(f"## {op.agent_name}")
            lines.append(f"Signal: {op.signal} (confidence: {op.confidence:.0%})")
            lines.append(op.reasoning)
            lines.append("")
        if ctx.risk_flags:
            lines.append("## Risk Flags")
            for rf in ctx.risk_flags:
                lines.append(f"- [{rf['severity']}] {rf['description']}")
        return "\n".join(lines)

    def _resolve_final_output(
        self,
        ctx: AgentContext,
        *,
        parse_dashboard: bool,
    ) -> tuple[Optional[Dict[str, Any]], str]:
        """Resolve the best available final output from context.

        For dashboard mode, prefer:
        1. Parsed/normalized decision dashboard
        2. Parsed raw dashboard text
        3. Synthesised dashboard from completed opinions
        4. Plaintext fallback summary
        """
        if ctx.opinions and not isinstance(ctx.get_data("decision_policy"), dict):
            self._refresh_decision_policy(ctx)

        final_dashboard = ctx.get_data("final_dashboard")
        final_raw = ctx.get_data("final_dashboard_raw")
        final_text = ctx.get_data("final_response_text")
        chat_mode = ctx.meta.get("response_mode") == "chat"

        if parse_dashboard:
            dashboard = self._resolve_dashboard_payload(ctx, final_dashboard, final_raw)
            if dashboard is not None:
                return dashboard, json.dumps(dashboard, ensure_ascii=False, indent=2)
            if ctx.opinions:
                return None, self._fallback_summary(ctx)
            return None, ""

        if chat_mode and isinstance(final_text, str) and final_text.strip():
            return None, final_text.strip()
        if isinstance(final_raw, str) and final_raw.strip():
            return None, final_raw
        if isinstance(final_dashboard, dict):
            dashboard = self._normalize_dashboard_payload(final_dashboard, ctx)
            if dashboard is not None:
                return dashboard, json.dumps(dashboard, ensure_ascii=False, indent=2)
        if ctx.opinions:
            return None, self._fallback_summary(ctx)
        return None, ""

    def _resolve_dashboard_payload(
        self,
        ctx: AgentContext,
        final_dashboard: Any,
        final_raw: Any,
    ) -> Optional[Dict[str, Any]]:
        """Return a normalized dashboard, or synthesize one from partial context."""
        dashboard: Optional[Dict[str, Any]] = None

        if isinstance(final_dashboard, dict):
            dashboard = self._normalize_dashboard_payload(final_dashboard, ctx)
        elif isinstance(final_raw, str) and final_raw.strip():
            parsed = parse_dashboard_json(final_raw)
            if isinstance(parsed, dict):
                dashboard = self._normalize_dashboard_payload(parsed, ctx)

        if dashboard is None:
            dashboard = self._normalize_dashboard_payload({}, ctx)

        if dashboard is None:
            return None

        dashboard = apply_hallucination_guard(ctx, dashboard)
        ctx.set_data("final_dashboard", dashboard)
        # Apply risk override (idempotent — safe to call even if already
        # applied in _execute_pipeline after the decision stage).
        self._apply_risk_override(ctx)
        overridden = ctx.get_data("final_dashboard")
        if isinstance(overridden, dict):
            return overridden
        return dashboard

    def _normalize_dashboard_payload(
        self,
        payload: Optional[Dict[str, Any]],
        ctx: AgentContext,
    ) -> Optional[Dict[str, Any]]:
        """Normalize or synthesize the dashboard shape expected downstream."""
        payload = dict(payload or {})
        meaningful_data_keys = (
            "realtime_quote",
            "daily_history",
            "chip_distribution",
            "trend_result",
            "news_context",
            "intel_opinion",
            "fundamental_context",
        )
        has_meaningful_context = any(ctx.get_data(key) is not None for key in meaningful_data_keys)
        if not payload and not ctx.opinions and not has_meaningful_context:
            return None

        decision_policy = ctx.get_data("decision_policy")
        if not isinstance(decision_policy, dict):
            decision_policy = {}

        base_opinion = self._select_base_opinion(ctx)
        policy_signal = decision_policy.get("adjusted_signal") or decision_policy.get("base_signal")
        decision_type = normalize_decision_signal(
            payload.get("decision_type") or policy_signal or (base_opinion.signal if base_opinion else "hold")
        )
        raw_confidence = (
            base_opinion.confidence
            if base_opinion is not None
            else decision_policy.get("confidence", 0.5)
        )
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            confidence = 0.5
        sentiment_score = payload.get("sentiment_score")
        try:
            sentiment_score = int(sentiment_score)
        except (TypeError, ValueError):
            sentiment_score = _estimate_sentiment_score(decision_type, confidence)

        dashboard_block = payload.get("dashboard")
        if not isinstance(dashboard_block, dict):
            dashboard_block = {}
        else:
            dashboard_block = dict(dashboard_block)

        core = dashboard_block.get("core_conclusion")
        if not isinstance(core, dict):
            core = {}
        else:
            core = dict(core)

        intelligence = dashboard_block.get("intelligence")
        if not isinstance(intelligence, dict):
            intelligence = {}
        else:
            intelligence = dict(intelligence)

        battle = dashboard_block.get("battle_plan")
        if not isinstance(battle, dict):
            battle = {}
        else:
            battle = dict(battle)

        analysis_summary = _first_non_empty_text(
            payload.get("analysis_summary"),
            core.get("one_sentence"),
            getattr(base_opinion, "reasoning", ""),
        )
        if not analysis_summary:
            analysis_summary = f"多 Agent 未生成完整仪表盘，当前按{_signal_to_operation(decision_type)}处理。"
        analysis_summary = _truncate_text(analysis_summary, 220)

        trend_prediction = _first_non_empty_text(
            payload.get("trend_prediction"),
            (getattr(base_opinion, "raw_data", {}) or {}).get("trend_summary")
            if base_opinion is not None else "",
        )
        if not trend_prediction:
            technical = self._latest_opinion(ctx, {"technical"})
            tech_raw = technical.raw_data if technical and isinstance(technical.raw_data, dict) else {}
            ma_alignment = tech_raw.get("ma_alignment")
            trend_score = tech_raw.get("trend_score")
            if ma_alignment or trend_score is not None:
                trend_prediction = f"技术面{ma_alignment or 'neutral'}，趋势评分 {trend_score if trend_score is not None else 'N/A'}"
            else:
                trend_prediction = "待结合更多阶段结果确认"

        operation_advice_raw = payload.get("operation_advice")
        operation_advice = _normalize_operation_advice_value(operation_advice_raw, decision_type)

        existing_position = core.get("position_advice")
        position_advice = dict(existing_position) if isinstance(existing_position, dict) else {}
        if isinstance(operation_advice_raw, dict):
            no_position = _first_non_empty_text(
                operation_advice_raw.get("no_position"),
                operation_advice_raw.get("empty_position"),
            )
            has_position = _first_non_empty_text(
                operation_advice_raw.get("has_position"),
                operation_advice_raw.get("holding_position"),
            )
            if no_position and "no_position" not in position_advice:
                position_advice["no_position"] = no_position
            if has_position and "has_position" not in position_advice:
                position_advice["has_position"] = has_position
        defaults = _default_position_advice(decision_type)
        position_advice.setdefault("no_position", defaults["no_position"])
        position_advice.setdefault("has_position", defaults["has_position"])

        key_levels = self._collect_key_levels(ctx, payload, dashboard_block)
        sniper = battle.get("sniper_points")
        if not isinstance(sniper, dict):
            sniper = {}
        else:
            sniper = dict(sniper)

        ideal_buy = _pick_first_level(
            sniper.get("ideal_buy"),
            key_levels.get("ideal_buy_if_valuation_improves"),
            key_levels.get("ideal_buy"),
            key_levels.get("support"),
            key_levels.get("immediate_support"),
        )
        sniper["ideal_buy"] = ideal_buy if ideal_buy is not None else "N/A"

        secondary_buy = _coerce_level_value(sniper.get("secondary_buy"))
        if secondary_buy is None:
            secondary_buy = _pick_first_level(
                key_levels.get("secondary_buy"),
                key_levels.get("support"),
                key_levels.get("immediate_support"),
            )
        if _level_values_equal(secondary_buy, sniper.get("ideal_buy")):
            secondary_buy = None
        sniper["secondary_buy"] = secondary_buy if secondary_buy is not None else "N/A"
        sniper.setdefault(
            "stop_loss",
            key_levels.get("stop_loss")
            or key_levels.get("strong_support_stop_loss")
            or "待补充",
        )
        sniper.setdefault(
            "take_profit",
            key_levels.get("take_profit")
            or key_levels.get("next_breakout_target")
            or key_levels.get("current_resistance")
            or key_levels.get("resistance")
            or "N/A",
        )

        risk_alerts = self._collect_risk_alerts(ctx, intelligence)
        positive_catalysts = self._collect_positive_catalysts(ctx, intelligence)
        latest_news = _extract_latest_news_title(intelligence)

        if not intelligence.get("risk_alerts"):
            intelligence["risk_alerts"] = risk_alerts
        if positive_catalysts and not intelligence.get("positive_catalysts"):
            intelligence["positive_catalysts"] = positive_catalysts
        if latest_news and not intelligence.get("latest_news"):
            intelligence["latest_news"] = latest_news

        if not core.get("one_sentence"):
            core["one_sentence"] = _truncate_text(analysis_summary, 60)
        if not core.get("time_sensitivity"):
            core["time_sensitivity"] = "本周内"
        if not core.get("signal_type"):
            core["signal_type"] = _signal_to_signal_type(decision_type)
        core["position_advice"] = position_advice

        battle["sniper_points"] = sniper
        if "action_checklist" not in battle:
            battle["action_checklist"] = []
        position_strategy = battle.get("position_strategy")
        if not isinstance(position_strategy, dict) or not position_strategy:
            battle["position_strategy"] = {
                "suggested_position": _default_position_size(decision_type),
                "entry_plan": position_advice["no_position"],
                "risk_control": f"止损参考 {sniper.get('stop_loss', '待补充')}",
            }

        data_perspective = dashboard_block.get("data_perspective")
        if not isinstance(data_perspective, dict):
            data_perspective = {}
        if not data_perspective:
            built_data_perspective = self._build_data_perspective(ctx, key_levels)
            if built_data_perspective:
                data_perspective = built_data_perspective
        if data_perspective:
            dashboard_block["data_perspective"] = data_perspective

        dashboard_block["core_conclusion"] = core
        dashboard_block["intelligence"] = intelligence
        dashboard_block["battle_plan"] = battle

        key_points = payload.get("key_points")
        if not isinstance(key_points, list) or not key_points:
            key_points = [
                _truncate_text(op.reasoning, 120)
                for op in ctx.opinions
                if isinstance(op.reasoning, str) and op.reasoning.strip()
            ][:5]

        evidence_summary = payload.get("evidence_summary")
        if not isinstance(evidence_summary, dict):
            evidence_summary = {}
        evidence_summary.setdefault("bullish", decision_policy.get("bullish_evidence", [])[:6])
        evidence_summary.setdefault("bearish", decision_policy.get("bearish_evidence", [])[:6])
        evidence_summary.setdefault("risk", decision_policy.get("risk_evidence", [])[:6])
        if any(evidence_summary.values()):
            payload["evidence_summary"] = evidence_summary

        if not payload.get("contradiction_summary") and decision_policy.get("contradictions"):
            payload["contradiction_summary"] = " / ".join(decision_policy.get("contradictions", [])[:3])
        if not payload.get("invalidation_conditions") and decision_policy.get("invalidation_conditions"):
            payload["invalidation_conditions"] = decision_policy.get("invalidation_conditions", [])[:8]
        if not payload.get("next_watchlist") and decision_policy.get("watch_items"):
            payload["next_watchlist"] = decision_policy.get("watch_items", [])[:8]
        if not isinstance(payload.get("action_plan"), dict) and decision_policy:
            payload["action_plan"] = {
                "no_position": position_advice["no_position"],
                "has_position": position_advice["has_position"],
                "decision_policy": decision_policy.get("rationale", ""),
            }
        if not isinstance(payload.get("position_plan"), dict) and decision_policy:
            payload["position_plan"] = {
                "initial": _default_position_size(decision_type),
                "add": "Only add after price and volume confirm the thesis.",
                "reduce": "Reduce if risk flags increase or invalidation conditions trigger.",
                "stop_loss": sniper.get("stop_loss", "N/A"),
            }

        risk_warning = _first_non_empty_text(
            payload.get("risk_warning"),
            "；".join(risk_alerts[:3]),
            getattr(self._latest_opinion(ctx, {"risk"}), "reasoning", ""),
        )
        if not risk_warning:
            risk_warning = "暂无额外风险提示"

        payload["stock_name"] = _first_non_empty_text(payload.get("stock_name"), ctx.stock_name, ctx.stock_code)
        payload["sentiment_score"] = sentiment_score
        payload["trend_prediction"] = trend_prediction
        payload["operation_advice"] = operation_advice
        payload["decision_type"] = decision_type
        payload["confidence_level"] = _confidence_label(confidence)
        payload["analysis_summary"] = analysis_summary
        payload["key_points"] = key_points
        payload["risk_warning"] = risk_warning
        payload["dashboard"] = dashboard_block
        return payload

    def _collect_key_levels(
        self,
        ctx: AgentContext,
        payload: Dict[str, Any],
        dashboard_block: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Collect key price levels from dashboard payloads and agent opinions."""
        levels: Dict[str, Any] = {}

        def absorb(source: Any) -> None:
            if not isinstance(source, dict):
                return
            for key, value in source.items():
                normalized = _coerce_level_value(value)
                if normalized is not None and key not in levels:
                    levels[key] = normalized

        absorb(payload.get("key_levels"))
        absorb(dashboard_block.get("key_levels"))
        for opinion in reversed(ctx.opinions):
            absorb(getattr(opinion, "key_levels", {}))
            raw = opinion.raw_data if isinstance(opinion.raw_data, dict) else {}
            absorb(raw.get("key_levels"))
        return levels

    def _build_data_perspective(
        self,
        ctx: AgentContext,
        key_levels: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build a lightweight data_perspective block from cached market data."""
        realtime = ctx.get_data("realtime_quote")
        chip = ctx.get_data("chip_distribution")
        trend = ctx.get_data("trend_result")
        technical = self._latest_opinion(ctx, {"technical"})
        tech_raw = technical.raw_data if technical and isinstance(technical.raw_data, dict) else {}
        trend_dict = trend if isinstance(trend, dict) else {}

        data_perspective: Dict[str, Any] = {}
        ma_alignment = tech_raw.get("ma_alignment")
        trend_score = tech_raw.get("trend_score")
        if ma_alignment or trend_score is not None:
            data_perspective["trend_status"] = {
                "ma_alignment": ma_alignment or "N/A",
                "trend_score": trend_score if trend_score is not None else "N/A",
                "is_bullish": str(ma_alignment).lower() == "bullish",
            }

        def _bias_label(bias):
            if not isinstance(bias, (int, float)):
                return ""
            if bias > 5:
                return "超买"
            elif bias > 2:
                return "偏高"
            elif bias < -5:
                return "超卖"
            elif bias < -2:
                return "偏低"
            return "中性"

        def _r(val, n=2):
            """Round numeric values for display."""
            return round(val, n) if isinstance(val, (int, float)) else val

        def _pick(primary_dict, primary_key, fallback_dict, fallback_key, default="N/A"):
            """Pick first non-None value, avoiding falsy-zero trap."""
            v = primary_dict.get(primary_key)
            if v is not None:
                return v
            v2 = fallback_dict.get(fallback_key, default)
            return v2 if v2 is not None else default

        if isinstance(realtime, dict) or trend_dict:
            data_perspective["price_position"] = {
                "current_price": _r(_pick(trend_dict, "current_price", realtime or {}, "price")),
                "ma5": _r(_pick(trend_dict, "ma5", tech_raw, "ma5")),
                "ma10": _r(_pick(trend_dict, "ma10", tech_raw, "ma10")),
                "ma20": _r(_pick(trend_dict, "ma20", tech_raw, "ma20")),
                "bias_ma5": _r(_pick(trend_dict, "bias_ma5", tech_raw, "bias_ma5")),
                "bias_status": _bias_label(trend_dict.get("bias_ma5")) or tech_raw.get("bias_status", "N/A"),
                "support_level": key_levels.get("support") or key_levels.get("immediate_support") or "N/A",
                "resistance_level": key_levels.get("resistance") or key_levels.get("current_resistance") or "N/A",
            }
            data_perspective["volume_analysis"] = {
                "volume_ratio": (realtime or {}).get("volume_ratio", "N/A"),
                "turnover_rate": (realtime or {}).get("turnover_rate", "N/A"),
                "volume_status": trend_dict.get("volume_status") or tech_raw.get("volume_status", "N/A"),
                "volume_meaning": tech_raw.get("reasoning", "") if tech_raw else "",
            }

        if isinstance(chip, dict):
            concentration = chip.get("concentration_90")
            if concentration is None:
                concentration = chip.get("concentration")
            data_perspective["chip_structure"] = {
                "profit_ratio": chip.get("profit_ratio", "N/A"),
                "avg_cost": chip.get("avg_cost", "N/A"),
                "concentration": concentration if concentration is not None else "N/A",
                "chip_health": chip.get("chip_health", "一般"),
            }

        fundamental_context = ctx.get_data("fundamental_context")
        if isinstance(fundamental_context, dict):
            fm: Dict[str, Any] = {}
            for src_key, dst_key in [
                ("pe_ratio", "pe_ratio"), ("pb_ratio", "pb_ratio"),
                ("roe", "roe"), ("debt_ratio", "debt_ratio"),
                ("revenue_growth", "revenue_growth"),
                ("profit_growth", "profit_growth"),
                ("dividend_yield", "dividend_yield"),
                ("market_cap", "market_cap"),
            ]:
                val = fundamental_context.get(src_key)
                if val is not None:
                    fm[dst_key] = val
            if fm:
                data_perspective["financial_metrics"] = fm

        industry = self._latest_opinion(ctx, {"industry"})
        if industry and isinstance(industry.raw_data, dict):
            ind_raw = industry.raw_data
            sc: Dict[str, Any] = {}
            for src_key, dst_key in [
                ("sector_name", "sector_name"), ("sector_rank", "sector_rank"),
                ("sector_trend", "sector_trend"), ("peer_avg_pe", "peer_avg_pe"),
                ("relative_strength", "relative_strength"),
            ]:
                val = ind_raw.get(src_key)
                if val is not None:
                    sc[dst_key] = val
            if ind_raw.get("sector_leading") is not None:
                sc["sector_leading"] = ind_raw["sector_leading"]
            if sc:
                data_perspective["sector_comparison"] = sc

        current_price = _coerce_level_value(
            (data_perspective.get("price_position") or {}).get("current_price")
        )
        stop_loss_val = _coerce_level_value(key_levels.get("stop_loss") or key_levels.get("strong_support_stop_loss"))
        take_profit_val = _coerce_level_value(
            key_levels.get("take_profit")
            or key_levels.get("next_breakout_target")
            or key_levels.get("current_resistance")
            or key_levels.get("resistance")
        )
        if current_price is not None:
            rm: Dict[str, Any] = {}
            if take_profit_val is not None and isinstance(take_profit_val, (int, float)):
                upside = round((take_profit_val - current_price) / current_price * 100, 2)
                rm["potential_upside_pct"] = upside
            if stop_loss_val is not None and isinstance(stop_loss_val, (int, float)):
                downside = round((current_price - stop_loss_val) / current_price * 100, 2)
                rm["potential_downside_pct"] = downside
            if (
                rm.get("potential_upside_pct") is not None
                and rm.get("potential_downside_pct") is not None
                and rm["potential_downside_pct"] > 0
            ):
                rm["risk_reward_ratio"] = round(
                    rm["potential_upside_pct"] / rm["potential_downside_pct"], 2
                )
            if rm:
                data_perspective["risk_metrics"] = rm

        factor_scores = ctx.get_data("factor_scores")
        if isinstance(factor_scores, dict) and factor_scores:
            data_perspective["factor_scores"] = factor_scores

        return data_perspective

    def _collect_risk_alerts(
        self,
        ctx: AgentContext,
        intelligence: Dict[str, Any],
    ) -> List[str]:
        alerts: List[str] = []

        def absorb(values: Any) -> None:
            if not isinstance(values, list):
                return
            for item in values:
                text = ""
                if isinstance(item, str):
                    text = item.strip()
                elif isinstance(item, dict):
                    text = str(item.get("description") or item.get("title") or "").strip()
                if text and text not in alerts:
                    alerts.append(text)

        absorb(intelligence.get("risk_alerts"))
        intel = self._latest_opinion(ctx, {"intel"})
        intel_raw = intel.raw_data if intel and isinstance(intel.raw_data, dict) else {}
        absorb(intel_raw.get("risk_alerts"))
        risk = self._latest_opinion(ctx, {"risk"})
        risk_raw = risk.raw_data if risk and isinstance(risk.raw_data, dict) else {}
        absorb(risk_raw.get("flags"))
        for flag in ctx.risk_flags:
            description = str(flag.get("description", "")).strip()
            if description and description not in alerts:
                alerts.append(description)
        return alerts[:8]

    def _collect_positive_catalysts(
        self,
        ctx: AgentContext,
        intelligence: Dict[str, Any],
    ) -> List[str]:
        catalysts: List[str] = []

        def absorb(values: Any) -> None:
            if not isinstance(values, list):
                return
            for item in values:
                text = str(item).strip()
                if text and text not in catalysts:
                    catalysts.append(text)

        absorb(intelligence.get("positive_catalysts"))
        intel = self._latest_opinion(ctx, {"intel"})
        intel_raw = intel.raw_data if intel and isinstance(intel.raw_data, dict) else {}
        absorb(intel_raw.get("positive_catalysts"))
        return catalysts[:8]

    @staticmethod
    def _latest_opinion(ctx: AgentContext, names: set[str]) -> Optional[Any]:
        for opinion in reversed(ctx.opinions):
            if opinion.agent_name in names:
                return opinion
        return None

    def _select_base_opinion(self, ctx: AgentContext) -> Optional[Any]:
        preferred_groups = (
            {"decision"},
            {"skill_consensus", "strategy_consensus"},
            {"technical"},
            {"intel"},
            {"risk"},
        )
        for names in preferred_groups:
            opinion = self._latest_opinion(ctx, names)
            if opinion is not None:
                return opinion
        if ctx.opinions:
            return ctx.opinions[-1]
        return None

    @staticmethod
    def _mark_partial_dashboard(
        dashboard: Dict[str, Any],
        *,
        note: str,
    ) -> Dict[str, Any]:
        tagged = dict(dashboard)
        summary = _first_non_empty_text(tagged.get("analysis_summary"))
        prefix = "[降级结果] "
        if summary and not summary.startswith(prefix):
            tagged["analysis_summary"] = prefix + summary
        elif not summary:
            tagged["analysis_summary"] = prefix + note

        warning = _first_non_empty_text(tagged.get("risk_warning"))
        tagged["risk_warning"] = f"{note} {warning}".strip() if warning else note

        nested = tagged.get("dashboard")
        if isinstance(nested, dict):
            nested = dict(nested)
            core = nested.get("core_conclusion")
            if isinstance(core, dict):
                core = dict(core)
                one_sentence = _first_non_empty_text(core.get("one_sentence"), tagged.get("analysis_summary"))
                if one_sentence and not str(one_sentence).startswith(prefix):
                    core["one_sentence"] = prefix + str(one_sentence)
                nested["core_conclusion"] = core
            tagged["dashboard"] = nested
        return tagged

    def _apply_risk_override(self, ctx: AgentContext) -> None:
        """Apply risk-agent veto/downgrade rules to the final dashboard.

        Idempotent: skips if already applied in this pipeline run.
        """
        if ctx.get_data("risk_override_applied"):
            return

        if not getattr(self.config, "agent_risk_override", True):
            return

        dashboard = ctx.get_data("final_dashboard")
        if not isinstance(dashboard, dict):
            return

        risk_opinion = next((op for op in reversed(ctx.opinions) if op.agent_name == "risk"), None)
        risk_raw = risk_opinion.raw_data if risk_opinion and isinstance(risk_opinion.raw_data, dict) else {}

        adjustment = str(risk_raw.get("signal_adjustment") or "").lower()
        has_high_flag = any(str(flag.get("severity", "")).lower() == "high" for flag in ctx.risk_flags)
        veto_buy = bool(risk_raw.get("veto_buy")) or adjustment == "veto" or has_high_flag

        current_signal = normalize_decision_signal(dashboard.get("decision_type", "hold"))
        new_signal = current_signal
        if veto_buy and current_signal == "buy":
            new_signal = "hold"
        elif adjustment == "downgrade_one":
            new_signal = _downgrade_signal(current_signal, steps=1)
        elif adjustment == "downgrade_two":
            new_signal = _downgrade_signal(current_signal, steps=2)

        if new_signal == current_signal:
            return

        dashboard["decision_type"] = new_signal
        dashboard["risk_warning"] = self._merge_risk_warning(
            dashboard.get("risk_warning"),
            risk_raw,
            ctx.risk_flags,
            new_signal,
        )

        sentiment_score = dashboard.get("sentiment_score")
        try:
            score = int(sentiment_score)
        except (TypeError, ValueError):
            score = 50
        dashboard["sentiment_score"] = _adjust_sentiment_score(score, new_signal)

        operation_advice = dashboard.get("operation_advice")
        if isinstance(operation_advice, str):
            dashboard["operation_advice"] = _adjust_operation_advice(operation_advice, new_signal)

        summary = dashboard.get("analysis_summary")
        if isinstance(summary, str) and summary:
            dashboard["analysis_summary"] = f"[风控下调: {current_signal} -> {new_signal}] {summary}"

        dashboard_block = dashboard.get("dashboard")
        if isinstance(dashboard_block, dict):
            core = dashboard_block.get("core_conclusion")
            if isinstance(core, dict):
                signal_type = {
                    "buy": "🟡持有观望",
                    "hold": "🟡持有观望",
                    "sell": "🔴卖出信号",
                }.get(new_signal, "⚠️风险警告")
                core["signal_type"] = signal_type
                sentence = core.get("one_sentence")
                if isinstance(sentence, str) and sentence:
                    core["one_sentence"] = f"{sentence}（风控下调）"
                position = core.get("position_advice")
                if isinstance(position, dict):
                    if new_signal == "hold":
                        position["no_position"] = "风险未解除前先观望，等待更清晰的入场条件。"
                        position["has_position"] = "谨慎持有并收紧止损，待风险缓解后再考虑加仓。"
                    elif new_signal == "sell":
                        position["no_position"] = "风险明显偏高，暂不新开仓。"
                        position["has_position"] = "优先控制回撤，建议减仓或退出高风险仓位。"

        ctx.set_data("final_dashboard", dashboard)
        ctx.set_data("risk_override_applied", {
            "from": current_signal,
            "to": new_signal,
            "adjustment": adjustment or ("veto" if veto_buy else "none"),
        })

        for opinion in reversed(ctx.opinions):
            if opinion.agent_name == "decision":
                opinion.signal = new_signal
                if isinstance(dashboard.get("analysis_summary"), str):
                    opinion.reasoning = dashboard["analysis_summary"]
                opinion.raw_data = dashboard
                break

        logger.info(
            "[Orchestrator] risk override applied: %s -> %s (adjustment=%s, high_flag=%s)",
            current_signal,
            new_signal,
            adjustment or ("veto" if veto_buy else "none"),
            has_high_flag,
        )

    @staticmethod
    def _merge_risk_warning(
        existing_warning: Any,
        risk_raw: Dict[str, Any],
        risk_flags: List[Dict[str, Any]],
        signal: str,
    ) -> str:
        """Build a concise risk warning after a forced downgrade."""
        warnings: List[str] = []
        if isinstance(existing_warning, str) and existing_warning.strip():
            warnings.append(existing_warning.strip())
        if isinstance(risk_raw.get("reasoning"), str) and risk_raw["reasoning"].strip():
            warnings.append(risk_raw["reasoning"].strip())
        for flag in risk_flags[:3]:
            description = str(flag.get("description", "")).strip()
            severity = str(flag.get("severity", "")).lower()
            if description:
                warnings.append(f"[{severity or 'risk'}] {description}")
        prefix = f"风控接管：最终信号已下调为 {signal}。"
        merged = " ".join(dict.fromkeys([prefix] + warnings))
        return merged[:500]


# Common English words (2-5 uppercase letters) that should NOT be treated as
# US stock tickers.  This set is checked by _extract_stock_code() and should
# be kept at module level to avoid re-creating it on every call.
_COMMON_WORDS: set[str] = {
    # Pronouns / articles / prepositions / conjunctions
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL",
    "CAN", "HAD", "HER", "WAS", "ONE", "OUR", "OUT", "HAS",
    "HIS", "HOW", "ITS", "LET", "MAY", "NEW", "NOW", "OLD",
    "SEE", "WAY", "WHO", "DID", "GET", "HIM", "USE", "SAY",
    "SHE", "TOO", "ANY", "WITH", "FROM", "THAT", "THAN",
    "THIS", "WHAT", "WHEN", "WILL", "JUST", "ALSO",
    "BEEN", "EACH", "HAVE", "MUCH", "ONLY", "OVER",
    "SOME", "SUCH", "THEM", "THEN", "THEY", "VERY",
    "WERE", "YOUR", "ABOUT", "AFTER", "COULD", "EVERY",
    "OTHER", "THEIR", "THERE", "THESE", "THOSE", "WHICH",
    "WOULD", "BEING", "STILL", "WHERE",
    # Finance/analysis jargon that looks like tickers
    "BUY", "SELL", "HOLD", "LONG", "PUT", "CALL",
    "ETF", "IPO", "RSI", "EPS", "PEG", "ROE", "ROA",
    "USA", "USD", "CNY", "HKD", "EUR", "GBP",
    "STOCK", "TRADE", "PRICE", "INDEX", "FUND",
    "HIGH", "LOW", "OPEN", "CLOSE", "STOP", "LOSS",
    "TREND", "BULL", "BEAR", "RISK", "CASH", "BOND",
    "MACD", "VWAP", "BOLL",
    # Greetings / filler words that often appear in chat messages
    "HELLO", "PLEASE", "THANKS", "CHECK", "LOOK", "THINK",
    "MAYBE", "GUESS", "TELL", "SHOW", "WHAT", "WHATS",
    "WHY", "WHEN", "HOWDY", "HEY", "HI",
}

_LOWERCASE_TICKER_HINTS = re.compile(
    r"分析|看看|查一?下|研究|诊断|走势|趋势|股价|股票|个股",
)


def _extract_stock_code(text: str) -> str:
    """Best-effort stock code extraction from free text."""
    # A-share 6-digit — use lookarounds instead of \b because Python's \b
    # does not fire at Chinese-character / digit boundaries.
    m = re.search(r'(?<!\d)((?:[03648]\d{5}|92\d{4}))(?!\d)', text)
    if m:
        return m.group(1)
    # HK — same lookaround approach
    m = re.search(r'(?<![a-zA-Z])(hk\d{5})(?!\d)', text, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    # US ticker — require 2+ uppercase letters bounded by non-alpha chars.
    m = re.search(r'(?<![a-zA-Z])([A-Z]{2,5}(?:\.[A-Z]{1,2})?)(?![a-zA-Z])', text)
    if m:
        candidate = m.group(1)
        if candidate not in _COMMON_WORDS:
            return candidate

    stripped = (text or "").strip()
    bare_match = re.fullmatch(r'([A-Za-z]{2,5}(?:\.[A-Za-z]{1,2})?)', stripped)
    if bare_match:
        candidate = bare_match.group(1).upper()
        if candidate not in _COMMON_WORDS:
            return candidate

    if not _LOWERCASE_TICKER_HINTS.search(stripped):
        return ""

    for match in re.finditer(r'(?<![a-zA-Z])([A-Za-z]{2,5}(?:\.[A-Za-z]{1,2})?)(?![a-zA-Z])', text):
        raw_candidate = match.group(1)
        candidate = raw_candidate.upper()
        if candidate in _COMMON_WORDS:
            continue
        return candidate
    return ""


def _downgrade_signal(signal: str, steps: int = 1) -> str:
    """Downgrade a dashboard decision signal by one or more levels."""
    order = ["buy", "hold", "sell"]
    try:
        index = order.index(signal)
    except ValueError:
        return signal
    return order[min(len(order) - 1, index + max(0, steps))]


def _adjust_sentiment_score(score: int, signal: str) -> int:
    """Clamp sentiment score into the target band for the overridden signal."""
    bands = {
        "buy": (60, 79),
        "hold": (40, 59),
        "sell": (0, 39),
    }
    low, high = bands.get(signal, (0, 100))
    return max(low, min(high, score))


def _adjust_operation_advice(advice: str, signal: str) -> str:
    """Normalize action wording to the overridden decision signal."""
    mapping = {
        "buy": "买入",
        "hold": "观望",
        "sell": "减仓/卖出",
    }
    if signal not in mapping:
        return advice
    if advice == mapping[signal]:
        return advice
    return f"{mapping[signal]}（原建议已被风控下调）"


def _signal_to_operation(signal: str) -> str:
    mapping = {
        "buy": "买入",
        "hold": "观望",
        "sell": "减仓/卖出",
    }
    return mapping.get(signal, "观望")


def _signal_to_signal_type(signal: str) -> str:
    mapping = {
        "buy": "🟢买入信号",
        "hold": "⚪观望信号",
        "sell": "🔴卖出信号",
    }
    return mapping.get(signal, "⚪观望信号")


def _default_position_advice(signal: str) -> Dict[str, str]:
    mapping = {
        "buy": {
            "no_position": "可结合支撑位分批试仓，避免一次性追高。",
            "has_position": "可继续持有，回踩关键位不破再考虑加仓。",
        },
        "hold": {
            "no_position": "暂不追高，等待更清晰的入场条件。",
            "has_position": "以观察为主，跌破止损位再执行风控。",
        },
        "sell": {
            "no_position": "暂不参与，等待风险充分释放。",
            "has_position": "优先控制回撤，按计划减仓或离场。",
        },
    }
    return mapping.get(signal, mapping["hold"])


def _default_position_size(signal: str) -> str:
    mapping = {
        "buy": "轻仓试仓",
        "hold": "控制仓位",
        "sell": "降仓防守",
    }
    return mapping.get(signal, "控制仓位")


def _normalize_operation_advice_value(value: Any, signal: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _signal_to_operation(signal)


def _confidence_label(confidence: float) -> str:
    if confidence >= 0.75:
        return "高"
    if confidence >= 0.45:
        return "中"
    return "低"


def _estimate_sentiment_score(signal: str, confidence: float) -> int:
    confidence = max(0.0, min(1.0, float(confidence)))
    bands = {
        "buy": (65, 79),
        "hold": (45, 59),
        "sell": (20, 39),
    }
    low, high = bands.get(signal, (45, 59))
    return int(round(low + (high - low) * confidence))


def _coerce_level_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).replace(",", "").replace("，", "").strip()
    if not text or text.upper() == "N/A" or text in {"-", "—"}:
        return None
    try:
        return round(float(text), 2)
    except ValueError:
        return text


def _pick_first_level(*values: Any) -> Any:
    for value in values:
        normalized = _coerce_level_value(value)
        if normalized is not None:
            return normalized
    return None


def _level_values_equal(left: Any, right: Any) -> bool:
    left_normalized = _coerce_level_value(left)
    right_normalized = _coerce_level_value(right)
    return (
        left_normalized is not None
        and right_normalized is not None
        and left_normalized == right_normalized
    )


def _first_non_empty_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _truncate_text(text: Any, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _extract_latest_news_title(intelligence: Dict[str, Any]) -> str:
    key_news = intelligence.get("key_news")
    if isinstance(key_news, list):
        for item in key_news:
            if isinstance(item, dict):
                title = str(item.get("title", "")).strip()
                if title:
                    return title
    latest_news = intelligence.get("latest_news")
    if isinstance(latest_news, str) and latest_news.strip():
        return latest_news.strip()
    return ""
