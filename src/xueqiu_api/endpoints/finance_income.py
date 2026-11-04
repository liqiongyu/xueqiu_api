"""finance_income: income statement report periods."""

from __future__ import annotations

from .._finance import MAX_COUNT, finance_endpoint
from ..registry import AgentInfo

ENDPOINT = finance_endpoint(
    "income",
    agent=AgentInfo(
        title_zh="利润表",
        title_en="Income statement",
        summary=(
            "取 A 股、港股或美股公司的利润表，每个科目给本期值和同比，可选全部报告期或只取年报。"
        ),
        keywords=(
            "利润表",
            "营业收入",
            "净利润",
            "每股收益",
            "财报",
            "income statement",
            "revenue",
            "net profit",
            "earnings",
        ),
        default_fields=(
            "report_date",
            "report_name",
            "total_revenue",
            "op",
            "net_profit",
            "net_profit_atsopc",
            "basic_eps",
        ),
        row_default=MAX_COUNT,
        row_cap=MAX_COUNT,
        upstream_cap=MAX_COUNT,
        examples=({"symbol": "SH600000", "count": 2},),
    ),
)
