"""finance_cash_flow: cash flow statement report periods."""

from __future__ import annotations

from .._finance import MAX_COUNT, finance_endpoint
from ..registry import AgentInfo

ENDPOINT = finance_endpoint(
    "cash_flow",
    agent=AgentInfo(
        title_zh="现金流量表",
        title_en="Cash flow statement",
        summary=(
            "取 A 股、港股或美股公司的现金流量表，每个科目给本期值和同比，"
            "可选全部报告期或只取年报。"
        ),
        keywords=(
            "现金流量表",
            "经营活动现金流",
            "投资活动现金流",
            "筹资活动现金流",
            "财报",
            "cash flow statement",
            "operating cash flow",
            "investing",
            "financing",
        ),
        default_fields=(
            "report_date",
            "report_name",
            "ncf_from_oa",
            "ncf_from_ia",
            "ncf_from_fa",
            "net_increase_in_cce",
        ),
        row_default=MAX_COUNT,
        row_cap=MAX_COUNT,
        upstream_cap=MAX_COUNT,
        examples=({"symbol": "SH600000", "count": 2},),
    ),
)
