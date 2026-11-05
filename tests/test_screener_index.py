"""The screener and index valuation endpoints: parsing, paging, validation and error paths."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import pytest

from xueqiu_api import (
    REGISTRY,
    InvalidParams,
    SymbolNotFound,
    UpstreamChanged,
    UpstreamError,
)
from xueqiu_api._index import IndexValuation, ValuationHistory
from xueqiu_api._render import Series, compact_json, render
from xueqiu_api._replay import ReplayTransport
from xueqiu_api.endpoints.index_stock_bond_percentile import StockBondPercentile
from xueqiu_api.endpoints.screener_events import ENDPOINT as EVENTS
from xueqiu_api.endpoints.screener_events import StockEvent
from xueqiu_api.endpoints.screener_industries import ScreenerIndustry
from xueqiu_api.endpoints.screener_popularity_rank import ENDPOINT as POPULARITY
from xueqiu_api.endpoints.screener_popularity_rank import PopularStock
from xueqiu_api.endpoints.screener_quote_list import ENDPOINT as QUOTE_LIST
from xueqiu_api.endpoints.screener_quote_list import ScreenerQuote

from .harness import (
    REPLAY_DIR,
    Harness,
    Reply,
    danjuan_error_reply,
    fixture_reply,
    json_reply,
    replay_body,
)

STOCK = "stock.xueqiu.com"
DANJUAN = "danjuanfunds.com"
QUOTE_LIST_PATH = "/v5/stock/screener/quote/list.json"
EVENTS_PATH = "/v5/stock/screener/event/list.json"
POPULARITY_PATH = "/v5/stock/screener/screen.json"
INDUSTRIES_PATH = "/v5/stock/screener/industries.json"
VALUATION_LIST_PATH = "/djapi/index_eva/dj"
VALUATION_PATH = "/djapi/index_eva/detail/SH000300"
BOND_PATH = "/djapi/fundx/base/index/stock/bond/line"
SHANGHAI = ZoneInfo("Asia/Shanghai")
HISTORIES = [
    pytest.param(
        "index_pe_history",
        "pe",
        "index_eva_pe_growths",
        13.1303,
        11.9876,
        marks=pytest.mark.sample("index_pe_history"),
    ),
    pytest.param(
        "index_pb_history",
        "pb",
        "index_eva_pb_growths",
        1.4342,
        1.3711,
        marks=pytest.mark.sample("index_pb_history"),
    ),
]
HISTORY_NAMES = ["index_pe_history", "index_pb_history"]


def rendered(h: Harness, name: str, *, limit: int | None = None, **params: Any) -> str:
    spec = REGISTRY[name]
    return render(
        spec,
        spec.validate(params),
        h.call(name, **params),
        fields=None,
        limit=limit or spec.agent.row_default,
        max_chars=4000,
        fetched_at=datetime(2026, 11, 5, 2, 0, tzinfo=SHANGHAI),
        next_call=lambda following, offset: compact_json(following),
    )


def counted_rows(
    count: int, row: Callable[[int], dict[str, Any]], key: str = "list", real_size: int = 0
) -> Reply:
    """A list of ``count`` rows numbered from 0, paged by the request's page and size."""

    def reply(request: httpx2.Request) -> httpx2.Response:
        page = int(request.url.params["page"])
        size = real_size or int(request.url.params["size"])
        rows = [row(n) for n in range((page - 1) * size, min(page * size, count))]
        body = {"data": {"count": count, key: rows}, "error_code": 0, "error_description": ""}
        return httpx2.Response(200, json=body)

    return reply


def stock_row(n: int) -> dict[str, Any]:
    return {"symbol": f"SH{600000 + n}", "name": "示例", "type": 11, "has_follow": False}


def sent_pages(h: Harness, path: str) -> list[str]:
    return [sent.params["page"] for sent in h.upstream.requests(STOCK, path)]


# screener_quote_list


