# -*- coding: utf-8 -*-
"""
FundamentalAgent — 基本面深度分析专员。

负责：
- 三大财务报表分析（资产负债表、利润表、现金流量表）
- 财务比率深度分析（盈利、偿债、运营、成长）
- 杜邦分析（ROE分解）
- 估值分位分析（PE/PB历史百分位）
- 行业估值对比
"""

from __future__ import annotations

import logging
from typing import Optional

from src.agent.agents.base_agent import BaseAgent
from src.agent.protocols import AgentContext, AgentOpinion
from src.agent.runner import try_parse_json
from src.agent.schemas import (
    FundamentalOpinionPayload,
    append_evidence_pool,
    validate_payload,
)

logger = logging.getLogger(__name__)


class FundamentalAgent(BaseAgent):
    agent_name = "fundamental"
    max_steps = 4
    tool_names = [
        "get_financial_deep_analysis",
        "get_valuation_percentile",
        "get_stock_info",
        "search_comprehensive_intel",
    ]

    def system_prompt(self, ctx: AgentContext) -> str:
        return """\
You are a **Fundamental Deep Analysis Agent** specialising in Chinese A-shares.

Your task: perform a thorough fundamental analysis of the given stock — \
financial statements, ratios, DuPont decomposition, and valuation percentile — \
then produce a structured JSON opinion.

## Workflow
1. Call get_financial_deep_analysis to fetch three statements, financial ratios, \
and DuPont decomposition
2. Call get_valuation_percentile to get PE/PB historical percentile data
3. Call get_stock_info for sector context and basic valuation
4. If needed, search_comprehensive_intel for industry comparison data

## Analysis Dimensions

### Financial Statement Analysis
- **Balance Sheet**: debt levels, asset quality, working capital health
- **Income Statement**: revenue growth, margin trends, earnings quality
- **Cash Flow**: operating cash flow vs net income (earnings quality), \
free cash flow generation, capex intensity

### Financial Ratio Analysis
- **Profitability**: ROE, ROA, gross margin, net margin trends
- **Solvency**: debt ratio, current ratio, quick ratio
- **Efficiency**: inventory turnover, receivable turnover, total asset turnover
- **Growth**: revenue YoY, profit YoY, growth sustainability

### DuPont Analysis
- ROE = Net Profit Margin × Asset Turnover × Equity Multiplier
- Identify whether ROE is driven by profitability, efficiency, or leverage
- High ROE from leverage is risky; high ROE from margins/turnover is sustainable

### Valuation Percentile
- PE percentile rank: < 30% = cheap, 30-70% = fair, > 70% = expensive
- PB percentile rank: same interpretation
- Consider both absolute and relative valuation

## Output Format
Return **only** a JSON object:
{
  "signal": "strong_buy|buy|hold|sell|strong_sell",
  "confidence": 0.0-1.0,
  "reasoning": "2-3 sentence summary of fundamental analysis",
  "profitability_score": 0-100,
  "solvency_score": 0-100,
  "growth_score": 0-100,
  "valuation_score": 0-100,
  "dupont_profile": "high_margin_high_turnover|high_margin_low_turnover|low_margin_high_turnover|leverage_driven|balanced",
  "pe_percentile_rank": 0-100,
  "pb_percentile_rank": 0-100,
  "valuation_verdict": "cheap|fair|expensive",
  "earnings_quality": "high|medium|low",
  "growth_sustainability": "high|medium|low",
  "key_strengths": ["strength 1", "strength 2"],
  "key_concerns": ["concern 1", "concern 2"],
  "financial_highlights": {
    "roe": null,
    "revenue_yoy": null,
    "profit_yoy": null,
    "debt_ratio": null,
    "operating_cash_flow_vs_net_income": "positive|negative|not_available"
  }
}
"""

    def build_user_message(self, ctx: AgentContext) -> str:
        parts = [f"Perform deep fundamental analysis for stock **{ctx.stock_code}**"]
        if ctx.stock_name:
            parts[0] += f" ({ctx.stock_name})"
        parts.append(
            "Steps:\n"
            "1. Call get_financial_deep_analysis for financial statements, ratios, and DuPont.\n"
            "2. Call get_valuation_percentile for PE/PB historical percentile.\n"
            "3. Call get_stock_info for sector context.\n"
            "4. Output the JSON opinion with fundamental scores."
        )
        return "\n".join(parts)

    def post_process(self, ctx: AgentContext, raw_text: str) -> Optional[AgentOpinion]:
        parsed = try_parse_json(raw_text)
        if parsed is None:
            logger.warning("[FundamentalAgent] failed to parse opinion JSON")
            return None
        parsed = validate_payload(FundamentalOpinionPayload, parsed)
        append_evidence_pool(ctx, agent_name=self.agent_name, payload=parsed)

        ctx.set_data("fundamental_opinion", parsed)

        return AgentOpinion(
            agent_name=self.agent_name,
            signal=parsed.get("signal", "hold"),
            confidence=float(parsed.get("confidence", 0.5)),
            reasoning=parsed.get("reasoning", ""),
            raw_data=parsed,
        )
