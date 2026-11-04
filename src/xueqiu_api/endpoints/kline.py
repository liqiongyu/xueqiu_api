"""kline: candlestick bars of one symbol, kept columnar."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .._time import from_ms, to_ms
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolCode, normalize_symbol

Period = Literal["1m", "5m", "15m", "30m", "60m", "120m", "day", "week", "month", "quarter", "year"]
"""Bar period. The survey measured day, 1m, 5m and 60m."""
Adjust = Literal["before", "after", "normal"]
"""Price adjustment: before (forward-adjusted), after (backward-adjusted), normal (none)."""

DEFAULT_COUNT = 100
MAX_COUNT = 10_000
"""Largest count sent upstream; one request returned all 8756 daily bars of SH000001."""


class KlineParams(BaseModel):
    """Bars ending at ``end`` (default: now) going back, or starting at ``begin`` going forward.

    With ``begin``, up to ``count`` bars from that date are requested; when ``count`` is not set,
    enough to cover ``begin`` through ``end`` (default: today). Bars outside ``begin`` and
    ``end`` are dropped. Dates are in the exchange timezone.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    symbol: SymbolCode
    period: Period = "day"
    """1m to 120m are minute bars, kept upstream for about 3 trading days (1m) to about 6
    months (60m); day, week, month, quarter and year cover the full history."""
    adjust: Adjust = "before"
    """before (前复权, forward-adjusted), after (后复权, backward-adjusted) or normal (不复权)."""
    begin: date | None = None
    """First date, inclusive."""
    end: date | None = None
    """Last date, inclusive."""
    count: int | None = Field(default=None, ge=1, le=MAX_COUNT)
    """Most bars to fetch: by default 100 back from ``end``, or enough to cover the dates."""

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        if self.begin is not None and self.end is not None and self.begin > self.end:
            raise ValueError("begin must not be after end")
        return self


@dataclass(frozen=True, slots=True)
class Kline:
    """Bars of one symbol in columns, oldest first.

    ``columns`` holds every upstream column except ``timestamp`` under its upstream name,
    including ones this package does not know. Common columns: ``open``, ``high``, ``low``,
    ``close`` (quote currency), ``volume`` (shares), ``amount`` (quote currency), ``chg``
    (quote currency), ``percent`` and ``turnoverrate`` (percent points).
    """

    symbol: str
    period: Period
    adjust: Adjust
    timestamps: tuple[datetime, ...]
    """Bar times, timezone-aware in the exchange timezone; daily bars fall on local midnight."""
    columns: Mapping[str, tuple[float | int | None, ...]]

    def __len__(self) -> int:
        return len(self.timestamps)


def _window(params: KlineParams, tz: tzinfo) -> tuple[datetime | None, datetime | None]:
    first = None if params.begin is None else datetime.combine(params.begin, time(), tz)
    last = None
    if params.end is not None:
        next_day = datetime.combine(params.end + timedelta(days=1), time(), tz)
        last = next_day - timedelta(milliseconds=1)
    return first, last


def _bars_upper_bound(first: date, last: date, period: Period) -> int:
    days = max((last - first).days + 1, 1)
    per_day = 24 * 60 // int(period[:-1]) if period.endswith("m") else 1
    return min(days * per_day, MAX_COUNT)


def _build_query(params: KlineParams, now: datetime) -> dict[str, str]:
    tz = normalize_symbol(params.symbol).timezone
    if params.begin is not None:
        until = params.end or now.astimezone(tz).date()
        count = params.count or _bars_upper_bound(params.begin, until, params.period)
        begin = datetime.combine(params.begin, time(), tz)
    else:
        count = -(params.count or DEFAULT_COUNT)
        begin = _window(params, tz)[1] or now
    return {
        "symbol": params.symbol,
        "begin": str(to_ms(begin)),
        "period": params.period,
        "type": params.adjust,
        "count": str(count),
        "indicator": "kline",
    }


def _parse(payload: Any, params: KlineParams) -> Kline:
    if not isinstance(payload, dict):
        raise ValueError("kline data is not an object")
    data = cast(dict[str, Any], payload)
    if "column" not in data and data.get("items") == []:
        # Shape the upstream sends when there are no bars.
        names: list[str] = []
        rows: list[list[Any]] = []
    else:
        names, rows = _table(data)
    tz = normalize_symbol(params.symbol).timezone
    values = list(zip(*rows, strict=True)) if rows else [() for _ in names]
    by_name = dict(zip(names, values, strict=True))
    stamps = [from_ms(value, tz) for value in by_name.pop("timestamp", ())]
    first, last = _window(params, tz)
    keep = [
        index
        for index, stamp in enumerate(stamps)
        if (first is None or stamp >= first) and (last is None or stamp <= last)
    ]
    if len(keep) < len(stamps):
        stamps = [stamps[index] for index in keep]
        by_name = {name: tuple(column[index] for index in keep) for name, column in by_name.items()}
    return Kline(
        symbol=params.symbol,
        period=params.period,
        adjust=params.adjust,
        timestamps=tuple(stamps),
        columns={name: tuple(column) for name, column in by_name.items()},
    )


def _table(data: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    names: Any = data.get("column")
    rows: Any = data.get("item")
    if not isinstance(names, list) or not isinstance(rows, list):
        raise ValueError("kline data needs column and item lists")
    checked_names = [name for name in cast(list[Any], names) if isinstance(name, str)]
    if len(checked_names) != len(cast(list[Any], names)) or "timestamp" not in checked_names:
        raise ValueError("kline column must be strings including timestamp")
    checked_rows: list[list[Any]] = []
    for row in cast(list[Any], rows):
        if not isinstance(row, list) or len(cast(list[Any], row)) != len(checked_names):
            raise ValueError("kline item rows must match the column list")
        checked_rows.append(cast(list[Any], row))
    return checked_names, checked_rows


ENDPOINT = Endpoint(
    name="kline",
    host="stock.xueqiu.com",
    path="/v5/stock/chart/kline.json",
    params=KlineParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="intraday",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    agent=AgentInfo(
        title_zh="K线",
        title_en="Candlestick bars",
        summary="取一只股票或指数的日线、周线、月线或分钟线，可选前复权、后复权或不复权。",
        keywords=(
            "K线",
            "日线",
            "周线",
            "月线",
            "分钟线",
            "前复权",
            "后复权",
            "kline",
            "candlestick",
            "OHLCV",
            "bars",
            "history",
        ),
        default_fields=("timestamp", "open", "high", "low", "close", "volume", "amount", "percent"),
        row_default=100,
        row_cap=500,
        upstream_cap=MAX_COUNT,
        examples=({"symbol": "SH600000", "period": "day", "adjust": "before", "count": 3},),
        mcp_tool="xq_kline",
    ),
)
