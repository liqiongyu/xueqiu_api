"""The data feature methods of both clients: what they send, limits kept, errors passed on."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from itertools import pairwise
from typing import Any, Self

import httpx2
import pytest

from xueqiu_api import (
    Blocked,
    InvalidParams,
    SymbolNotFound,
    UpstreamChanged,
    UpstreamError,
    XueqiuError,
)
from xueqiu_api._time import to_ms
from xueqiu_api.endpoints.kline import ENDPOINT as KLINE
from xueqiu_api.endpoints.quote_detail import ENDPOINT as QUOTE_DETAIL
from xueqiu_api.sites import SITES
from xueqiu_api.symbols import Market, normalize_symbol
from xueqiu_api.trading_calendar import CALENDAR_SOURCES
from xueqiu_api.universe import check_snapshot

from .harness import Harness, Reply, error_reply, json_reply, text_reply
from .test_universe import (
    LIST_PATH,
    RANK_PATH,
    STOCK,
    check,
    cn_boards,
    cn_symbols,
    hk_symbols,
    quote_lists,
    serve_cn,
    serve_us,
    us_symbols,
)

CLOSE = datetime(2026, 11, 5, 8, 0, tzinfo=UTC)
"""16:00 in Shanghai and Hong Kong, 03:00 in New York."""
Moment = Callable[[datetime], None]
Scale = Callable[[date], float]


def days_from(first: date, count: int) -> list[date]:
    return [first + timedelta(days=n) for n in range(count)]


def days_through(first: date, last: date) -> list[date]:
    return days_from(first, (last - first).days + 1)


def midnight(symbol: str, day: date) -> datetime:
    return datetime.combine(day, time(), normalize_symbol(symbol).timezone)


def end_of_day_ms(symbol: str, day: date) -> str:
    return str(to_ms(midnight(symbol, day + timedelta(days=1))) - 1)


def bars_response(symbol: str, days: Sequence[date], scale: Scale = lambda day: 1.0) -> Any:
    rows = [
        [to_ms(midnight(symbol, day)), 100, *[round(10.0 * scale(day), 4)] * 4, 0.0, 0.0]
        for day in days
    ]
    data = {
        "symbol": symbol,
        "column": ["timestamp", "volume", "open", "high", "low", "close", "chg", "percent"],
        "item": rows,
    }
    return httpx2.Response(200, json={"data": data, "error_code": 0, "error_description": ""})


def serve_bars(h: Harness, symbol: str, days: Sequence[date]) -> None:
    h.upstream.route(STOCK, KLINE.path, lambda request: bars_response(symbol, days))


def serve_factors(h: Harness, symbol: str, days: Sequence[date], scale: Scale) -> None:
    """Unadjusted bars, and adjusted ones multiplied by ``scale``, whichever ``type`` asks for."""

    def reply(request: httpx2.Request) -> httpx2.Response:
        adjusted = request.url.params["type"] == "after"
        return bars_response(symbol, days, scale if adjusted else lambda day: 1.0)

    h.upstream.route(STOCK, KLINE.path, reply)


def quote_body(symbol: str, status: int, moment: datetime) -> dict[str, Any]:
    fields = {"symbol": symbol, "current": 10.0, "timestamp": to_ms(moment)}
    data: dict[str, Any] = {"market": {"status_id": status}, "quote": fields, "tags": []}
    return {"data": data, "error_code": 0, "error_description": ""}


def gaps(sent: Sequence[Any]) -> list[float]:
    return [later.at - earlier.at for earlier, later in pairwise(sent)]


@pytest.fixture
def at(monkeypatch: pytest.MonkeyPatch) -> Moment:
    """Set the time both clients read from ``datetime.now``."""

    def freeze(moment: datetime) -> None:
        class Frozen(datetime):
            @classmethod
            def now(cls, tz: tzinfo | None = None) -> Self:
                return cls.fromtimestamp(moment.timestamp(), tz)

        for module in ("_sync", "_async"):
            monkeypatch.setattr(f"xueqiu_api.{module}.datetime", Frozen)

    return freeze


# ---- security_snapshot


def test_a_snapshot_walks_every_page_at_the_interval_of_the_site(h: Harness, at: Moment) -> None:
    at(CLOSE)
    serve_cn(h.upstream)

    snapshot = h.run("security_snapshot", "CN")

    sent = h.upstream.requests(STOCK)
    assert len(sent) == 61
    assert gaps(sent) == pytest.approx([SITES[STOCK].min_interval_s] * 60)
    assert snapshot.complete
    assert snapshot.fetched_at == CLOSE
    assert snapshot.screen_total == len(snapshot.securities) == len(cn_symbols())
    assert check_snapshot(snapshot).ok


def test_a_snapshot_keeps_a_longer_interval_of_the_caller(make: Callable[..., Harness]) -> None:
    h = make(min_intervals={STOCK: 2.5})
    serve_cn(h.upstream)

    h.run("security_snapshot", "CN", segments=["kcb"])

    assert gaps(h.upstream.requests(STOCK)) == pytest.approx([2.5] * 6)


def test_a_snapshot_walks_the_list_of_each_other_market(h: Harness) -> None:
    h.upstream.route(STOCK, LIST_PATH, quote_lists({"HK": {"hk": hk_symbols(250)}}))
    serve_us(h.upstream, us_symbols(150))

    hong_kong = h.run("security_snapshot", "HK")
    united_states = h.run("security_snapshot", "US")

    assert (hong_kong.complete, len(hong_kong.securities)) == (True, 250)
    assert (united_states.complete, len(united_states.securities)) == (True, 150)
    assert len(h.upstream.requests(STOCK, LIST_PATH)) == 3
    assert len(h.upstream.requests(STOCK, RANK_PATH)) == 2


def test_some_segments_walk_only_those_lists(h: Harness) -> None:
    serve_cn(h.upstream)

    snapshot = h.run("security_snapshot", "CN", segments=["kcb", "shb"])

    assert [item.segment for item in snapshot.coverage] == ["kcb", "shb"]
    assert snapshot.screen_total is None
    assert h.upstream.requests(STOCK, RANK_PATH) == []


@pytest.mark.parametrize(
    ("failure", "error"),
    [(error_reply("99999", "synthetic"), UpstreamError), (text_reply("no", 418), Blocked)],
    ids=["upstream-code", "blocked"],
)
def test_an_error_on_a_page_comes_out_as_it_is_and_ends_the_walk(
    h: Harness, failure: Reply, error: type[XueqiuError]
) -> None:
    boards = quote_lists({"CN": cn_boards()})

    def reply(request: httpx2.Request) -> httpx2.Response:
        return failure(request) if request.url.params["page"] == "3" else boards(request)

    h.upstream.route(STOCK, LIST_PATH, reply)

    with pytest.raises(error) as caught:
        h.run("security_snapshot", "CN")

    assert caught.value.endpoint == "screener_quote_list"
    assert [sent.params["page"] for sent in h.upstream.requests(STOCK)] == ["1", "2", "3"]


@pytest.mark.parametrize(
    "failure",
    [
        error_reply("99999", "synthetic"),
        text_reply("no", 418),
        json_reply({"data": {"list": []}, "error_code": 0, "error_description": ""}),
    ],
    ids=["upstream-code", "blocked", "no-count"],
)
def test_a_failed_ranking_count_leaves_the_snapshot_without_it(h: Harness, failure: Reply) -> None:
    h.upstream.route(STOCK, LIST_PATH, quote_lists({"CN": cn_boards()}))
    h.upstream.route(STOCK, RANK_PATH, failure)

    snapshot = h.run("security_snapshot", "CN")

    assert snapshot.complete
    assert snapshot.screen_total is None
    assert len(snapshot.securities) == len(cn_symbols())
    assert h.upstream.requests(STOCK, RANK_PATH)
    total = check(check_snapshot(snapshot), "snapshot.total_matches_screen")
    assert total.status == "skipped"
    assert total.reason is not None
    assert "can fail" in total.reason


def test_a_page_served_again_or_a_capped_list_only_clears_complete(h: Harness) -> None:
    symbols = hk_symbols(250)
    served = quote_lists({"HK": {"hk": symbols}})

    def again(request: httpx2.Request) -> httpx2.Response:
        if request.url.params["page"] != "2":
            return served(request)
        return served(httpx2.Request("GET", request.url.copy_set_param("page", "1")))

    h.upstream.route(STOCK, LIST_PATH, again)
    repeated = h.run("security_snapshot", "HK")
    h.upstream.route(STOCK, LIST_PATH, quote_lists({"HK": {"hk": hk_symbols(5000)}}))
    capped = h.run("security_snapshot", "HK")

    assert (repeated.complete, repeated.coverage[0].repeated) == (False, 100)
    assert (capped.complete, capped.coverage[0].capped) == (False, True)


# ---- trading_calendar


@pytest.mark.parametrize("market", ["CN", "HK", "US"])
def test_a_calendar_takes_one_request_for_the_daily_bars_of_the_source(
    h: Harness, at: Moment, market: Market
) -> None:
    at(CLOSE)
    source = CALENDAR_SOURCES[market]
    days = days_through(date(2023, 1, 2), date(2026, 11, 4))
    serve_bars(h, source.symbol, days)

    calendar = h.run("trading_calendar", market)

    [sent] = h.upstream.requests(STOCK)
    assert sent.path == KLINE.path
    assert sent.params["symbol"] == source.symbol
    assert sent.params["period"] == "day"
    assert sent.params["type"] == "normal"
    assert sent.params["count"] == "-10000"
    assert (calendar.market, calendar.source) == (market, source.symbol)
    assert calendar.sessions == tuple(day for day in days if day >= source.usable_from)
    assert calendar.last == date(2026, 11, 4)
    assert calendar.fetched_at == CLOSE


def test_a_calendar_needs_enough_bars(h: Harness) -> None:
    serve_bars(h, "SH000001", days_from(date(2020, 1, 1), 1000))
    assert len(h.run("trading_calendar", "CN").sessions) == 1000

    before = len(h.upstream.requests(STOCK))
    serve_bars(h, "SH000001", days_from(date(2020, 1, 1), 999))
    with pytest.raises(UpstreamChanged):
        h.run("trading_calendar", "CN")
    assert len(h.upstream.requests(STOCK)) == before + 1


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({"symbol": "SH000001", "column": [], "item": []}, UpstreamChanged),
        ({}, SymbolNotFound),
    ],
    ids=["no-bars", "no-symbol"],
)
def test_a_calendar_from_no_bars_raises(
    h: Harness, data: dict[str, Any], error: type[Exception]
) -> None:
    body = {"data": data, "error_code": 0, "error_description": ""}
    h.upstream.route(STOCK, KLINE.path, json_reply(body))

    with pytest.raises(error):
        h.run("trading_calendar", "CN")


@pytest.mark.parametrize("method", ["security_snapshot", "trading_calendar", "market_state"])
def test_an_unknown_market_sends_nothing(h: Harness, method: str) -> None:
    with pytest.raises(InvalidParams) as caught:
        h.run(method, "JP")

    assert caught.value.field == "market"
    assert caught.value.valid_values == ("CN", "HK", "US")
    assert h.upstream.sent == []


# ---- market_state


@pytest.mark.parametrize(
    ("market", "status", "quoted", "phase", "traded_today"),
    [
        ("CN", 7, datetime(2026, 11, 5, 7, 0, tzinfo=UTC), "closed", True),
        ("HK", 5, datetime(2026, 11, 5, 7, 59, tzinfo=UTC), "trading", True),
        ("US", 7, datetime(2026, 11, 4, 21, 0, tzinfo=UTC), "closed", None),
        ("CN", 7, datetime(2026, 11, 4, 7, 0, tzinfo=UTC), "closed", False),
        ("HK", 8, datetime(2026, 11, 4, 8, 8, tzinfo=UTC), "unknown", None),
    ],
)
def test_a_market_state_reads_one_quote_of_the_source(
    h: Harness,
    at: Moment,
    market: Market,
    status: int,
    quoted: datetime,
    phase: str,
    traded_today: bool | None,
) -> None:
    at(CLOSE)
    symbol = CALENDAR_SOURCES[market].symbol
    h.upstream.route(STOCK, QUOTE_DETAIL.path, json_reply(quote_body(symbol, status, quoted)))

    state = h.run("market_state", market)

    [sent] = h.upstream.requests(STOCK)
    assert sent.path == QUOTE_DETAIL.path
    assert sent.params == {"symbol": symbol, "extend": "detail"}
    assert (state.market, state.status_id, state.phase) == (market, status, phase)
    assert state.quote_time == quoted
    assert state.traded_today is traded_today


def test_an_error_of_the_quote_comes_out_as_it_is(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTE_DETAIL.path, error_reply("99999", "synthetic"))

    with pytest.raises(UpstreamError):
        h.run("market_state", "CN")


# ---- adjust_factors


@pytest.mark.parametrize(
    ("symbol", "moment", "end"),
    [
        ("SH600000", datetime(2026, 11, 5, 15, 59, tzinfo=UTC), date(2026, 11, 4)),
        ("SH600000", datetime(2026, 11, 5, 16, 0, tzinfo=UTC), date(2026, 11, 5)),
        ("00700", datetime(2026, 11, 5, 23, 30, tzinfo=UTC), date(2026, 11, 5)),
        ("AAPL", datetime(2026, 11, 6, 4, 59, tzinfo=UTC), date(2026, 11, 4)),
        ("AAPL", datetime(2026, 11, 6, 5, 0, tzinfo=UTC), date(2026, 11, 5)),
    ],
)
def test_factors_end_yesterday_on_the_exchange_by_default_in_both_requests(
    h: Harness, at: Moment, symbol: str, moment: datetime, end: date
) -> None:
    at(moment)
    serve_factors(h, symbol, days_from(date(2026, 10, 1), 40), lambda day: 2.0)

    factors = h.run("adjust_factors", symbol)

    normal, after = h.upstream.requests(STOCK, KLINE.path)
    assert (normal.params["type"], after.params["type"]) == ("normal", "after")
    assert normal.params["begin"] == after.params["begin"] == end_of_day_ms(symbol, end)
    assert normal.params["count"] == after.params["count"] == "-10000"
    assert normal.params["symbol"] == after.params["symbol"] == symbol
    assert factors.dates[-1] == end
    assert factors.fetched_at == moment


def test_factors_take_the_end_of_the_caller_as_it_is(h: Harness) -> None:
    end = date(2026, 7, 16)
    serve_factors(h, "SH600000", days_from(date(2026, 7, 1), 40), lambda day: 2.0)

    factors = h.run("adjust_factors", "SH600000", end=end)

    normal, after = h.upstream.requests(STOCK, KLINE.path)
    assert normal.params["begin"] == after.params["begin"] == end_of_day_ms("SH600000", end)
    assert factors.dates[-1] == end


def test_factors_leave_out_the_bar_of_today_that_can_differ_between_the_requests(
    h: Harness, at: Moment
) -> None:
    at(CLOSE)
    today = date(2026, 11, 5)

    def scale(day: date) -> float:
        return 3.0 if day == today else 2.0

    serve_factors(h, "SH600000", days_through(date(2026, 10, 1), today), scale)

    factors = h.run("adjust_factors", "SH600000")

    assert factors.dates[-1] == today - timedelta(days=1)
    assert factors.steps == ()


def test_factors_step_on_the_ex_date(h: Harness) -> None:
    ex_date = date(2026, 7, 16)

    def scale(day: date) -> float:
        return 2.0 if day < ex_date else 2.5

    serve_factors(h, "AAPL", days_from(date(2026, 7, 1), 30), scale)

    factors = h.run("adjust_factors", "AAPL", end=date(2026, 7, 30))

    assert factors.symbol == "AAPL"
    [step] = factors.steps
    assert step.date == ex_date
    assert step.ratio == pytest.approx(1.25, rel=1e-4)


def test_factors_of_a_history_longer_than_one_request_cover_all_of_it(h: Harness) -> None:
    days = days_from(date(1990, 1, 1), 12_000)

    def reply(request: httpx2.Request) -> httpx2.Response:
        query = request.url.params
        ending = [day for day in days if to_ms(midnight("SH000001", day)) <= int(query["begin"])]
        return bars_response("SH000001", ending[int(query["count"]) :])

    h.upstream.route(STOCK, KLINE.path, reply)

    factors = h.run("adjust_factors", "SH000001", end=days[-1])

    types = [sent.params["type"] for sent in h.upstream.requests(STOCK, KLINE.path)]
    assert types == ["normal", "normal", "after", "after"]
    assert factors.dates == tuple(days)


def test_an_error_of_the_second_request_comes_out_as_it_is(h: Harness) -> None:
    days = days_from(date(2026, 7, 1), 10)

    def reply(request: httpx2.Request) -> httpx2.Response:
        if request.url.params["type"] == "after":
            return error_reply("99999", "synthetic")(request)
        return bars_response("SH600000", days)

    h.upstream.route(STOCK, KLINE.path, reply)

    with pytest.raises(UpstreamError):
        h.run("adjust_factors", "SH600000", end=days[-1])

    assert len(h.upstream.requests(STOCK, KLINE.path)) == 2


def test_factors_from_no_bars_raise(h: Harness) -> None:
    serve_bars(h, "SH600000", [])

    with pytest.raises(UpstreamChanged):
        h.run("adjust_factors", "SH600000")


@pytest.mark.parametrize(
    ("symbol", "options", "error"),
    [
        ("!!", {}, SymbolNotFound),
        (123, {}, InvalidParams),
        ("SH600000", {"end": "someday"}, InvalidParams),
    ],
    ids=["symbol", "symbol-type", "end"],
)
def test_bad_factor_params_send_nothing(
    h: Harness, symbol: Any, options: dict[str, Any], error: type[Exception]
) -> None:
    with pytest.raises(error):
        h.run("adjust_factors", symbol, **options)

    assert h.upstream.sent == []
