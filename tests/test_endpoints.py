"""Quotes, search, finance and F10 through Client and AsyncClient: parsing, routing, errors."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import pytest

from xueqiu_api import (
    REGISTRY,
    InvalidParams,
    RateLimited,
    SymbolNotFound,
    UpstreamChanged,
)
from xueqiu_api._render import compact_json, render
from xueqiu_api._replay import ReplayTransport
from xueqiu_api.models import (
    BlockTrade,
    Company,
    CubeNav,
    Financials,
    FundNav,
    QuoteDetail,
    StockSuggestion,
)

from .harness import (
    REPLAY_DIR,
    Harness,
    Reply,
    error_reply,
    fixture_reply,
    json_reply,
    replay_body,
)

STOCK = "stock.xueqiu.com"
WWW = "www.xueqiu.com"
BATCH = "/v5/stock/batch/quote.json"
DETAIL = "/v5/stock/quote.json"
SUGGEST = "/query/v1/suggest_stock.json"
KLINE = "/v5/stock/chart/kline.json"
BLOCK_TRADES = "/v5/stock/capital/blocktrans.json"
FUND_NAV = "/djapi/fund/nav/history/008975"
CUBE_NAV = "/cubes/nav_daily/all.json"
DANJUAN = "danjuanfunds.com"
TOKEN = "xq_a_token=fake-login-token-0001; u=fake-login-u-0001"
SHANGHAI = ZoneInfo("Asia/Shanghai")
HONG_KONG = ZoneInfo("Asia/Hong_Kong")
NEW_YORK = ZoneInfo("America/New_York")
REGIONS = [("SH600000", "cn"), ("00700", "hk"), ("AAPL", "us")]
STATEMENTS = [
    pytest.param("income", marks=pytest.mark.sample("finance_income")),
    pytest.param("balance", marks=pytest.mark.sample("finance_balance")),
    pytest.param("cash_flow", marks=pytest.mark.sample("finance_cash_flow")),
    pytest.param("indicator", marks=pytest.mark.sample("finance_indicator")),
]
RENAMED = {
    "balance": {"asset_liab_ratio": ("asset_liab_ratio", 100), "ta_tlia": ("tlia_ta", 100)},
    "indicator": {"capital_reserve": ("capital_reserve_ps", 1)},
}
"""Line items converted or renamed so that one name keeps one meaning across statements."""


def paths(h: Harness) -> list[str]:
    return [f"{sent.host}{sent.path}" for sent in h.upstream.sent]


def finance_path(region: str, statement: str) -> str:
    return f"/v5/stock/finance/{region}/{statement}.json"


def company_path(region: str) -> str:
    return f"/v5/stock/f10/{region}/company.json"


def gaps(times: Sequence[float]) -> list[float]:
    return [later - earlier for earlier, later in itertools.pairwise(sorted(times))]


@pytest.mark.sample("batch_quote")
def test_batch_quote_sends_one_request_and_parses_every_item(h: Harness) -> None:
    h.upstream.route(STOCK, BATCH, fixture_reply("batch_quote/cn_hk_us"))

    quotes = h.run("batch_quote", ["sh600000", "0700.HK", "aapl"])

    [sent] = h.upstream.requests(STOCK)
    assert sent.params == {"symbol": "SH600000,00700,AAPL", "extend": "detail"}
    assert "xq_a_token=fake-anon-token-0001" in sent.headers["cookie"]
    assert all(isinstance(quote, QuoteDetail) for quote in quotes)
    sh, hk, us = quotes
    assert (sh.symbol, sh.name, sh.current, sh.percent, sh.chg) == (
        "SH600000",
        "浦发银行",
        10.09,
        0,
        0,
    )
    assert (sh.open, sh.high, sh.low, sh.last_close) == (10.05, 10.23, 10.05, 10.09)
    assert (sh.volume, sh.amount, sh.turnover_rate) == (82408649, 835024728.0, 0.25)
    assert sh.market_capital == 336055908447.0
    assert sh.timestamp == datetime(2026, 11, 4, 15, 0, tzinfo=SHANGHAI)
    assert (hk.name, hk.market_status_id) == ("腾讯控股", 7)
    assert hk.timestamp == datetime(2026, 11, 4, 16, 8, 10, tzinfo=HONG_KONG)
    assert (us.name, us.market_status_id) == ("苹果", 5)
    assert us.timestamp == datetime(2026, 11, 4, 10, 27, 42, 573000, tzinfo=NEW_YORK)
    assert us.timestamp is not None and us.timestamp.tzinfo == NEW_YORK


def test_batch_quote_keeps_the_remaining_fields(h: Harness) -> None:
    h.upstream.route(STOCK, BATCH, fixture_reply("batch_quote/cn_hk_us"))

    sh = h.run("batch_quote", "SH600000,00700,AAPL")[0]

    assert sh.model_extra is not None
    assert (sh.model_extra["pe_ttm"], sh.model_extra["currency"]) == (6.491, "CNY")
    assert sh.model_extra["market"]["status"] == "已收盘"
    assert sh.model_extra["others"] == {"cyb_switch": True}
    assert "quote" not in sh.model_extra


def test_batch_quote_size_is_capped(h: Harness) -> None:
    with pytest.raises(InvalidParams):
        h.run("batch_quote", [f"SH60{n:04d}" for n in range(21)])
    assert h.upstream.sent == []


@pytest.mark.sample("quote_detail")
def test_quote_detail_parses_one_symbol(h: Harness) -> None:
    h.upstream.route(STOCK, DETAIL, fixture_reply("quote_detail/sh600000"))

    detail = h.run("quote_detail", "600000")

    assert h.upstream.requests(STOCK)[0].params == {"symbol": "SH600000", "extend": "detail"}
    assert isinstance(detail, QuoteDetail)
    assert (detail.symbol, detail.name, detail.current, detail.market_status_id) == (
        "SH600000",
        "浦发银行",
        10.09,
        7,
    )
    assert detail.timestamp == datetime(2026, 11, 4, 15, 0, tzinfo=SHANGHAI)
    assert detail.model_extra is not None
    assert (detail.model_extra["pb"], detail.model_extra["delayed"]) == (0.443, 0)
    assert detail.model_extra["tags"][0] == {"description": "沪股通", "value": 1}


def test_quote_detail_of_a_us_stock_uses_new_york_time(h: Harness) -> None:
    h.upstream.route(STOCK, DETAIL, fixture_reply("quote_detail/aapl"))

    detail = h.run("quote_detail", "AAPL")

    assert (detail.name, detail.market_status_id) == ("苹果", 5)
    assert detail.timestamp == datetime(2026, 11, 4, 10, 27, 40, 206000, tzinfo=NEW_YORK)
    assert detail.time == detail.timestamp
    assert detail.timestamp_ext == datetime(2026, 11, 4, 9, 30, 1, 77000, tzinfo=NEW_YORK)
    night = detail.timestamp_night_session
    assert night == datetime(2026, 11, 4, 3, 59, 12, 159000, tzinfo=NEW_YORK)
    assert night is not None and night.tzinfo == NEW_YORK
    # Shanghai midnight, which is still the day before in New York.
    assert detail.issue_date == date(1980, 12, 12)


def test_quote_detail_times_are_exchange_times(h: Harness) -> None:
    h.upstream.route(STOCK, DETAIL, fixture_reply("quote_detail/sh600000"))

    detail = h.run("quote_detail", "SH600000")

    assert detail.time == datetime(2026, 11, 4, 15, 0, tzinfo=SHANGHAI)
    assert detail.timestamp_ext == datetime(2026, 11, 4, 15, 30, tzinfo=SHANGHAI)
    assert detail.issue_date == date(1999, 11, 10)
    assert detail.model_extra is not None
    assert not {"time", "timestamp_ext", "issue_date"} & set(detail.model_extra)


def test_suggest_stock_searches_www_with_browser_headers_and_cookies(h: Harness) -> None:
    h.upstream.route(WWW, SUGGEST, fixture_reply("suggest_stock/pfyh"))

    results = h.run("suggest_stock", query=" pfyh ")

    [sent] = h.upstream.requests(WWW, SUGGEST)
    assert sent.params == {"q": "pfyh"}
    assert sent.headers["user-agent"].startswith("Mozilla/5.0")
    assert "xq_a_token=fake-anon-token-0001" in sent.headers["cookie"]
    [first] = results
    assert isinstance(first, StockSuggestion)
    assert (first.symbol, first.name, first.stock_type) == ("SH600000", "浦发银行", 11)
    assert first.model_extra == {"label": "11", "state": 1, "type": 1}


@pytest.mark.sample("suggest_stock")
def test_suggest_stock_returns_every_match(h: Harness) -> None:
    h.upstream.route(WWW, SUGGEST, fixture_reply("suggest_stock/aapl"))

    results = h.run("suggest_stock", query="AAPL")

    assert [(item.symbol, item.stock_type) for item in results] == [
        ("AAPL", 0),
        ("XVAP", 4),
        ("IOSX", 4),
    ]


def test_suggest_stock_encodes_the_query(h: Harness) -> None:
    h.upstream.route(WWW, SUGGEST, fixture_reply("suggest_stock/pfyh"))

    h.run("suggest_stock", query="浦发 & #1+")

    assert h.upstream.requests(WWW, SUGGEST)[0].params == {"q": "浦发 & #1+"}


@pytest.mark.parametrize("query", ["", "   ", "x" * 51])
def test_suggest_stock_query_is_validated(h: Harness, query: str) -> None:
    with pytest.raises(InvalidParams):
        h.run("suggest_stock", query=query)
    assert h.upstream.sent == []


def test_suggest_stock_keeps_the_3_second_interval_of_www(h: Harness) -> None:
    h.upstream.route(WWW, SUGGEST, fixture_reply("suggest_stock/pfyh"))

    h.run("suggest_stock", query="pfyh")
    h.run("suggest_stock", query="AAPL")

    searches = [sent.at for sent in h.upstream.requests(WWW, SUGGEST)]
    assert gaps(searches) == pytest.approx([3.0])


def test_suggest_stock_31005_is_rate_limited_without_retry(h: Harness) -> None:
    h.upstream.route(WWW, SUGGEST, error_reply("31005", "访问频率太快了，请休息下", 400))

    with pytest.raises(RateLimited) as caught:
        h.run("suggest_stock", query="pfyh")

    error = caught.value
    assert (error.upstream_code, error.status, error.retry_after_s) == ("31005", 400, 10.0)
    assert (error.endpoint, error.params) == ("suggest_stock", {"query": "pfyh"})
    assert paths(h) == ["xueqiu.com/", f"{WWW}/", f"{WWW}{SUGGEST}"]


LOGIN_REPLIES = [
    error_reply("400016", "请刷新页面", 400),
    # The form www sent on 2026-11-05 to a search without cookies.
    json_reply({"code": 400016, "message": "", "success": False}),
]


@pytest.mark.parametrize("reply", LOGIN_REPLIES, ids=["error_code", "query_envelope"])
def test_suggest_stock_login_codes_refresh_the_cookies_once(h: Harness, reply: Reply) -> None:
    h.upstream.route(WWW, SUGGEST, reply)

    with pytest.raises(UpstreamChanged) as caught:
        h.run("suggest_stock", query="pfyh")

    assert (caught.value.upstream_code, caught.value.endpoint) == ("400016", "suggest_stock")
    assert "XUEQIU_TOKEN" not in caught.value.hint
    home = ["xueqiu.com/", f"{WWW}/"]
    assert paths(h) == [*home, f"{WWW}{SUGGEST}", *home, f"{WWW}{SUGGEST}"]


@pytest.mark.parametrize("reply", LOGIN_REPLIES, ids=["error_code", "query_envelope"])
def test_suggest_stock_login_codes_are_upstream_changed_with_a_token_too(
    make: Callable[..., Harness], reply: Reply
) -> None:
    h = make(token=TOKEN)
    h.upstream.route(WWW, SUGGEST, reply)

    with pytest.raises(UpstreamChanged) as caught:
        h.run("suggest_stock", query="pfyh")

    assert (caught.value.upstream_code, caught.value.recoverable) == ("400016", "none")
    home = ["xueqiu.com/", f"{WWW}/"]
    assert paths(h) == [*home, f"{WWW}{SUGGEST}", *home, f"{WWW}{SUGGEST}"]
    assert "fake-login" not in h.upstream.requests(WWW, SUGGEST)[0].headers["cookie"]


@pytest.mark.parametrize("statement", STATEMENTS)
@pytest.mark.parametrize(("symbol", "region"), REGIONS)
def test_finance_goes_to_the_region_of_the_symbol(
    h: Harness, statement: str, symbol: str, region: str
) -> None:
    fixture = f"finance_{statement}/{symbol.lower()}"
    h.upstream.route(STOCK, finance_path(region, statement), fixture_reply(fixture))

    report = h.run(f"finance_{statement}", symbol, count=2)

    [sent] = h.upstream.requests(STOCK)
    assert sent.path == finance_path(region, statement)
    assert sent.params == {"symbol": symbol, "type": "all", "is_detail": "true", "count": "2"}
    assert isinstance(report, Financials)
    assert (report.symbol, len(report)) == (symbol, 2)
    renamed = RENAMED.get(statement, {})
    for period, raw in zip(report.periods, replay_body(fixture)["data"]["list"], strict=True):
        assert period.report_name == raw["report_name"]
        pairs: dict[str, list[Any]] = {k: v for k, v in raw.items() if isinstance(v, list)}
        assert set(period.items) == {renamed.get(key, (key, 1))[0] for key in pairs}
        for key, (value, yoy) in pairs.items():
            name, factor = renamed.get(key, (key, 1))
            assert period.items[name].value == (None if value is None else value * factor)
            assert period.items[name].yoy == (None if yoy is None else pytest.approx(yoy * 100))


@pytest.mark.parametrize(
    ("symbol", "region", "balance", "indicator"),
    [
        ("SH600000", "cn", "asset_liab_ratio", "asset_liab_ratio"),
        ("00700", "hk", "tlia_ta", "tlia_ta"),
        ("AAPL", "us", "asset_liab_ratio", "asset_liab_ratio"),
    ],
)
def test_the_debt_ratio_is_in_percent_points_in_both_statements(
    h: Harness, symbol: str, region: str, balance: str, indicator: str
) -> None:
    for statement in ("balance", "indicator"):
        fixture = fixture_reply(f"finance_{statement}/{symbol.lower()}")
        h.upstream.route(STOCK, finance_path(region, statement), fixture)

    sheet = h.run("finance_balance", symbol, count=2).periods[0]
    ratios = h.run("finance_indicator", symbol, count=2).periods[0]

    assert sheet.items[balance].value == pytest.approx(ratios.items[indicator].value, abs=1e-3)
    assert 1 < sheet.items[balance].value < 100


def test_finance_report_type_and_detail_map_to_upstream_values(h: Harness) -> None:
    h.upstream.route(
        STOCK, finance_path("cn", "indicator"), fixture_reply("finance_indicator/sh600000")
    )

    h.run("finance_indicator", "SH600000", report_type="annual", is_detail=False, count=20)
    h.run("finance_indicator", "SH600000")

    first, default = h.upstream.requests(STOCK)
    assert first.params == {"symbol": "SH600000", "type": "Q4", "is_detail": "false", "count": "20"}
    assert default.params == {
        "symbol": "SH600000",
        "type": "all",
        "is_detail": "true",
        "count": "5",
    }


@pytest.mark.parametrize(
    "params",
    [{"report_type": "Q4"}, {"report_type": "quarterly"}, {"count": 0}, {"count": 21}],
)
def test_finance_params_are_validated(h: Harness, params: dict[str, Any]) -> None:
    with pytest.raises(InvalidParams):
        h.run("finance_income", "SH600000", **params)
    with pytest.raises(InvalidParams):
        h.call("finance_income", symbol="SH600000", period="annual")
    with pytest.raises(InvalidParams):
        h.call("finance_income", symbol="SH600000", type="annual")
    assert h.upstream.sent == []


def test_finance_periods_have_dates_names_and_line_items(h: Harness) -> None:
    h.upstream.route(STOCK, finance_path("cn", "income"), fixture_reply("finance_income/sh600000"))

    report = h.run("finance_income", "SH600000", count=2)

    assert (report.name, report.currency) == ("浦发银行", "CNY")
    assert report.extra["last_report_name"] == "2026三季报"
    latest, previous = report.periods
    assert (latest.report_date, latest.report_name) == (date(2026, 9, 30), "2026三季报")
    assert (previous.report_date, previous.report_name) == (date(2026, 6, 30), "2026中报")
    net_profit = latest.items["net_profit"]
    assert net_profit.value == 41129000000.0
    assert net_profit.yoy == pytest.approx(4.998595900028082)
    value, yoy = latest.items["invest_incomes_from_rr"]
    assert (value, yoy) == (None, None)
    assert latest.extra == {"ctime": datetime(2026, 10, 30, 19, 0, 14, tzinfo=SHANGHAI)}


def test_us_report_dates_are_read_in_shanghai(h: Harness) -> None:
    h.upstream.route(STOCK, finance_path("us", "income"), fixture_reply("finance_income/aapl"))

    report = h.run("finance_income", "AAPL", count=2)

    # The upstream sends Beijing midnight; the same instant is still the day before in New York.
    assert [period.report_date for period in report.periods] == [
        date(2026, 9, 26),
        date(2026, 6, 27),
    ]
    assert all(period.report_date.isoformat() == period.extra["ed"] for period in report.periods)
    assert report.periods[0].report_name == "2026年FY"
    assert report.periods[0].items["preferred_dividend"] == (0.0, None)
    assert report.extra["sas"] == "美国公认会计准则(US GAAP)"


def test_hong_kong_finance_keeps_its_own_line_items(h: Harness) -> None:
    h.upstream.route(STOCK, finance_path("hk", "income"), fixture_reply("finance_income/00700"))

    report = h.run("finance_income", "00700", count=2)

    assert (report.name, report.currency) == ("腾讯控股", "CNY")
    latest = report.periods[0]
    assert (latest.report_date, latest.report_name) == (date(2026, 6, 30), "2026中报")
    assert latest.items["tto"].value == 401243000000.0
    assert (latest.extra["sd"], latest.extra["ed"], latest.extra["month_num"]) == (
        "2026-01-01",
        "2026-06-30",
        6,
    )


@pytest.mark.parametrize(("symbol", "region"), REGIONS)
def test_company_goes_to_the_region_of_the_symbol(h: Harness, symbol: str, region: str) -> None:
    h.upstream.route(STOCK, company_path(region), fixture_reply(f"f10_company/{symbol.lower()}"))

    company = h.run("f10_company", symbol)

    [sent] = h.upstream.requests(STOCK)
    assert (sent.path, sent.params) == (company_path(region), {"symbol": symbol})
    assert isinstance(company, Company)
    assert company.symbol == symbol
    assert company.main_operation_business == "合成主营业务"
    assert company.org_cn_introduction == "合成公司简介"


@pytest.mark.sample("f10_company")
def test_company_dates_are_exchange_dates(h: Harness) -> None:
    h.upstream.route(STOCK, company_path("cn"), fixture_reply("f10_company/sh600000"))
    h.upstream.route(STOCK, company_path("us"), fixture_reply("f10_company/aapl"))

    spdb = h.run("f10_company", "SH600000")
    apple = h.run("f10_company", "AAPL")

    assert (spdb.org_name_cn, spdb.org_short_name_cn) == (
        "上海浦东发展银行股份有限公司",
        "浦发银行",
    )
    assert (spdb.established_date, spdb.listed_date) == (date(1992, 10, 19), date(1999, 11, 10))
    assert (spdb.staff_num, spdb.chairman) == (62204, "示例董事长")
    assert spdb.model_extra is not None
    assert spdb.model_extra["affiliate_industry"] == {"ind_code": "BK0055", "ind_name": "银行"}
    assert (apple.org_name_en, apple.staff_num) == ("Apple Inc.", 167000)
    assert (apple.established_date, apple.listed_date) == (date(1977, 1, 3), date(1980, 12, 12))


def test_hong_kong_company_names_are_read_into_the_common_fields(h: Harness) -> None:
    h.upstream.route(STOCK, company_path("hk"), fixture_reply("f10_company/00700"))

    tencent = h.run("f10_company", "00700")

    assert (tencent.org_name_cn, tencent.org_name_en) == (
        "腾讯控股有限公司",
        "Tencent Holdings Limited",
    )
    assert (tencent.established_date, tencent.listed_date) == (
        date(1999, 11, 23),
        date(2004, 6, 16),
    )
    assert tencent.org_website == "http://www.example.com"
    assert tencent.org_short_name_cn is None
    assert tencent.model_extra is not None
    assert tencent.model_extra["mainholder"] == "MIH Internet Holdings B.V."
    assert not {"comcnname", "incdate", "mbu", "comintr"} & set(tencent.model_extra)


QUERY_ENVELOPE: dict[str, Any] = {"code": 200, "message": "success", "success": True}
INCOME_CN = finance_path("cn", "income")
Case = tuple[str, str, str, dict[str, Any]]
"""Method, host, path and response body."""

EMPTY: list[Case] = [
    ("batch_quote", STOCK, BATCH, {"data": {"items": [], "items_size": 0}, "error_code": 0}),
    ("suggest_stock", WWW, SUGGEST, {**QUERY_ENVELOPE, "data": []}),
    (
        "finance_income",
        STOCK,
        INCOME_CN,
        {"data": {"currency": "CNY", "list": []}, "error_code": 0},
    ),
]
MISMATCHES: list[Case] = [
    ("batch_quote", STOCK, BATCH, {"data": []}),
    ("batch_quote", STOCK, BATCH, {"data": {"items": [{"market": {"status_id": 7}}]}}),
    ("quote_detail", STOCK, DETAIL, {"data": {}}),
    ("quote_detail", STOCK, DETAIL, {"data": {"quote": {"current": 1}}}),
    ("suggest_stock", WWW, SUGGEST, {"data": [], "success": False}),
    ("suggest_stock", WWW, SUGGEST, {"success": True}),
    ("suggest_stock", WWW, SUGGEST, {"data": [{"code": "X"}], "success": True}),
    ("finance_income", STOCK, INCOME_CN, {"data": {"list": [{"report_name": "x"}]}}),
    ("finance_income", STOCK, INCOME_CN, {"data": {"currency": "CNY"}}),
    (
        "finance_income",
        STOCK,
        INCOME_CN,
        {"data": {"list": [{"report_date": 0, "report_name": "x", "op": [1, 2, 3]}]}},
    ),
    ("f10_company", STOCK, company_path("cn"), {"data": {"company": None}}),
    (
        "f10_company",
        STOCK,
        company_path("cn"),
        {"data": {"company": {"listed_date": 9_999_999_999_999_999}}},
    ),
]


def call_once(h: Harness, method: str) -> Any:
    if method == "suggest_stock":
        return h.run(method, query="pfyh")
    return h.run(method, "SH600000")


@pytest.mark.parametrize(("method", "host", "path", "body"), EMPTY, ids=[case[0] for case in EMPTY])
def test_empty_data_is_a_success(
    h: Harness, method: str, host: str, path: str, body: dict[str, Any]
) -> None:
    h.upstream.route(host, path, json_reply(body))

    assert len(call_once(h, method)) == 0


@pytest.mark.parametrize(("method", "host", "path", "body"), MISMATCHES)
def test_shape_mismatch_is_upstream_changed(
    h: Harness, method: str, host: str, path: str, body: dict[str, Any]
) -> None:
    h.upstream.route(host, path, json_reply(body))

    with pytest.raises(UpstreamChanged) as caught:
        call_once(h, method)

    assert caught.value.endpoint == method
    assert len(h.upstream.requests(host, path)) == 1


UNKNOWN_CODE: list[Case] = [
    (
        "quote_detail",
        STOCK,
        DETAIL,
        {
            "data": {"market": None, "quote": None, "others": None, "tags": None},
            "error_code": 0,
            "error_description": "",
        },
    ),
    ("kline", STOCK, KLINE, {"data": {}, "error_code": 0, "error_description": ""}),
    *(
        (
            f"finance_{statement}",
            STOCK,
            finance_path("cn", statement),
            {"data": {}, "error_code": 0},
        )
        for statement in ("income", "balance", "cash_flow", "indicator")
    ),
]


@pytest.mark.parametrize(
    ("method", "host", "path", "body"), UNKNOWN_CODE, ids=[case[0] for case in UNKNOWN_CODE]
)
def test_an_unknown_code_is_symbol_not_found(
    h: Harness, method: str, host: str, path: str, body: dict[str, Any]
) -> None:
    h.upstream.route(host, path, json_reply(body))

    with pytest.raises(SymbolNotFound) as caught:
        call_once(h, method)

    assert caught.value.endpoint == method
    assert caught.value.params is not None and caught.value.params["symbol"] == "SH600000"
    assert len(h.upstream.requests(host, path)) == 1


def block_trade_rows(start: int, count: int) -> list[dict[str, Any]]:
    """Rows that differ, one a day, the latest first, as the upstream sends them."""
    day = 86_400_000
    return [
        {"td_date": 1793721600000 - row * day, "vol": 100.0, "trans_price": 10.0, "trans_amt": 1e3}
        for row in range(start, start + count)
    ]


def block_trades_reply(request: httpx2.Request) -> httpx2.Response:
    """Two full pages of 2 rows and a short last page."""
    page, size = int(request.url.params["page"]), int(request.url.params["size"])
    rows = block_trade_rows((page - 1) * size, size)[: max(0, 5 - (page - 1) * size)]
    return httpx2.Response(200, json={"data": {"items": rows}, "error_code": 0})


def fund_nav_reply(request: httpx2.Request) -> httpx2.Response:
    """Two pages of 2 rows; the newest row of page 1 pushed its last row onto page 2."""
    page = int(request.url.params["page"])
    dates = [["2026-11-04", "2026-11-03"], ["2026-11-03", "2026-11-02"]][page - 1]
    rows = [{"date": day, "nav": "1.5", "percentage": "0.1", "value": "1.5"} for day in dates]
    data = {"items": rows, "current_page": page, "size": 2, "total_items": 4, "total_pages": 2}
    return httpx2.Response(200, json={"data": data, "result_code": 0})


def rendered(h: Harness, name: str, **params: Any) -> str:
    spec = REGISTRY[name]
    return render(
        spec,
        spec.validate(params),
        h.call(name, **params),
        fields=None,
        limit=spec.agent.row_default,
        max_chars=4000,
        fetched_at=datetime(2026, 11, 5, 2, 0, tzinfo=SHANGHAI),
        next_call=lambda following, offset: compact_json(following),
    )


@pytest.mark.sample("capital_block_trades")
def test_capital_block_trades_parses_dates_and_percent_points(h: Harness) -> None:
    h.upstream.route(STOCK, BLOCK_TRADES, fixture_reply("capital_block_trades/sh600000_p1"))

    page = h.run("capital_block_trades", "600000", page_size=20)

    [sent] = h.upstream.requests(STOCK, BLOCK_TRADES)
    assert sent.params == {"symbol": "SH600000", "page": "1", "size": "20"}
    assert (page.page, page.page_size, page.total, page.next_params) == (1, 20, None, None)
    assert all(isinstance(row, BlockTrade) for row in page)
    latest, older, _ = page
    assert latest.trade_date == date(2026, 11, 4)
    assert (older.trade_date, older.premium_pct, older.trans_price) == (
        date(2026, 10, 16),
        -0.21,
        9.71,
    )
    assert older.vol * older.trans_price == pytest.approx(older.trans_amt, rel=1e-4)
    assert older.buy_branch_org_name == "示例证券甲营业部"
    assert REGISTRY["capital_block_trades"].order == "newest_first"
    dates = [row.trade_date for row in page]
    assert dates == sorted(dates, reverse=True)


def test_capital_block_trades_pages_end_on_the_short_page(h: Harness) -> None:
    h.upstream.route(STOCK, BLOCK_TRADES, block_trades_reply)

    pages = h.pages("capital_block_trades_pages", "SH600000", page_size=2)

    assert [len(page) for page in pages] == [2, 2, 1]
    assert pages[0].next_params == {"symbol": "SH600000", "page": 2, "page_size": 2}
    assert pages[-1].next_params is None
    assert [sent.params["page"] for sent in h.upstream.requests(STOCK, BLOCK_TRADES)] == [
        "1",
        "2",
        "3",
    ]


def test_capital_block_trades_pages_ask_for_the_largest_page_by_default(h: Harness) -> None:
    h.upstream.route(STOCK, BLOCK_TRADES, json_reply({"data": {"items": []}, "error_code": 0}))

    assert [len(page) for page in h.pages("capital_block_trades_pages", "SH600000")] == [0]

    assert h.upstream.requests(STOCK, BLOCK_TRADES)[0].params["size"] == "100"


@pytest.mark.parametrize(
    ("method", "code"),
    [("capital_block_trades_pages", "SH600000"), ("fund_nav_history_pages", "008975")],
)
def test_page_methods_reject_bad_params_before_any_request(
    h: Harness, method: str, code: str
) -> None:
    with pytest.raises(InvalidParams) as caught:
        getattr(h.client, method)(code, page_size=0)

    assert caught.value.field == "page_size"
    assert h.upstream.sent == []


def test_capital_block_trades_is_for_a_shares_only(h: Harness) -> None:
    with pytest.raises(InvalidParams) as caught:
        h.run("capital_block_trades", "00700")

    assert (caught.value.field, list(caught.value.valid_values)) == ("symbol", ["CN"])
    assert h.upstream.sent == []


def test_capital_block_trades_call_and_render(h: Harness) -> None:
    h.upstream.route(STOCK, BLOCK_TRADES, fixture_reply("capital_block_trades/sh600000_p1"))

    text = rendered(h, "capital_block_trades", symbol="SH600000", page_size=3)

    assert "# page: page=1 page_size=3\n" in text
    assert "trade_date,trans_price,vol,trans_amt,premium_pct," in text
    assert (
        "2026-10-16,9.71,347899900,3378107700,-0.21,示例证券甲营业部,示例证券乙资产管理部" in text
    )
    assert text.endswith('# next: {"symbol":"SH600000","page":2,"page_size":3}')


@pytest.mark.sample("fund_nav_history")
def test_fund_nav_history_parses_numbers_and_dates(h: Harness) -> None:
    h.upstream.route(DANJUAN, FUND_NAV, fixture_reply("fund_nav_history/008975_p1"))

    page = h.run("fund_nav_history", "008975", page_size=3)

    [sent] = h.upstream.requests(DANJUAN)
    assert sent.path == FUND_NAV
    assert sent.params == {"page": "1", "size": "3"}
    assert "cookie" not in sent.headers
    assert (page.page, page.page_size, page.total, page.last_page) == (1, 3, 5, 2)
    assert page.next_params == {"fund_code": "008975", "page": 2, "page_size": 3}
    latest = page.items[0]
    assert isinstance(latest, FundNav)
    assert (latest.date, latest.nav, latest.nav_value, latest.daily_pct) == (
        date(2026, 11, 4),
        1.2048,
        1.2048,
        -1.27,
    )
    assert REGISTRY["fund_nav_history"].order == "newest_first"
    dates = [row.date for row in page]
    assert dates == sorted(dates, reverse=True)


def test_fund_nav_history_reads_nav_value_and_the_page_the_upstream_echoes(h: Harness) -> None:
    row = {"date": "2026-11-04", "nav": "1.5", "percentage": "0.1", "value": "1.7"}
    data = {"items": [row], "current_page": 3, "size": 1, "total_items": 3, "total_pages": 3}
    h.upstream.route(DANJUAN, FUND_NAV, json_reply({"data": data, "result_code": 0}))

    page = h.run("fund_nav_history", "008975", page_size=1)

    assert (page.items[0].nav, page.items[0].nav_value) == (1.5, 1.7)
    assert page.page == 3


def test_fund_nav_history_pages_drop_rows_repeated_across_pages(h: Harness) -> None:
    h.upstream.route(DANJUAN, FUND_NAV, fund_nav_reply)

    pages = h.pages("fund_nav_history_pages", "008975", page_size=2)

    assert [row.date.isoformat() for page in pages for row in page] == [
        "2026-11-04",
        "2026-11-03",
        "2026-11-02",
    ]
    assert pages[-1].next_params is None
    assert [sent.params["page"] for sent in h.upstream.requests(DANJUAN)] == ["1", "2"]


def test_fund_nav_history_pages_walk_the_replay_fixtures(make: Callable[..., Harness]) -> None:
    h = make(transport=ReplayTransport(REPLAY_DIR))

    pages = h.pages("fund_nav_history_pages", "008975")

    assert [len(page) for page in pages] == [3, 2]
    oldest = pages[-1].items[-1]
    assert (oldest.date, oldest.nav, oldest.daily_pct) == (date(2020, 3, 18), 1.0, None)


def test_fund_nav_history_page_size_cap_is_the_measured_one(h: Harness) -> None:
    with pytest.raises(InvalidParams) as caught:
        h.run("fund_nav_history", "008975", page_size=2001)

    assert caught.value.field == "page_size"
    assert h.upstream.sent == []


def test_fund_nav_history_call_and_render(make: Callable[..., Harness]) -> None:
    h = make(transport=ReplayTransport(REPLAY_DIR))

    text = rendered(h, "fund_nav_history", fund_code="008975")

    assert "# page: page=1 page_size=3 total=5 last_page=2\n" in text
    assert "date,nav,daily_pct\n2026-11-04,1.2048,-1.27\n" in text
    assert text.endswith('# next: {"fund_code":"008975","page":2,"page_size":50}')


@pytest.mark.sample("cube_nav_daily")
def test_cube_nav_daily_joins_the_portfolio_and_its_benchmark(h: Harness) -> None:
    h.upstream.route(WWW, CUBE_NAV, fixture_reply("cube_nav_daily/zh2567925"))

    nav = h.run("cube_nav_daily", "zh2567925")

    [sent] = h.upstream.requests(WWW, CUBE_NAV)
    assert sent.params == {"cube_symbol": "ZH2567925"}
    assert sent.headers["user-agent"].startswith("Mozilla")
    assert isinstance(nav, CubeNav)
    assert (nav.symbol, nav.benchmark, nav.benchmark_name) == ("ZH2567925", "SH000300", "沪深300")
    assert nav.name == "示例组合"
    assert len(nav) == 3
    assert nav.dates == (date(2021, 6, 11), date(2021, 6, 15), date(2021, 6, 16))
    assert nav.columns["nav"] == (0.9857, 1.0122, 0.9842)
    assert nav.columns["total_gain"] == (-1.43, 1.22, -1.58)
    assert nav.columns["benchmark_value"] == (5224.7, 5166.56, 5080.49)
    assert nav.columns["benchmark_total_gain"] == (-0.89, -1.99, -3.63)
    for value, gain in zip(nav.columns["nav"], nav.columns["total_gain"], strict=True):
        assert value is not None and gain == pytest.approx((value - 1) * 100, abs=0.01)
    assert REGISTRY["cube_nav_daily"].order == "oldest_first"


def test_cube_nav_daily_cuts_the_range_locally(h: Harness) -> None:
    h.upstream.route(WWW, CUBE_NAV, fixture_reply("cube_nav_daily/zh2567925"))

    nav = h.call(
        "cube_nav_daily", cube_symbol="ZH2567925", begin="2021-06-15", end=date(2021, 6, 15)
    )

    assert nav.dates == (date(2021, 6, 15),)
    assert nav.columns["nav"] == (1.0122,)
    assert h.upstream.requests(WWW, CUBE_NAV)[0].params == {"cube_symbol": "ZH2567925"}


def test_cube_nav_daily_without_a_benchmark_or_with_a_gap(h: Harness) -> None:
    cube = {
        "symbol": "ZH2567925",
        "list": [
            {"date": "2021-06-11", "value": 1.0, "percent": 0.0},
            {"date": "2021-06-15", "value": 1.1, "percent": 10.0},
        ],
    }
    benchmark = {
        "symbol": "SH000300",
        "list": [{"date": "2021-06-15", "value": 5000.0, "percent": 1.0}],
    }
    h.upstream.route(WWW, CUBE_NAV, json_reply([cube]), json_reply([cube, benchmark]))

    alone = h.run("cube_nav_daily", "ZH2567925")
    joined = h.run("cube_nav_daily", "ZH2567925")

    assert (alone.benchmark, alone.header()) == (None, {})
    assert alone.columns["benchmark_value"] == (None, None)
    assert joined.columns["benchmark_value"] == (None, 5000.0)


@pytest.mark.parametrize("body", [{"symbol": "ZH2567925"}, [], [{"symbol": "ZH2567925"}]])
def test_cube_nav_daily_of_another_shape_is_upstream_changed(h: Harness, body: Any) -> None:
    h.upstream.route(WWW, CUBE_NAV, json_reply(body))

    with pytest.raises(UpstreamChanged):
        h.run("cube_nav_daily", "ZH2567925")


def test_cube_nav_daily_rejects_other_codes_before_any_request(h: Harness) -> None:
    with pytest.raises(InvalidParams):
        h.run("cube_nav_daily", "SH600000")
    assert h.upstream.sent == []


def test_cube_nav_daily_call_and_render(h: Harness) -> None:
    h.upstream.route(WWW, CUBE_NAV, fixture_reply("cube_nav_daily/zh2567925"))

    text = rendered(h, "cube_nav_daily", cube_symbol="ZH2567925")

    assert "# info: benchmark=SH000300 benchmark_name=沪深300\n" in text
    assert "# stats: points=3 first_date=2021-06-11 last_date=2021-06-16 nav_last=0.9842" in text
    assert "date,nav,total_gain,benchmark_value,benchmark_total_gain\n" in text
    assert "2021-06-16,0.9842,-1.58,5080.49,-3.63\n" in text
    assert "示例组合" not in text
    assert text.endswith("# next: none")


def test_statements_drop_the_keys_naming_a_user(h: Harness) -> None:
    someone = {"id": 7, "screen_name": "someone"}
    period = {
        "report_date": 1790000000000,
        "report_name": "2026三季报",
        "total_revenue": [1.0, 0.1],
        "user": someone,
        "owner_id": 42,
        "sd": 1,
    }
    data = {"quote_name": "X", "currency": "CNY", "owner": someone, "org_type": 1, "list": [period]}
    h.upstream.route(
        STOCK, finance_path("cn", "income"), json_reply({"data": data, "error_code": 0})
    )

    report = h.run("finance_income", "SH600000", count=1)

    assert report.extra == {"org_type": 1}
    assert report.periods[0].extra == {"sd": 1}
    assert set(report.periods[0].items) == {"total_revenue"}


EARLY_BARS = [
    (544982400000, 2766.3999),  # 1987-04-10 00:00 in Hong Kong, a Friday
    (545238000000, 2708.8999),  # 1987-04-12 23:00, the bar of Monday the 13th
    (545324400000, 2668.3999),  # 1987-04-13 23:00, the bar of Tuesday the 14th
]


def early_bars_reply(request: httpx2.Request) -> httpx2.Response:
    """Going forward from ``begin``, inclusive, or back from it, like the upstream."""
    begin, count = int(request.url.params["begin"]), int(request.url.params["count"])
    if count < 0:
        chosen = [bar for bar in EARLY_BARS if bar[0] <= begin][count:]
    else:
        chosen = [bar for bar in EARLY_BARS if bar[0] >= begin][:count]
    data = {"symbol": "HKHSI", "column": ["timestamp", "close"], "item": chosen}
    return httpx2.Response(200, json={"data": data, "error_code": 0})


def test_hong_kong_bars_written_at_shanghai_midnight_keep_their_date(h: Harness) -> None:
    h.upstream.route(STOCK, KLINE, early_bars_reply)

    kline = h.kline("HKHSI", adjust="normal", begin=date(1987, 4, 1), end=date(1987, 4, 30))

    assert kline.timestamps == (
        datetime(1987, 4, 10, tzinfo=HONG_KONG),
        datetime(1987, 4, 13, tzinfo=HONG_KONG),
        datetime(1987, 4, 14, tzinfo=HONG_KONG),
    )
    assert kline.columns["close"] == (2766.3999, 2708.8999, 2668.3999)


@pytest.mark.parametrize(
    ("day", "closes"),
    [(date(1987, 4, 12), ()), (date(1987, 4, 13), (2708.8999,)), (date(1987, 4, 14), (2668.3999,))],
    ids=["sunday", "monday", "tuesday"],
)
def test_a_range_of_one_day_selects_the_bar_of_that_day(
    h: Harness, day: date, closes: tuple[float, ...]
) -> None:
    h.upstream.route(STOCK, KLINE, early_bars_reply)

    kline = h.kline("HKHSI", adjust="normal", begin=day, end=day)

    assert kline.columns.get("close", ()) == closes