@pytest.mark.sample("screener_quote_list")
def test_quote_list_parses_units_dates_and_drops_the_session_flag(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTE_LIST_PATH, fixture_reply("screener_quote_list/cn_kcb_percent"))

    page = h.call(
        "screener_quote_list",
        board="kcb",
        order_by="percent",
        order="desc",
        page_size=3,
    )

    [sent] = h.upstream.requests(STOCK, QUOTE_LIST_PATH)
    assert sent.params == {
        "market": "CN",
        "type": "kcb",
        "order_by": "percent",
        "order": "desc",
        "page": "1",
        "size": "3",
    }
    assert (page.page, page.page_size, page.total, page.capped) == (1, 3, 618, False)
    assert page.next_params == {
        "market": "CN",
        "board": "kcb",
        "order_by": "percent",
        "order": "desc",
        "page": 2,
        "page_size": 3,
    }
    assert all(isinstance(row, ScreenerQuote) for row in page)
    first, second, third = page
    assert (first.symbol, first.name, first.stock_type) == ("SH688517", "金冠电气", 82)
    assert (first.percent, first.chg, first.current) == (20.02, 3.58, 21.46)
    assert (first.turnover_rate, first.dividend_yield) == (12.58, 2.246)
    assert first.volume == 17184879
    assert first.market_capital == 2931718929.0
    assert first.issue_date == date(2021, 6, 18)
    assert first.north_net_inflow_date == date(2026, 11, 3)
    assert second.issue_date == date(2019, 7, 19)
    assert third.issue_date is None
    assert first.model_extra is not None
    assert first.model_extra["lot_size"] == 1
    assert "has_follow" not in first.model_dump()
    assert "type" not in first.model_dump()
    assert QUOTE_LIST.order == "rank"


@pytest.mark.sample("screener_quote_list")
def test_quote_list_reads_us_rows_as_data_and_in_shanghai(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTE_LIST_PATH, fixture_reply("screener_quote_list/us_percent"))

    page = h.call(
        "screener_quote_list",
        market="US",
        order_by="percent",
        order="desc",
        page_size=3,
    )

    [sent] = h.upstream.requests(STOCK, QUOTE_LIST_PATH)
    assert (sent.params["market"], sent.params["type"]) == ("US", "us")
    omh, preferred, cris = page
    assert omh.issue_date == date(2023, 3, 21)
    assert cris.issue_date == date(2000, 8, 1)
    assert (preferred.symbol, preferred.stock_type) == ("DX-A.CL", 0)
    assert preferred.percent is None
    assert (omh.float_shares, omh.float_market_capital) == (None, None)
    # The upstream counts at most 5000 rows, so this list has more than it says.
    assert (page.total, page.capped) == (5000, True)


@pytest.mark.sample("screener_quote_list")
def test_quote_list_sends_hk_as_a_type_too(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTE_LIST_PATH, fixture_reply("screener_quote_list/hk_percent"))

    page = h.call("screener_quote_list", market="HK", order_by="percent", page_size=2)

    [sent] = h.upstream.requests(STOCK, QUOTE_LIST_PATH)
    assert (sent.params["market"], sent.params["type"]) == ("HK", "hk")
    assert [row.stock_type for row in page] == [30, 30]
    assert page.total == 2874


def test_quote_list_without_a_board_sends_no_type_and_defaults_to_code_order(h: Harness) -> None:
    h.upstream.route(
        STOCK, QUOTE_LIST_PATH, json_reply(replay_body("screener_quote_list/cn_unknown_industry"))
    )

    h.call("screener_quote_list")

    [sent] = h.upstream.requests(STOCK, QUOTE_LIST_PATH)
    assert sent.params == {
        "market": "CN",
        "order_by": "symbol",
        "order": "asc",
        "page": "1",
        "size": "50",
    }


@pytest.mark.sample("screener_quote_list")
def test_quote_list_filters_by_an_industry_code_of_screener_industries(h: Harness) -> None:
    h.upstream.route(STOCK, INDUSTRIES_PATH, fixture_reply("screener_industries/cn"))
    h.upstream.route(STOCK, QUOTE_LIST_PATH, fixture_reply("screener_quote_list/cn_industry"))
    code = h.call("screener_industries")[0].ind_code

    page = h.call("screener_quote_list", ind_code=code, page_size=2)

    assert h.upstream.requests(STOCK, QUOTE_LIST_PATH)[0].params["ind_code"] == "S1101"
    assert len(page) == 2


