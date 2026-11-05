"""market_bj_indexes: the three indexes of the Beijing panel on the Xueqiu home page."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .._model import NoParams, QuoteRow, UpstreamModel
from ..registry import AgentInfo, Endpoint


class BoardIndexQuote(QuoteRow):
    """Quote of one index; ``current`` and ``chg`` are in index points."""


class _Payload(UpstreamModel):
    items: tuple[BoardIndexQuote, ...]


def _build_query(params: NoParams, now: datetime) -> dict[str, str]:
    return {}


def _parse(payload: Any, params: NoParams) -> list[BoardIndexQuote]:
    return list(_Payload.model_validate(payload).items)


ENDPOINT = Endpoint(
    name="market_bj_indexes",
    doc="Quotes of the BSE 50 and its two comparison indexes, SSE 50 and SZSE 100.",
    host="stock.xueqiu.com",
    path="/v5/stock/bj/index/list.json",
    params=NoParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="realtime",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    agent=AgentInfo(
        category="market",
        group="xq_market",
        section="bj_indexes",
        title_zh="北交所栏目指数",
        title_en="BSE 50 and comparison indexes",
        summary="取雪球首页北交所栏目里北证50、上证50和深证100三只指数的最新点位和涨跌幅。",
        keywords=(
            "北证50",
            "北交所指数",
            "上证50",
            "深证100",
            "BSE 50",
            "Beijing Stock Exchange",
            "index quotes",
        ),
        default_fields=("symbol", "name", "current", "chg", "percent"),
        row_default=10,
        row_cap=10,
        upstream_cap=10,
        examples=({},),
    ),
)
