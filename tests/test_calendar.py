"""Trading calendars from daily bars, and the market state of a quote."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from xueqiu_api import InvalidParams, UpstreamChanged
from xueqiu_api.endpoints.kline import ENDPOINT as KLINE
from xueqiu_api.endpoints.kline import Kline
from xueqiu_api.endpoints.quote_detail import QuoteDetail
from xueqiu_api.symbols import normalize_symbol
from xueqiu_api.trading_calendar import (
    CALENDAR_SOURCES,
    CalendarSource,
    TradingCalendar,
    calendar_from_kline,
    market_state_from_quote,
)

from .harness import replay_body

FETCHED_AT = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)
SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")


def replay_kline(name: str) -> Kline:
    data = replay_body(name)["data"]
    return KLINE.parse(data, KLINE.validate({"symbol": data["symbol"], "adjust": "normal"}))


def bars_on(symbol: str, days: Iterable[date]) -> Kline:
    zone = normalize_symbol(symbol).timezone
    stamps = tuple(datetime(day.year, day.month, day.day, tzinfo=zone) for day in days)
    return Kline(symbol, "day", "normal", stamps, {})


def weekdays(first: date, count: int) -> list[date]:
    days = (first + timedelta(days=offset) for offset in range(count * 2))
    return [day for day in days if day.weekday() < 5][:count]


@pytest.fixture
def gap_calendar() -> TradingCalendar:
    """10 sessions from 2025-12-24 to 2026-01-08, none from 2026-01-01 to 2026-01-04."""
    kline = replay_kline("kline/sh000001_day")
    return calendar_from_kline(kline, fetched_at=FETCHED_AT, min_sessions=1)


def test_sources_are_the_measured_symbols_and_dates() -> None:
    assert dict(CALENDAR_SOURCES) == {
        "CN": CalendarSource("SH000001", date(1990, 12, 19)),
        "HK": CalendarSource("00700", date(2012, 11, 1)),
        "US": CalendarSource("AAPL", date(1997, 1, 2)),
    }
    for market, source in CALENDAR_SOURCES.items():
        assert normalize_symbol(source.symbol).market == market


def test_the_calendar_keeps_market_source_and_fetch_time(gap_calendar: TradingCalendar) -> None:
    assert gap_calendar.market == "CN"
    assert gap_calendar.source == "SH000001"
    assert gap_calendar.fetched_at == FETCHED_AT
    assert gap_calendar.first == date(2025, 12, 24)
    assert gap_calendar.last == date(2026, 1, 8)
    assert list(gap_calendar.sessions) == sorted(gap_calendar.sessions)


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2025, 12, 31), True),
        (date(2026, 1, 1), False),
        (date(2026, 1, 4), False),
        (date(2026, 1, 5), True),
        (date(2025, 12, 24), True),
        (date(2026, 1, 8), True),
        (date(2025, 12, 23), None),
        (date(2026, 1, 9), None),
        (date(2030, 1, 1), None),
    ],
)
def test_is_session(gap_calendar: TradingCalendar, day: date, expected: bool | None) -> None:
    assert gap_calendar.is_session(day) is expected


def test_the_gap_is_stepped_over(gap_calendar: TradingCalendar) -> None:
    assert gap_calendar.next_session(date(2025, 12, 31)) == date(2026, 1, 5)
    assert gap_calendar.next_session(date(2026, 1, 2)) == date(2026, 1, 5)
    assert gap_calendar.previous_session(date(2026, 1, 5)) == date(2025, 12, 31)
    assert gap_calendar.previous_session(date(2026, 1, 3)) == date(2025, 12, 31)


def test_sessions_between_is_inclusive_and_stays_in_range(gap_calendar: TradingCalendar) -> None:
    between = gap_calendar.sessions_between(date(2025, 12, 31), date(2026, 1, 5))
    assert between == (date(2025, 12, 31), date(2026, 1, 5))
    assert gap_calendar.sessions_between(date(2026, 1, 1), date(2026, 1, 4)) == ()
    assert gap_calendar.sessions_between(date(2000, 1, 1), date(2030, 1, 1)) == (
        gap_calendar.sessions
    )


@pytest.mark.parametrize(
    ("day", "previous", "following"),
    [
        (date(2026, 1, 8), date(2026, 1, 7), None),
        (date(2026, 1, 9), date(2026, 1, 8), None),
        (date(2026, 1, 10), None, None),
        (date(2025, 12, 24), None, date(2025, 12, 25)),
        (date(2025, 12, 23), None, date(2025, 12, 24)),
        (date(2025, 12, 22), None, None),
    ],
)
def test_neighbours_past_the_range_are_not_guessed(
    gap_calendar: TradingCalendar, day: date, previous: date | None, following: date | None
) -> None:
    assert gap_calendar.previous_session(day) == previous
    assert gap_calendar.next_session(day) == following


def test_dates_stay_right_across_the_new_york_clock_change() -> None:
    kline = replay_kline("kline/aapl_normal_dst")
    calendar = calendar_from_kline(kline, fetched_at=FETCHED_AT, min_sessions=1)

    assert calendar.market == "US"
    assert calendar.sessions == (
        date(2026, 10, 28),
        date(2026, 10, 29),
        date(2026, 10, 30),
        date(2026, 11, 2),
        date(2026, 11, 3),
    )
    assert calendar.is_session(date(2026, 11, 1)) is False
    offsets = {stamp.utcoffset() for stamp in kline.timestamps}
    assert offsets == {timedelta(hours=-4), timedelta(hours=-5)}


@pytest.mark.parametrize(
    "data",
    [
        {"items": [], "items_size": 0},
        {"symbol": "SH000001", "column": [], "item": []},
    ],
)
@pytest.mark.parametrize("min_sessions", [1000, 1, 0])
def test_a_success_with_no_bars_is_not_a_calendar(data: Any, min_sessions: int) -> None:
    kline = KLINE.parse(data, KLINE.validate({"symbol": "SH000001"}))
    with pytest.raises(UpstreamChanged) as caught:
        calendar_from_kline(kline, fetched_at=FETCHED_AT, min_sessions=min_sessions)
    assert caught.value.endpoint == "kline"


def test_too_few_sessions_is_an_upstream_change() -> None:
    days = weekdays(date(2020, 1, 1), 1000)
    with pytest.raises(UpstreamChanged, match="999 daily bars"):
        calendar_from_kline(bars_on("SH000001", days[:-1]), fetched_at=FETCHED_AT)

    calendar = calendar_from_kline(bars_on("SH000001", days), fetched_at=FETCHED_AT)
    assert len(calendar.sessions) == 1000


def test_hong_kong_sessions_start_at_the_usable_date() -> None:
    days = weekdays(date(2012, 1, 2), 1100)
    calendar = calendar_from_kline(bars_on("00700", days), fetched_at=FETCHED_AT)

    assert calendar.source == "00700"
    assert calendar.first == date(2012, 11, 1)
    assert calendar.is_session(date(2012, 10, 31)) is None
    assert calendar.sessions == tuple(day for day in days if day >= date(2012, 11, 1))


def test_bars_only_before_the_usable_date_are_not_a_calendar() -> None:
    days = weekdays(date(2005, 1, 3), 1100)
    with pytest.raises(UpstreamChanged, match="0 of them"):
        calendar_from_kline(bars_on("00700", days), fetched_at=FETCHED_AT)


@pytest.mark.parametrize("symbol", ["SH600000", "HKHSI", ".INX"])
def test_only_the_source_symbol_makes_a_calendar(symbol: str) -> None:
    days = weekdays(date(2024, 1, 1), 5)
    with pytest.raises(InvalidParams, match="calendar comes from"):
        calendar_from_kline(bars_on(symbol, days), fetched_at=FETCHED_AT, min_sessions=1)


def test_only_daily_bars_make_a_calendar() -> None:
    weekly = Kline(
        "SH000001", "week", "normal", (datetime(2026, 11, 2, tzinfo=SHANGHAI),), {"close": (1.0,)}
    )
    with pytest.raises(InvalidParams, match="week bars"):
        calendar_from_kline(weekly, fetched_at=FETCHED_AT, min_sessions=1)


def quote(symbol: str, status: int | None, moment: datetime | None) -> QuoteDetail:
    fields: dict[str, Any] = {"symbol": symbol}
    if moment is not None:
        fields["timestamp"] = int(moment.timestamp() * 1000)
    return QuoteDetail.model_validate({"market": {"status_id": status}, "quote": fields})


DAY_CN = datetime(2026, 11, 5, 15, 0, tzinfo=SHANGHAI)
"""A CN quote from a Thursday's close."""


