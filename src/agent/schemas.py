# -*- coding: utf-8 -*-
"""
Structured schemas for professional stock-analysis agents.

These models are intentionally lightweight: LLMs may return extra fields, so
validation normalizes the fields needed by downstream synthesis and UI while
preserving the original payload in each AgentOpinion.raw_data.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Type

from pydantic import BaseModel, Field, ValidationError

from src.agent.protocols import AgentContext


SignalLabel = Literal["strong_buy", "buy", "hold", "sell", "strong_sell"]
RiskLevel = Literal["high", "medium", "low", "none"]


class AgentPayloadBase(BaseModel):
    signal: SignalLabel = "hold"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reasoning: str = ""
    evidence: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    contradictory_evidence: List[str] = Field(default_factory=list)
    invalid_if: List[str] = Field(default_factory=list)
    watch_items: List[str] = Field(default_factory=list)


class TechnicalOpinionPayload(AgentPayloadBase):
    key_levels: Dict[str, float] = Field(default_factory=dict)
    action_triggers: Dict[str, str] = Field(default_factory=dict)
    trend_score: Optional[float] = None
    ma_alignment: str = ""
    volume_status: str = ""
    pattern: str = ""


class IntelOpinionPayload(AgentPayloadBase):
    risk_alerts: List[str] = Field(default_factory=list)
    positive_catalysts: List[str] = Field(default_factory=list)
    sentiment_label: str = ""
    capital_flow_signal: str = ""
    key_news: List[Dict[str, Any]] = Field(default_factory=list)


class RiskFlagPayload(BaseModel):
    category: str = "unknown"
    severity: Literal["high", "medium", "low"] = "medium"
    description: str = ""
    source: str = ""


class RiskOpinionPayload(BaseModel):
    risk_level: RiskLevel = "none"
    risk_score: float = Field(default=50.0, ge=0.0, le=100.0)
    bear_case: str = ""
    thesis_breakers: List[str] = Field(default_factory=list)
    flags: List[RiskFlagPayload] = Field(default_factory=list)
    veto_buy: bool = False
    reasoning: str = ""
    signal_adjustment: str = "none"
    evidence: List[str] = Field(default_factory=list)


class CapitalFlowOpinionPayload(AgentPayloadBase):
    main_force_signal: str = ""
    northbound_signal: str = ""
    margin_signal: str = ""
    accumulation_stage: str = ""
    smart_money_signal: str = ""
    volume_price_divergence: str = ""
    key_observations: List[str] = Field(default_factory=list)


class FundamentalOpinionPayload(AgentPayloadBase):
    profitability_score: Optional[float] = None
    solvency_score: Optional[float] = None
    growth_score: Optional[float] = None
    valuation_score: Optional[float] = None
    valuation_verdict: str = ""
    earnings_quality: str = ""
    growth_sustainability: str = ""
    key_strengths: List[str] = Field(default_factory=list)
    key_concerns: List[str] = Field(default_factory=list)
    financial_highlights: Dict[str, Any] = Field(default_factory=dict)


def model_to_dict(model: BaseModel) -> Dict[str, Any]:
    """Return a dict for pydantic v1/v2 without coupling to either version."""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def validate_payload(
    schema: Type[BaseModel],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate an LLM payload and return a normalized dict.

    Extra original fields are preserved so downstream prompts do not lose
    useful provider-specific or agent-specific details.
    """
    if not isinstance(payload, dict):
        payload = {}

    try:
        model = schema(**payload)
        normalized = dict(payload)
        normalized.update(model_to_dict(model))
        normalized["_schema_valid"] = True
        return normalized
    except ValidationError as exc:
        normalized = dict(payload)
        normalized["_schema_valid"] = False
        normalized["_schema_errors"] = [str(err) for err in exc.errors()]
        normalized["signal"] = _coerce_signal(payload.get("signal"))
        normalized["confidence"] = _coerce_confidence(payload.get("confidence"))
        normalized.setdefault("reasoning", "")
        normalized.setdefault("evidence", [])
        normalized.setdefault("risks", [])
        normalized.setdefault("contradictory_evidence", [])
        normalized.setdefault("invalid_if", [])
        normalized.setdefault("watch_items", [])
        return normalized


def append_evidence_pool(
    ctx: AgentContext,
    *,
    agent_name: str,
    payload: Dict[str, Any],
) -> None:
    """Append normalized evidence/risk/watch items to ctx.data['evidence_pool']."""
    pool = ctx.get_data("evidence_pool") or []
    if not isinstance(pool, list):
        pool = []

    def add_items(items: Any, kind: str) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, str):
                claim = item.strip()
                detail: Dict[str, Any] = {}
            elif isinstance(item, dict):
                claim = str(item.get("claim") or item.get("title") or item.get("description") or "").strip()
                detail = dict(item)
            else:
                continue
            if not claim:
                continue
            source_type = detail.get("source_type") or _infer_source_type(agent_name, kind)
            source = detail.get("source") or ("agent_output" if source_type == "inference" else "unknown")
            pool.append({
                "agent": agent_name,
                "kind": kind,
                "claim": claim,
                "detail": detail,
                "source_type": source_type,
                "source": source,
                "trust_score": detail.get("trust_score"),
            })

    add_items(payload.get("evidence"), "evidence")
    add_items(payload.get("positive_catalysts"), "catalyst")
    add_items(payload.get("risk_alerts"), "risk")
    add_items(payload.get("risks"), "risk")
    add_items(payload.get("contradictory_evidence"), "contradiction")
    add_items(payload.get("invalid_if"), "invalidation")
    add_items(payload.get("thesis_breakers"), "invalidation")
    add_items(payload.get("watch_items"), "watch")
    add_items(payload.get("key_observations"), "evidence")
    add_items(payload.get("key_strengths"), "evidence")
    add_items(payload.get("key_concerns"), "risk")

    ctx.set_data("evidence_pool", pool)


def _coerce_signal(value: Any) -> str:
    if isinstance(value, str) and value in {"strong_buy", "buy", "hold", "sell", "strong_sell"}:
        return value
    return "hold"


def _infer_source_type(agent_name: str, kind: str) -> str:
    if kind in {"risk", "contradiction", "invalidation", "watch"}:
        return "inference"
    mapping = {
        "technical": "technical",
        "capital_flow": "capital_flow",
        "fundamental": "fundamental",
        "intel": "news",
        "sentiment": "sentiment",
        "risk": "inference",
    }
    return mapping.get(agent_name, "inference")


def _coerce_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, number))
