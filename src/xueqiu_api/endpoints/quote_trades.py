"""quote_trades: the most recent executed trades of one symbol."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from .._model import UpstreamModel
from .._values import Number
from ..registry import AgentInfo, Endpoint, Pagination
from ..symbols import SymbolCode
from .quotec import exchange_time

COUNT_CAP = 100
"""Trades per request. Not measured: the survey sent count=3."""


class QuoteTradesParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    symbol: SymbolCode
    """Stock code, such as SH600000."""
    count: int = Field(default=50, ge=1, le=COUNT_CAP)
    """How many of the most recent trades to fetch; earlier ones are out of reach."""


class TradeTick(UpstreamModel):
    """One executed trade. Unknown upstream fields, such as ``trade_type``, stay in
    ``model_extra``."""

    symbol: str
    timestamp: datetime | None = None
    """Trade time, timezone-aware in the exchange timezone. Trades can share a timestamp."""
    current: Number = None
    """Trade price, in the quote currency."""
    chg: Number = None
    """Trade price minus the previous close, in the quote currency."""
    percent: Number = None
    """Change from the previous close, in percent points (1.23 means 1.23%)."""
    trade_volume: Number = None
    """Shares traded."""
    side: int | None = None
    """Upstream code, as sent; 0 and 1 were seen. It may be the direction, which is unconfirmed."""
    trade_unique_id: str | None = None
    """Upstream id of the trade, unique among the rows seen; use it to tell trades apart."""

    @field_validator("timestamp", mode="before")
    @classmethod
    def _exchange_time(cls, value: Any, info: ValidationInfo) -> Any:
        return exchange_time(value, info)


class _Payload(UpstreamModel):
    items: tuple[TradeTick, ...]


def _build_query(params: QuoteTradesParams, now: datetime) -> dict[str, str]:
    return {"symbol": params.symbol, "count": str(params.count)}


def _parse(payload: Any, params: QuoteTradesParams) -> list[TradeTick]:
    return list(_Payload.model_validate(payload).items)


ENDPOINT = Endpoint(
    name="quote_trades",
    doc=(
        "The most recent executed trades of one symbol, oldest first; resting orders are in "
        "``quote_order_book``."
    ),
    host="stock.xueqiu.com",
    path="/v5/stock/history/trade.json",
    params=QuoteTradesParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="realtime",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    pagination=Pagination(style="latest"),
    markets=("CN", "US"),
    order="oldest_first",
    agent=AgentInfo(
        category="quote",
        group="xq_intraday",
        section="trades",
        title_zh="成交明细",
        title_en="Trade ticks",
        summary="取一只股票最近若干笔逐笔成交的时间、成交价、成交量和买卖方向代码。",
        keywords=(
            "成交明细",
            "逐笔成交",
            "分笔成交",
            "最新成交",
            "trades",
            "ticks",
            "time and sales",
        ),
        default_fields=("timestamp", "current", "trade_volume", "side", "chg", "percent"),
        row_default=50,
        row_cap=COUNT_CAP,
        upstream_cap=COUNT_CAP,
        examples=({"symbol": "SH600000", "count": 3},),
    ),
)