def test_quote_list_normalizes_the_case_of_an_industry_code(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTE_LIST_PATH, fixture_reply("screener_quote_list/cn_industry"))

    h.call("screener_quote_list", ind_code=" s6308 ", page_size=2)

    assert h.upstream.requests(STOCK, QUOTE_LIST_PATH)[0].params["ind_code"] == "S6308"


def test_quote_list_unknown_industry_is_an_empty_page_with_a_hint(h: Harness) -> None:
    h.upstream.route(
        STOCK, QUOTE_LIST_PATH, fixture_reply("screener_quote_list/cn_unknown_industry")
    )

    page = h.call("screener_quote_list", ind_code="S9999")

    assert (len(page), page.total, page.next_params) == (0, 0, None)
    text = rendered(h, "screener_quote_list", ind_code="S9999")
    assert "this ind_code may not exist" in text


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"page_size": 101}, "page_size"),
        ({"page_size": 0}, "page_size"),
        ({"page": 0}, "page"),
        ({"page": 51, "page_size": 100}, "page"),
        ({"board": "sh"}, "board"),
        ({"board": "sha,sza"}, "board"),
        ({"market": "JP"}, "market"),
        ({"order_by": "amount"}, "order_by"),
        ({"order": "up"}, "order"),
        ({"ind_code": "S630"}, "ind_code"),
        ({"ind_code": "6308"}, "ind_code"),
        ({"market": "HK", "board": "sha"}, None),
        ({"market": "US", "ind_code": "S6308"}, None),
        ({"type": "sha"}, "type"),
    ],
)
def test_quote_list_rejects_bad_params_before_any_request(
    h: Harness, params: dict[str, Any], field: str | None
) -> None:
    with pytest.raises(InvalidParams) as caught:
        h.call("screener_quote_list", **params)

    assert caught.value.field == field
    assert h.upstream.sent == []


def test_quote_list_accepts_the_last_page_under_the_cap(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTE_LIST_PATH, counted_rows(5000, stock_row))

    page = h.call("screener_quote_list", page=50, page_size=100)

    assert (len(page), page.capped, page.next_params) == (100, True, None)


def test_quote_list_walk_by_board_ends_on_the_total_and_drops_repeats(h: Harness) -> None:
    def reply(request: httpx2.Request) -> httpx2.Response:
        page = int(request.url.params["page"])
        numbers = {1: [0, 1], 2: [1, 2], 3: [3]}[page]
        body = {"count": 5, "list": [stock_row(n) for n in numbers]}
        return httpx2.Response(200, json={"data": body, "error_code": 0})

    h.upstream.route(STOCK, QUOTE_LIST_PATH, reply)

    pages, error = h.walk(QUOTE_LIST, board="shb", page_size=2)

    assert error is None
    assert [[row.symbol for row in page] for page in pages] == [
        ["SH600000", "SH600001"],
        ["SH600002"],
        ["SH600003"],
    ]
    assert sent_pages(h, QUOTE_LIST_PATH) == ["1", "2", "3"]
    assert pages[0].next_params["board"] == "shb"
    assert pages[-1].next_params is None


def test_quote_list_walk_refuses_a_capped_list_after_one_request(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTE_LIST_PATH, counted_rows(5000, stock_row))

    pages, error = h.walk(QUOTE_LIST, market="US")

    assert pages == []
    assert isinstance(error, InvalidParams)
    assert "screener_popularity_rank" in (error.hint or "")
    assert "sha, kcb, sza, bj, shb, szb" in (error.hint or "")
    assert sent_pages(h, QUOTE_LIST_PATH) == ["1"]


def test_quote_list_walk_of_a_capped_list_stops_at_the_cap_when_limited(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTE_LIST_PATH, counted_rows(5000, stock_row))

    pages, error = h.walk(QUOTE_LIST, market="US", max_pages=2)

    assert error is None
    assert [len(page) for page in pages] == [100, 100]


def test_quote_list_page_size_fallback_is_upstream_changed(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTE_LIST_PATH, counted_rows(250, stock_row, real_size=30))

    with pytest.raises(UpstreamChanged):
        h.call("screener_quote_list", board="sha", page_size=100)


# screener_events


