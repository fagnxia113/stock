# -*- coding: utf-8 -*-
"""
DecisionAgent — final synthesis and decision-making specialist.

Responsible for:
- Aggregating opinions from technical + intel + risk + skill agents
- Producing the final Decision Dashboard JSON
- Generating actionable buy/hold/sell recommendations with price levels
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

from src.agent.agents.base_agent import BaseAgent
from src.agent.protocols import AgentContext, AgentOpinion, normalize_decision_signal
from src.report_language import normalize_report_language

logger = logging.getLogger(__name__)


class DecisionAgent(BaseAgent):
    """Synthesise prior agent opinions into the final dashboard."""

    agent_name = "decision"
    max_steps = 3  # pure synthesis, should not need many tool calls
    tool_names: Optional[List[str]] = []  # no tool access — works from context only

    @staticmethod
    def _is_chat_mode(ctx: AgentContext) -> bool:
        return ctx.meta.get("response_mode") == "chat"

    def system_prompt(self, ctx: AgentContext) -> str:
        report_language = normalize_report_language(ctx.meta.get("report_language", "zh"))
        if self._is_chat_mode(ctx):
            prompt = """\
You are a **Decision Synthesis Agent** replying directly to the user's latest
stock-analysis question.

You will receive structured opinions from the technical, intelligence, risk,
and skill stages. Synthesize them into a concise, natural-language answer.

Requirements:
- Answer the user's actual question directly
- Use Markdown when helpful
- Keep the response practical and specific
- Highlight the main signal, key reasoning, and major risks
- Separate what is known, what is inferred, and what would invalidate the view
- Provide a concrete watchlist of next conditions instead of vague optimism
- Do NOT output JSON or code fences unless the user explicitly asks for them
"""
            if report_language == "en":
                return prompt + "\nAlways answer in English.\n"
            return prompt + "\n默认使用中文回答。\n"

        skills = ""
        if self.skill_instructions:
            skills = f"\n## Active Trading Skills\n\n{self.skill_instructions}\n"

        prompt = f"""\
You are a **Decision Synthesis Agent** that produces the final investment \
Decision Dashboard.

You will receive:
1. Structured opinions from a Technical Agent and an Intel Agent
2. Any risk flags raised by a Risk Agent
3. Skill evaluation results (if applicable)
4. Industry analysis from an Industry Agent (if available)
5. Capital flow analysis from a Capital Flow Agent (if available)
6. Devil's Advocate audit identifying cognitive biases and weak links (if available)
7. Deep debate consensus with evidence mapping and cross-examination (if available)
8. Scenario analysis with bull/base/bear probabilities (if available)
9. Quantitative factor scores from a Factor Scoring Agent (if available)
10. Market sentiment analysis from a Sentiment Agent (if available)
11. Deep fundamental analysis from a Fundamental Agent (if available)

Your task: synthesise all inputs into a single, actionable Decision Dashboard.
{skills}
## Professional Output Discipline
- Separate evidence from inference. Do not treat news sentiment as fact.
- Treat realtime quote / recent OHLCV / volume data as the highest-weight evidence.
- If realtime quote is missing, stale, or conflicts with your draft, cap the decision at hold unless the user explicitly asks for a historical-only view.
- Every final recommendation must include an invalidation condition.
- If evidence is mixed or stale, choose "hold" and state what data would change it.
- Never recommend chasing after a large short-term move unless pullback/volume confirmation is specified.
- Position advice must be conditional and risk-first, not absolute.

## ⚠️ Long Chain-of-Thought Requirement
Before producing the final JSON, you MUST think through the following steps \
explicitly in your reasoning:
1. **Evidence Inventory**: List all bullish and bearish evidence, classified by \
   strength (hard/soft/speculation)
2. **Contradiction Resolution**: For every pair of conflicting opinions, state \
   which you trust more and WHY
3. **Bias Check**: Review the Devil's Advocate audit. For each identified bias, \
   state whether you've adjusted for it
