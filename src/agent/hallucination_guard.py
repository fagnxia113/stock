# -*- coding: utf-8 -*-
"""Post-decision guardrails against price and data hallucinations."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.agent.data_confidence import best_realtime_price, build_context_quality_summary
from src.agent.protocols import AgentContext, normalize_decision_signal


def apply_hallucination_guard(ctx: AgentContext, dashboard: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a final dashboard against hard market data.

    The guard is deliberately conservative: it corrects obvious current-price
    conflicts, caps buy signals when fresh realtime data is unavailable, and
    records warnings for suspicious target/stop levels.
    """
    if not isinstance(dashboard, dict):
        return dashboard

    data_quality = ctx.get_data("data_quality_summary")
    if not isinstance(data_quality, dict):
        data_quality = build_context_quality_summary(ctx.data)
        ctx.set_data("data_quality_summary", data_quality)

    warnings: List[str] = []
    realtime_price = best_realtime_price(ctx.data)
    has_realtime = bool(data_quality.get("has_realtime"))
    decision_type = normalize_decision_signal(dashboard.get("decision_type", "hold"))
    changed = False

    if decision_type == "buy" and not has_realtime:
        dashboard["decision_type"] = "hold"
        _cap_sentiment_score(dashboard, high=59)
        _replace_operation_advice(dashboard, "观望")
        warnings.append("实时行情缺失或过期，买入信号已自动降级为观望。")
        changed = True

    if realtime_price is not None:
        changed = _validate_current_price(dashboard, realtime_price, warnings) or changed
        _validate_sniper_levels(dashboard, realtime_price, warnings)
    else:
        warnings.append("未取得可用实时价格，所有价格位只能按低置信度处理。")

    if warnings:
        existing_warning = str(dashboard.get("risk_warning") or "").strip()
        merged = " ".join(dict.fromkeys([existing_warning] + warnings if existing_warning else warnings))
        dashboard["risk_warning"] = merged[:600]

    dashboard["data_quality_summary"] = data_quality
    dashboard["hallucination_guard"] = {
        "applied": bool(warnings or changed),
        "warnings": warnings,
        "realtime_price": realtime_price,
        "has_fresh_realtime": has_realtime,
    }
    return dashboard


def _validate_current_price(
    dashboard: Dict[str, Any],
    realtime_price: float,
    warnings: List[str],
) -> bool:
    data_perspective = ((dashboard.get("dashboard") or {}).get("data_perspective") or {})
    if not isinstance(data_perspective, dict):
        return False
    price_position = data_perspective.get("price_position")
    if not isinstance(price_position, dict):
        return False

    model_price = _as_float(price_position.get("current_price"))
    if model_price is None or model_price <= 0:
        price_position["current_price"] = round(realtime_price, 3)
        warnings.append("当前价缺失，已用实时行情价格补齐。")
        return True

    deviation = abs(model_price - realtime_price) / realtime_price
    if deviation > 0.05:
        price_position["current_price"] = round(realtime_price, 3)
        warnings.append(
            f"模型当前价与实时行情偏离 {deviation:.1%}，已以实时行情价为准。"
        )
        return True
    return False


def _validate_sniper_levels(
    dashboard: Dict[str, Any],
    realtime_price: float,
    warnings: List[str],
) -> None:
    battle_plan = ((dashboard.get("dashboard") or {}).get("battle_plan") or {})
    if not isinstance(battle_plan, dict):
        return
    sniper = battle_plan.get("sniper_points")
    if not isinstance(sniper, dict):
        return

    for key in ("ideal_buy", "secondary_buy", "stop_loss", "take_profit"):
        value = _as_float(sniper.get(key))
        if value is None or value <= 0:
            continue
        deviation = abs(value - realtime_price) / realtime_price
        if deviation > 0.60:
            warnings.append(f"{key} 与实时价偏离超过 60%，需人工复核该价格位。")

    stop_loss = _as_float(sniper.get("stop_loss"))
    decision_type = normalize_decision_signal(dashboard.get("decision_type", "hold"))
    if decision_type == "buy" and stop_loss is not None and stop_loss >= realtime_price:
        dashboard["decision_type"] = "hold"
        _cap_sentiment_score(dashboard, high=59)
        _replace_operation_advice(dashboard, "观望")
        warnings.append("买入方案的止损价不应高于或等于实时价，买入信号已降级为观望。")


def _cap_sentiment_score(dashboard: Dict[str, Any], *, high: int) -> None:
    try:
        score = int(dashboard.get("sentiment_score", 50))
    except (TypeError, ValueError):
        score = 50
    dashboard["sentiment_score"] = min(score, high)


def _replace_operation_advice(dashboard: Dict[str, Any], value: str) -> None:
    advice = dashboard.get("operation_advice")
    if not isinstance(advice, dict):
        dashboard["operation_advice"] = value


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if not text or text.upper() == "N/A":
        return None
    try:
        return float(text)
    except ValueError:
        return None
