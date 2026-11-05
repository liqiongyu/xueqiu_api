"""quote_relevant: other stocks the upstream relates to one stock, with their quotes."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, ClassVar

from pydantic import Field

from .._model import QuoteRow, UpstreamModel
from .._values import WholeNumber
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolParams

RESULT_CAP = 10
"""Rows one request returns. Not measured: the survey saw 6."""


class RelatedStock(QuoteRow):
    """One related stock; the 6 of SH600000 were all banks, measured 2026-11-04.

    The session's follow state and anything naming or quoting a user stay out.
    """

    drop: ClassVar[frozenset[str]] = frozenset({"has_follow", "url", "img_url", "content"})

    stock_type: WholeNumber = Field(default=None, validation_alias="type")
    """Xueqiu security type code; 11 is an A share."""


class _Payload(UpstreamModel):
    """The upstream's list of portfolios, written by their owners, is not read."""

    items: tuple[RelatedStock, ...]


def _build_query(params: SymbolParams, now: datetime) -> dict[str, str]:
    return {"symbol": params.symbol}


def _parse(payload: Any, params: SymbolParams) -> list[RelatedStock]:
    return list(_Payload.model_validate(payload).items)


ENDPOINT = Endpoint(
    name="quote_relevant",
    doc="Other stocks the upstream relates to one A-share, with their last price and change.",
    host="stock.xueqiu.com",
    path="/v5/stock/quote/relevant.json",
    params=SymbolParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="intraday",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    markets=("CN",),
    agent=AgentInfo(
        category="quote",
        group="xq_market",
        section="relevant",
        title_zh="相关股票",
        title_en="Related stocks",
        summary="取和一只 A 股相关的其他股票及其最新价和涨跌幅。",
        keywords=("相关股票", "相关个股", "同类股票", "related stocks", "peers"),
        default_fields=("symbol", "name", "current", "percent"),
        row_default=RESULT_CAP,
        row_cap=RESULT_CAP,
        upstream_cap=RESULT_CAP,
        examples=({"symbol": "SH600000"},),
        empty_hint="no rows: the upstream relates no other stock to this one.",
    ),
)
