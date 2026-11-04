"""Quotes, search, finance and F10 through Client and AsyncClient: parsing, routing, errors."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from xueqiu_api import (
    Company,
    Financials,
    InvalidParams,
    QuoteDetail,
    RateLimited,
    StockSuggestion,
    UpstreamChanged,
)

from .harness import Harness, error_reply, fixture_reply, json_reply, replay_body

STOCK = "stock.xueqiu.com"
WWW = "www.xueqiu.com"
BATCH = "/v5/stock/batch/quote.json"
DETAIL = "/v5/stock/quote.json"
SUGGEST = "/query/v1/suggest_stock.json"
TOKEN = "xq_a_token=fake-login-token-0001; u=fake-login-u-0001"
SHANGHAI = ZoneInfo("Asia/Shanghai")
HONG_KONG = ZoneInfo("Asia/Hong_Kong")
NEW_YORK = ZoneInfo("America/New_York")
REGIONS = [("SH600000", "cn"), ("00700", "hk"), ("AAPL", "us")]
STATEMENTS = ["income", "balance", "cash_flow", "indicator"]
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


def test_suggest_stock_searches_www_with_browser_headers_and_no_cookies(h: Harness) -> None:
    h.upstream.route(WWW, SUGGEST, fixture_reply("suggest_stock/pfyh"))

    results = h.run("suggest_stock", query=" pfyh ")

    [sent] = h.upstream.sent
    assert (sent.host, sent.path, sent.params) == (WWW, SUGGEST, {"q": "pfyh"})
    assert sent.headers["user-agent"].startswith("Mozilla/5.0")
    assert "cookie" not in sent.headers
    [first] = results
    assert isinstance(first, StockSuggestion)
    assert (first.symbol, first.name, first.stock_type) == ("SH600000", "浦发银行", 11)
    assert first.model_extra == {"label": "11", "state": 1, "type": 1}


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
    assert paths(h) == [f"{WWW}{SUGGEST}"]


@pytest.mark.parametrize("token", [None, TOKEN])
def test_suggest_stock_login_codes_are_upstream_changed_without_a_refresh(
    make: Callable[..., Harness], token: str | None
) -> None:
    h = make(token=token)
    h.upstream.route(WWW, SUGGEST, error_reply("400016", "请刷新页面", 400))

    with pytest.raises(UpstreamChanged) as caught:
        h.run("suggest_stock", query="pfyh")

    assert (caught.value.upstream_code, caught.value.endpoint) == ("400016", "suggest_stock")
    # No cookies go with the search, so a token would not help.
    assert "without cookies" in caught.value.hint and "XUEQIU_TOKEN" not in caught.value.hint
    assert paths(h) == [f"{WWW}{SUGGEST}"]
    assert "cookie" not in h.upstream.sent[0].headers


def test_suggest_stock_works_while_the_home_page_fails(h: Harness) -> None:
    h.upstream.route(WWW, "/", json_reply({}, 503))
    h.upstream.route(WWW, SUGGEST, fixture_reply("suggest_stock/pfyh"))

    results = h.run("suggest_stock", query="pfyh")

    assert [item.symbol for item in results] == ["SH600000"]
    assert paths(h) == [f"{WWW}{SUGGEST}"]


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

    h.run("finance_indicator", "SH600000", type="annual", is_detail=False, count=20)
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
    "params", [{"type": "Q4"}, {"type": "quarterly"}, {"count": 0}, {"count": 21}]
)
def test_finance_params_are_validated(h: Harness, params: dict[str, Any]) -> None:
    with pytest.raises(InvalidParams):
        h.run("finance_income", "SH600000", **params)
    with pytest.raises(InvalidParams):
        h.call("finance_income", symbol="SH600000", period="annual")
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
    ("quote_detail", STOCK, DETAIL, {"data": {"quote": None}}),
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
