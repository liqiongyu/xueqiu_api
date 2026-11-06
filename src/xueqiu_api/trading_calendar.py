"""Trading calendars derived from daily bars, and the market state of a quote.

The upstream publishes no calendar. Sessions are read from the daily bars of a source symbol, so
a calendar only knows the past and a date outside its range gets ``None``. Half-day sessions are
not modelled.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from types import MappingProxyType
from typing import Literal
from zoneinfo import ZoneInfo

from .endpoints.kline import Kline
from .endpoints.quote_detail import QuoteDetail
from .errors import InvalidParams, UpstreamChanged
from .symbols import TIMEZONES, Market, normalize_symbol

__all__ = [
    "CALENDAR_SOURCES",
    "SESSION_HOURS",
    "CalendarSource",
    "MarketState",
    "Phase",
    "TradingCalendar",
    "calendar_from_kline",
    "in_session",
    "market_state_from_quote",
    "session_over",
]

Phase = Literal["trading", "closed", "pre_open", "unknown"]

_ONE_DAY = timedelta(days=1)
_PHASES: Mapping[int, Phase] = MappingProxyType({1: "pre_open", 5: "trading", 7: "closed"})
SESSION_HOURS: Mapping[Market, tuple[time, time]] = MappingProxyType(
    {
        "CN": (time(9, 15), time(15, 30)),
        "HK": (time(9, 0), time(16, 30)),
        "US": (time(9, 30), time(16, 30)),
    }
)
"""Open and close of each market's day, in the exchange's local time (``symbols.TIMEZONES``).

The opens follow the exchange rules; the CN and HK ones include the opening auctions. A close
is the local time by which the session's quote has arrived: the CN extended quote stamps 15:30
and the HK quote 16:08 (measured 2026-11-04). There is no holiday table.
"""


@dataclass(frozen=True, slots=True)
class CalendarSource:
    """The symbol whose daily bars give the sessions of a market."""

    symbol: str
    usable_from: date
    """First date its bars can be read as the sessions of the market."""


CALENDAR_SOURCES: Mapping[Market, CalendarSource] = MappingProxyType(
    {
        # Equal day by day to XSHG in exchange_calendars 4.13.2, measured 2026-11-05.
        "CN": CalendarSource("SH000001", date(1990, 12, 19)),
        # No bar off XHKG in exchange_calendars 4.13.2, measured 2026-11-05. 53 XHKG sessions have
        # no bar. The last two, 2023-09-01 and 2023-09-08, were weather closures that library
        # lacks; before them comes 2012-10-31, when HKHSI had volume, so the stock was not trading.
        # HKHSI is no source: it has placeholder rows.
        "HK": CalendarSource("00700", date(2012, 11, 1)),
        # Equal day by day to XNYS in exchange_calendars 4.13.2, 7508 sessions, measured
        # 2026-11-05. .INX is no source: it has extra days and more bars than one request returns.
        "US": CalendarSource("AAPL", date(1997, 1, 2)),
    }
)
"""Source of each market's calendar. Fetch its unadjusted daily bars in one request."""


@dataclass(frozen=True, slots=True)
class TradingCalendar:
    """Past sessions of one market, in the exchange's local dates.

    Today counts once the source has a bar for it, even an unfinished one.
    """

    market: Market
    source: str
    """Symbol the sessions come from."""
    sessions: tuple[date, ...]
    """Sessions, oldest first."""
    fetched_at: datetime

    @property
    def first(self) -> date:
        return self.sessions[0]

    @property
    def last(self) -> date:
        return self.sessions[-1]

    def is_session(self, day: date) -> bool | None:
        """Whether ``day`` is a session; None outside the calendar's range."""
        if not self.first <= day <= self.last:
            return None
        index = bisect_left(self.sessions, day)
        return self.sessions[index] == day

    def sessions_between(self, begin: date, end: date) -> tuple[date, ...]:
        """Sessions from ``begin`` to ``end``, both inclusive, as far as the calendar reaches."""
        return self.sessions[bisect_left(self.sessions, begin) : bisect_right(self.sessions, end)]

    def previous_session(self, day: date) -> date | None:
        """The last session before ``day``; None when the calendar cannot tell."""
        if not self.first < day <= self.last + _ONE_DAY:
            return None
        return self.sessions[bisect_left(self.sessions, day) - 1]

    def next_session(self, day: date) -> date | None:
        """The first session after ``day``; None when the calendar cannot tell."""
        if not self.first - _ONE_DAY <= day < self.last:
            return None
        return self.sessions[bisect_right(self.sessions, day)]