4. **Scenario Weighting**: If scenario analysis is available, explain how the \
   probability-weighted expected value influences your decision
5. **Thesis Stress Test**: State explicitly what would make you WRONG, and how \
   likely that is
6. **Confidence Calibration**: Is your confidence justified by EVIDENCE QUALITY, \
   or just by the number of agreeing agents?

## Core Principles
1. **Core conclusion first** — one sentence, ≤30 chars
2. **Split advice** — different for no-position vs has-position
3. **Precise sniper levels** — concrete price numbers, no hedging
4. **Checklist visual** — ✅⚠️❌ for each checkpoint
5. **Risk priority** — risk alerts must be prominent. If high-severity risk exists, \
   the overall signal must be downgraded accordingly.

## Signal Weighting Guidelines
- Technical opinion weight: ~25%
- Intel / sentiment weight: ~15%
- Industry analysis weight: ~10% (if available)
- Capital flow weight: ~15% (if available)
- Devil's Advocate audit: no direct signal weight, but MUST adjust confidence \
  downward if overall_assessment is "fragile" or bias risks are "high"
- Debate consensus weight: ~20% (if available, represents deliberated conclusion)
- Scenario analysis expected value: ~10% (if available, probability-weighted)
- Factor scores: cross-check against qualitative signal (if available)
- Risk flags weight: ~5% (negative override: any high-severity risk caps signal at "hold")
- If a skill opinion is present, blend it at 15% weight (reducing others proportionally)

## When Devil's Advocate Is Present
If a DevilsAdvocateAgent opinion is available:
- Review the bias_audit and adjust confidence accordingly
- Address each "weakest_link" in your reasoning
- If overall_assessment is "fragile", cap confidence at 0.5 and signal at "hold"
- If overall_assessment is "moderate", reduce confidence by 0.1
- Incorporate "what_could_go_wrong" into risk_warning

## When Debate Agent Is Present
If a DebateAgent opinion is available:
- The debate consensus signal should be strongly considered
- Include the trading plan (entry/exit/stop-loss) in the dashboard
- Highlight the evidence_map (bullish vs bearish evidence tiers)
- Include thesis_breakers as explicit monitoring points
- If confidence_calibration shows significant adjustment, reflect that

## When Scenario Analysis Is Present
If a ScenarioAnalysisAgent opinion is available:
- Reference the probability-weighted expected return in your confidence
- Include swing_factors in the monitoring checklist
- If bear case probability > 30%, cap signal at "hold"
- If bear case probability > 50%, signal must be "sell"
- Include recommended_position_sizing in position advice

## When Factor Scores Are Present
If a FactorScoringAgent opinion is available:
- Reference the composite score and dimension scores in the dashboard
- Flag any dimension conflicts (e.g. technical bullish but fundamental bearish)
- Use the score interpretation to calibrate confidence_level

## Scoring
- 80-100: buy (all conditions met, high conviction)
- 60-79: buy (mostly positive, minor caveats)
- 40-59: hold (mixed signals, or risk present)
- 20-39: sell (negative trend + risk)
- 0-19: sell (major risk + bearish)

## Output Format
Return a valid JSON object following the Decision Dashboard schema.  The JSON \
must include at minimum these top-level keys:
  stock_name, sentiment_score, trend_prediction, operation_advice,
  decision_type, confidence_level, dashboard, analysis_summary,
  key_points, risk_warning

Also include these professional decision fields when possible:
  evidence_summary, contradiction_summary, invalidation_conditions,
  action_plan, position_plan, next_watchlist

Suggested shape:
  "evidence_summary": {"bullish": [], "bearish": [], "neutral": []}
  "contradiction_summary": "How conflicting agent opinions were resolved"
  "invalidation_conditions": ["specific price/data/event conditions"]
  "action_plan": {"no_position": "...", "has_position": "..."}
  "position_plan": {"initial": "...", "add": "...", "reduce": "...", "stop_loss": "..."}
  "next_watchlist": ["what to check tomorrow or before acting"]

