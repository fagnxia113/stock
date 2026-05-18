# -*- coding: utf-8 -*-
"""Data trust scoring helpers for stock-analysis agents.

LLM opinions should be calibrated against the quality of the data they rely on.
This module gives tool payloads a compact, uniform trust profile so the
decision layer can prioritize realtime market data over model inference.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional


SOURCE_TRUST: Dict[str, float] = {
    "tushare": 0.95,
    "tencent": 0.92,
    "akshare_sina": 0.88,
    "akshare_qq": 0.88,
    "efinance": 0.84,
    "akshare_em": 0.82,
    "db_cache": 0.72,
    "history_close": 0.68,
    "fundamental_context": 0.78,
    "capital_flow": 0.76,
    "cache_fallback": 0.45,
    "missing": 0.0,
    "unknown": 0.55,
}

TYPE_WEIGHT: Dict[str, float] = {
    "realtime": 1.00,
    "history": 0.85,
    "technical": 0.80,
    "fundamental": 0.75,
    "capital_flow": 0.72,
    "sentiment": 0.45,
    "news": 0.45,
    "inference": 0.20,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def assess_payload(
    payload: Dict[str, Any],
    *,
    source_type: str,
    source: Optional[str] = None,
    as_of: Optional[str] = None,
    stale: Optional[bool] = None,
) -> Dict[str, Any]:
    """Return a compact data-quality block for a tool payload."""
    source_name = _normalize_source(source or payload.get("source"))
    freshness_seconds = _freshness_seconds(as_of or payload.get("as_of") or payload.get("collected_at"))
    is_stale = bool(stale) if stale is not None else _infer_stale(source_type, freshness_seconds, payload)
    source_score = SOURCE_TRUST.get(source_name, SOURCE_TRUST["unknown"])
    type_score = TYPE_WEIGHT.get(source_type, TYPE_WEIGHT["inference"])
    freshness_score = _freshness_score(source_type, freshness_seconds, is_stale)
    completeness_score = _completeness_score(source_type, payload)
    trust_score = round(source_score * type_score * freshness_score * completeness_score, 3)

    return {
        "source_type": source_type,
        "source": source_name,
        "as_of": as_of or payload.get("as_of") or payload.get("collected_at") or utc_now_iso(),
        "freshness_seconds": freshness_seconds,
        "is_stale": is_stale,
        "source_score": round(source_score, 3),
        "type_score": round(type_score, 3),
        "freshness_score": round(freshness_score, 3),
        "completeness_score": round(completeness_score, 3),
        "trust_score": trust_score,
    }


def build_context_quality_summary(data: Dict[str, Any]) -> Dict[str, Any]:
    """Summarize the best available data quality in AgentContext.data."""
    blocks: Dict[str, Any] = {}
    for key, source_type in (
        ("realtime_quote", "realtime"),
        ("daily_history", "history"),
        ("trend_result", "technical"),
        ("chip_distribution", "technical"),
        ("fundamental_context", "fundamental"),
        ("news_context", "news"),
    ):
        value = data.get(key)
        if not isinstance(value, dict):
            continue
        quality = value.get("data_quality")
        if isinstance(quality, dict):
            blocks[key] = quality
        else:
            blocks[key] = assess_payload(value, source_type=source_type)

    trust_scores = [
        float(block.get("trust_score"))
        for block in blocks.values()
        if isinstance(block.get("trust_score"), (int, float))
    ]
    realtime_quality = blocks.get("realtime_quote")
    return {
        "blocks": blocks,
        "best_trust_score": max(trust_scores) if trust_scores else 0.0,
        "has_realtime": bool(realtime_quality and not realtime_quality.get("is_stale")),
        "realtime_trust_score": (
            realtime_quality.get("trust_score", 0.0)
            if isinstance(realtime_quality, dict)
            else 0.0
        ),
    }


def evidence_trust_from_detail(detail: Dict[str, Any], *, default_kind: str = "inference") -> float:
    """Read or infer trust_score for an evidence-pool detail payload."""
    if not isinstance(detail, dict):
        return TYPE_WEIGHT["inference"]
    quality = detail.get("data_quality")
    if isinstance(quality, dict) and isinstance(quality.get("trust_score"), (int, float)):
        return float(quality["trust_score"])
    if isinstance(detail.get("trust_score"), (int, float)):
        return float(detail["trust_score"])
    source_type = str(detail.get("source_type") or default_kind or "inference").lower()
    source = str(detail.get("source") or "unknown")
    return assess_payload(detail, source_type=source_type, source=source).get("trust_score", 0.2)


def best_realtime_price(data: Dict[str, Any]) -> Optional[float]:
    quote = data.get("realtime_quote")
    if not isinstance(quote, dict):
        return None
    for key in ("price", "current_price", "close"):
        value = quote.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return None


def _normalize_source(source: Any) -> str:
    text = str(source or "unknown").strip().lower()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text or "unknown"


def _freshness_seconds(as_of: Any) -> Optional[int]:
    if not isinstance(as_of, str) or not as_of.strip():
        return None
    text = as_of.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))


def _freshness_score(source_type: str, freshness_seconds: Optional[int], is_stale: bool) -> float:
    if is_stale:
        return 0.35
    if freshness_seconds is None:
        return 0.80 if source_type == "realtime" else 0.70
    if source_type == "realtime":
        if freshness_seconds <= 15 * 60:
            return 1.0
        if freshness_seconds <= 60 * 60:
            return 0.82
        return 0.50
    if source_type in {"history", "technical"}:
        if freshness_seconds <= 3 * 24 * 60 * 60:
            return 0.95
        return 0.75
    return 0.80


def _infer_stale(source_type: str, freshness_seconds: Optional[int], payload: Dict[str, Any]) -> bool:
    if payload.get("is_stale") is True or payload.get("stale") is True:
        return True
    if source_type == "realtime" and freshness_seconds is not None:
        return freshness_seconds > 60 * 60
    return False


def _completeness_score(source_type: str, payload: Dict[str, Any]) -> float:
    required_by_type = {
        "realtime": ("price", "change_pct", "volume"),
        "history": ("data", "actual_records"),
        "technical": ("current_price",),
        "fundamental": ("fundamental_context",),
        "capital_flow": ("status",),
    }
    required = required_by_type.get(source_type)
    if not required:
        return 0.85
    present = 0
    for key in required:
        value = payload.get(key)
        if value not in (None, "", [], {}):
            present += 1
    return max(0.40, present / len(required))


def weighted_average(values: Iterable[float]) -> float:
    numbers = [float(v) for v in values if isinstance(v, (int, float))]
    if not numbers:
        return 0.0
    return sum(numbers) / len(numbers)
