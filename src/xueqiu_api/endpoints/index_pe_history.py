"""index_pe_history: PE of one index over time, oldest first."""

from __future__ import annotations

from datetime import date
from typing import Any

from .._index import ValuationHistory, ValuationHistoryParams, history_query, parse_history
from ..registry import AgentInfo, Endpoint


def _parse(payload: Any, params: ValuationHistoryParams) -> ValuationHistory:
    return parse_history(payload, params, "pe")


ENDPOINT = Endpoint(
    name="index_pe_history",
    doc="PE of one index over time with Danjuan's reference lines, oldest first.",
    host="danjuanfunds.com",
    path="/djapi/index_eva/pe_history/{index_code}",
    params=ValuationHistoryParams,
    build_query=history_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="danjuan",
    verified_on=date(2026, 11, 5),
    order="oldest_first",
    agent=AgentInfo(
        category="index",
        group="xq_market",
        section="index_pe_history",
        title_zh="指数PE历史",
        title_en="Index PE history",
        summary="取一个指数的历史市盈率和分位参考线，约每周一个点，最早的在前。",
        keywords=(
            "指数市盈率",
            "市盈率走势",
            "PE历史",
            "估值分位",
            "pe history",
            "index pe",
        ),
        default_fields=("date", "pe"),
        row_default=100,
        row_cap=500,
        upstream_cap=600,
        examples=({"index_code": "SH000300"}, {"index_code": "SZ399006", "period": "5y"}),
    ),
)
