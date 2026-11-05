"""Quotes and rankings: what they ask the upstream, units, time zones, order, what stays out."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from xueqiu_api import InvalidParams, UpstreamChanged, UpstreamError
from xueqiu_api._async import AsyncClient, AsyncRuntime
from xueqiu_api.endpoints import REGISTRY
from xueqiu_api.endpoints.market_bj_indexes import BoardIndexQuote
from xueqiu_api.endpoints.market_hot_stocks import HotStock
from xueqiu_api.endpoints.market_industry_stocks import IndustryPeer, IndustryStocks
from xueqiu_api.endpoints.quote_heat import RankPoint, StockHeat
from xueqiu_api.endpoints.quote_minute import MinuteChart
from xueqiu_api.endpoints.quote_order_book import BookLevel, OrderBook
from xueqiu_api.endpoints.quote_relevant import RelatedStock
from xueqiu_api.endpoints.quote_trades import TradeTick
from xueqiu_api.endpoints.search_stock import StockHit
from xueqiu_api.mcp_server import McpOptions
from xueqiu_api.mcp_server.tools import Toolbox

from .harness import (
    FakeClock,
    FakeUpstream,
    Harness,
    Sent,
    error_reply,
    fixture,
    fixture_reply,
    json_reply,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
HONG_KONG = ZoneInfo("Asia/Hong_Kong")
NEW_YORK = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 5, 10, 0, tzinfo=SHANGHAI)

McpCall = Callable[..., tuple[str, bool]]


def serve(upstream: FakeUpstream, name: str, file: str) -> None:
    """Answer the route of endpoint ``name`` with its replay fixture ``file``."""
    spec = REGISTRY[name]
    upstream.route(spec.host, spec.path, fixture_reply(f"{name}/{file}"))


def answer(upstream: FakeUpstream, name: str, body: Any, status: int = 200) -> None:
    spec = REGISTRY[name]
    upstream.route(spec.host, spec.path, json_reply(body, status))


def sent_to(upstream: FakeUpstream, name: str) -> list[Sent]:
    spec = REGISTRY[name]
    return upstream.requests(spec.host, spec.path)


def xueqiu(data: Any) -> dict[str, Any]:
    return {"data": data, "error_code": 0, "error_description": ""}


@pytest.fixture
def mcp_call(upstream: FakeUpstream, clock: FakeClock, session_path: Path) -> McpCall:
    """Run ``xq_call`` of a server on the fake upstream; return its text and the error flag."""

    def call(name: str, *, max_chars: int = McpOptions().max_chars, **arguments: Any) -> Any:
        async def run() -> tuple[str, bool]:
            runtime = AsyncRuntime(
                transport=upstream.transport, clock=clock.now, sleep=clock.async_sleep
            )
            async with AsyncClient(session_path=session_path, _runtime=runtime) as client:
                toolbox = Toolbox(McpOptions(max_chars=max_chars), client=client, now=lambda: NOW)
                return await toolbox.call("xq_call", {"endpoint": name, **arguments})

        return asyncio.run(run())

    return call


def table_rows(text: str) -> list[str]:
    """The lines of a rendered table without the comment lines; the first holds the columns."""
    return [line for line in text.splitlines() if not line.startswith("#")]


@pytest.mark.parametrize(
    ("name", "symbol"),
    [
        ("quote_order_book", "00700"),
        ("quote_order_book", "AAPL"),
        ("quote_trades", "00700"),
        ("quote_heat", "00700"),
        ("quote_heat", "AAPL"),
        ("quote_relevant", "00700"),
        ("quote_relevant", "AAPL"),
        ("market_industry_stocks", "00700"),
        ("market_industry_stocks", "AAPL"),
    ],
)
def test_a_market_without_measured_data_is_rejected_before_a_request(
    h: Harness, name: str, symbol: str
) -> None:
    with pytest.raises(InvalidParams) as caught:
        h.call(name, symbol=symbol)

    assert caught.value.field == "symbol"
    assert h.upstream.sent == []


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("quote_order_book", {"symbol": "SH600000", "level": 5}),
        ("quote_minute", {"symbol": "SH600000", "days": 2}),
        ("quote_minute", {"symbol": "SH600000", "period": "1d"}),
        ("quote_trades", {"symbol": "SH600000", "count": 0}),
        ("quote_trades", {"symbol": "SH600000", "count": 101}),
        ("market_bj_indexes", {"symbol": "BJ899050"}),
        ("market_hot_stocks", {"market": "cn"}),
        ("market_hot_stocks", {"region": "eu"}),
        ("market_hot_stocks", {"window": "week"}),
        ("market_hot_stocks", {"count": 0}),
        ("market_hot_stocks", {"count": 51}),
        ("market_industry_stocks", {"symbol": "SH600000", "count": 51}),
        ("market_industry_stocks", {"symbol": "SH600000", "type": 2}),
        ("search_stock", {"query": "   "}),
        ("search_stock", {"query": "x" * 51}),
    ],
)
def test_bad_params_are_rejected_before_a_request(
    h: Harness, name: str, params: Mapping[str, Any]
) -> None:
    with pytest.raises(InvalidParams):
        h.call(name, **params)

    assert h.upstream.sent == []


@pytest.mark.sample("quote_order_book")
def test_quote_order_book_levels_recompute_the_upstream_imbalance(h: Harness) -> None:
    serve(h.upstream, "quote_order_book", "sh600000")

    book = h.call("quote_order_book", symbol="600000")

    [sent] = sent_to(h.upstream, "quote_order_book")
    assert sent.params == {"symbol": "SH600000"}
    assert isinstance(book, OrderBook)
    assert book.symbol == "SH600000"
    assert book.timestamp == datetime(2026, 11, 4, 15, 30, 59, 140000, tzinfo=SHANGHAI)
    assert book.timestamp is not None
    assert book.timestamp.utcoffset() == timedelta(hours=8)
    assert book.current == 10.09
    assert [level.price for level in book.bids] == [10.09, 10.08, 10.07, 10.06, 10.05]
    assert [level.price for level in book.asks] == [10.1, 10.11, 10.12, 10.13, 10.14]
    assert book.bids[0] == BookLevel(10.09, 32500, None)
    assert book.asks[3].volume == 29230, "shares: the volume is not a multiple of a board lot"
    bid = sum(level.volume or 0 for level in book.bids)
    ask = sum(level.volume or 0 for level in book.asks)
    assert book.diff == bid - ask
    assert book.ratio == pytest.approx((bid - ask) / (bid + ask) * 100, abs=0.005)
    assert book.buypct == pytest.approx(bid / (bid + ask) * 100, abs=0.005)
    assert book.sellpct == pytest.approx(ask / (bid + ask) * 100, abs=0.005)
    assert REGISTRY["quote_order_book"].order is None


def test_quote_order_book_keeps_no_level_key_outside_the_levels(h: Harness) -> None:
    serve(h.upstream, "quote_order_book", "sh600000")

    book = h.call("quote_order_book", symbol="SH600000")

    extra = book.model_extra
    assert extra["level"] == 1
    assert {"by1", "sy1", "bid1_order_list", "ask1_order_list"} <= extra.keys()
    assert not [key for key in extra if re.fullmatch(r"[bs][pcn]\d+", key)]


def test_quote_order_book_reads_all_ten_levels_in_order(h: Harness) -> None:
    levels = {f"bp{n}": 11 - n for n in range(1, 11)} | {f"bc{n}": n * 100 for n in range(1, 11)}
    data = {"symbol": "SH600000", "timestamp": 1793777459140, "bn10": 7, **levels}
    answer(h.upstream, "quote_order_book", xueqiu(data))

    book = h.call("quote_order_book", symbol="SH600000")

    assert [level.price for level in book.bids] == [float(11 - n) for n in range(1, 11)]
    assert book.bids[9] == BookLevel(1.0, 1000, 7)


def test_quote_order_book_with_one_empty_side_keeps_the_other(h: Harness) -> None:
    data = {"symbol": "SH600000", "bp1": 11.0, "bc1": "500", "sp1": None, "sc1": None}
    answer(h.upstream, "quote_order_book", xueqiu(data))

    book = h.call("quote_order_book", symbol="SH600000")

    assert book.bids == (BookLevel(11.0, 500, None),)
    assert book.asks == ()
    assert book.timestamp is None


def test_quote_order_book_call_and_render(upstream: FakeUpstream, mcp_call: McpCall) -> None:
    serve(upstream, "quote_order_book", "sh600000")

    text, is_error = mcp_call("quote_order_book", params={"symbol": "SH600000"})

    assert not is_error, text
    assert "timestamp: 2026-11-04T15:30:59+08:00\n" in text
    assert "bids: [[10.09,32500,null]," in text
    assert "ratio: 71.43\n" in text


CN_DAY = ("sh600000_1d", "SH600000", 1, "1d", SHANGHAI, datetime(2026, 11, 4, 9, 30), 10.09)
HK_DAY = ("00700_1d", "00700", 1, "1d", HONG_KONG, datetime(2026, 11, 4, 9, 30), 397.2)
US_DAY = ("aapl_1d", "AAPL", 1, "1d", NEW_YORK, datetime(2026, 11, 4, 9, 30), 331.9)
CN_FIVE = ("sh600000_5d", "SH600000", 5, "5d", SHANGHAI, datetime(2026, 10, 29, 9, 30), 9.85)


@pytest.mark.sample("quote_minute")
@pytest.mark.parametrize(
    ("file", "symbol", "days", "period", "zone", "first", "last_close"),
    [CN_DAY, HK_DAY, US_DAY, CN_FIVE],
    ids=lambda value: value if isinstance(value, str) and "_" in value else "",
)
def test_quote_minute_times_and_values_follow_the_market(
    h: Harness,
    file: str,
    symbol: str,
    days: int,
    period: str,
    zone: ZoneInfo,
    first: datetime,
    last_close: float,
) -> None:
    serve(h.upstream, "quote_minute", file)

    chart = h.call("quote_minute", symbol=symbol, days=days)

    [sent] = sent_to(h.upstream, "quote_minute")
    assert sent.params == {"symbol": symbol, "period": period}
    assert isinstance(chart, MinuteChart)
    assert (chart.symbol, chart.days, chart.last_close) == (symbol, days, last_close)
    assert chart.timestamps[0] == first.replace(tzinfo=zone)
    assert chart.timestamps[0].tzinfo == zone
    step = timedelta(minutes=1 if days == 1 else 10)
    assert all(later - earlier == step for earlier, later in pairwise(chart.timestamps))
    assert all(earlier < later for earlier, later in pairwise(chart.timestamps))
    assert REGISTRY["quote_minute"].order == "oldest_first"
    current, chg, percent = (chart.columns[name] for name in ("current", "chg", "percent"))
    for price, change, points in zip(current, chg, percent, strict=True):
        assert price is not None and change is not None and points is not None
        assert change == pytest.approx(price - last_close, abs=0.0051)
        assert points == pytest.approx(change / last_close * 100, abs=0.0051)
    assert "timestamp" not in chart.columns
    assert chart.extra["after"] == []


def test_quote_minute_one_day_volumes_are_those_of_the_minute(h: Harness) -> None:
    serve(h.upstream, "quote_minute", "sh600000_1d")

    columns = h.call("quote_minute", symbol="SH600000").columns

    totals = columns["volume_total"]
    assert columns["volume"][1] == totals[1] - totals[0]
    assert columns["amount"][1] == columns["amount_total"][1] - columns["amount_total"][0]
    assert columns["avg_price"][2] == pytest.approx(
        columns["amount_total"][2] / totals[2], abs=0.0005
    )
    capital = [columns[f"capital.{size}"][0] for size in ("small", "medium", "large", "xlarge")]
    assert sum(capital) == pytest.approx(0)
    assert columns["volume_compare.volume_sum"] == totals


def test_quote_minute_five_day_volumes_are_not_the_differences_of_the_totals(h: Harness) -> None:
    serve(h.upstream, "quote_minute", "sh600000_5d")

    columns = h.call("quote_minute", symbol="SH600000", days=5).columns

    assert columns["volume"][1] != columns["volume_total"][1] - columns["volume_total"][0]
    assert "capital.small" not in columns


def test_quote_minute_average_is_the_upstream_own_outside_a_shares(h: Harness) -> None:
    serve(h.upstream, "quote_minute", "00700_1d")

    columns = h.call("quote_minute", symbol="00700").columns

    average = columns["amount_total"][0] / columns["volume_total"][0]
    assert abs(columns["avg_price"][0] - average) > 0.5
    assert "capital.small" not in columns


def test_quote_minute_fills_a_nested_value_that_only_some_points_have(h: Harness) -> None:
    points = [
        {"timestamp": 1793755800000, "current": 1, "capital": None},
        {"timestamp": 1793755860000, "current": "2.5", "capital": {"small": 1.5}, "macd": None},
    ]
    answer(h.upstream, "quote_minute", xueqiu({"last_close": 1, "after": [1], "items": points}))

    chart = h.call("quote_minute", symbol="SH600000")

    assert chart.columns == {"current": (1.0, 2.5), "capital.small": (None, 1.5)}
    assert chart.extra == {"after": [1]}


def test_quote_minute_without_points_is_an_empty_success(h: Harness) -> None:
    answer(h.upstream, "quote_minute", xueqiu({"last_close": None, "after": [], "items": []}))

    chart = h.call("quote_minute", symbol="SH600000")

    assert (len(chart), chart.columns, chart.header()) == (0, {}, {})


@pytest.mark.parametrize(
    "data",
    [
        {"last_close": 1},
        {"items": [{"current": 1}]},
        {"items": [{"timestamp": 9.9e15, "current": 1}]},
        {"items": [{"timestamp": 1793755800000, "current": "x"}]},
        {"items": [{"timestamp": 1793755800000, "current": [1]}]},
    ],
    ids=["no-items", "no-timestamp", "far-timestamp", "text-cell", "list-cell"],
)
def test_quote_minute_rejects_other_shapes(h: Harness, data: Mapping[str, Any]) -> None:
    answer(h.upstream, "quote_minute", xueqiu(data))

    with pytest.raises(UpstreamChanged):
        h.call("quote_minute", symbol="SH600000")


def test_quote_minute_days_may_be_text_in_an_mcp_call(
    upstream: FakeUpstream, mcp_call: McpCall
) -> None:
    serve(upstream, "quote_minute", "sh600000_5d")

    text, is_error = mcp_call("quote_minute", params={"symbol": "SH600000", "days": "5"})

    assert not is_error, text
    [sent] = sent_to(upstream, "quote_minute")
    assert sent.params["period"] == "5d"
    assert "# info: last_close=9.85\n" in text
    assert "timestamp,current,avg_price,percent,volume,amount\n" in text
    assert "2026-10-29T09:30:00+08:00,9.89,9.88,0.41,1407000,13901009\n" in text


def test_a_full_session_at_the_smallest_budget_shows_the_latest_points_with_a_way_back(
    upstream: FakeUpstream, mcp_call: McpCall
) -> None:
    point = {
        "current": 10.14,
        "volume": 2614930,
        "avg_price": 10.102,
        "chg": 0.05,
        "percent": 0.5,
        "amount": 26415830.0,
        "high": 10.15,
        "low": 10.05,
        "amount_total": 26415830.0,
        "volume_total": 2614930,
        "capital": {"small": -5.84, "medium": -1.13, "large": 5.32, "xlarge": 1.65},
        "volume_compare": {"volume_sum": 2614930, "volume_sum_last": 2118730},
    }
    start = 1793755800000
    points = [{**point, "timestamp": start + index * 60_000} for index in range(242)]
    answer(upstream, "quote_minute", xueqiu({"last_close": 10.09, "items": points}))

    text, is_error = mcp_call(
        "quote_minute", max_chars=4000, params={"symbol": "SH600000"}, limit=242
    )

    assert not is_error, text
    assert len(text) <= 4000
    [_, *rows] = table_rows(text)
    assert len(rows) >= 5
    assert rows[-1].startswith("2026-11-04T13:31:00+08:00,")
    assert f'"offset":{len(rows)}' in text.splitlines()[-1]


@pytest.mark.sample("quote_trades")
def test_quote_trades_a_shares_are_in_shanghai_time_oldest_first(h: Harness) -> None:
    serve(h.upstream, "quote_trades", "sh600000")

    trades = h.call("quote_trades", symbol="sh600000", count=3)

    [sent] = sent_to(h.upstream, "quote_trades")
    assert sent.params == {"symbol": "SH600000", "count": "3"}
    assert all(isinstance(trade, TradeTick) for trade in trades)
    assert trades[0].timestamp == datetime(2026, 11, 4, 15, 20, 47, 860000, tzinfo=SHANGHAI)
    assert trades[0].timestamp.tzinfo == SHANGHAI
    assert [trade.trade_volume for trade in trades] == [500, 2100, 3000]
    assert REGISTRY["quote_trades"].order == "oldest_first"
    times = [trade.timestamp for trade in trades]
    assert times == sorted(times)


@pytest.mark.sample("quote_trades")
def test_quote_trades_us_rows_are_in_new_york_time_and_can_share_a_time(h: Harness) -> None:
    serve(h.upstream, "quote_trades", "aapl")

    trades = h.call("quote_trades", symbol="AAPL", count=3)

    first, second, last = trades
    assert first.timestamp == datetime(2026, 11, 4, 10, 28, 18, 975000, tzinfo=NEW_YORK)
    assert first.timestamp == second.timestamp
    assert len({trade.trade_unique_id for trade in trades}) == 3
    assert first.model_extra["trade_type"] == "I_ODD_LOT_TRADE"
    assert (last.side, last.trade_volume, last.current, last.chg) == (0, 200, 353.0, 21.1)
    assert last.percent == pytest.approx(21.1 / (353.0 - 21.1) * 100, abs=0.0001)


def test_quote_trades_default_count_is_sent(h: Harness) -> None:
    answer(h.upstream, "quote_trades", xueqiu({"symbol": "SH600000", "items": []}))

    assert h.call("quote_trades", symbol="SH600000") == []
    [sent] = sent_to(h.upstream, "quote_trades")
    assert sent.params["count"] == "50"


def test_quote_trades_rejects_a_body_without_items(h: Harness) -> None:
    answer(h.upstream, "quote_trades", xueqiu({"symbol": "SH600000"}))

    with pytest.raises(UpstreamChanged):
        h.call("quote_trades", symbol="SH600000")


def test_quote_trades_limit_keeps_the_latest_trades(
    upstream: FakeUpstream, mcp_call: McpCall
) -> None:
    serve(upstream, "quote_trades", "sh600000")

    text, is_error = mcp_call("quote_trades", params={"symbol": "SH600000", "count": 3}, limit=1)

    assert not is_error, text
    [_, row] = table_rows(text)
    assert row == "2026-11-04T15:29:44+08:00,10.09,3000,0,0,0"
    assert '"offset":1' in text.splitlines()[-1]


@pytest.mark.sample("quote_heat")
def test_quote_heat_turns_text_ranks_and_dates_into_values(h: Harness) -> None:
    serve(h.upstream, "quote_heat", "sh600000")

    heat = h.call("quote_heat", symbol="600000")

    [sent] = sent_to(h.upstream, "quote_heat")
    assert sent.params == {"symbol": "SH600000"}
    assert isinstance(heat, StockHeat)
    assert heat.symbol == "SH600000"
    assert (heat.rank, heat.rank_change, heat.total) == (1111, 47, 32035)
    assert (heat.visitors, heat.discussions) == (84, 58)
    assert heat.rank_history[0] == RankPoint.model_validate({"time": "20261006", "rank": "2810"})
    assert heat.rank_history[0].day == date(2026, 10, 6)
    assert heat.rank_history[0].rank == 2810
    days = [point.day for point in heat.rank_history]
    assert days == sorted(days)
    assert heat.rank_tag[0].code == "industryHot"
    assert heat.model_extra == {"rank_string": "1111"}
    assert REGISTRY["quote_heat"].order is None


def test_quote_heat_leaves_the_award_out(h: Harness, mcp_call: McpCall) -> None:
    award = {"award_name": "合成奖项", "award_link": "https://example.invalid/a", "weight": 1}
    answer(h.upstream, "quote_heat", xueqiu({"rank": "7", "award": award}))

    heat = h.call("quote_heat", symbol="SH600000")
    text, is_error = mcp_call("quote_heat", params={"symbol": "SH600000"})

    assert heat.rank == 7
    assert "award" not in heat.model_dump_json()
    assert not is_error, text
    assert "award" not in text
    assert "example.invalid" not in text


@pytest.mark.parametrize("day", ["2026-10-06", "x", 20261006])
def test_quote_heat_rejects_another_form_of_date(h: Harness, day: object) -> None:
    answer(h.upstream, "quote_heat", xueqiu({"rank_history": [{"rank": "1", "time": day}]}))

    with pytest.raises(UpstreamChanged):
        h.call("quote_heat", symbol="SH600000")


@pytest.mark.sample("quote_relevant")
def test_quote_relevant_reads_the_stocks_only(h: Harness) -> None:
    serve(h.upstream, "quote_relevant", "sh600000")

    rows = h.call("quote_relevant", symbol="600000")

    [sent] = sent_to(h.upstream, "quote_relevant")
    assert sent.params == {"symbol": "SH600000"}
    assert all(isinstance(row, RelatedStock) for row in rows)
    first = rows[0]
    assert (first.symbol, first.name, first.current, first.percent) == (
        "SH601169",
        "北京银行",
        5.85,
        -0.17,
    )
    assert first.stock_type == 11
    assert first.chg is None
    assert first.model_extra == {"tick_size": 0.01, "list": []}
    assert REGISTRY["quote_relevant"].order is None
    assert "组合甲" not in json.dumps([row.model_dump() for row in rows], ensure_ascii=False)


STAYS_OUT = ("cubes", "url", "img_url", "content", "owner")
SOMEONE = {"id": 7, "screen_name": "someone"}
SECRET_TEXT = ("someone", "example.invalid", "合成正文", "组合甲")
WRITTEN = {
    "url": "https://example.invalid/page",
    "img_url": "https://example.invalid/image.png",
    "content": "合成正文",
    "owner": SOMEONE,
}


def test_portfolios_links_and_user_data_reach_no_result_or_mcp_text(
    h: Harness, mcp_call: McpCall
) -> None:
    cube = {"name": "组合甲", "symbol": "ZH0000001", "cube_id": 1, **WRITTEN}
    row = {"symbol": "SH601169", "name": "北京银行", "current": 5.85, "type": 11, **WRITTEN}
    data = {"cubes": [cube], "items": [row], **WRITTEN}
    answer(h.upstream, "quote_relevant", xueqiu(data))

    [stock] = h.call("quote_relevant", symbol="SH600000")
    text, is_error = mcp_call("quote_relevant", params={"symbol": "SH600000"})

    dump = stock.model_dump()
    assert dump["symbol"] == "SH601169"
    assert not set(STAYS_OUT) & dump.keys()
    assert not is_error, text
    assert "SH601169,北京银行,5.85," in text
    for word in (*STAYS_OUT, *SECRET_TEXT):
        assert word not in json.dumps(dump, ensure_ascii=False)
        assert word not in text
    for field in STAYS_OUT:
        refusal, is_error = mcp_call(
            "quote_relevant", params={"symbol": "SH600000"}, fields=[field]
        )
        assert is_error, refusal
        assert not set(STAYS_OUT) & set(json.loads(refusal)["valid_values"])


def test_quote_relevant_without_stocks_is_empty_with_a_note(
    upstream: FakeUpstream, mcp_call: McpCall
) -> None:
    answer(upstream, "quote_relevant", xueqiu({"cubes": [], "items": []}))

    text, is_error = mcp_call("quote_relevant", params={"symbol": "SH600000"})

    assert not is_error, text
    assert "# note: no rows: the upstream relates no other stock to this one.\n" in text


def test_quote_relevant_rejects_a_body_without_items(h: Harness) -> None:
    answer(h.upstream, "quote_relevant", xueqiu({"cubes": []}))

    with pytest.raises(UpstreamChanged):
        h.call("quote_relevant", symbol="SH600000")


@pytest.mark.sample("market_bj_indexes")
def test_market_bj_indexes_are_quoted_in_index_points(h: Harness) -> None:
    serve(h.upstream, "market_bj_indexes", "default")

    rows = h.call("market_bj_indexes")

    [sent] = sent_to(h.upstream, "market_bj_indexes")
    assert sent.params == {}
    assert all(isinstance(row, BoardIndexQuote) for row in rows)
    assert [row.symbol for row in rows] == ["BJ899050", "SH000016", "SZ399330"]
    first = rows[0]
    assert (first.name, first.current, first.chg, first.percent) == ("北证50", 894.08, 39.02, 4.56)
    assert first.percent == pytest.approx(first.chg / (first.current - first.chg) * 100, abs=0.005)
    assert REGISTRY["market_bj_indexes"].order is None


def test_market_bj_indexes_call_and_render(upstream: FakeUpstream, mcp_call: McpCall) -> None:
    serve(upstream, "market_bj_indexes", "default")

    text, is_error = mcp_call("market_bj_indexes")

    assert not is_error, text
    assert text.splitlines()[0] == "# market_bj_indexes {}"
    assert "BJ899050,北证50,894.08,39.02,4.56\n" in text


HOT_LISTS = [
    ("global", "hour", "10", "10", "NVDA"),
    ("global", "day", "20", "10", "SZ300308"),
    ("us", "hour", "11", "11", "SKHY"),
    ("us", "day", "21", "11", "NVDA"),
    ("cn", "hour", "12", "12", "SZ300408"),
    ("cn", "day", "22", "12", "SZ300308"),
    ("hk", "hour", "13", "13", "00700"),
    ("hk", "day", "23", "13", "00700"),
]


@pytest.mark.sample("market_hot_stocks")
@pytest.mark.parametrize(("region", "window", "type_", "market", "first"), HOT_LISTS)
def test_market_hot_stocks_every_region_and_window_has_its_list(
    h: Harness, region: str, window: str, type_: str, market: str, first: str
) -> None:
    file = f"{region}_{window}"
    serve(h.upstream, "market_hot_stocks", file)

    rows = h.call("market_hot_stocks", region=region, window=window, count=3)

    [sent] = sent_to(h.upstream, "market_hot_stocks")
    assert sent.params == {"type": type_, "_type": market, "size": "3"}
    recorded = fixture(f"replay/market_hot_stocks/{file}.json")["params"]
    assert recorded == {"type": type_, "_type": market}
    assert all(isinstance(row, HotStock) for row in rows)
    assert rows[0].symbol == first
    assert REGISTRY["market_hot_stocks"].order == "rank"


def test_market_hot_stocks_defaults_to_the_a_share_hour_list(h: Harness) -> None:
    serve(h.upstream, "market_hot_stocks", "cn_hour")

    rows = h.call("market_hot_stocks")

    [sent] = sent_to(h.upstream, "market_hot_stocks")
    assert sent.params == {"type": "12", "_type": "12", "size": "10"}
    assert [row.value for row in rows] == [7321.0, 5891.0, 4933.0]
    top = rows[0]
    assert (top.symbol, top.name, top.exchange, top.stock_type) == (
        "SZ300408",
        "三环集团",
        "SZ",
        11,
    )
    assert top.percent == pytest.approx(top.chg / (top.current - top.chg) * 100, abs=0.005)
    assert (top.increment, top.rank_change, top.ad) == (-485, 0, 0)
    assert (top.current_ext, top.percent_ext) == (None, None)


def test_market_hot_stocks_extended_hours_change_is_in_percent_points(h: Harness) -> None:
    serve(h.upstream, "market_hot_stocks", "us_hour")

    rows = h.call("market_hot_stocks", region="us")

    for row in rows:
        assert row.percent_ext == pytest.approx((row.current_ext / row.current - 1) * 100, abs=1e-4)


def test_market_hot_stocks_leaves_promotion_references_out(h: Harness) -> None:
    row = {
        "symbol": "SH600000",
        "code": "SH600000",
        "type": 12,
        "ad": 1,
        "ad_id": 9,
        "content_id": 9,
        "page": "x",
        "model": "x",
        "location": "x",
        "has_exist": True,
        "trade_session": 1,
    }
    answer(h.upstream, "market_hot_stocks", xueqiu({"items": [row], "items_size": 1}))

    [stock] = h.call("market_hot_stocks")

    assert (stock.symbol, stock.ad) == ("SH600000", 1)
    assert stock.model_extra == {"trade_session": 1}


@pytest.mark.sample("market_industry_stocks")
def test_market_industry_stocks_turns_text_into_numbers(h: Harness) -> None:
    serve(h.upstream, "market_industry_stocks", "sh600000")

    result = h.call("market_industry_stocks", symbol="600000", count=3)

    [sent] = sent_to(h.upstream, "market_industry_stocks")
    assert sent.host == "www.xueqiu.com"
    assert sent.params == {"code": "SH600000", "type": "1", "size": "3"}
    assert isinstance(result, IndustryStocks)
    assert len(result) == 3
    assert (result.symbol, result.industry_name, result.plate_name) == (
        "SH600000",
        "银行",
        "银行",
    )
    assert all(isinstance(row, IndustryPeer) for row in result.stocks)
    first, second, _ = result.stocks
    assert (first.symbol, first.name, first.exchange) == ("02888", "渣打集团", "HK")
    assert (first.current, first.chg, first.percent) == (219.4, 6.0, 2.81)
    assert first.percent == pytest.approx(6.0 / (219.4 - 6.0) * 100, abs=0.005)
    assert (first.volume, first.pe_ttm, first.market_capital) == (1043575.0, None, 4.75476923138e11)
    assert second.pe_ttm == 14.34
    assert first.model_extra == {}
    assert REGISTRY["market_industry_stocks"].order == "rank"
    percents = [row.percent or 0.0 for row in result.stocks]
    assert percents == sorted(percents, reverse=True)


def test_a_top_level_code_on_the_bare_envelope_is_not_an_error(h: Harness) -> None:
    body: dict[str, Any] = {
        "code": "SH600000",
        "exchange": "CN",
        "industryname": "银行",
        "industrystocks": [],
    }
    answer(h.upstream, "market_industry_stocks", body)

    result = h.call("market_industry_stocks", symbol="SH600000")

    assert len(result) == 0
    assert result.extra == {"code": "SH600000", "exchange": "CN"}
    assert result.header() == {"industry_name": "银行"}


def test_market_industry_stocks_reports_the_error_of_the_host(h: Harness) -> None:
    spec = REGISTRY["market_industry_stocks"]
    h.upstream.route(spec.host, spec.path, error_reply("10020", "synthetic", status=400))

    with pytest.raises(UpstreamError) as caught:
        h.call("market_industry_stocks", symbol="SH600000")

    assert caught.value.upstream_code == "10020"


def test_market_industry_stocks_rejects_a_body_without_stocks(h: Harness) -> None:
    answer(h.upstream, "market_industry_stocks", {"code": "SH600000", "industryname": "银行"})

    with pytest.raises(UpstreamChanged):
        h.call("market_industry_stocks", symbol="SH600000")


def test_market_industry_stocks_call_and_render(upstream: FakeUpstream, mcp_call: McpCall) -> None:
    serve(upstream, "market_industry_stocks", "sh600000")

    text, is_error = mcp_call("market_industry_stocks", params={"symbol": "SH600000", "count": 3})

    assert not is_error, text
    assert "# info: industry_name=银行 plate_name=银行\n" in text
    assert "symbol,name,exchange,current,percent,pe_ttm,market_capital\n" in text
    assert "02888,渣打集团,HK,219.4,2.81,,475476923138\n" in text


@pytest.mark.sample("search_stock")
@pytest.mark.parametrize(
    ("file", "query", "current", "percent"),
    [("name", "浦发银行", 9.89, -1.98), ("sh600000", "SH600000", 10.09, 0.0)],
    ids=["name", "code"],
)
def test_search_stock_finds_a_security_by_name_or_code(
    h: Harness, file: str, query: str, current: float, percent: float
) -> None:
    serve(h.upstream, "search_stock", file)

    rows = h.call("search_stock", query=query)

    [sent] = sent_to(h.upstream, "search_stock")
    assert sent.host == "www.xueqiu.com"
    assert sent.params == {"code": query}
    [hit] = rows
    assert isinstance(hit, StockHit)
    assert (hit.symbol, hit.name, hit.exchange, hit.stock_type) == (
        "SH600000",
        "浦发银行",
        "SH",
        11,
    )
    assert (hit.current, hit.percent, hit.chg) == (current, percent, None)
    assert (hit.name_en, hit.ind_name) == (None, "银行")
    extra = hit.model_extra
    assert extra is not None
    assert extra["stock_id"] == 1000000
    assert not {"hasexist", "highlight_name", "ai_result", "promotion_reason"} & extra.keys()
    assert REGISTRY["search_stock"].order is None


def test_search_stock_sends_special_characters_as_they_are(h: Harness) -> None:
    answer(h.upstream, "search_stock", {"q": "a", "page": 1, "size": 10, "stocks": []})

    assert h.call("search_stock", query="  A&B#C  ") == []
    [sent] = sent_to(h.upstream, "search_stock")
    assert sent.params == {"code": "A&B#C"}


def test_search_stock_rejects_a_body_without_stocks(h: Harness) -> None:
    answer(h.upstream, "search_stock", {"q": "a", "page": 1, "size": 10})

    with pytest.raises(UpstreamChanged):
        h.call("search_stock", query="a")


def test_search_stock_on_a_cookie_rejection_is_upstream_changed(h: Harness) -> None:
    answer(h.upstream, "search_stock", {"code": 400016, "success": False})

    with pytest.raises(UpstreamChanged):
        h.call("search_stock", query="a")

    assert len(sent_to(h.upstream, "search_stock")) == 2


def test_search_stock_without_a_match_gets_a_note(
    upstream: FakeUpstream, mcp_call: McpCall
) -> None:
    answer(upstream, "search_stock", {"q": "a", "page": 1, "size": 10, "stocks": []})

    text, is_error = mcp_call("search_stock", params={"query": "a"})

    assert not is_error, text
    assert "# note: no rows: nothing matches this code or name;" in text
