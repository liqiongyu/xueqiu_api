"""quote_order_book: snapshot of the resting bids and asks of one A-share."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, NamedTuple, cast

from pydantic import ValidationInfo, field_validator, model_validator

from .._model import UpstreamModel
from .._values import Number
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolParams
from .quotec import exchange_time

_DEPTH = 10
"""Levels per side in the upstream's keys; anonymous responses filled 5, measured 2026-11-04."""
_LEVEL_KEY = re.compile(r"[bs][pcn](?:[1-9]|10)")


class BookLevel(NamedTuple):
    """One price level of the book."""

    price: float
    """In the quote currency."""
    volume: int | None
    """Resting shares; not always a multiple of a board lot."""
    orders: int | None
    """Resting orders, going by the upstream's name; null on every level seen."""


def _side(fields: Mapping[str, Any], side: str) -> list[tuple[Any, Any, Any]]:
    """The levels of one side from the best price on; a level without a price is not there."""
    return [
        (fields[f"{side}p{n}"], fields.get(f"{side}c{n}"), fields.get(f"{side}n{n}"))
        for n in range(1, _DEPTH + 1)
        if fields.get(f"{side}p{n}") is not None
    ]


class OrderBook(UpstreamModel):
    """Snapshot of the resting orders: price levels and their imbalance, as of ``timestamp``.

    It holds no executed trades: those are in ``quote_trades``. An empty book still reports
    ``buypct`` and ``sellpct`` 50 and ``diff`` and ``ratio`` 0; these are placeholders that do not
    mean a balanced book. Both sides were empty for 00700 and AAPL in anonymous requests,
    measured 2026-11-04 with AAPL in its regular session.
    """

    symbol: str
    timestamp: datetime | None = None
    """Book time, timezone-aware in the exchange timezone."""
    current: Number = None
    """Last price, in the quote currency."""
    bids: tuple[BookLevel, ...] = ()
    """Buy side, best price first."""
    asks: tuple[BookLevel, ...] = ()
    """Sell side, best price first."""
    buypct: Number = None
    """Bid volume over the volume of all levels shown, in percent points (1.23 means 1.23%)."""
    sellpct: Number = None
    """Ask volume over the volume of all levels shown, in percent points."""
    diff: Number = None
    """Bid volume minus ask volume over the levels shown, in shares (委差)."""
    ratio: Number = None
    """``diff`` over the volume of all levels shown, in percent points (委比)."""

    @model_validator(mode="before")
    @classmethod
    def _collect_levels(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        fields = cast(dict[str, Any], value)
        others = {key: item for key, item in fields.items() if not _LEVEL_KEY.fullmatch(key)}
        return {**others, "bids": _side(fields, "b"), "asks": _side(fields, "s")}

    @field_validator("timestamp", mode="before")
    @classmethod
    def _exchange_time(cls, value: Any, info: ValidationInfo) -> Any:
        return exchange_time(value, info)


def _build_query(params: SymbolParams, now: datetime) -> dict[str, str]:
    return {"symbol": params.symbol}


def _parse(payload: Any, params: SymbolParams) -> OrderBook:
    return OrderBook.model_validate(payload)


ENDPOINT = Endpoint(
    name="quote_order_book",
    doc=(
        "Snapshot of the resting bids and asks of one A-share with their imbalance; executed "
        "trades are in ``quote_trades``."
    ),
    host="stock.xueqiu.com",
    path="/v5/stock/realtime/pankou.json",
    params=SymbolParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="realtime",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    markets=("CN",),
    agent=AgentInfo(
        category="quote",
        group="xq_intraday",
        section="order_book",
        title_zh="盘口",
        title_en="Order book",
        summary=(
            "取一只 A 股此刻的盘口快照，含买卖五档的价格和挂单量、委比和委差；逐笔成交用成交明细。"
        ),
        keywords=(
            "盘口",
            "五档",
            "买卖盘",
            "委比",
            "委差",
            "挂单",
            "order book",
            "bid ask",
            "depth",
            "pankou",
        ),
        default_fields=(
            "timestamp",
            "current",
            "bids",
            "asks",
            "buypct",
            "sellpct",
            "diff",
            "ratio",
        ),
        row_default=1,
        row_cap=1,
        upstream_cap=1,
        examples=({"symbol": "SH600000"},),
    ),
)
