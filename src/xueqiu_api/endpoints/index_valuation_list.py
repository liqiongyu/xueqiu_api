"""index_valuation_list: Danjuan's valuation table, one row per index."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .._index import IndexValuation
from .._model import NoParams, UpstreamModel
from ..registry import AgentInfo, Endpoint


class _Payload(UpstreamModel):
    items: tuple[IndexValuation, ...]
    total_items: int


def _build_query(params: NoParams, now: datetime) -> dict[str, str]:
    return {}


def _parse(payload: Any, params: NoParams) -> list[IndexValuation]:
    data = _Payload.model_validate(payload)
    if data.total_items != len(data.items):
        raise ValueError(f"one request gave {len(data.items)} of {data.total_items} indexes")
    return list(data.items)


ENDPOINT = Endpoint(
    name="index_valuation_list",
    doc="Danjuan's valuation table, one row per index.",
    host="danjuanfunds.com",
    path="/djapi/index_eva/dj",
    params=NoParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="danjuan",
    verified_on=date(2026, 11, 5),
    agent=AgentInfo(
        category="index",
        group="xq_market",
        section="index_valuation_list",
        title_zh="指数估值表",
        title_en="Index valuation table",
        summary="取蛋卷的指数估值表，每个指数一行，包括市盈率、市净率、历史百分位和低估高估判断。",
        keywords=(
            "指数估值",
            "估值表",
            "低估指数",
            "高估",
            "市盈率",
            "市净率",
            "百分位",
            "index valuation",
            "pe percentile",
        ),
        default_fields=(
            "index_code",
            "name",
            "trade_date",
            "pe",
            "pe_percentile",
            "pb",
            "pb_percentile",
            "roe",
            "dividend_yield",
            "eva_type",
        ),
        row_default=100,
        row_cap=100,
        upstream_cap=100,
        examples=({},),
    ),
)