Important: ``decision_type`` must stay within the existing enum
``buy|hold|sell``. Express stronger conviction via ``confidence_level``,
``sentiment_score``, and the natural-language fields instead of inventing
new decision_type values.
"""
        if report_language == "en":
            return prompt + """

## Output Language
- Keep every JSON key unchanged.
- `decision_type` must remain `buy|hold|sell`.
- Write all human-readable JSON values in English.
"""
        return prompt + """

## 输出语言
- 所有 JSON 键名保持不变。
- `decision_type` 必须保持为 `buy|hold|sell`。
- 所有面向用户的人类可读文本值必须使用中文。
"""

    def build_user_message(self, ctx: AgentContext) -> str:
        if self._is_chat_mode(ctx):
            parts = [
                "# User Question",
                ctx.query,
                "",
                f"Stock: {ctx.stock_code} ({ctx.stock_name})" if ctx.stock_name else f"Stock: {ctx.stock_code}",
                "",
            ]
        else:
            parts = [
                f"# Synthesis Request for {ctx.stock_code}",
                f"Stock: {ctx.stock_code} ({ctx.stock_name})" if ctx.stock_name else f"Stock: {ctx.stock_code}",
                "",
            ]

        # Feed prior opinions
        if ctx.opinions:
            parts.append("## Agent Opinions")
            for op in ctx.opinions:
                parts.append(f"\n### {op.agent_name}")
                parts.append(f"Signal: {op.signal} | Confidence: {op.confidence:.2f}")
                parts.append(f"Reasoning: {op.reasoning}")
                if op.key_levels:
                    parts.append(f"Key levels: {json.dumps(op.key_levels)}")
                if op.raw_data:
                    extra_keys = {k: v for k, v in op.raw_data.items()
                                  if k not in ("signal", "confidence", "reasoning", "key_levels")}
                    if extra_keys:
                        parts.append(f"Extra data: {json.dumps(extra_keys, ensure_ascii=False, default=str)}")
                parts.append("")

        # Feed risk flags
        if ctx.risk_flags:
            parts.append("## Risk Flags")
            for rf in ctx.risk_flags:
                parts.append(f"- [{rf.get('severity', 'medium')}] {rf.get('category', '')}: {rf.get('description', '')}")
            parts.append("")

        devils_advocate_audit = ctx.get_data("devils_advocate_audit")
        if devils_advocate_audit and isinstance(devils_advocate_audit, dict):
            audit_brief = {
                "overall_assessment": devils_advocate_audit.get("overall_assessment"),
                "weakest_links": devils_advocate_audit.get("weakest_links", []),
                "what_could_go_wrong": devils_advocate_audit.get("what_could_go_wrong", []),
                "bias_audit": devils_advocate_audit.get("bias_audit", {}),
                "confidence_adjustment": devils_advocate_audit.get("confidence_adjustment"),
            }
            parts.append("## ⚠️ Devil's Advocate Audit")
            parts.append(json.dumps(audit_brief, ensure_ascii=False, indent=2))
            parts.append("")

        scenario_analysis = ctx.get_data("scenario_analysis")
        if scenario_analysis and isinstance(scenario_analysis, dict):
            scenario_brief = {
                "scenarios": scenario_analysis.get("scenarios", {}),
                "expected_value": scenario_analysis.get("expected_value", {}),
                "swing_factors": scenario_analysis.get("swing_factors", []),
                "recommended_position_sizing": scenario_analysis.get("recommended_position_sizing"),
            }
            parts.append("## 📊 Scenario Analysis")
            parts.append(json.dumps(scenario_brief, ensure_ascii=False, indent=2))
            parts.append("")

        evidence_map = ctx.get_data("evidence_map")
        if evidence_map and isinstance(evidence_map, dict):
            parts.append("## 🔍 Evidence Map from Debate")
            parts.append(json.dumps(evidence_map, ensure_ascii=False, indent=2)[:2000])
            parts.append("")

        evidence_pool = ctx.get_data("evidence_pool")
        if evidence_pool and isinstance(evidence_pool, list):
            parts.append("## Evidence Pool")
            parts.append(
                json.dumps(evidence_pool[:80], ensure_ascii=False, indent=2, default=str)[:6000]
            )
            parts.append(
                "Use the evidence pool as the source of truth. Separate hard evidence, "
                "soft evidence, contradictions, invalidation conditions, and watch items."
            )
            parts.append("")

        decision_policy = ctx.get_data("decision_policy")
        if decision_policy and isinstance(decision_policy, dict):
            parts.append("## Rule-Based Decision Policy")
            parts.append(json.dumps(decision_policy, ensure_ascii=False, indent=2, default=str)[:4000])
            parts.append(
                "Use this policy as a calibration anchor. You may disagree only if you "
                "explicitly explain which evidence overrides the rule score."
            )
            parts.append("")

        data_quality_summary = ctx.get_data("data_quality_summary")
        if data_quality_summary and isinstance(data_quality_summary, dict):
            parts.append("## Data Quality Summary")
            parts.append(json.dumps(data_quality_summary, ensure_ascii=False, indent=2, default=str)[:2500])
            parts.append(
                "Hard market data has priority over model inference. If hard data is missing, "
                "state the limitation and reduce confidence."
            )
            parts.append("")

        thesis_breakers = ctx.get_data("thesis_breakers")
        if thesis_breakers and isinstance(thesis_breakers, list):
            parts.append("## 🎯 Thesis Breakers")
            for tb in thesis_breakers:
                parts.append(f"- {tb}")
            parts.append("")

        # Skill meta
        requested_skills = ctx.meta.get("skills_requested") or ctx.meta.get("strategies_requested")
        if requested_skills:
            parts.append(f"## Skills: {', '.join(requested_skills)}")
            parts.append("")

        if self._is_chat_mode(ctx):
            parts.append(
                "Answer the user in natural language using the evidence above. "
                "Do not output JSON unless the user explicitly requests structured data."
            )
        else:
            parts.append("Synthesise the above into the Decision Dashboard JSON.")
        return "\n".join(parts)

    def post_process(self, ctx: AgentContext, raw_text: str) -> Optional[AgentOpinion]:
        """Store the parsed dashboard in ctx.meta; also return an opinion."""
        if self._is_chat_mode(ctx):
            text = (raw_text or "").strip()
            if not text:
                return None

            ctx.set_data("final_response_text", text)
            prior = next((op for op in reversed(ctx.opinions) if op.agent_name != self.agent_name), None)
            return AgentOpinion(
                agent_name=self.agent_name,
                signal=prior.signal if prior is not None else "hold",
                confidence=prior.confidence if prior is not None else 0.5,
                reasoning=text,
                raw_data={"response_mode": "chat"},
            )

        from src.agent.runner import parse_dashboard_json

        dashboard = parse_dashboard_json(raw_text)
        if dashboard:
            dashboard["decision_type"] = normalize_decision_signal(
                dashboard.get("decision_type", "hold")
            )
            ctx.set_data("final_dashboard", dashboard)
            try:
                _raw_score = dashboard.get("sentiment_score", 50) or 50
                _score = float(_raw_score)
            except (TypeError, ValueError):
                _score = 50.0
            return AgentOpinion(
                agent_name=self.agent_name,
                signal=dashboard.get("decision_type", "hold"),
                confidence=min(1.0, _score / 100.0),
                reasoning=dashboard.get("analysis_summary", ""),
                raw_data=dashboard,
            )
        else:
            # Even if JSON parsing fails, store the raw text for downstream use
            ctx.set_data("final_dashboard_raw", raw_text)
            logger.warning("[DecisionAgent] failed to parse dashboard JSON")
            return None
