"""kline: candlestick bars of one symbol, kept columnar."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Annotated, Any, Literal, Self, cast

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .._time import from_ms, to_ms
from ..errors import SymbolNotFound, UpstreamChanged
from ..registry import AgentInfo, Endpoint, Freshness, Pagination
from ..symbols import SymbolCode, normalize_symbol

Period = Literal["1m", "5m", "15m", "30m", "60m", "120m", "day", "week", "month", "quarter", "year"]
"""Bar period. The survey measured day, 1m, 5m and 60m."""
Adjust = Literal["before", "after", "normal"]
"""Price adjustment: before (forward-adjusted), after (backward-adjusted), normal (none)."""

DEFAULT_COUNT = 100
MAX_COUNT = 10_000
"""Largest count sent upstream; one request returned all 8756 daily bars of SH000001."""
HISTORY_RUNAWAY_PAGES = 20
"""``kline_history`` fails past this many requests for one symbol."""
_EARLY_BAR = timedelta(hours=1)
"""How early a bar of a day or longer can arrive; see ``_bar_moment``."""
_EARLY_BARS_END = date(1992, 1, 1)
"""No bar from this date on was seen to arrive early."""

BarDate = Annotated[date, Field(ge=date(1900, 1, 1), le=date(2100, 12, 31))]
"""A range date, 1900-01-01 to 2100-12-31."""
BarTime = Annotated[
    AwareDatetime, Field(ge=datetime(1900, 1, 2, tzinfo=UTC), le=datetime(2100, 12, 31, tzinfo=UTC))
]
"""A range moment with its UTC offset, from 1900-01-02 to 2100-12-31."""


def check_range(begin: date | None, end: date | None, before: datetime | None) -> None:
    """Raise ``ValueError`` for a range that is given twice or ends before it begins."""
    if end is not None and before is not None:
        raise ValueError("give end or before, not both")
    last = end if before is None else before.date()
    if begin is not None and last is not None and begin > last:
        raise ValueError("begin must not be after the end of the range")


class KlineParams(BaseModel):
    """Bars ending at ``end`` (default: now) going back, or starting at ``begin`` going forward.

    With ``begin``, up to ``count`` bars from that date are requested; when ``count`` is not set,
    enough to cover ``begin`` through ``end`` (default: today), or the latest ``MAX_COUNT`` of
    them when the dates can hold more: one request returns no more than that, and
    ``kline_history`` gets every bar of a range. Bars outside ``begin`` and ``end`` are dropped.
    Dates are in the exchange timezone. ``before`` ends the range just before a moment instead
    of with a day, to page back through minute bars.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    symbol: SymbolCode
    """Security or index code, such as SH600000, 00700 or AAPL. Bare 6 digits read as a stock, so
    an index needs its prefix, as SH000001 does."""
    period: Period = "day"
    """1m to 120m are minute bars, kept upstream for about 3 trading days (1m) to about 6
    months (60m); day, week, month, quarter and year cover the full history."""
    adjust: Adjust = "before"
    """before (前复权, forward-adjusted), after (后复权, backward-adjusted) or normal (不复权)."""
    begin: BarDate | None = None
    """First date, inclusive."""
    end: BarDate | None = None
    """Last date, inclusive."""
    before: BarTime | None = None
    """End of the range, exclusive, as an ISO time with offset; instead of ``end``."""
    count: int | None = Field(default=None, ge=1, le=MAX_COUNT)
    """Most bars to fetch: by default 100 back from ``end``, or the dates' bars, 10000 at most."""

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        check_range(self.begin, self.end, self.before)
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
    if params.before is not None:
        last = params.before - timedelta(milliseconds=1)
    elif params.end is not None:
        next_day = datetime.combine(params.end + timedelta(days=1), time(), tz)
        last = next_day - timedelta(milliseconds=1)
    return first, last