@pytest.mark.sample("screener_events")
def test_events_parse_shanghai_dates_latest_first(h: Harness) -> None:
    h.upstream.route(STOCK, EVENTS_PATH, fixture_reply("screener_events/sh600000"))

    page = h.call("screener_events", symbol="600000", page_size=3)

    [sent] = h.upstream.requests(STOCK, EVENTS_PATH)
    assert sent.params == {"symbol": "SH600000", "page": "1", "size": "3"}
    assert (page.total, page.next_params) == (
        467,
        {"symbol": "SH600000", "page": 2, "page_size": 3},
    )
    assert all(isinstance(row, StockEvent) for row in page)
    tagged, block_trade, report = page
    assert block_trade.event_date == date(2026, 11, 4)
    assert (block_trade.subtype, block_trade.title) == (16, "大宗交易")
    assert block_trade.priority == 17
    assert report.event_date == date(2026, 10, 31)
    assert tagged.model_extra is not None
    assert tagged.model_extra["tags"] == [{"description": "利好", "value": 1}]
    assert EVENTS.order == "newest_first"
    dates = [row.event_date for row in page]
    assert dates == sorted(dates, reverse=True)


def test_events_are_for_a_shares_only(h: Harness) -> None:
    for symbol in ("00700", "AAPL"):
        with pytest.raises(InvalidParams) as caught:
            h.call("screener_events", symbol=symbol)
        assert (caught.value.field, list(caught.value.valid_values)) == ("symbol", ["CN"])
    assert h.upstream.sent == []


def test_events_page_size_stops_at_the_measured_50(h: Harness) -> None:
    with pytest.raises(InvalidParams) as caught:
        h.call("screener_events", symbol="SH600000", page_size=51)

    assert caught.value.field == "page_size"
    assert h.upstream.sent == []


def test_events_default_and_walk_use_50_rows(h: Harness) -> None:
    h.upstream.route(STOCK, EVENTS_PATH, counted_rows(0, stock_row, key="items"))

    h.call("screener_events", symbol="SH600000")
    pages, error = h.walk(EVENTS, symbol="SH600000")

    assert error is None and [len(page) for page in pages] == [0]
    assert [sent.params["size"] for sent in h.upstream.requests(STOCK, EVENTS_PATH)] == ["50", "50"]


def test_events_walk_tells_rows_apart_by_their_message(h: Harness) -> None:
    day = 1743177600000

    def event(subtype: int, message: str, timestamp: int = day) -> dict[str, Any]:
        return {
            "symbol": "SH600000",
            "subtype": subtype,
            "message": message,
            "timestamp": timestamp,
        }

    pages_of_rows = {
        1: [event(30, "甲"), event(30, "甲")],
        2: [event(30, "甲"), event(30, "乙")],
        3: [event(29, "甲")],
    }

    def reply(request: httpx2.Request) -> httpx2.Response:
        rows = pages_of_rows[int(request.url.params["page"])]
        return httpx2.Response(200, json={"data": {"count": 5, "items": rows}, "error_code": 0})

    h.upstream.route(STOCK, EVENTS_PATH, reply)

    pages, error = h.walk(EVENTS, symbol="SH600000", page_size=2)

    assert error is None
    assert [[(row.subtype, row.message) for row in page] for page in pages] == [
        # Identical rows on one page both stay; only a repeat from an earlier page is dropped.
        [(30, "甲"), (30, "甲")],
        [(30, "乙")],
        [(29, "甲")],
    ]


def test_events_render_the_message_only_when_asked_and_fenced(h: Harness) -> None:
    h.upstream.route(STOCK, EVENTS_PATH, fixture_reply("screener_events/sh600000"))

    text = rendered(h, "screener_events", symbol="SH600000", page_size=3)

    assert "event_date,title,subtype" in text
    assert "2026-11-04,大宗交易,16" in text
    assert "成交均价" not in text
    assert text.endswith('# next: {"symbol":"SH600000","page":2,"page_size":3}')
    spec = REGISTRY["screener_events"]
    params = spec.validate({"symbol": "SH600000", "page_size": 3})
    asked = render(
        spec,
        params,
        h.call("screener_events", symbol="SH600000", page_size=3),
        fields=["event_date", "message"],
        limit=3,
        max_chars=4000,
        fetched_at=datetime(2026, 11, 5, 2, 0, tzinfo=SHANGHAI),
        next_call=None,
    )
    assert "# untrusted upstream text begin" in asked and "成交均价" in asked


