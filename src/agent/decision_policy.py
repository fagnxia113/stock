# -*- coding: utf-8 -*-
"""Rule-based synthesis layer before the final decision agent.

The LLM-written decision dashboard is easier to trust when it receives a
stable, inspectable pre-decision.  This module scores prior agent opinions,
collects evidence from the shared evidence pool, applies risk veto rules, and
returns a compact policy object for the DecisionAgent and UI.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.agent.data_confidence import (
    build_context_quality_summary,
    evidence_trust_from_detail,
)
from src.agent.protocols import AgentContext, normalize_decision_signal


SIGNAL_SCORE: Dict[str, float] = {
    "strong_buy": 2.0,
    "buy": 1.0,
    "hold": 0.0,
    "sell": -1.0,
    "strong_sell": -2.0,
}

AGENT_WEIGHTS: Dict[str, float] = {
    "technical": 0.25,
    "capital_flow": 0.20,
    "intel": 0.15,
    "sentiment": 0.15,
    "fundamental": 0.15,
    "industry": 0.10,
    "debate": 0.20,
    "scenario_analysis": 0.10,
    "factor_scoring": 0.10,
    "skill_consensus": 0.15,
    "strategy_consensus": 0.15,
}

RISK_LEVEL_PENALTY: Dict[str, float] = {
    "none": 0.0,
    "low": 0.15,
    "medium": 0.40,
    "high": 0.75,
}

RISK_FLAG_PENALTY: Dict[str, float] = {
    "low": 0.10,
    "medium": 0.25,
    "high": 0.45,
}


def build_decision_policy(ctx: AgentContext) -> Dict[str, Any]:
    """Build an explainable policy from prior agent outputs."""
    data_quality = ctx.get_data("data_quality_summary")
    if not isinstance(data_quality, dict):
        data_quality = build_context_quality_summary(ctx.data)
        ctx.set_data("data_quality_summary", data_quality)
    has_fresh_realtime = bool(data_quality.get("has_realtime"))
    realtime_trust = _clamp_float(data_quality.get("realtime_trust_score"), 0.0, 1.0, 0.0)

    contributions: List[Dict[str, Any]] = []
    weighted_score = 0.0
    total_weight = 0.0
    confidence_values: List[Tuple[float, float]] = []

    bullish_evidence: List[str] = []
    bearish_evidence: List[str] = []
    risk_evidence: List[str] = []
    contradictions: List[str] = []
    invalidation_conditions: List[str] = []
    watch_items: List[str] = []

    for opinion in ctx.opinions:
        agent_name = str(opinion.agent_name or "").lower()
        if agent_name in {"decision", "risk"}:
            continue

        signal = _normalize_full_signal(opinion.signal)
        score = SIGNAL_SCORE.get(signal, 0.0)
        confidence = _clamp_float(opinion.confidence, 0.0, 1.0, 0.5)
        weight = AGENT_WEIGHTS.get(agent_name, 0.10) * _data_availability_multiplier(
            agent_name,
            has_fresh_realtime=has_fresh_realtime,
            realtime_trust=realtime_trust,
        )
        contribution = score * confidence * weight
        weighted_score += contribution
        total_weight += weight
        confidence_values.append((confidence, weight))

        contributions.append({
            "agent": agent_name,
            "signal": signal,
            "confidence": round(confidence, 3),
            "weight": weight,
            "score": round(contribution, 4),
            "data_multiplier": round(
                weight / AGENT_WEIGHTS.get(agent_name, 0.10),
                3,
            ),
            "reasoning": _truncate(opinion.reasoning, 180),
        })

        raw = opinion.raw_data if isinstance(opinion.raw_data, dict) else {}
        evidence_target = bullish_evidence if score > 0 else bearish_evidence if score < 0 else watch_items
        evidence_target.extend(_text_list(raw.get("evidence")))
        bullish_evidence.extend(_text_list(raw.get("positive_catalysts")))
        bullish_evidence.extend(_text_list(raw.get("key_strengths")))
        bearish_evidence.extend(_text_list(raw.get("risks")))
        bearish_evidence.extend(_text_list(raw.get("key_concerns")))
        contradictions.extend(_text_list(raw.get("contradictory_evidence")))
        invalidation_conditions.extend(_text_list(raw.get("invalid_if")))
        invalidation_conditions.extend(_text_list(raw.get("thesis_breakers")))
        watch_items.extend(_text_list(raw.get("watch_items")))
        watch_items.extend(_text_list(raw.get("swing_factors")))

    opinion_score = weighted_score / total_weight if total_weight else 0.0
    hard_data_score, hard_data_evidence = _score_hard_data(ctx, data_quality)
    if hard_data_score is not None:
        combined_score = opinion_score * 0.40 + hard_data_score * 0.75
        if hard_data_score > 0:
            bullish_evidence.extend(hard_data_evidence)
        elif hard_data_score < 0:
            bearish_evidence.extend(hard_data_evidence)
        else:
            watch_items.extend(hard_data_evidence)
    else:
        combined_score = opinion_score

    risk_penalty, risk_veto, risk_items = _score_risk(ctx)
    adjusted_score = combined_score - risk_penalty if combined_score > 0 else combined_score - risk_penalty * 0.35

    base_signal = _score_to_signal(combined_score)
    adjusted_signal = _score_to_signal(adjusted_score)
    if risk_veto and adjusted_signal == "buy":
        adjusted_signal = "hold"
    if adjusted_signal == "buy" and not has_fresh_realtime:
        adjusted_signal = "hold"
        risk_evidence.append("Fresh realtime quote is missing or stale; buy signal capped at hold.")

    risk_evidence.extend(risk_items)
    _absorb_evidence_pool(
        ctx,
        bullish_evidence=bullish_evidence,
        bearish_evidence=bearish_evidence,
        risk_evidence=risk_evidence,
        contradictions=contradictions,
        invalidation_conditions=invalidation_conditions,
        watch_items=watch_items,
    )

    confidence = _calibrate_confidence(
        adjusted_score=adjusted_score,
        confidence_values=confidence_values,
        contributions=contributions,
        risk_penalty=risk_penalty,
        risk_veto=risk_veto,
        contradictions=contradictions,
    )
    if not has_fresh_realtime:
        confidence = min(confidence, 0.55)
    elif realtime_trust >= 0.75:
        confidence = min(0.90, confidence + 0.04)

    policy = {
        "schema_version": 1,
        "base_signal": normalize_decision_signal(base_signal),
        "adjusted_signal": normalize_decision_signal(adjusted_signal),
        "raw_score": round(combined_score, 4),
        "agent_score": round(opinion_score, 4),
        "hard_data_score": round(hard_data_score, 4) if hard_data_score is not None else None,
        "hard_data_evidence": hard_data_evidence,
        "score": round(adjusted_score, 4),
        "risk_penalty": round(risk_penalty, 4),
        "confidence": round(confidence, 3),
        "risk_veto": risk_veto,
        "data_quality": data_quality,
        "realtime_anchor": {
            "available": has_fresh_realtime,
            "trust_score": round(realtime_trust, 3),
            "decision_rule": "buy capped at hold when fresh realtime quote is unavailable",
        },
        "weights_used": dict(AGENT_WEIGHTS),
        "agent_contributions": contributions,
        "bullish_evidence": _dedupe(bullish_evidence, limit=10),
        "bearish_evidence": _dedupe(bearish_evidence, limit=10),
        "risk_evidence": _dedupe(risk_evidence, limit=10),
        "contradictions": _dedupe(contradictions, limit=8),
        "invalidation_conditions": _dedupe(invalidation_conditions, limit=8),
        "watch_items": _dedupe(watch_items, limit=8),
    }
    policy["rationale"] = _build_rationale(policy)
    return policy


def _score_risk(ctx: AgentContext) -> Tuple[float, bool, List[str]]:
    penalty = 0.0
    veto = False
    risk_items: List[str] = []

    risk_opinion = next((op for op in reversed(ctx.opinions) if op.agent_name == "risk"), None)
    risk_raw = risk_opinion.raw_data if risk_opinion and isinstance(risk_opinion.raw_data, dict) else {}
    risk_level = str(risk_raw.get("risk_level") or "none").lower()
    penalty += RISK_LEVEL_PENALTY.get(risk_level, 0.0)

    risk_score = _clamp_float(risk_raw.get("risk_score"), 0.0, 100.0, 0.0)
    if risk_score >= 80:
        penalty += 0.45
    elif risk_score >= 65:
        penalty += 0.25

    adjustment = str(risk_raw.get("signal_adjustment") or "").lower()
    if bool(risk_raw.get("veto_buy")) or adjustment == "veto" or risk_level == "high":
        veto = True
        penalty += 0.55
    elif adjustment == "downgrade_two":
        penalty += 0.45
    elif adjustment == "downgrade_one":
        penalty += 0.25

    risk_items.extend(_text_list(risk_raw.get("evidence")))
    risk_items.extend(_text_list(risk_raw.get("bear_case")))
    risk_items.extend(_text_list(risk_raw.get("thesis_breakers")))
    risk_items.extend(_text_list(risk_raw.get("reasoning")))

    for flag in ctx.risk_flags:
        if not isinstance(flag, dict):
            continue
        severity = str(flag.get("severity") or "medium").lower()
        penalty += RISK_FLAG_PENALTY.get(severity, 0.25)
        if severity == "high":
            veto = True
        description = str(flag.get("description") or "").strip()
        if description:
            risk_items.append(description)

    return min(1.75, penalty), veto, risk_items


def _score_hard_data(ctx: AgentContext, data_quality: Dict[str, Any]) -> Tuple[Optional[float], List[str]]:
    """Score direct market data before considering LLM-written opinions."""
    score = 0.0
    weight = 0.0
    evidence: List[str] = []

    realtime = ctx.get_data("realtime_quote")
    if isinstance(realtime, dict) and not realtime.get("error"):
        quote_quality = realtime.get("data_quality")
        quote_trust = _clamp_float(
            quote_quality.get("trust_score") if isinstance(quote_quality, dict) else data_quality.get("realtime_trust_score"),
            0.0,
            1.0,
            0.0,
        )
        change_pct = _optional_float(realtime.get("change_pct"))
        volume_ratio = _optional_float(realtime.get("volume_ratio"))
        quote_score = 0.0
        if change_pct is not None:
            if change_pct >= 3:
                quote_score += 0.65
                evidence.append(f"Realtime price change is strong: {change_pct:.2f}%.")
            elif change_pct >= 1:
                quote_score += 0.30
                evidence.append(f"Realtime price change is positive: {change_pct:.2f}%.")
            elif change_pct <= -3:
                quote_score -= 0.65
                evidence.append(f"Realtime price change is weak: {change_pct:.2f}%.")
            elif change_pct <= -1:
                quote_score -= 0.30
                evidence.append(f"Realtime price change is negative: {change_pct:.2f}%.")

        if volume_ratio is not None:
            if volume_ratio >= 1.5:
                quote_score += 0.20 if quote_score >= 0 else -0.20
                evidence.append(f"Realtime volume ratio is active: {volume_ratio:.2f}.")
            elif volume_ratio < 0.7 and quote_score > 0:
                quote_score -= 0.15
                evidence.append(f"Realtime rise lacks volume confirmation: volume_ratio={volume_ratio:.2f}.")

        score += quote_score * quote_trust * 1.00
        weight += quote_trust * 1.00

    trend = ctx.get_data("trend_result")
    if isinstance(trend, dict) and not trend.get("error"):
        trend_quality = trend.get("data_quality")
        trend_trust = _clamp_float(
            trend_quality.get("trust_score") if isinstance(trend_quality, dict) else 0.65,
            0.0,
            1.0,
            0.65,
        )
        signal_score = _optional_float(trend.get("signal_score"))
        trend_score = 0.0
        if signal_score is not None:
            trend_score = max(-1.0, min(1.0, (signal_score - 50.0) / 50.0))
            evidence.append(f"Technical signal_score={signal_score:.1f}.")
        buy_signal = str(trend.get("buy_signal") or "").lower()
        if "buy" in buy_signal:
            trend_score += 0.15
        elif "sell" in buy_signal:
            trend_score -= 0.15
        score += max(-1.0, min(1.0, trend_score)) * trend_trust * 0.70
        weight += trend_trust * 0.70

    if weight <= 0:
        return None, []
    return max(-1.0, min(1.0, score / weight)), evidence


def _absorb_evidence_pool(
    ctx: AgentContext,
    *,
    bullish_evidence: List[str],
    bearish_evidence: List[str],
    risk_evidence: List[str],
    contradictions: List[str],
    invalidation_conditions: List[str],
    watch_items: List[str],
) -> None:
    pool = ctx.get_data("evidence_pool")
    if not isinstance(pool, list):
        return

    signal_by_agent = {
        str(op.agent_name).lower(): SIGNAL_SCORE.get(_normalize_full_signal(op.signal), 0.0)
        for op in ctx.opinions
    }
    for item in pool:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or "").strip()
        if not claim:
            continue
        kind = str(item.get("kind") or "evidence").lower()
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        if isinstance(detail, dict):
            detail = dict(detail)
            for key in ("source_type", "source", "trust_score", "data_quality"):
                if key in item and key not in detail:
                    detail[key] = item[key]
        item_trust = evidence_trust_from_detail(detail, default_kind=kind)
        weighted_claim = claim
        if item_trust < 0.35:
            weighted_claim = f"{claim} (low-trust evidence)"
        if kind in {"risk"}:
            risk_evidence.append(weighted_claim)
        elif kind in {"contradiction"}:
            contradictions.append(weighted_claim)
            bearish_evidence.append(weighted_claim)
        elif kind in {"invalidation"}:
            invalidation_conditions.append(weighted_claim)
        elif kind in {"watch"}:
            watch_items.append(weighted_claim)
        elif kind in {"catalyst"}:
            bullish_evidence.append(weighted_claim)
        else:
            agent_score = signal_by_agent.get(str(item.get("agent") or "").lower(), 0.0)
            if agent_score > 0:
                bullish_evidence.append(weighted_claim)
            elif agent_score < 0:
                bearish_evidence.append(weighted_claim)
            else:
                watch_items.append(weighted_claim)


def _calibrate_confidence(
    *,
    adjusted_score: float,
    confidence_values: List[Tuple[float, float]],
    contributions: List[Dict[str, Any]],
    risk_penalty: float,
    risk_veto: bool,
    contradictions: Iterable[str],
) -> float:
    if confidence_values:
        weight_sum = sum(weight for _, weight in confidence_values) or 1.0
        source_confidence = sum(conf * weight for conf, weight in confidence_values) / weight_sum
    else:
        source_confidence = 0.45

    directional_scores = [
        1 if SIGNAL_SCORE.get(str(item.get("signal")), 0.0) > 0 else -1 if SIGNAL_SCORE.get(str(item.get("signal")), 0.0) < 0 else 0
        for item in contributions
    ]
    non_neutral = [score for score in directional_scores if score != 0]
    agreement = 0.0
    if non_neutral:
        positive = sum(1 for score in non_neutral if score > 0)
        negative = sum(1 for score in non_neutral if score < 0)
        agreement = abs(positive - negative) / len(non_neutral)

    confidence = 0.35 + source_confidence * 0.30 + min(abs(adjusted_score) / 2.0, 1.0) * 0.25 + agreement * 0.10
    confidence -= min(0.20, risk_penalty * 0.08)
    if list(contradictions):
        confidence -= 0.08
    if risk_veto:
        confidence = min(confidence, 0.55)
    if agreement < 0.35 and len(non_neutral) >= 2:
        confidence = min(confidence, 0.62)
    return max(0.25, min(0.90, confidence))


def _build_rationale(policy: Dict[str, Any]) -> str:
    signal = policy.get("adjusted_signal", "hold")
    score = policy.get("score", 0)
    risk_penalty = policy.get("risk_penalty", 0)
    veto = policy.get("risk_veto", False)
    evidence_count = len(policy.get("bullish_evidence", [])) + len(policy.get("bearish_evidence", []))
    if veto:
        return f"Rule policy is {signal}: risk veto is active, score={score}, risk_penalty={risk_penalty}, evidence_items={evidence_count}."
    return f"Rule policy is {signal}: weighted agent score={score}, risk_penalty={risk_penalty}, evidence_items={evidence_count}."


def _score_to_signal(score: float) -> str:
    if score >= 0.55:
        return "buy"
    if score <= -0.55:
        return "sell"
    return "hold"


def _normalize_full_signal(signal: Any) -> str:
    if not isinstance(signal, str):
        return "hold"
    normalized = signal.strip().lower()
    if normalized in SIGNAL_SCORE:
        return normalized
    return normalize_decision_signal(normalized)


def _data_availability_multiplier(
    agent_name: str,
    *,
    has_fresh_realtime: bool,
    realtime_trust: float,
) -> float:
    """Reduce weights for realtime-sensitive agents when quote quality is weak."""
    if has_fresh_realtime and realtime_trust >= 0.65:
        return 1.0
    if agent_name in {"technical", "capital_flow", "sentiment"}:
        return 0.65
    if agent_name in {"debate", "scenario_analysis", "factor_scoring"}:
        return 0.75
    return 0.85


def _text_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        text = str(value.get("claim") or value.get("title") or value.get("description") or value.get("summary") or "").strip()
        return [text] if text else []
    if isinstance(value, list):
        result: List[str] = []
        for item in value:
            result.extend(_text_list(item))
        return result
    return []


def _dedupe(values: Iterable[str], *, limit: int) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        text = _truncate(value, 220)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _clamp_float(value: Any, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _optional_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