def calendar_from_kline(
    kline: Kline, *, fetched_at: datetime, min_sessions: int = 1000
) -> TradingCalendar:
    """The calendar of a market from the daily bars of its ``CALENDAR_SOURCES`` symbol.

    Raises ``InvalidParams`` for other bars, because a stock's suspensions would read as closed
    days. Raises ``UpstreamChanged`` when ``kline`` has fewer than ``min_sessions`` bars or none
    in the source's usable range: the upstream can answer with 0 bars and no error, and an empty
    calendar must not read as a market without sessions.
    """
    market = normalize_symbol(kline.symbol).market
    source = CALENDAR_SOURCES[market]
    if kline.symbol != source.symbol or kline.period != "day":
        raise InvalidParams(
            f"the {market} calendar comes from {source.symbol} daily bars, "
            f"not {kline.symbol} {kline.period} bars"
        )
    days = {stamp.date() for stamp in kline.timestamps}
    sessions = tuple(sorted(day for day in days if day >= source.usable_from))
    if len(kline) < min_sessions or not sessions:
        raise UpstreamChanged(
            f"{kline.symbol} has {len(kline)} daily bars, {len(sessions)} of them from "
            f"{source.usable_from} on; a calendar needs at least {min_sessions}",
            endpoint="kline",
            params={"symbol": kline.symbol},
        )
    return TradingCalendar(market, source.symbol, sessions, fetched_at)


@dataclass(frozen=True, slots=True)
class MarketState:
    """What one quote says about its market right now."""

    market: Market
    status_id: int | None
    """Upstream ``market.status_id``: 5 (交易中) and 7 (已收盘) were measured on 2026-11-04, and 1
    (未开盘) at 02:00 Shanghai time on 2026-11-06 for CN and HK. Other values give unknown."""
    phase: Phase
    """trading for status 5, closed for 7, unknown for anything else."""
    quote_time: datetime | None
    """Quote time, in the exchange timezone."""
    traded_today: bool | None
    """Whether the market has traded today. None when the quote cannot tell, which covers both
    the time before the open and a closed day until the session would have ended."""


def market_state_from_quote(quote: QuoteDetail, *, now: datetime) -> MarketState:
    """The state of the market of ``quote`` at ``now``, an aware datetime.

    ``traded_today`` does not rely on what ``status_id`` says on closed days, which is not known
    yet: it is True while trading or when the quote is from today, False when the quote is from
    an earlier day and today's quote would have arrived, and None otherwise.
    """
    symbol = normalize_symbol(quote.symbol)
    status = quote.market_status_id
    phase: Phase = "unknown" if status is None else _PHASES.get(status, "unknown")
    today = now.astimezone(symbol.timezone)
    quote_day = (
        None if quote.timestamp is None else quote.timestamp.astimezone(symbol.timezone).date()
    )
    traded_today: bool | None = None
    if phase == "trading" or quote_day == today.date():
        traded_today = True
    elif quote_day is not None and quote_day < today.date() and session_over(symbol.market, now):
        traded_today = False
    return MarketState(
        market=symbol.market,
        status_id=status,
        phase=phase,
        quote_time=quote.timestamp,
        traded_today=traded_today,
    )


def in_session(market: Market, at: datetime) -> bool:
    """Whether ``at``, an aware datetime, is Monday to Friday within ``SESSION_HOURS`` of
    ``market``, both ends included. Holidays count as sessions."""
    local = at.astimezone(ZoneInfo(TIMEZONES[market]))
    opens, closes = SESSION_HOURS[market]
    return local.weekday() < 5 and opens <= local.time() <= closes


def session_over(market: Market, at: datetime) -> bool:
    """Whether ``at``, an aware datetime, is later than the close of ``market`` on its local
    date, on any day of the week."""
    local = at.astimezone(ZoneInfo(TIMEZONES[market]))
    return local.time() > SESSION_HOURS[market][1]