# screener_popularity_rank


@pytest.mark.sample("screener_popularity_rank")
def test_popularity_rank_parses_the_rename_and_drops_the_session_flag(h: Harness) -> None:
    h.upstream.route(STOCK, POPULARITY_PATH, fixture_reply("screener_popularity_rank/cn"))

    page = h.call("screener_popularity_rank", page_size=3)

    [sent] = h.upstream.requests(STOCK, POPULARITY_PATH)
    assert sent.params == {
        "category": "CN",
        "order_by": "follow7d",
        "order": "desc",
        "only_count": "0",
        "page": "1",
        "size": "3",
    }
    assert (page.total, page.capped) == (5656, False)
    assert all(isinstance(row, PopularStock) for row in page)
    top = page.items[0]
    assert (top.symbol, top.name, top.stock_type) == ("SH601919", "中远海控", 11)
    assert (top.current, top.percent, top.follow7d, top.tick_size) == (17.65, -1.56, 7609.0, 0.01)
    assert "has_follow" not in top.model_dump()
    assert "pct" not in top.model_dump()
    assert POPULARITY.order == "rank"


@pytest.mark.sample("screener_popularity_rank")
@pytest.mark.parametrize(("market", "first"), [("HK", "00700"), ("US", "INTC")])
def test_popularity_rank_sends_the_market_as_category(h: Harness, market: str, first: str) -> None:
    h.upstream.route(
        STOCK,
        POPULARITY_PATH,
        fixture_reply(f"screener_popularity_rank/{'hk' if market == 'HK' else 'us_p1'}"),
    )

    page = h.call("screener_popularity_rank", market=market, page_size=1)

    assert h.upstream.requests(STOCK, POPULARITY_PATH)[0].params["category"] == market
    assert page.items[0].symbol == first


@pytest.mark.sample("screener_popularity_rank")
def test_popularity_rank_reaches_the_last_us_page_past_5000_rows(
    make: Callable[..., Harness],
) -> None:
    h = make(transport=ReplayTransport(REPLAY_DIR))

    page = h.call("screener_popularity_rank", market="US", page=92, page_size=100)

    assert (page.total, page.capped, page.next_params) == (9124, False, None)
    assert [row.follow7d for row in page] == [None, None, None]


def test_popularity_rank_walks_every_row_without_a_cap(h: Harness) -> None:
    h.upstream.route(STOCK, POPULARITY_PATH, counted_rows(205, stock_row))

    pages, error = h.walk(POPULARITY, market="US")

    assert error is None
    assert [len(page) for page in pages] == [100, 100, 5]
    assert len({row.symbol for page in pages for row in page}) == 205
    assert h.upstream.requests(STOCK, POPULARITY_PATH)[0].params["category"] == "US"


@pytest.mark.parametrize(
    ("params", "field"),
    [({"page_size": 101}, "page_size"), ({"market": "SH"}, "market"), ({"order": "asc"}, "order")],
)
def test_popularity_rank_rejects_bad_params_before_any_request(
    h: Harness, params: dict[str, Any], field: str
) -> None:
    with pytest.raises(InvalidParams) as caught:
        h.call("screener_popularity_rank", **params)

    assert caught.value.field == field
    assert h.upstream.sent == []


# screener_industries


@pytest.mark.sample("screener_industries")
def test_industries_rename_the_code(h: Harness) -> None:
    h.upstream.route(STOCK, INDUSTRIES_PATH, fixture_reply("screener_industries/cn"))

    rows = h.call("screener_industries")

    [sent] = h.upstream.requests(STOCK, INDUSTRIES_PATH)
    assert sent.params == {"category": "cn"}
    assert all(isinstance(row, ScreenerIndustry) for row in rows)
    assert [(row.ind_code, row.name, row.pinyin) for row in rows] == [
        ("S1101", "种植业", "zzy"),
        ("S1102", "渔业", "yy"),
        ("S1103", "林业", "ly"),
    ]
    assert REGISTRY["screener_industries"].order is None


def test_industries_take_no_params(h: Harness) -> None:
    with pytest.raises(InvalidParams):
        h.call("screener_industries", category="hk")

    assert h.upstream.sent == []


# index_valuation_list


