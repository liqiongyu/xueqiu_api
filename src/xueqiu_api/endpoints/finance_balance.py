"""finance_balance: balance sheet report periods."""

from __future__ import annotations

from .._finance import DEFAULT_COUNT, MAX_COUNT, finance_endpoint
from ..registry import AgentInfo

ENDPOINT = finance_endpoint(
    "balance",
    agent=AgentInfo(
        title_zh="资产负债表",
        title_en="Balance sheet",
        summary=(
            "取 A 股、港股或美股公司的资产负债表，每个科目给期末值和同比，"
            "可选全部报告期或只取年报。"
        ),
        keywords=(
            "资产负债表",
            "总资产",
            "总负债",
            "股东权益",
            "财报",
            "balance sheet",
            "assets",
            "liabilities",
            "equity",
        ),
        default_fields=(
            "report_date",
            "report_name",
            "total_assets",
            "total_liab",
            "total_holders_equity",
            "asset_liab_ratio",
        ),
        row_default=DEFAULT_COUNT,
        row_cap=MAX_COUNT,
        upstream_cap=MAX_COUNT,
        examples=({"symbol": "SH600000", "count": 2},),
    ),
)