def _bars_upper_bound(first: date, last: date, period: Period) -> int:
    days = max((last - first).days + 1, 1)
    per_day = 24 * 60 // int(period[:-1]) if period.endswith("m") else 1
    return min(days * per_day, MAX_COUNT)


def _build_query(params: KlineParams, now: datetime) -> dict[str, str]:
    tz = normalize_symbol(params.symbol).timezone
    first, last = _window(params, tz)
    if first is None:
        count, begin = -(params.count or DEFAULT_COUNT), last or now
    else:
        until = (last or now).astimezone(tz).date()
        count = params.count or _bars_upper_bound(first.date(), until, params.period)
        early = first.date() < _EARLY_BARS_END and not params.period.endswith("m")
        begin = first - _EARLY_BAR if early else first
        if params.count is None and count >= MAX_COUNT:
            # The dates may hold more bars than one request returns: the latest come first, going
            # back as a request without begin does, and _parse drops any before begin.
            count, begin = -MAX_COUNT, last or now
    return {
        "symbol": params.symbol,
        "begin": str(to_ms(begin)),
        "period": params.period,
        "type": params.adjust,
        "count": str(count),
        "indicator": "kline",
    }


def _freshness_of(params: KlineParams, fetched_at: datetime) -> Freshness:
    """Daily for day and minute bars of a range that ended before the exchange date of the
    fetch; longer bars can cover days after ``end``, so they stay intraday."""
    tz = normalize_symbol(params.symbol).timezone
    _, last = _window(params, tz)
    closed = last is not None and last.astimezone(tz).date() < fetched_at.astimezone(tz).date()
    minute_or_day = params.period == "day" or params.period.endswith("m")
    return "daily" if closed and minute_or_day else "intraday"


def _parse(payload: Any, params: KlineParams) -> Kline:
    if not isinstance(payload, dict):
        raise ValueError("kline data is not an object")
    data = cast(dict[str, Any], payload)
    if not data:
        # What unknown and delisted codes both get, measured 2026-11-05.
        raise SymbolNotFound(f"the upstream has no bars for {params.symbol}")
    if "column" not in data and data.get("items") == []:
        # What the upstream sends when the request lacks the u cookie, measured 2026-11-05.
        names: list[str] = []
        rows: list[list[Any]] = []
    else:
        names, rows = _table(data)
    tz = normalize_symbol(params.symbol).timezone
    values = list(zip(*rows, strict=True)) if rows else [() for _ in names]
    by_name = dict(zip(names, values, strict=True))
    stamps = [_bar_moment(value, tz, params.period) for value in by_name.pop("timestamp", ())]
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


def _bar_moment(value: Any, tz: tzinfo, period: Period) -> datetime:
    """A bar's time. Hong Kong daily bars of 1987-04 to 1991-09 arrive at 23:00 the day before,
    which is midnight in the daylight saving time Shanghai had then (measured 2026-11-05)."""
    moment = from_ms(value, tz)
    return moment + _EARLY_BAR if moment.hour == 23 and not period.endswith("m") else moment