@pytest.mark.sample("index_valuation_list")
def test_valuation_list_converts_fractions_and_blanks_the_placeholder(h: Harness) -> None:
    h.upstream.route(DANJUAN, VALUATION_LIST_PATH, fixture_reply("index_valuation_list/all"))

    rows = h.call("index_valuation_list")

    assert all(isinstance(row, IndexValuation) for row in rows)
    placeholder, csi300, hsi, msci_india = rows
    assert (placeholder.index_code, placeholder.pe, placeholder.pb) == ("SZ399393", None, 0.7468)
    assert (placeholder.pe_percentile, placeholder.pe_over_history) == (None, None)
    assert (placeholder.pb_percentile, placeholder.pb_over_history) == (9.28, 90.72)
    assert (placeholder.peg, placeholder.roe) == (None, 0.0)
    assert csi300.index_code == "SH000300"
    assert hsi.trade_date == date(2026, 11, 4)
    assert (msci_india.index_code, msci_india.pe, msci_india.pe_percentile) == (
        "935600",
        34.953,
        75.12,
    )
    assert msci_india.dividend_yield == 0.96
    assert msci_india.bond_yield == 5.0
    assert REGISTRY["index_valuation_list"].order is None


def test_valuation_list_fails_when_one_request_no_longer_holds_every_index(h: Harness) -> None:
    body = replay_body("index_valuation_list/all")
    body["data"]["total_items"] = 63
    h.upstream.route(DANJUAN, VALUATION_LIST_PATH, json_reply(body))

    with pytest.raises(UpstreamChanged) as caught:
        h.call("index_valuation_list")

    assert "4 of 63" in caught.value.message


def test_valuation_list_error_code_takes_the_error_path(h: Harness) -> None:
    h.upstream.route(DANJUAN, VALUATION_LIST_PATH, danjuan_error_reply(999001, "参数错误"))

    with pytest.raises(UpstreamError) as caught:
        h.call("index_valuation_list")

    assert caught.value.upstream_code == "999001"


# index_valuation


@pytest.mark.sample("index_valuation")
def test_valuation_parses_percent_points_and_shanghai_times(h: Harness) -> None:
    h.upstream.route(DANJUAN, VALUATION_PATH, fixture_reply("index_valuation/sh000300"))

    row = h.call("index_valuation", index_code="sh000300")

    assert isinstance(row, IndexValuation)
    [sent] = h.upstream.requests(DANJUAN, VALUATION_PATH)
    assert sent.params == {}
    assert "cookie" not in sent.headers
    assert (row.pe, row.pb, row.peg) == (13.6029, 1.3501, 0.9703)
    assert (row.pe_percentile, row.pb_percentile) == (69.36, 23.44)
    assert (row.pe_over_history, row.pb_over_history) == (30.64, 76.56)
    assert (row.roe, row.dividend_yield, row.bond_yield) == (9.93, 2.99, 5.0)
    assert row.trade_date == date(2026, 11, 4)
    assert row.updated_at == datetime(2026, 11, 4, 23, 32, 0, 353000, tzinfo=SHANGHAI)
    assert row.updated_at is not None and row.updated_at.tzinfo == SHANGHAI
    assert (row.eva_type, row.pb_flag) == ("mid", False)
    dumped = row.model_dump()
    assert not {"date", "created_at", "begin_at", "ts", "yeild", "bond_yeild"} & set(dumped)
    assert dumped["eva_type_int"] == 1
    assert REGISTRY["index_valuation"].order is None


def test_valuation_of_an_unknown_index_is_symbol_not_found(h: Harness) -> None:
    h.upstream.route(DANJUAN, "/djapi/index_eva/detail/SH999999", json_reply({"result_code": 0}))

    with pytest.raises(SymbolNotFound) as caught:
        h.call("index_valuation", index_code="SH999999")

    assert caught.value.endpoint == "index_valuation"
    assert "SH999999" in caught.value.message


def test_valuation_error_code_is_not_a_missing_index(h: Harness) -> None:
    h.upstream.route(DANJUAN, VALUATION_PATH, danjuan_error_reply(999001, "参数错误"))

    with pytest.raises(UpstreamError) as caught:
        h.call("index_valuation", index_code="SH000300")

    assert caught.value.upstream_code == "999001"