@pytest.mark.parametrize(
    ("status", "phase"),
    [(1, "pre_open"), (5, "trading"), (7, "closed"), (9, "unknown"), (None, "unknown")],
)
def test_status_id_gives_the_phase_and_unknown_values_are_kept(
    status: int | None, phase: str
) -> None:
    state = market_state_from_quote(quote("SH600000", status, DAY_CN), now=DAY_CN)

    assert state.market == "CN"
    assert state.status_id == status
    assert state.phase == phase
    assert state.quote_time == DAY_CN
    assert state.quote_time is not None and state.quote_time.utcoffset() == timedelta(hours=8)


@pytest.mark.parametrize(
    ("symbol", "status", "moment", "now", "expected"),
    [
        pytest.param(
            "SH600000",
            5,
            DAY_CN - timedelta(days=1),
            DAY_CN,
            True,
            id="trading-with-an-old-quote",
        ),
        pytest.param(
            "SH600000",
            7,
            DAY_CN,
            DAY_CN + timedelta(hours=8),
            True,
            id="closed-with-a-quote-from-today",
        ),
        pytest.param(
            "SH600000",
            7,
            DAY_CN - timedelta(days=1),
            datetime(2026, 11, 5, 15, 31, tzinfo=SHANGHAI),
            False,
            id="cn-past-the-day-with-an-older-quote",
        ),
        pytest.param(
            "00700",
            7,
            DAY_CN - timedelta(days=1),
            datetime(2026, 11, 5, 16, 31, tzinfo=SHANGHAI),
            False,
            id="hk-past-the-day-with-an-older-quote",
        ),
        pytest.param(
            "AAPL",
            7,
            datetime(2026, 11, 4, 16, 0, tzinfo=NEW_YORK),
            datetime(2026, 11, 5, 16, 31, tzinfo=NEW_YORK),
            False,
            id="us-past-the-day-with-an-older-quote",
        ),
        pytest.param(
            "SH600000",
            7,
            DAY_CN - timedelta(days=1),
            datetime(2026, 11, 5, 15, 30, tzinfo=SHANGHAI),
            None,
            id="cn-not-yet-past-the-day",
        ),
        pytest.param(
            "SH600000",
            7,
            DAY_CN - timedelta(days=1),
            datetime(2026, 11, 5, 8, 0, tzinfo=SHANGHAI),
            None,
            id="cn-before-the-open-or-closed",
        ),
        pytest.param(
            "SH600000",
            9,
            None,
            datetime(2026, 11, 5, 18, 0, tzinfo=SHANGHAI),
            None,
            id="no-quote-time",
        ),
        pytest.param(
            "AAPL",
            7,
            datetime(2026, 11, 4, 16, 0, tzinfo=NEW_YORK),
            datetime(2026, 11, 6, 4, 40, tzinfo=SHANGHAI),
            None,
            id="us-read-in-new-york-time",
        ),
        pytest.param(
            "AAPL",
            7,
            datetime(2026, 11, 5, 16, 0, tzinfo=NEW_YORK),
            datetime(2026, 11, 6, 8, 0, tzinfo=SHANGHAI),
            True,
            id="us-quote-from-today-in-new-york",
        ),
    ],
)
def test_traded_today(
    symbol: str, status: int | None, moment: datetime | None, now: datetime, expected: bool | None
) -> None:
    state = market_state_from_quote(quote(symbol, status, moment), now=now)
    assert state.traded_today is expected