def _table(data: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    names: Any = data.get("column")
    rows: Any = data.get("item")
    if not isinstance(names, list) or not isinstance(rows, list):
        raise ValueError("kline data needs column and item lists")
    if not names and not rows:
        # Minute bars older than the upstream keeps, measured 2026-11-05: no columns, no rows.
        return [], []
    checked_names = [name for name in cast(list[Any], names) if isinstance(name, str)]
    if len(checked_names) != len(cast(list[Any], names)) or "timestamp" not in checked_names:
        raise ValueError("kline column must be strings including timestamp")
    checked_rows: list[list[Any]] = []
    for row in cast(list[Any], rows):
        if not isinstance(row, list) or len(cast(list[Any], row)) != len(checked_names):
            raise ValueError("kline item rows must match the column list")
        cells = cast(list[Any], row)
        if not all(cell is None or _is_number(cell) for cell in cells):
            raise ValueError("kline item cells must be numbers or null")
        checked_rows.append(cells)
    return checked_names, checked_rows


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def older_range(moment: datetime, period: Period) -> dict[str, Any]:
    """The range params ending just before the bar at ``moment``: ``end`` the day before it, or
    for minute bars ``before`` its time."""
    if period.endswith("m"):
        return {"end": None, "before": moment}
    return {"end": moment.date() - timedelta(days=1), "before": None}


def came_back_full(kline: Kline, count: int) -> bool:
    """Whether a request for ``count`` bars going back may have left older ones out. A page one
    short may still be a full one, if the parse dropped a bar at the boundary."""
    return len(kline) > 0 and len(kline) >= count - 1


class BarWalk:
    """All bars of one symbol in a range, walking back from ``end``.

    One request is enough unless it comes back full. Without ``begin`` a request asks for
    ``MAX_COUNT`` bars; with it, for as many as the dates can hold, so a daily update does not
    fetch the whole history.
    """

    def __init__(
        self,
        symbol: str,
        *,
        period: Period,
        adjust: Adjust,
        begin: date | None,
        end: date | None,
        now: datetime,
    ) -> None:
        checked = ENDPOINT.validate(
            {"symbol": symbol, "period": period, "adjust": adjust, "begin": begin, "end": end}
        )
        self._zone = normalize_symbol(checked.symbol).timezone
        self._period: Period = checked.period
        self._begin = checked.begin
        self._fetched = 0
        self._chunks: list[Kline] = []
        self._count = MAX_COUNT
        if checked.begin is not None:
            until = checked.end or now.astimezone(self._zone).date()
            self._count = _bars_upper_bound(checked.begin, until, checked.period)
        self.params: dict[str, Any] | None = {
            "symbol": checked.symbol,
            "period": checked.period,
            "adjust": checked.adjust,
            "end": checked.end,
            "count": self._count,
        }

    def accept(self, kline: Kline) -> None:
        """Take the bars of the request sent; ``params`` becomes the next one, or None."""
        self._fetched += 1
        self._chunks.append(kline)
        if not came_back_full(kline, self._count) or (
            self._begin is not None and kline.timestamps[0].date() <= self._begin
        ):
            self.params = None
        elif self._fetched >= HISTORY_RUNAWAY_PAGES:
            raise UpstreamChanged(
                f"kline did not end within {HISTORY_RUNAWAY_PAGES} requests",
                endpoint="kline",
                params={"symbol": kline.symbol, "period": self._period},
            )
        else:
            older = older_range(kline.timestamps[0], self._period)
            self.params = {**(self.params or {}), **older}

    def result(self) -> Kline:
        """The bars fetched, without repeats and oldest first, from ``begin`` on."""
        names = list(dict.fromkeys(name for chunk in self._chunks for name in chunk.columns))
        floor = None if self._begin is None else datetime.combine(self._begin, time(), self._zone)
        rows: dict[datetime, list[float | int | None]] = {}
        for chunk in self._chunks:
            for index, stamp in enumerate(chunk.timestamps):
                if floor is None or stamp >= floor:
                    rows.setdefault(
                        stamp,
                        [chunk.columns[n][index] if n in chunk.columns else None for n in names],
                    )
        ordered = sorted(rows)
        columns = {
            name: tuple(rows[stamp][position] for stamp in ordered)
            for position, name in enumerate(names)
        }
        return replace(self._chunks[0], timestamps=tuple(ordered), columns=columns)


ENDPOINT = Endpoint(
    name="kline",
    doc="Bars of one symbol in one request.",
    host="stock.xueqiu.com",
    path="/v5/stock/chart/kline.json",
    params=KlineParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="intraday",
    freshness_of=_freshness_of,
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    pagination=Pagination(style="bars"),
    markets=("CN", "HK", "US"),
    order="oldest_first",
    # Without u the upstream answers HTTP 200 with 0 bars (measured 2026-11-05).
    needs_cookies=("u",),
    agent=AgentInfo(
        category="kline",
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