@pytest.mark.parametrize("code", ["SH000300", "SZ399006", "CSIH30269", "HKHSI", "NDX", "935600"])
def test_valuation_takes_every_shape_of_index_code(h: Harness, code: str) -> None:
    path = f"/djapi/index_eva/detail/{code}"
    h.upstream.route(DANJUAN, path, json_reply({"data": {"index_code": code}, "result_code": 0}))

    assert h.call("index_valuation", index_code=code).index_code == code


@pytest.mark.parametrize("code", ["000300", "600000", "S", "SH-000300", ""])
def test_valuation_rejects_other_codes_before_any_request(h: Harness, code: str) -> None:
    with pytest.raises(InvalidParams) as caught:
        h.call("index_valuation", index_code=code)

    assert caught.value.field == "index_code"
    assert h.upstream.sent == []


def test_valuation_renders_one_record(h: Harness) -> None:
    h.upstream.route(DANJUAN, VALUATION_PATH, fixture_reply("index_valuation/sh000300"))

    text = rendered(h, "index_valuation", index_code="SH000300")

    assert "pe_percentile: 69.36" in text
    assert "trade_date: 2026-11-04" in text
    assert text.endswith("# next: none")


# index_pe_history and index_pb_history


@pytest.mark.parametrize(("name", "metric", "key", "first", "line"), HISTORIES)
def test_history_is_columns_oldest_first_in_shanghai(
    h: Harness, name: str, metric: str, key: str, first: float, line: float
) -> None:
    path = f"/djapi/index_eva/{metric}_history/SH000300"
    h.upstream.route(DANJUAN, path, fixture_reply(f"{name}/sh000300"))

    history = h.call(name, index_code="SH000300")

    [sent] = h.upstream.requests(DANJUAN, path)
    assert sent.params == {"day": "all"}
    assert isinstance(history, ValuationHistory)
    assert isinstance(history, Series)
    assert history.index() == ("date", history.dates)
    assert len(history) == 6
    assert history.dates[0] == date(2016, 11, 4)
    assert history.dates[-1] == date(2026, 11, 4)
    assert list(history.dates) == sorted(history.dates)
    values = history.columns[metric]
    assert values[0] == first
    # The upstream's 0 is no value.
    assert values[4] is None
    assert list(history.lines) == ["30分位值", "中位值", "70分位值"]
    assert history.lines["30分位值"] == line
    assert REGISTRY[name].order == "oldest_first"


@pytest.mark.parametrize("name", HISTORY_NAMES)
def test_history_sorts_a_descending_body(h: Harness, name: str) -> None:
    metric = name.split("_")[1]
    path = f"/djapi/index_eva/{metric}_history/NDX"
    points = [{metric: 2.0, "ts": 1793721600000}, {metric: 1.0, "ts": 1478188800000}]
    data = {f"index_eva_{metric}_growths": points, "horizontal_lines": []}
    h.upstream.route(DANJUAN, path, json_reply({"data": data, "result_code": 0}))

    history = h.call(name, index_code="ndx", day="3y")

    assert h.upstream.requests(DANJUAN, path)[0].params == {"day": "3y"}
    assert history.dates == (date(2016, 11, 4), date(2026, 11, 4))
    assert history.columns[metric] == (1.0, 2.0)


@pytest.mark.parametrize("name", HISTORY_NAMES)
def test_history_keeps_the_rows_between_begin_and_end(h: Harness, name: str) -> None:
    metric = name.split("_")[1]
    path = f"/djapi/index_eva/{metric}_history/SH000300"
    h.upstream.route(DANJUAN, path, fixture_reply(f"{name}/sh000300"))

    history = h.call(name, index_code="SH000300", begin=date(2016, 11, 9), end=date(2016, 11, 14))

    assert history.dates == (date(2016, 11, 9), date(2016, 11, 14))
    assert len(history.lines) == 3


@pytest.mark.parametrize("name", HISTORY_NAMES)
@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"day": "1y"}, "day"),
        ({"day": "10y"}, "day"),
        ({"begin": date(2026, 1, 2), "end": date(2026, 1, 1)}, None),
    ],
)
def test_history_rejects_bad_params_before_any_request(
    h: Harness, name: str, params: dict[str, Any], field: str | None
) -> None:
    with pytest.raises(InvalidParams) as caught:
        h.call(name, index_code="SH000300", **params)

    assert caught.value.field == field
    assert h.upstream.sent == []


