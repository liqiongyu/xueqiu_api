"""F10 dividends, holders, indicators, industry and institutional holdings: units, dates, tables."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from xueqiu_api import REGISTRY, InvalidParams, UpstreamChanged
from xueqiu_api._f10 import report_date_from_label
from xueqiu_api._render import FENCE_BEGIN, FENCE_END, FREE_TEXT_CHARS, compact_json, render
from xueqiu_api._replay import ReplayTransport
from xueqiu_api._time import to_ms
from xueqiu_api.endpoints.f10_bonus import ENDPOINT as BONUS
from xueqiu_api.endpoints.f10_bonus import Addition, Allotment, BonusPage, Dividend
from xueqiu_api.endpoints.f10_business_analysis import BusinessAnalysis
from xueqiu_api.endpoints.f10_holders import (
    HolderCount,
    Holders,
    MajorHolder,
    Repurchase,
    ShareChangeRecord,
    Split,
)
from xueqiu_api.endpoints.f10_indicator import MainIndicator
from xueqiu_api.endpoints.f10_industry import IndustryBoard, IndustryInfo
from xueqiu_api.endpoints.f10_industry_compare import CompareItem, CompareMetrics, IndustryCompare
from xueqiu_api.endpoints.f10_org_holding_change import (
    OrgHoldingChange,
    OrgHoldingDay,
    OrgHoldingPeriod,
)
from xueqiu_api.endpoints.f10_org_holding_detail import OrgHoldingDetail, StockFundHolding

from .harness import REPLAY_DIR, Harness, fixture_reply, json_reply, replay_body

STOCK = "stock.xueqiu.com"
SHANGHAI = ZoneInfo("Asia/Shanghai")
HONG_KONG = ZoneInfo("Asia/Hong_Kong")
NEW_YORK = ZoneInfo("America/New_York")
SH600000_FLOAT_SHARES = 33305838300
"""Tradable shares of SH600000 in the f10_indicator fixture."""


@pytest.fixture
def replay(make: Callable[..., Harness]) -> Harness:
    """A client of each kind answered from the replay fixtures."""
    return make(transport=ReplayTransport(REPLAY_DIR))


def expect_type(value: Any, cls: type) -> None:
    """``isinstance`` that leaves ``value`` as ``Any``, so tests can do sums on optional fields."""
    assert isinstance(value, cls)


def envelope(data: Any) -> Any:
    return {"data": data, "error_code": 0, "error_description": ""}


def answered(h: Harness, path: str, data: Any) -> Harness:
    h.upstream.route(STOCK, path, json_reply(envelope(data)))
    return h


def rendered(
    h: Harness,
    name: str,
    *,
    fields: Sequence[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
    **params: Any,
) -> str:
    spec = REGISTRY[name]
    return render(
        spec,
        spec.validate(params),
        h.call(name, **params),
        fields=fields,
        limit=spec.agent.row_default if limit is None else limit,
        max_chars=8000,
        fetched_at=datetime(2026, 11, 5, 2, 0, tzinfo=UTC),
        next_call=lambda following, more: compact_json({**following, "offset": more}),
        offset=offset,
    )


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("2026一季报", date(2026, 3, 31)),
        ("2026中报", date(2026, 6, 30)),
        ("2026三季报", date(2026, 9, 30)),
        ("2025年报", date(2025, 12, 31)),
        ("2026-09-30", date(2026, 9, 30)),
        ("2026年FY", None),
        ("年报", None),
        ("", None),
        (None, None),
        (1790697600000, None),
    ],
)
def test_report_date_from_label(label: object, expected: date | None) -> None:
    assert report_date_from_label(label) == expected


@pytest.mark.parametrize(
    ("name", "symbol", "path", "query"),
    [
        (
            "f10_bonus",
            "SH600000",
            "/v5/stock/f10/cn/bonus.json",
            {"symbol": "SH600000", "page": "1", "size": "50"},
        ),
        (
            "f10_bonus",
            "00700",
            "/v5/stock/f10/hk/bonus.json",
            {"symbol": "00700", "page": "1", "size": "50"},
        ),
        (
            "f10_bonus",
            "AAPL",
            "/v5/stock/f10/us/bonus.json",
            {"symbol": "AAPL", "page": "1", "size": "50"},
        ),
        (
            "f10_business_analysis",
            "SH600000",
            "/v5/stock/f10/cn/business_analysis.json",
            {"symbol": "SH600000"},
        ),
        ("f10_holders", "SH600000", "/v5/stock/f10/cn/holders.json", {"symbol": "SH600000"}),
        ("f10_holders", "00700", "/v5/stock/f10/hk/holders.json", {"symbol": "00700"}),
        ("f10_holders", "AAPL", "/v5/stock/f10/us/holders.json", {"symbol": "AAPL"}),
        ("f10_indicator", "SH600000", "/v5/stock/f10/cn/indicator.json", {"symbol": "SH600000"}),
        ("f10_industry", "SH600000", "/v5/stock/f10/cn/industry.json", {"symbol": "SH600000"}),
        ("f10_industry", "00700", "/v5/stock/f10/hk/industry.json", {"symbol": "00700"}),
        ("f10_industry", "AAPL", "/v5/stock/f10/us/industry.json", {"symbol": "AAPL"}),
        (
            "f10_industry_compare",
            "SH600000",
            "/v5/stock/f10/cn/industry/compare.json",
            {"symbol": "SH600000", "type": "single"},
        ),
        (
            "f10_org_holding_change",
            "SH600000",
            "/v5/stock/f10/cn/org_holding/change.json",
            {"symbol": "SH600000"},
        ),
        (
            "f10_org_holding_change",
            "AAPL",
            "/v5/stock/f10/us/org_holding/change.json",
            {"symbol": "AAPL"},
        ),
        (
            "f10_org_holding_detail",
            "SH600000",
            "/v5/stock/f10/cn/org_holding/detail.json",
            {"symbol": "SH600000", "extend": "true"},
        ),
    ],
)
def test_each_market_asks_its_path(
    h: Harness, name: str, symbol: str, path: str, query: dict[str, str]
) -> None:
    h.upstream.route(STOCK, path, fixture_reply(f"{name}/{symbol.lower()}"))

    assert h.call(name, symbol=symbol)

    [sent] = h.upstream.requests(STOCK, path)
    assert sent.params == query


@pytest.mark.parametrize(
    ("name", "symbol", "markets"),
    [
        ("f10_business_analysis", "00700", ["CN"]),
        ("f10_business_analysis", "AAPL", ["CN"]),
        ("f10_indicator", "00700", ["CN"]),
        ("f10_indicator", "AAPL", ["CN"]),
        ("f10_industry_compare", "00700", ["CN"]),
        ("f10_industry_compare", "AAPL", ["CN"]),
        ("f10_org_holding_change", "00700", ["CN", "US"]),
        ("f10_org_holding_detail", "00700", ["CN"]),
        ("f10_org_holding_detail", "AAPL", ["CN"]),
    ],
)
def test_other_markets_are_rejected_before_any_request(
    h: Harness, name: str, symbol: str, markets: list[str]
) -> None:
    with pytest.raises(InvalidParams) as caught:
        h.call(name, symbol=symbol)

    assert (caught.value.field, list(caught.value.valid_values)) == ("symbol", markets)
    assert h.upstream.sent == []


@pytest.mark.parametrize(
    ("name", "symbol", "path", "data"),
    [
        ("f10_bonus", "SH600000", "/v5/stock/f10/cn/bonus.json", {"items": []}),
        (
            "f10_business_analysis",
            "SH600000",
            "/v5/stock/f10/cn/business_analysis.json",
            {"items": []},
        ),
        ("f10_holders", "SH600000", "/v5/stock/f10/cn/holders.json", {"items": []}),
        ("f10_holders", "AAPL", "/v5/stock/f10/us/holders.json", {}),
        ("f10_indicator", "SH600000", "/v5/stock/f10/cn/indicator.json", {"items": []}),
        ("f10_industry", "SH600000", "/v5/stock/f10/cn/industry.json", {}),
        (
            "f10_industry_compare",
            "SH600000",
            "/v5/stock/f10/cn/industry/compare.json",
            {"items": []},
        ),
        (
            "f10_org_holding_change",
            "SH600000",
            "/v5/stock/f10/cn/org_holding/change.json",
            {"items": []},
        ),
        (
            "f10_org_holding_change",
            "AAPL",
            "/v5/stock/f10/us/org_holding/change.json",
            {"items": []},
        ),
        (
            "f10_org_holding_detail",
            "SH600000",
            "/v5/stock/f10/cn/org_holding/detail.json",
            {"fund_items": [], "timestamp": 1790697600000},
        ),
    ],
)
def test_no_rows_is_an_empty_success(
    h: Harness, name: str, symbol: str, path: str, data: Any
) -> None:
    result = answered(h, path, data).call(name, symbol=symbol)

    assert len(result) == 0
    assert not result


@pytest.mark.parametrize(
    ("name", "symbol", "path", "data"),
    [
        ("f10_bonus", "SH600000", "/v5/stock/f10/cn/bonus.json", {"items": "x"}),
        ("f10_bonus", "SH600000", "/v5/stock/f10/cn/bonus.json", {}),
        (
            "f10_business_analysis",
            "SH600000",
            "/v5/stock/f10/cn/business_analysis.json",
            {"items": "x"},
        ),
        ("f10_holders", "SH600000", "/v5/stock/f10/cn/holders.json", {"items": "x"}),
        ("f10_holders", "SH600000", "/v5/stock/f10/cn/holders.json", {"x": 1}),
        ("f10_indicator", "SH600000", "/v5/stock/f10/cn/indicator.json", {"items": "x"}),
        ("f10_industry", "SH600000", "/v5/stock/f10/cn/industry.json", {"industry": "x"}),
        ("f10_industry", "SH600000", "/v5/stock/f10/cn/industry.json", {"x": 1}),
        ("f10_industry", "SH600000", "/v5/stock/f10/cn/industry.json", [1]),
        (
            "f10_industry_compare",
            "SH600000",
            "/v5/stock/f10/cn/industry/compare.json",
            {"items": "x"},
        ),
        (
            "f10_org_holding_change",
            "SH600000",
            "/v5/stock/f10/cn/org_holding/change.json",
            {"items": "x"},
        ),
        (
            "f10_org_holding_change",
            "AAPL",
            "/v5/stock/f10/us/org_holding/change.json",
            {"items": "x"},
        ),
        (
            "f10_org_holding_detail",
            "SH600000",
            "/v5/stock/f10/cn/org_holding/detail.json",
            {"fund_items": "x"},
        ),
    ],
)
def test_a_changed_shape_is_upstream_changed(
    h: Harness, name: str, symbol: str, path: str, data: Any
) -> None:
    answered(h, path, data)

    with pytest.raises(UpstreamChanged):
        h.call(name, symbol=symbol)


@pytest.mark.sample("f10_bonus")
def test_f10_bonus_reads_a_shanghai_page(replay: Harness) -> None:
    page = replay.call("f10_bonus", symbol="SH600000", page_size=3)

    expect_type(page, BonusPage)
    assert (len(page), page.page, page.page_size, page.total) == (3, 1, 3, None)
    latest = page.items[0]
    expect_type(latest, Dividend)
    assert latest.report_name == "2025年报"
    assert latest.plan == "10派4.2元(实施方案)"
    assert (latest.record_date, latest.ex_date) == (date(2026, 7, 15), date(2026, 7, 16))
    assert (latest.pay_date, latest.announcement_date, latest.cancelled_date) == (None, None, None)
    raw = replay_body("f10_bonus/sh600000")["data"]["items"][0]["ashare_ex_dividend_date"]
    assert raw == to_ms(datetime(2026, 7, 16, tzinfo=SHANGHAI))

    assert BONUS.order == "newest_first"
    dates = [row.ex_date for row in page]
    assert dates == sorted(dates, reverse=True)

    issue = page.additions[0]
    expect_type(issue, Addition)
    assert issue.listing_ad == date(2017, 9, 6)
    assert (issue.actual_issue_vol, issue.actual_issue_price) == (1248316498, 11.88)
    assert issue.actual_rc_net_amt == 14816552046
    assert issue.actual_rc_net_amt < issue.actual_issue_vol * issue.actual_issue_price
    assert page.allotments == ()


@pytest.mark.sample("f10_bonus")
def test_f10_bonus_reads_hong_kong_dates_there(replay: Harness) -> None:
    page = replay.call("f10_bonus", symbol="00700")

    latest = page.items[0]
    assert latest.plan == "末期派息每股股息5.3元（HKD ）"
    assert (latest.ex_date, latest.pay_date) == (date(2026, 5, 15), date(2026, 6, 1))
    assert (latest.report_name, latest.record_date, latest.announcement_date) == (None, None, None)
    assert (page.additions, page.allotments) == ((), ())
    raw = replay_body("f10_bonus/00700")["data"]["items"][0]["dertsdiv"]
    assert raw == to_ms(datetime(2026, 5, 15, tzinfo=HONG_KONG))


@pytest.mark.sample("f10_bonus")
def test_f10_bonus_reads_us_dates_in_new_york(replay: Harness) -> None:
    page = replay.call("f10_bonus", symbol="AAPL")

    latest = page.items[0]
    assert latest.plan == "每股分配0.27美元"
    assert latest.announcement_date == date(2026, 11, 2)
    assert (latest.ex_date, latest.pay_date) == (date(2026, 11, 12), date(2026, 11, 16))
    assert (page.additions, page.allotments) == ((), ())
    raw = replay_body("f10_bonus/aapl")["data"]["items"][0]["exright_date"]
    assert raw == to_ms(datetime(2026, 11, 12, tzinfo=NEW_YORK))


@pytest.mark.parametrize(
    ("symbol", "key", "moment"),
    [
        ("SH600000", "ashare_ex_dividend_date", datetime(2026, 7, 16, 0, 30, tzinfo=SHANGHAI)),
        ("00700", "dertsdiv", datetime(2026, 5, 15, 0, 30, tzinfo=HONG_KONG)),
        ("AAPL", "exright_date", datetime(2026, 11, 12, 23, 30, tzinfo=NEW_YORK)),
    ],
    ids=["shanghai", "hong-kong", "new-york"],
)
def test_f10_bonus_dates_are_in_the_exchange_timezone(
    h: Harness, symbol: str, key: str, moment: datetime
) -> None:
    # Each moment falls on another date in UTC, so a read in any other timezone fails here.
    assert moment.astimezone(UTC).date() != moment.date()
    h.upstream.route(
        STOCK,
        BONUS.path.format(region={"SH600000": "cn", "00700": "hk", "AAPL": "us"}[symbol]),
        json_reply(envelope({"items": [{key: to_ms(moment)}]})),
    )

    assert h.call("f10_bonus", symbol=symbol).items[0].ex_date == moment.date()


@pytest.mark.sample("f10_bonus")
def test_f10_bonus_reads_an_allotment_and_a_plan_with_no_dates(replay: Harness) -> None:
    page = replay.call("f10_bonus", symbol="SH601166")

    [allotment] = page.allotments
    expect_type(allotment, Allotment)
    assert allotment.allot_date == date(2010, 5, 20)
    assert (allotment.allot_price, allotment.actual_allot_num) == (18.0, 992450630)
    assert allotment.actual_rc_total_amt == allotment.allot_price * allotment.actual_allot_num
    assert allotment.model_extra == {}
    assert page.additions[1].actual_issue_vol == pytest.approx(1915146700)
    proposal = page.items[0]
    assert (proposal.ex_date, proposal.record_date) == (None, None)
    assert proposal.report_name == "2026中报"


def test_f10_bonus_keeps_a_date_in_place_of_a_period(h: Harness) -> None:
    answered(h, "/v5/stock/f10/cn/bonus.json", {"items": [{"dividend_year": "2023-11-21"}]})

    assert h.call("f10_bonus", symbol="SH600519").items[0].report_name == "2023-11-21"


@pytest.mark.sample("f10_bonus")
def test_f10_bonus_walk_ends_on_the_empty_page_and_takes_tables_from_page_1(h: Harness) -> None:
    path = "/v5/stock/f10/cn/bonus.json"
    h.upstream.route(
        STOCK,
        path,
        fixture_reply("f10_bonus/sh600000"),
        fixture_reply("f10_bonus/sh600000_p2"),
    )

    pages, error = h.walk(BONUS, symbol="SH600000")

    assert error is None
    assert [len(page) for page in pages] == [3, 0]
    assert [len(page.additions) for page in pages] == [3, 0]
    assert pages[0].short
    assert pages[0].next_params == {"symbol": "SH600000", "page": 2, "page_size": 100}
    assert pages[1].next_params is None
    sent = h.upstream.requests(STOCK, path)
    assert [(one.params["page"], one.params["size"]) for one in sent] == [
        ("1", "100"),
        ("2", "100"),
    ]


@pytest.mark.sample("f10_bonus")
def test_f10_bonus_second_page_drops_the_tables_the_upstream_repeats(replay: Harness) -> None:
    # The upstream sends the additions on every page, so they are in the fixture of page 2.
    assert len(replay_body("f10_bonus/sh600000_p2")["data"]["addtions"]) == 3

    page = replay.call("f10_bonus", symbol="SH600000", page=2)

    assert (len(page), page.additions, page.allotments) == (0, (), ())
    assert page.next_params is None


@pytest.mark.parametrize(
    "params",
    [{"page": 0}, {"page_size": 0}, {"page_size": 101}],
    ids=["page", "size-low", "size-high"],
)
def test_f10_bonus_checks_paging_before_any_request(h: Harness, params: dict[str, int]) -> None:
    with pytest.raises(InvalidParams):
        h.call("f10_bonus", symbol="SH600000", **params)

    assert h.upstream.sent == []


def test_f10_bonus_renders_each_table_of_a_flat_run(replay: Harness) -> None:
    text = rendered(replay, "f10_bonus", symbol="SH600000", page_size=3)

    assert "# note: empty sections: allotments\n" in text
    assert "# section: dividends\nreport_name,plan,record_date,ex_date\n" in text
    assert "2025年报,10派4.2元(实施方案),2026-07-15,2026-07-16\n" in text
    assert "# section: additions\nlisting_ad,actual_issue_vol," in text
    assert "2017-09-06,1248316498,11.88,14816552046\n" in text
    assert text.endswith('# next: {"symbol":"SH600000","page":2,"page_size":3,"offset":0}')


def test_f10_bonus_limit_and_offset_count_the_flat_run(replay: Harness) -> None:
    first = rendered(replay, "f10_bonus", symbol="SH600000", page_size=3, limit=4)
    second = rendered(replay, "f10_bonus", symbol="SH600000", page_size=3, limit=4, offset=4)

    assert "# section: additions" in first
    assert "1248316498" in first
    assert "999510332" not in first
    assert first.endswith('# next: {"symbol":"SH600000","page":1,"page_size":3,"offset":4}')
    assert "# section: dividends" not in second
    assert "999510332" in second
    assert second.endswith('# next: {"symbol":"SH600000","page":2,"page_size":3,"offset":0}')


def test_f10_bonus_renders_the_allotments(replay: Harness) -> None:
    text = rendered(replay, "f10_bonus", symbol="SH601166")

    assert (
        "# section: allotments\nallot_date,allot_price,actual_allot_num,actual_rc_total_amt\n"
        in text
    )
    assert "2010-05-20,18,992450630,17864111340\n" in text
    assert "empty sections" not in text


@pytest.mark.parametrize("symbol", ["00700", "AAPL"])
def test_f10_bonus_of_other_markets_renders_one_table(replay: Harness, symbol: str) -> None:
    text = rendered(replay, "f10_bonus", symbol=symbol)

    assert "# section:" not in text
    assert "empty sections" not in text


def test_f10_bonus_later_page_renders_no_table_from_page_1(replay: Harness) -> None:
    text = rendered(replay, "f10_bonus", symbol="SH600000", page=2)

    assert "no rows on page 2" in text
    assert "11.88" not in text
    assert "empty sections" not in text
    assert text.endswith("# next: none")


@pytest.mark.sample("f10_business_analysis")
def test_f10_business_analysis_derives_the_period_end(replay: Harness) -> None:
    rows = replay.call("f10_business_analysis", symbol="SH600000")

    assert all(isinstance(row, BusinessAnalysis) for row in rows)
    assert [(row.report_name, row.report_date) for row in rows] == [
        ("2026三季报", date(2026, 9, 30)),
        ("2026中报", date(2026, 6, 30)),
        ("2026一季报", date(2026, 3, 31)),
    ]
    assert rows[0].operating_analysis_explain == "合成经营分析第1期\r\n第二段"
    assert REGISTRY["f10_business_analysis"].order == "newest_first"


def test_f10_business_analysis_shows_prose_only_when_asked_and_fenced(h: Harness) -> None:
    long_text = "合成长文本\r\n" * 200
    answered(
        h,
        "/v5/stock/f10/cn/business_analysis.json",
        {"items": [{"report_date": "2026三季报", "operating_analysis_explain": long_text}]},
    )

    default = rendered(h, "f10_business_analysis", symbol="SH600000")
    asked = rendered(
        h,
        "f10_business_analysis",
        symbol="SH600000",
        fields=["report_name", "operating_analysis_explain"],
    )

    assert "report_name,report_date\n2026三季报,2026-09-30\n" in default
    assert "合成长文本" not in default
    assert FENCE_BEGIN in asked
    assert FENCE_END in asked
    [row] = [line for line in asked.splitlines() if line.startswith("2026三季报,")]
    assert "\r" not in row
    assert len(row.split(",", 1)[1]) <= FREE_TEXT_CHARS


@pytest.mark.sample("f10_holders")
def test_f10_holders_of_an_a_share_cross_check(replay: Harness) -> None:
    holders = replay.call("f10_holders", symbol="SH600000")

    expect_type(holders, Holders)
    assert len(holders) == 3
    latest = holders.holder_counts[0]
    expect_type(latest, HolderCount)
    assert latest.report_date == date(2026, 9, 30)
    assert latest.ashare_holder == 151330
    assert (latest.top_holder_ratio, latest.top_float_holder_ratio) == (74.68, 74.6823)
    assert (holders.major_holders, holders.share_changes, holders.splits) == ((), (), ())
    assert holders.repurchases == ()
    assert REGISTRY["f10_holders"].order == "newest_first"
    dates = [row.report_date for row in holders.holder_counts]
    assert dates == sorted(dates, reverse=True)
    raw = replay_body("f10_holders/sh600000")["data"]["items"][0]["timestamp"]
    assert raw == to_ms(datetime(2026, 9, 30, tzinfo=SHANGHAI))

    for row in holders.holder_counts:
        assert row.per_float * row.ashare_holder == pytest.approx(SH600000_FLOAT_SHARES)
        assert row.per_amount == pytest.approx(row.per_float * row.price)
    for newer, older in pairwise(holders.holder_counts):
        holder_change = (newer.ashare_holder / older.ashare_holder - 1) * 100
        assert newer.ashare_holder_chg_pct == pytest.approx(holder_change, abs=0.01)
        float_change = (newer.per_float / older.per_float - 1) * 100
        assert newer.per_float_chg_pct == pytest.approx(float_change, abs=0.01)


@pytest.mark.sample("f10_holders")
def test_f10_holders_of_hong_kong(replay: Harness) -> None:
    holders = replay.call("f10_holders", symbol="00700")

    assert holders.currency == "HKD"
    assert holders.holder_counts == ()
    holder = holders.major_holders[0]
    expect_type(holder, MajorHolder)
    assert (holder.holder_name, holder.held_num) == ("示例股东甲", 2055231800)
    change = holders.share_changes[0]
    expect_type(change, ShareChangeRecord)
    assert (change.change_date, change.total_shares) == (date(2026, 10, 14), 9092644829)
    assert change.model_extra == {"refcshgty": None, "epshgch": None, "prefsh": None}
    assert holder.held_ratio == pytest.approx(holder.held_num / change.total_shares * 100, abs=0.01)
    raw = replay_body("f10_holders/00700")["data"]["shares"][0]["chdate"]
    assert raw == to_ms(datetime(2026, 10, 14, tzinfo=HONG_KONG))
    split = holders.splits[0]
    expect_type(split, Split)
    assert split.explain == "1股拆为5股"
    assert (split.announcement_date, split.effective_date) == (date(2014, 3, 19), date(2014, 5, 15))
    assert split.parallel_trading_start == date(2014, 5, 29)
    assert split.parallel_trading_end == date(2014, 6, 20)
    assert (split.temporary_code, split.ex_date) == ("02988", None)
    repurchase = holders.repurchases[0]
    expect_type(repurchase, Repurchase)
    assert repurchase.repurchase_date == date(2026, 10, 15)
    assert (repurchase.repurchase_shares, repurchase.avg_price) == (240000, 418.27)


@pytest.mark.sample("f10_holders")
def test_f10_holders_of_the_us(replay: Harness) -> None:
    holders = replay.call("f10_holders", symbol="AAPL")

    assert (holders.currency, holders.holder_counts, holders.repurchases) == (None, (), ())
    holder = holders.major_holders[0]
    assert (holder.holder_name, holder.held_num, holder.held_ratio) == (
        "示例机构甲",
        1415826462,
        9.63,
    )
    assert holder.model_extra == {"chg": 7.43}
    newest, weekend, _ = holders.share_changes
    assert (newest.change_date, newest.total_shares) == (date(2026, 10, 23), 14532182000)
    assert newest.common_shares == 14532182000
    assert newest.reason is None
    raw = replay_body("f10_holders/aapl")["data"]["sharechg"][0]["chg_date"]
    assert raw == to_ms(datetime(2026, 10, 23, tzinfo=NEW_YORK))
    # A fiscal year end, not a trading day.
    assert weekend.change_date == date(2026, 9, 26)
    assert weekend.change_date.weekday() == 5
    assert weekend.reason == "合成变动原因\r\n第二行"
    split = holders.splits[0]
    assert split.explain == "每1.0股拆为4.0股"
    assert (split.announcement_date, split.effective_date, split.ex_date) == (
        date(2020, 7, 30),
        date(2020, 8, 28),
        date(2020, 8, 31),
    )


def test_f10_holders_render_the_tables_of_the_market(replay: Harness) -> None:
    a_share = rendered(replay, "f10_holders", symbol="SH600000")
    hong_kong = rendered(replay, "f10_holders", symbol="00700")
    us = rendered(replay, "f10_holders", symbol="AAPL")

    assert "# section:" not in a_share
    assert "empty sections" not in a_share
    assert "report_date,ashare_holder,ashare_holder_chg_pct,per_float,per_amount,price," in a_share
    assert "# info: currency=HKD\n" in hong_kong
    for table in ("major_holders", "share_changes", "splits", "repurchases"):
        assert f"# section: {table}\n" in hong_kong
    assert "empty sections" not in hong_kong
    assert "holder_name,held_num,held_ratio\n示例股东甲,2055231800,22.6043\n" in hong_kong
    assert "repurchase_date,repurchase_shares,avg_price\n2026-10-15,240000,418.27\n" in hong_kong
    assert "repurchases" not in us
    assert "explain,effective_date,ex_date\n每1.0股拆为4.0股,2020-08-28,2020-08-31\n" in us


def test_f10_holders_report_a_table_of_the_market_with_no_rows(h: Harness) -> None:
    answered(h, "/v5/stock/f10/hk/holders.json", {"holders": [{"numrts": 1}]})

    text = rendered(h, "f10_holders", symbol="00700")

    assert "# note: empty sections: share_changes, splits, repurchases\n" in text


def test_f10_holders_fence_the_reason_when_asked(replay: Harness) -> None:
    text = rendered(replay, "f10_holders", symbol="AAPL", fields=["change_date", "reason"])

    assert "合成变动原因" not in rendered(replay, "f10_holders", symbol="AAPL")
    assert f"{FENCE_BEGIN}\nchange_date,reason\n" in text
    assert "2026-09-26,合成变动原因 第二行\n" in text
    assert "tables with none of these columns are left out: major_holders" in text


@pytest.mark.sample("f10_indicator")
def test_f10_indicator_units_and_cross_checks(replay: Harness) -> None:
    [row] = replay.call("f10_indicator", symbol="SH600000")

    expect_type(row, MainIndicator)
    assert (row.report_name, row.report_date) == ("2026三季报", date(2026, 9, 30))
    assert (row.currency, row.gross_selling_rate) == ("CNY", None)
    assert REGISTRY["f10_indicator"].order is None

    quote = replay_body("quote_detail/sh600000")["data"]["quote"]
    close = quote["current"]
    assert row.pe_ttm == quote["pe_ttm"]
    assert row.pb == quote["pb"]
    assert row.market_capital == quote["market_capital"]
    assert row.float_market_capital == quote["float_market_capital"]
    assert row.dividend == quote["dividend"]
    assert row.dividend_yield == quote["dividend_yield"]
    assert row.np_per_share == quote["navps"]
    assert row.total_shares == row.float_shares == SH600000_FLOAT_SHARES
    assert row.market_capital == pytest.approx(row.total_shares * close)
    assert row.float_market_capital == pytest.approx(row.float_shares * close)
    assert row.dividend_yield == pytest.approx(row.dividend / close * 100, abs=0.001)
    assert row.pb == pytest.approx(close / row.np_per_share, abs=0.001)

    balance = replay_body("finance_balance/sh600000")["data"]["list"][0]
    goodwill = balance["goodwill"][0] / balance["total_quity_atsopc"][0] * 100
    assert row.goodwill_in_net_assets == goodwill


@pytest.mark.sample("f10_indicator")
def test_f10_indicator_growth_is_in_percent_points(replay: Harness) -> None:
    [row] = replay.call("f10_indicator", symbol="SH600000")

    income = replay_body("finance_income/sh600000")["data"]["list"][0]
    assert row.operating_income_yoy == pytest.approx(income["total_revenue"][1] * 100, abs=1e-3)
    assert row.net_profit_atsopc_yoy == pytest.approx(
        income["net_profit_atsopc"][1] * 100, abs=1e-3
    )
    assert row.total_revenue == income["total_revenue"][0]
    assert row.net_profit_atsopc == income["net_profit_atsopc"][0]
    assert row.basic_eps == income["basic_eps"][0]
    indicator = replay_body("finance_indicator/sh600000")["data"]["list"][0]
    assert row.avg_roe == indicator["avg_roe"][0]


@pytest.mark.parametrize("field", ["total_revenue", "net_profit_atsopc", "basic_eps", "avg_roe"])
def test_f10_indicator_says_which_fields_are_year_to_date(field: str) -> None:
    assert "year to date" in (MainIndicator.model_fields[field].description or "")


def test_f10_indicator_renders_the_default_columns(replay: Harness) -> None:
    text = rendered(replay, "f10_indicator", symbol="SH600000")

    assert "report_name,report_date,total_revenue,operating_income_yoy," in text
    assert "2026三季报,2026-09-30,138133000000,4.4247,40572000000,4.5158,1.17,5.2," in text


@pytest.mark.sample("f10_industry")
def test_f10_industry_of_three_markets(replay: Harness) -> None:
    a_share = replay.call("f10_industry", symbol="SH600000")
    hong_kong = replay.call("f10_industry", symbol="00700")
    us = replay.call("f10_industry", symbol="AAPL")

    expect_type(a_share, IndustryInfo)
    assert (a_share.industry_class, a_share.concept_class) == ("SW2014", "0003")
    assert (hong_kong.industry_class, hong_kong.concept_class) == ("HS", None)
    assert (us.industry_class, us.concept_class) == ("GICS", "0004")
    assert [(len(one.industries), len(one.concepts)) for one in (a_share, hong_kong, us)] == [
        (1, 3),
        (3, 0),
        (3, 3),
    ]
    assert (len(a_share), len(hong_kong), len(us)) == (4, 3, 6)
    board = a_share.industries[0]
    expect_type(board, IndustryBoard)
    assert (board.ind_name, board.ind_code) == ("银行", "BK0055")
    company = replay_body("f10_company/sh600000")["data"]["company"]
    assert board.ind_code == company["affiliate_industry"]["ind_code"]
    assert [row.ind_name for row in us.industries] == ["可选消费", "耐用消费品", "消费电子"]
    assert REGISTRY["f10_industry"].order is None
    for one in (a_share, hong_kong, us):
        assert one.model_extra == {}


def test_f10_industry_keeps_new_keys_and_drops_the_company(h: Harness) -> None:
    answered(
        h,
        "/v5/stock/f10/cn/industry.json",
        {"industry": [], "brand_new": 1, "company": {"chairman": "示例董事长"}},
    )

    assert h.call("f10_industry", symbol="SH600000").model_extra == {"brand_new": 1}


def test_f10_industry_renders_two_tables_or_notes_the_empty_one(replay: Harness) -> None:
    a_share = rendered(replay, "f10_industry", symbol="SH600000")
    hong_kong = rendered(replay, "f10_industry", symbol="00700")

    assert "# info: industry_class=SW2014 concept_class=0003\n" in a_share
    assert "# section: industries\nind_name,ind_code\n银行,BK0055\n" in a_share
    assert "# section: concepts\nind_name,ind_code\n融资融券,BK0409\n" in a_share
    assert "示例董事长" not in hong_kong
    assert "# info: industry_class=HS\n" in hong_kong
    assert "# note: empty sections: concepts\n" in hong_kong
    assert "# section:" not in hong_kong


@pytest.mark.sample("f10_industry_compare")
@pytest.mark.sample("f10_indicator")
def test_f10_industry_compare_units_and_cross_checks(replay: Harness) -> None:
    compare = replay.call("f10_industry_compare", symbol="SH600000")

    expect_type(compare, IndustryCompare)
    assert len(compare) == 1
    assert (compare.ind_name, compare.ind_code, compare.ind_class) == ("银行", "BK0055", "SW2014")
    assert (compare.report_name, compare.report_date) == ("2026-09-30", date(2026, 9, 30))
    assert compare.quote_time == datetime(2026, 11, 4, 15, 0, tzinfo=SHANGHAI)
    assert compare.quote_time.utcoffset() == timedelta(hours=8)
    assert compare.model_extra == {"count": 1}
    assert REGISTRY["f10_industry_compare"].order is None

    stock = compare.items[0]
    expect_type(stock, CompareItem)
    assert (stock.symbol, stock.name) == ("SH600000", "浦发银行")
    expect_type(compare.industry_avg, CompareMetrics)
    [indicator] = replay.call("f10_indicator", symbol="SH600000")
    for name in (
        "pe_ttm",
        "pb",
        "market_capital",
        "np_per_share",
        "net_selling_rate",
        "total_revenue",
        "net_profit_atsopc",
        "basic_eps",
        "operating_income_yoy",
        "net_profit_atsopc_yoy",
        "asset_liab_ratio",
    ):
        assert getattr(stock, name) == getattr(indicator, name), name
    assert (stock.avg_roe, indicator.avg_roe) == (4.8994, 5.2)
    assert stock.total_shares == indicator.total_shares
    balance = replay_body("finance_balance/sh600000")["data"]["list"][0]
    assert stock.total_assets == balance["total_assets"][0]

    assert compare.industry_min is not None
    assert compare.industry_max is not None
    compared = 0
    for name in CompareMetrics.model_fields:
        low, high = getattr(compare.industry_min, name), getattr(compare.industry_max, name)
        if low is not None and high is not None:
            assert low <= getattr(stock, name) <= high, name
            compared += 1
    assert compared == 9


@pytest.mark.sample("f10_industry_compare")
def test_f10_industry_compare_returns_the_odd_bank_average_as_sent(replay: Harness) -> None:
    compare = replay.call("f10_industry_compare", symbol="SH600000")

    assert compare.industry_avg.gross_selling_rate == 0.0
    assert compare.industry_min.gross_selling_rate is None
    assert compare.industry_max.gross_selling_rate is None
    assert compare.items[0].gross_selling_rate is None


@pytest.mark.parametrize(
    ("report_name", "expected"),
    [("2018三季报", date(2018, 9, 30)), ("2026-09-30", date(2026, 9, 30)), ("其他写法", None)],
)
def test_f10_industry_compare_reads_both_forms_of_the_period(
    h: Harness, report_name: str, expected: date | None
) -> None:
    answered(
        h,
        "/v5/stock/f10/cn/industry/compare.json",
        {"items": [{"symbol": "SH600000"}], "report_name": report_name, "new_key": 1},
    )

    compare = h.call("f10_industry_compare", symbol="SH600000")

    assert (compare.report_name, compare.report_date) == (report_name, expected)
    assert compare.industry_avg is None
    assert compare.model_extra == {"new_key": 1}


def test_f10_industry_compare_renders_the_stock_then_the_statistics(replay: Harness) -> None:
    text = rendered(replay, "f10_industry_compare", symbol="SH600000")

    assert (
        "# info: ind_name=银行 ind_code=BK0055 ind_class=SW2014 report_date=2026-09-30 "
        "quote_time=2026-11-04T15:00:00+08:00\n"
    ) in text
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("stat,"))
    assert lines[start].startswith("stat,symbol,name,pe_ttm,pb,avg_roe,")
    assert [line.split(",")[0] for line in lines[start + 1 : start + 5]] == [
        "stock",
        "avg",
        "min",
        "max",
    ]
    assert lines[start + 1].startswith("stock,SH600000,浦发银行,6.491,0.443,4.8994,")
    assert lines[start + 3].startswith("min,,,4.358,,3.5017,")
    assert "# section:" not in text


@pytest.mark.sample("f10_org_holding_change")
def test_f10_org_holding_change_by_report_period(replay: Harness) -> None:
    change = replay.call("f10_org_holding_change", symbol="SH600000")

    expect_type(change, OrgHoldingChange)
    assert (len(change), change.days) == (3, ())
    latest = change.periods[0]
    expect_type(latest, OrgHoldingPeriod)
    assert (latest.report_name, latest.report_date) == ("2026三季报", date(2026, 9, 30))
    assert latest.institution_num == 74
    expect_type(latest.institution_num, int)
    assert (latest.held_ratio, latest.held_ratio_chg_pct, latest.price) == (75.192, -1.37, 9.48)
    raw = replay_body("f10_org_holding_change/sh600000")["data"]["items"][0]["timestamp"]
    assert raw == to_ms(datetime(2026, 9, 30, tzinfo=SHANGHAI))
    assert REGISTRY["f10_org_holding_change"].order == "newest_first"
    dates = [row.report_date for row in change.periods]
    assert dates == sorted(dates, reverse=True)


@pytest.mark.parametrize("symbol", ["SH600000", "SZ002027"])
@pytest.mark.sample("f10_org_holding_change")
def test_f10_org_holding_change_is_a_relative_change(replay: Harness, symbol: str) -> None:
    change = replay.call("f10_org_holding_change", symbol=symbol)

    for newer, older in pairwise(change.periods):
        relative = (newer.held_ratio / older.held_ratio - 1) * 100
        assert newer.held_ratio_chg_pct == pytest.approx(relative, abs=0.01)
        assert abs(newer.held_ratio_chg_pct - (newer.held_ratio - older.held_ratio)) > 0.1


@pytest.mark.sample("f10_org_holding_change")
def test_f10_org_holding_change_by_day_in_the_us(replay: Harness) -> None:
    change = replay.call("f10_org_holding_change", symbol="AAPL")

    assert change.periods == ()
    latest, previous, _ = change.days
    expect_type(latest, OrgHoldingDay)
    assert latest.trade_date == date(2026, 11, 3)
    assert (latest.institution_num, latest.held_ratio) == (6466, 76.5831)
    assert latest.net_trade_vol == -2673177
    implied = (latest.held_ratio - previous.held_ratio) / 100 * 14532182000
    assert latest.net_trade_vol == pytest.approx(implied, rel=0.01)
    dates = [row.trade_date for row in change.days]
    assert dates == sorted(dates, reverse=True)

    kline = replay.call("kline", symbol="AAPL", period="day")
    closes = dict(
        zip(
            (moment.astimezone(NEW_YORK).date() for moment in kline.timestamps),
            kline.columns["close"],
            strict=True,
        )
    )
    assert latest.price == closes[date(2026, 11, 3)] == 331.9
    assert previous.price == closes[date(2026, 11, 2)] == 338.32


def test_f10_org_holding_change_empty_string_count_is_none(h: Harness) -> None:
    answered(
        h,
        "/v5/stock/f10/cn/org_holding/change.json",
        {"items": [{"chg_date": "2026三季报", "institution_num": ""}]},
    )

    change = h.call("f10_org_holding_change", symbol="SH600000")

    assert change.periods[0].institution_num is None


def test_f10_org_holding_change_renders_only_the_columns_of_the_market(replay: Harness) -> None:
    a_share = rendered(replay, "f10_org_holding_change", symbol="SH600000")
    us = rendered(replay, "f10_org_holding_change", symbol="AAPL")

    assert (
        "report_name,report_date,institution_num,held_ratio,held_ratio_chg_pct,price\n" in a_share
    )
    assert "2026三季报,2026-09-30,74,75.192,-1.37,9.48\n" in a_share
    assert "trade_date,institution_num,held_ratio,net_trade_vol,price\n" in us
    assert "2026-11-03,6466,76.5831,-2673177,331.9\n" in us
    for text in (a_share, us):
        assert "# section:" not in text
        assert "empty sections" not in text


@pytest.mark.sample("f10_org_holding_detail")
def test_f10_org_holding_detail_units_and_the_total_row(replay: Harness) -> None:
    detail = replay.call("f10_org_holding_detail", symbol="SH600000")

    expect_type(detail, OrgHoldingDetail)
    assert (detail.report_name, detail.report_date) == ("2026三季报", date(2026, 9, 30))
    raw = replay_body("f10_org_holding_detail/sh600000")["data"]["timestamp"]
    assert raw == to_ms(datetime(2026, 9, 30, tzinfo=SHANGHAI))
    assert len(detail) == 2
    assert all(isinstance(row, StockFundHolding) for row in detail.holdings)
    assert "全部合计" not in [row.holder_name for row in detail.holdings]
    assert detail.total is not None
    assert (detail.total.held_num, detail.total.to_float_shares_ratio) == (169885009, 0.51)
    for row in (detail.total, *detail.holdings):
        ratio = row.held_num / SH600000_FLOAT_SHARES * 100
        assert row.to_float_shares_ratio == pytest.approx(ratio, abs=0.001)
    assert detail.report_periods[1].report_name == "2026中报"
    assert detail.report_periods[1].report_date == date(2026, 6, 30)
    assert REGISTRY["f10_org_holding_detail"].order == "rank"
    held = [row.held_num for row in detail.holdings]
    assert held == sorted(held, reverse=True)


@pytest.mark.sample("f10_org_holding_detail")
def test_f10_org_holding_detail_asks_for_a_report_period(h: Harness) -> None:
    path = "/v5/stock/f10/cn/org_holding/detail.json"
    h.upstream.route(STOCK, path, fixture_reply("f10_org_holding_detail/sh600000_2026h1"))

    detail = h.call("f10_org_holding_detail", symbol="SH600000", report_date="2026-06-30")

    [sent] = h.upstream.requests(STOCK, path)
    assert sent.params == {"symbol": "SH600000", "extend": "true", "timestamp": "1782748800000"}
    assert int(sent.params["timestamp"]) == to_ms(datetime(2026, 6, 30, tzinfo=SHANGHAI))
    assert (detail.report_name, detail.report_date) == ("2026中报", date(2026, 6, 30))
    assert detail.total.held_num == 455525720


@pytest.mark.parametrize(
    ("rows", "total", "funds"),
    [
        (
            [
                {"org_name_or_fund_name": "合成基金甲", "held_num": 2},
                {"org_name_or_fund_name": "全部合计", "held_num": 3},
                {"org_name_or_fund_name": "合成基金乙", "held_num": 1},
            ],
            3,
            2,
        ),
        ([{"org_name_or_fund_name": "合成基金甲", "held_num": 2}], None, 1),
    ],
    ids=["total-not-first", "no-total"],
)
def test_f10_org_holding_detail_finds_the_total_by_name(
    h: Harness, rows: list[dict[str, Any]], total: int | None, funds: int
) -> None:
    answered(
        h,
        "/v5/stock/f10/cn/org_holding/detail.json",
        {"fund_items": rows, "timestamp": 1790697600000, "brand_new": 1},
    )

    detail = h.call("f10_org_holding_detail", symbol="SH600000")

    assert (detail.total.held_num if detail.total else None) == total
    assert len(detail) == funds
    assert detail.report_date == date(2026, 9, 30)
    assert detail.model_extra == {"brand_new": 1}


def test_f10_org_holding_detail_renders_the_total_in_the_header(replay: Harness) -> None:
    text = rendered(replay, "f10_org_holding_detail", symbol="SH600000", limit=1)

    assert (
        "# info: report_date=2026-09-30 report_name=2026三季报 total_held_num=169885009 "
        "total_to_float_shares_ratio=0.51 report_periods=3\n"
    ) in text
    assert "holder_name,held_num,to_float_shares_ratio\n" in text
    assert "华宝中证银行交易型开放式指数证券投资基金,40200187,0.1207\n" in text
    assert "全部合计" not in text
    assert text.endswith('# next: {"symbol":"SH600000","offset":1}')


@pytest.mark.sample("f10_holders")
@pytest.mark.sample("f10_org_holding_change")
@pytest.mark.sample("f10_org_holding_detail")
def test_the_institutional_ratio_adds_up_across_endpoints(replay: Harness) -> None:
    holders = replay.call("f10_holders", symbol="SH600000")
    change = replay.call("f10_org_holding_change", symbol="SH600000")
    detail = replay.call("f10_org_holding_detail", symbol="SH600000")

    top_float = holders.holder_counts[0].top_float_holder_ratio
    assert change.periods[0].held_ratio == pytest.approx(
        top_float + detail.total.to_float_shares_ratio, abs=0.001
    )
    assert [row.price for row in holders.holder_counts] == [row.price for row in change.periods]
    assert [row.report_date for row in holders.holder_counts] == [
        row.report_date for row in change.periods
    ]
