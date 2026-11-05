"""index_pb_history: PB of one index over time, oldest first."""

from __future__ import annotations

from datetime import date
from typing import Any

from .._index import ValuationHistory, ValuationHistoryParams, history_query, parse_history
from ..registry import AgentInfo, Endpoint


def _parse(payload: Any, params: ValuationHistoryParams) -> ValuationHistory:
    return parse_history(payload, params, "pb")


ENDPOINT = Endpoint(
    name="index_pb_history",
    doc="PB of one index over time with Danjuan's reference lines, oldest first.",
    host="danjuanfunds.com",
    path="/djapi/index_eva/pb_history/{index_code}",
    params=ValuationHistoryParams,
    build_query=history_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="danjuan",
    # Measured with day=all. The other windows were measured on the PE history, on 2026-11-05.
    verified_on=date(2026, 11, 4),
    order="oldest_first",
    agent=AgentInfo(
        category="index",
        group="xq_market",
        section="index_pb_history",
        title_zh="指数PB历史",
        title_en="Index PB history",
        summary="取一个指数的历史市净率和分位参考线，约每周一个点，最早的在前。",
        keywords=(
            "指数市净率",
            "市净率走势",
            "PB历史",
            "破净",
            "pb history",
            "index pb",
        ),
        default_fields=("date", "pb"),
        row_default=100,
        row_cap=500,
        upstream_cap=600,
        examples=({"index_code": "SH000300"}, {"index_code": "SZ399006", "day": "5y"}),
    ),
)