@pytest.mark.parametrize("name", HISTORY_NAMES)
def test_history_error_code_takes_the_error_path(h: Harness, name: str) -> None:
    path = f"/djapi/index_eva/{name.split('_')[1]}_history/SH000300"
    h.upstream.route(DANJUAN, path, danjuan_error_reply(999001, "参数错误"))

    with pytest.raises(UpstreamError) as caught:
        h.call(name, index_code="SH000300")

    assert caught.value.upstream_code == "999001"


@pytest.mark.parametrize("name", HISTORY_NAMES)
def test_history_without_data_stays_upstream_changed(h: Harness, name: str) -> None:
    path = f"/djapi/index_eva/{name.split('_')[1]}_history/SH999999"
    h.upstream.route(DANJUAN, path, json_reply({"result_code": 0}))

    with pytest.raises(UpstreamChanged):
        h.call(name, index_code="SH999999")


def test_history_renders_lines_stats_and_the_latest_rows(h: Harness) -> None:
    path = "/djapi/index_eva/pe_history/SH000300"
    h.upstream.route(DANJUAN, path, fixture_reply("index_pe_history/sh000300"))

    text = rendered(h, "index_pe_history", index_code="SH000300", limit=2)

    lines = text.splitlines()
    assert "# info: 30分位值=11.9876 中位值=12.7218 70分位值=13.7152" in lines
    assert any(line.startswith("# stats: points=6 first_date=2016-11-04") for line in lines)
    assert lines[lines.index("date,pe") + 1 :][:2] == ["2026-10-28,", "2026-11-04,13.6497"]
    assert lines[-1] == '# next: {"index_code":"SH000300","day":"all"}'


# index_stock_bond_percentile


@pytest.mark.sample("index_stock_bond_percentile")
def test_stock_bond_percentile_converts_fractions_and_sorts(h: Harness) -> None:
    h.upstream.route(DANJUAN, BOND_PATH, fixture_reply("index_stock_bond_percentile/time60"))

    series = h.call("index_stock_bond_percentile")

    [sent] = h.upstream.requests(DANJUAN, BOND_PATH)
    assert sent.params == {"time": "60"}
    assert isinstance(series, StockBondPercentile)
    assert isinstance(series, Series)
    assert series.dates[:2] == (date(2021, 11, 5), date(2021, 11, 8))
    assert list(series.dates) == sorted(series.dates)
    assert series.columns["percentile"][0] == 42.3423
    assert series.columns["benchmark"][0] == 5702.4807
    assert series.columns["benchmark"][-1] is None
    assert REGISTRY["index_stock_bond_percentile"].order == "oldest_first"


def test_stock_bond_percentile_sorts_a_descending_body_and_filters(h: Harness) -> None:
    points = [
        {"date": "2026-11-03", "quantile": 0.5, "performance_benchmark": 3.0},
        {"date": "2026-11-02", "quantile": 0.25, "performance_benchmark": 2.0},
        {"date": "2026-10-30", "quantile": 0.125, "performance_benchmark": 1.0},
    ]
    h.upstream.route(DANJUAN, BOND_PATH, json_reply({"data": points, "result_code": 0}))

    series = h.call("index_stock_bond_percentile", begin=date(2026, 11, 2))

    assert series.dates == (date(2026, 11, 2), date(2026, 11, 3))
    assert series.columns["percentile"] == (25.0, 50.0)


def test_stock_bond_percentile_rejects_a_range_that_ends_first(h: Harness) -> None:
    with pytest.raises(InvalidParams):
        h.call("index_stock_bond_percentile", begin=date(2026, 1, 2), end=date(2026, 1, 1))

    assert h.upstream.sent == []


def test_stock_bond_percentile_error_code_takes_the_error_path(h: Harness) -> None:
    h.upstream.route(DANJUAN, BOND_PATH, danjuan_error_reply(999001, "参数错误"))

    with pytest.raises(UpstreamError) as caught:
        h.call("index_stock_bond_percentile")

    assert caught.value.upstream_code == "999001"
