# -*- coding: utf-8 -*-
"""
CapitalFlowAgent — 资金流向分析专员

负责：
- 分析主力资金流入/流出模式
- 评估北向资金趋势（如可用）
- 分析融资融券数据
- 检测机构吸筹或派发信号
- 评估散户与机构资金流动动态
- 识别大单活动与聪明资金信号
"""

from __future__ import annotations

import logging
from typing import Optional

from src.agent.agents.base_agent import BaseAgent
from src.agent.protocols import AgentContext, AgentOpinion
from src.agent.runner import try_parse_json
from src.agent.schemas import (
    CapitalFlowOpinionPayload,
    append_evidence_pool,
    validate_payload,
)

logger = logging.getLogger(__name__)


class CapitalFlowAgent(BaseAgent):
    agent_name = "capital_flow"
    max_steps = 5
    tool_names = [
        "get_capital_flow",
        "get_realtime_quote",
        "get_stock_info",
        "search_comprehensive_intel",
    ]

    def system_prompt(self, ctx: AgentContext) -> str:
        return """\
You are a **Capital Flow Analysis Agent** specialising in Chinese A-shares, \
HK, and US equities.

Your task: perform a thorough capital flow analysis of the given stock and \
output a structured JSON opinion.

## Workflow (execute stages in order)
1. Call get_capital_flow to obtain main-force (主力) capital inflow/outflow data
2. Fetch realtime quote to cross-reference price action with fund flow
3. Get stock info for sector and market-cap context
4. If needed, search comprehensive intel for institutional activity news
5. Synthesise all data into a capital flow assessment

## Analysis Focus
- **Main-force (主力) capital flow**: evaluate net inflow/outflow magnitude \
and multi-day trends (5d, 10d). Strong sustained inflow = accumulation; \
sustained outflow = distribution.
- **Northbound capital (北向资金)**: if available, assess whether foreign \
institutional money is entering or exiting. Northbound inflow is a bullish \
signal for A-shares.
- **Margin trading (融资融券)**: rising margin balance = leveraged bullish \
positions; rapid margin selling = forced liquidation risk.
- **Institutional accumulation/distribution**: detect whether large orders \
cluster on the bid (accumulation) or ask (distribution) side.
- **Retail vs institutional dynamics**: small-order dominance on the bid with \
large-order selling = distribution to retail (bearish); the reverse = \
accumulation by institutions (bullish).
- **Large order activity & smart money**: track super-large and large order \
net flow as a proxy for smart money positioning.
- **Volume-price divergence**: rising price with shrinking main-force inflow \
or net outflow = bearish divergence; falling price with rising main-force \
inflow = bullish divergence.

## Output Format
Return **only** a JSON object (no markdown fences):
{
  "signal": "strong_buy|buy|hold|sell|strong_sell",
  "confidence": 0.0-1.0,
  "reasoning": "2-3 sentence summary of capital flow findings",
  "main_force_signal": "strong_inflow|inflow|neutral|outflow|strong_outflow",
  "northbound_signal": "strong_inflow|inflow|neutral|outflow|strong_outflow|not_available",
  "margin_signal": "expanding|stable|shrinking|not_available",
  "accumulation_stage": "accumulation|distribution|neutral",
  "smart_money_signal": "bullish|neutral|bearish",
  "volume_price_divergence": "bullish_divergence|bearish_divergence|none",
  "key_observations": [
    "observation 1",
    "observation 2",
    "observation 3"
  ]
}
"""

    def build_user_message(self, ctx: AgentContext) -> str:
        parts = [f"Analyze capital flow for stock **{ctx.stock_code}**"]
        if ctx.stock_name:
            parts[0] += f" ({ctx.stock_name})"
        parts.append(
            "Steps:\n"
            "1. Call get_capital_flow to get main-force capital flow data.\n"
            "2. Call get_realtime_quote for current price and volume context.\n"
            "3. Call get_stock_info for sector and market-cap context.\n"
            "4. If institutional activity news is needed, call search_comprehensive_intel.\n"
            "5. Output the JSON opinion with all capital flow signals."
        )
        return "\n".join(parts)

    def post_process(self, ctx: AgentContext, raw_text: str) -> Optional[AgentOpinion]:
        parsed = try_parse_json(raw_text)
        if parsed is None:
            logger.warning("[CapitalFlowAgent] failed to parse opinion JSON")
            return None
        parsed = validate_payload(CapitalFlowOpinionPayload, parsed)
        append_evidence_pool(ctx, agent_name=self.agent_name, payload=parsed)

        ctx.set_data("capital_flow_opinion", parsed)

        return AgentOpinion(
            agent_name=self.agent_name,
            signal=parsed.get("signal", "hold"),
            confidence=float(parsed.get("confidence", 0.5)),
            reasoning=parsed.get("reasoning", ""),
            raw_data=parsed,
        )
