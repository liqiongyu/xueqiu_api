"""finance_indicator: key financial indicators per report period."""

from __future__ import annotations

from .._finance import MAX_COUNT, finance_endpoint
from ..registry import AgentInfo

ENDPOINT = finance_endpoint(
    "indicator",
    doc="Key financial indicators, latest report period first.",
    agent=AgentInfo(
        category="finance",
        group="xq_financials",
        section="indicator",
        title_zh="财务指标",
        title_en="Financial indicators",
        summary=(
            "取 A 股、港股或美股公司的主要财务指标，如每股收益、净资产收益率和资产负债率，"
            "每项给本期值和同比。"
        ),
        keywords=(
            "财务指标",
            "主要指标",
            "净资产收益率",
            "毛利率",
            "资产负债率",
            "每股收益",
            "financial indicators",
            "ratios",
            "ROE",
            "margin",
        ),
        default_fields=(
            "report_date",
            "report_name",
            "basic_eps",
            "avg_roe",
            "gross_selling_rate",
            "net_selling_rate",
            "asset_liab_ratio",
        ),
        row_default=MAX_COUNT,
        row_cap=MAX_COUNT,
        upstream_cap=MAX_COUNT,
        examples=({"symbol": "SH600000", "count": 2},),
    ),
    # Per share here, a total in the balance sheet.
    items={"capital_reserve": ("capital_reserve_ps", 1.0)},
)
