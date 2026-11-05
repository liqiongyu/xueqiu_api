"""The fund endpoints through Client and AsyncClient: parsing, units, routes and errors."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any, get_args
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
from xueqiu_api._render import compact_json, render
from xueqiu_api._replay import ReplayTransport
from xueqiu_api.endpoints.fund_achievement import FundPeriodPerformance
from xueqiu_api.endpoints.fund_asset import FundAsset
from xueqiu_api.endpoints.fund_auto_invest_return import FundAutoInvestReturn
from xueqiu_api.endpoints.fund_derived import FundDerived
from xueqiu_api.endpoints.fund_detail import FundDetail
from xueqiu_api.endpoints.fund_growth import FundGrowth, FundGrowthPeriod
from xueqiu_api.endpoints.fund_info import FundInfo
from xueqiu_api.endpoints.fund_manager import FundManager
from xueqiu_api.endpoints.fund_rank import FundRankItem, FundRankOrder, FundRankType
from xueqiu_api.endpoints.fund_rank_options import FundRankOptions
from xueqiu_api.endpoints.fund_search import FundSearchResult
from xueqiu_api.endpoints.fund_trade_date import FundTradeDate

from .harness import REPLAY_DIR, Harness, danjuan_error_reply, json_reply

DANJUAN = "danjuanfunds.com"
SHANGHAI = ZoneInfo("Asia/Shanghai")
INFO = "/djapi/fund/008975"
DERIVED = "/djapi/fund/derived/008975"
GROWTH = "/djapi/fund/growth/008975"
RANK = "/djapi/v3/filter/fund"
SEARCH = "/djapi/v2/search"
ONE_CODE_ENDPOINTS = [
    "fund_achievement",
    "fund_asset",
    "fund_auto_invest_return",
    "fund_derived",
    "fund_detail",
    "fund_growth",
    "fund_info",
    "fund_manager",
    "fund_trade_date",
]


def data(body: Any) -> Any:
    return {"data": body, "result_code": 0}


def replayed(make: Callable[..., Harness]) -> Harness:
    return make(transport=ReplayTransport(REPLAY_DIR))


def text_of(h: Harness, name: str, fields: list[str] | None = None, **params: Any) -> str:
    spec = REGISTRY[name]
    return render(
        spec,
        spec.validate(params),
        h.call(name, **params),
        fields=fields,
        limit=spec.agent.row_default,
        max_chars=6000,
        fetched_at=datetime(2026, 11, 5, 2, 0, tzinfo=SHANGHAI),
        next_call=lambda following, offset: compact_json(following),
    )


@pytest.mark.parametrize(
    ("name", "path", "query", "params"),
    [
        ("fund_info", INFO, {}, {"fund_code": "008975"}),
        ("fund_derived", DERIVED, {}, {"fund_code": "008975"}),
        (
            "fund_achievement",
            "/djapi/fundx/base/fund/achievement/008975",
            {},
            {"fund_code": "008975"},
        ),
        (
            "fund_asset",
            "/djapi/fundx/base/fund/record/asset/percent",
            {"fund_code": "008975"},
            {"fund_code": "008975"},
        ),
        ("fund_detail", "/djapi/fund/detail/008975", {}, {"fund_code": "008975"}),
        ("fund_growth", GROWTH, {"day": "ty"}, {"fund_code": "008975"}),
        (
            "fund_manager",
            "/djapi/fundx/base/fund/record/manager/list",
            {"fund_code": "008975", "post_status": "1"},
            {"fund_code": "008975"},
        ),
        (
            "fund_rank",
            RANK,
            {"type": "1", "order_by": "1m", "size": "50", "page": "1"},
            {},
        ),
        ("fund_rank_options", "/djapi/v3/filter/conf", {"type": "yield"}, {}),
        (
            "fund_search",
            SEARCH,
            {"key": "AT&T #1+2", "xq_access_token": "", "source": "index"},
            {"query": "AT&T #1+2"},
        ),
        (
            "fund_trade_date",
            "/djapi/fund/order/v2/trade_date",
            {"fd_code": "008975"},
            {"fund_code": "008975"},
        ),
        (
            "fund_auto_invest_return",
            "/djapi/fundx/autoinvest/quote/yield/list",
            {"fd_code": "008975"},
            {"fund_code": "008975"},
        ),
    ],
)
def test_requests_send_exactly_these_values_and_no_cookie(
    h: Harness, name: str, path: str, query: dict[str, str], params: dict[str, Any]
) -> None:
    h.upstream.route(DANJUAN, path, json_reply({"result_code": 99999, "message": "stop here"}))

    with pytest.raises(UpstreamError):
        h.call(name, **params)

    [sent] = h.upstream.sent
    assert (sent.host, sent.path, sent.params) == (DANJUAN, path, query)
    assert "cookie" not in sent.headers


@pytest.mark.parametrize("name", ONE_CODE_ENDPOINTS)
@pytest.mark.parametrize("code", ["8975", "SH008975", "00897a", 8975])
def test_a_code_that_is_not_six_digits_is_refused_before_any_request(
    h: Harness, name: str, code: object
) -> None:
    with pytest.raises(InvalidParams) as caught:
        h.call(name, fund_code=code)

    assert caught.value.field == "fund_code"
    assert h.upstream.sent == []


@pytest.mark.parametrize("name", ONE_CODE_ENDPOINTS)
def test_a_code_stays_a_six_digit_string_in_a_request(h: Harness, name: str) -> None:
    spec = REGISTRY[name]

    query = spec.build_query(spec.validate({"fund_code": " 008975 "}), datetime.now(SHANGHAI))

    assert "008975" in query.values()


@pytest.mark.sample("fund_info")
def test_fund_info_parses_units_and_dates(make: Callable[..., Harness]) -> None:
    fund = replayed(make).call("fund_info", fund_code="008975")

    assert isinstance(fund, FundInfo)
    assert REGISTRY["fund_info"].order is None
    assert (fund.fund_code, fund.name, fund.manager_name) == (
        "008975",
        "富国中证消费50ETF联接A",
        "示例经理",
    )
    assert (fund.fund_type, fund.type_desc) == ("5", "股票型-标准指数")
    # 1582473600000 ms is midnight of 2020-02-24 in Shanghai, still 02-23 in UTC.
    assert fund.ipo_start_date == date(2020, 2, 24)
    assert fund.ipo_end_date == date(2020, 3, 13)
    assert fund.found_date == date(2020, 3, 18)
    assert fund.total_shares == 755_000_000.0
    assert (fund.risk_level, fund.purchase_status, fund.subscribe_status) == (4, 1, 0)
    assert (fund.purchase_rate_pct, fund.purchase_discount) == (1.2, 0.1)
    assert (fund.nav_date, fund.unit_nav, fund.daily_pct) == (date(2026, 11, 4), 1.2048, -1.2702)
    assert fund.unit_nav is not None and fund.unit_nav > 0
    assert [tag.name for tag in fund.tags] == ["股票型-标准指数", "中高风险"]
    assert fund.benchmark_indexes[0].symbol == "CSI931139"
    assert fund.benchmark_indexes[0].name == "中证消费50"


def test_fund_info_leaves_out_prompts_and_ui_fields(make: Callable[..., Harness]) -> None:
    fund = replayed(make).call("fund_info", fund_code="008975")

    kept = fund.model_dump()
    for key in (
        "base_data_tip",
        "fir_header_base_data",
        "sec_header_base_data",
        "nav_tab_list",
        "trade_reason",
        "display_annual_performance",
        "growth_default_period",
        "ipo_start_count_down",
        "ipo_end_count_down",
    ):
        assert key not in kept
    assert fund.model_extra is not None and fund.model_extra["growth_day"] == "360"


def test_the_fund_info_fixture_has_the_placeholder_manager_in_every_copy() -> None:
    path = REPLAY_DIR / "fund_info" / "008975.json"
    body = json.loads(path.read_text(encoding="utf-8"))["body"]["data"]

    cells = {cell["data_name"]: cell["data_value_str"] for cell in body["sec_header_base_data"]}
    assert cells["基金经理"] == body["manager_name"] == "示例经理"


@pytest.mark.parametrize(
    ("sent", "shares"),
    [("1234.56万", 12_345_600.0), ("--", None), ("", None), (None, None)],
)
def test_fund_info_reads_every_form_of_the_share_count(
    h: Harness, sent: str | None, shares: float | None
) -> None:
    body = {"fd_code": "008975", "totshare": sent}
    h.upstream.route(DANJUAN, INFO, json_reply(data(body)))

    assert h.call("fund_info", fund_code="008975").total_shares == shares


def test_fund_info_of_a_fund_without_a_net_value_yet(h: Harness) -> None:
    h.upstream.route(DANJUAN, INFO, json_reply(data({"fd_code": "008975"})))

    fund = h.call("fund_info", fund_code="008975")

    assert (fund.nav_date, fund.unit_nav, fund.daily_pct, fund.tags) == (None, None, None, ())


def test_fund_info_of_an_unknown_code_is_not_found_and_keeps_the_upstream_text(
    h: Harness,
) -> None:
    h.upstream.route(DANJUAN, "/djapi/fund/000000", danjuan_error_reply(600001, "该基金暂不销售"))

    with pytest.raises(SymbolNotFound) as caught:
        h.call("fund_info", fund_code="000000")

    assert caught.value.endpoint == "fund_info"
    assert caught.value.upstream_code == "600001"
    assert "该基金暂不销售" in caught.value.message


def test_fund_info_without_a_data_key_is_not_found(h: Harness) -> None:
    h.upstream.route(DANJUAN, INFO, json_reply({"result_code": 0}))

    with pytest.raises(SymbolNotFound) as caught:
        h.call("fund_info", fund_code="008975")

    assert caught.value.endpoint == "fund_info"
    assert "008975" in caught.value.message


def test_fund_info_with_empty_data_is_upstream_changed(h: Harness) -> None:
    h.upstream.route(DANJUAN, INFO, json_reply(data({})))

    with pytest.raises(UpstreamChanged):
        h.call("fund_info", fund_code="008975")


def test_only_fund_info_treats_the_unknown_code_answer_as_not_found(h: Harness) -> None:
    h.upstream.route(DANJUAN, DERIVED, danjuan_error_reply(600001, "该基金暂不销售"))

    with pytest.raises(UpstreamError) as caught:
        h.call("fund_derived", fund_code="008975")

    assert caught.value.upstream_code == "600001"
    assert "该基金暂不销售" in caught.value.message


@pytest.mark.sample("fund_derived")
def test_fund_derived_parses_returns_ranks_and_the_time(make: Callable[..., Harness]) -> None:
    fund = replayed(make).call("fund_derived", fund_code="008975")

    assert isinstance(fund, FundDerived)
    assert REGISTRY["fund_derived"].order is None
    assert (fund.fund_code, fund.fund_type) == ("008975", "5")
    assert (fund.nav_date, fund.unit_nav, fund.acc_nav) == (date(2026, 11, 4), 1.2048, 1.2048)
    # The absolute change, in CNY: 1.2048 - 1.2203.
    assert fund.nav_change == -0.0155
    assert fund.daily_pct == -1.2702
    assert fund.return_1m_pct == 7.2458607798
    assert fund.return_since_inception_pct == 20.48
    assert (fund.rank_1m, fund.rank_1m_total) == (9, 4521)
    assert (fund.rank_since_inception, fund.rank_since_inception_total) == (1253, 4587)
    assert (fund.aip_return_1y_pct, fund.smart_aip_return_5y_pct) == (2.96, 0.37)
    assert fund.updated_at == datetime(2026, 11, 4, 19, 26, 24, 557000, tzinfo=SHANGHAI)
    assert fund.updated_at is not None
    assert fund.updated_at.utcoffset() == timedelta(hours=8)


def test_fund_derived_reads_markers_and_missing_keys_as_none(h: Harness) -> None:
    body = {
        "fd_code": "008975",
        "nav_growth": "4.0E-4",
        "nav_grl5y": "--",
        "srank_l5y": "--",
    }
    h.upstream.route(DANJUAN, DERIVED, json_reply(data(body)))

    fund = h.call("fund_derived", fund_code="008975")

    assert fund.nav_change == 0.0004
    assert (fund.return_5y_pct, fund.rank_5y, fund.rank_5y_total) == (None, None, None)
    assert (fund.return_2y_pct, fund.rank_3y, fund.updated_at) == (None, None, None)


def test_fund_derived_refuses_a_rank_it_cannot_read(h: Harness) -> None:
    h.upstream.route(DANJUAN, DERIVED, json_reply(data({"fd_code": "008975", "srank_l1y": "9"})))

    with pytest.raises(UpstreamChanged):
        h.call("fund_derived", fund_code="008975")


@pytest.mark.sample("fund_achievement")
def test_fund_achievement_merges_both_lists(make: Callable[..., Harness]) -> None:
    rows = replayed(make).call("fund_achievement", fund_code="008975")

    assert all(isinstance(row, FundPeriodPerformance) for row in rows)
    assert REGISTRY["fund_achievement"].order is None
    assert [(row.group, row.period) for row in rows] == [
        ("annual", "成立以来"),
        ("annual", "今年以来"),
        ("annual", "2025"),
        ("stage", "近1月"),
        ("stage", "近3月"),
        ("stage", "近6月"),
    ]
    first, month = rows[0], rows[3]
    # Returns come bare, drawdowns with a percent sign, ranks as rank/total: all percent points.
    assert (first.return_pct, first.max_drawdown_pct) == (20.48, 51.59)
    assert (first.standard_index_return_pct, first.standard_index_max_drawdown_pct) == (
        12.7901,
        45.6,
    )
    assert (first.rank, first.rank_total) == (1253, 4587)
    assert month.max_drawdown_pct is None
    assert month.return_pct == 7.2458607798


def test_fund_achievement_agrees_with_fund_derived(make: Callable[..., Harness]) -> None:
    h = replayed(make)

    rows = h.call("fund_achievement", fund_code="008975")
    fund = h.call("fund_derived", fund_code="008975")

    assert rows[0].return_pct == fund.return_since_inception_pct
    assert rows[3].return_pct == fund.return_1m_pct


@pytest.mark.sample("fund_asset")
def test_fund_asset_keeps_symbols_and_uses_net_assets(make: Callable[..., Harness]) -> None:
    asset = replayed(make).call("fund_asset", fund_code="008975")

    assert isinstance(asset, FundAsset)
    assert REGISTRY["fund_asset"].order is None
    assert asset.report_date == date(2026, 9, 30)
    assert asset.bond_pct_net_assets == 6.48
    assert [stock.symbol for stock in asset.stocks] == ["SH600519", "SH600887", "SH600690"]
    assert [bond.symbol for bond in asset.bonds] == ["019792", "019846"]
    top = asset.stocks[0]
    assert (top.name, top.code, top.pct_net_assets, top.industry) == (
        "贵州茅台",
        "600519",
        0.17,
        "食品饮料",
    )
    assert (top.change_vs_prev_quarter_pct, top.change_type) == (0.05, 1)
    assert sum(bond.pct_net_assets or 0 for bond in asset.bonds) == pytest.approx(6.48)
    # On net assets the classes do not sum to 100, so a sum check must not run here.
    classes = (
        asset.stock_pct_net_assets,
        asset.bond_pct_net_assets,
        asset.cash_pct_net_assets,
        asset.other_pct_net_assets,
    )
    assert sum(value or 0 for value in classes) == pytest.approx(118.29)


def test_fund_asset_leaves_out_quotes_at_fetch_time_and_ui_fields(
    make: Callable[..., Harness],
) -> None:
    asset = replayed(make).call("fund_asset", fund_code="008975")

    kept = asset.model_dump()
    for key in ("source_mark", "chart_list", "important_list_show"):
        assert key not in kept
    for key in ("current_price", "change_percentage", "xq_url", "percent_double"):
        assert key not in kept["stocks"][0]
    assert kept["stocks"][0]["amarket"] is True


@pytest.mark.sample("fund_detail")
def test_fund_detail_separates_the_two_percent_bases(make: Callable[..., Harness]) -> None:
    detail = replayed(make).call("fund_detail", fund_code="008975")

    assert isinstance(detail, FundDetail)
    assert REGISTRY["fund_detail"].order is None
    position = detail.position
    assert position.report_date == date(2026, 9, 30)
    assert (position.report_name, position.report_code) == ("第三季度报", "4")
    assert (position.total_assets, position.net_assets) == (1379824964.74, 1166490565.91)
    classes = (
        position.stock_pct_total_assets,
        position.bond_pct_total_assets,
        position.cash_pct_total_assets,
        position.other_pct_total_assets,
    )
    assert sum(value or 0 for value in classes) == pytest.approx(100, abs=0.01)
    # Holdings are on net assets, so they exceed their class on total assets.
    assert sum(bond.pct_net_assets or 0 for bond in position.bonds) == pytest.approx(6.48)
    assert position.bond_pct_total_assets == 5.48
    assert position.bonds[0].symbol == "019792"
    assert position.stocks[0].symbol == "SH600519"
    assert position.stocks[0].industry is None


def test_fund_detail_parses_fees_days_and_managers(make: Callable[..., Harness]) -> None:
    detail = replayed(make).call("fund_detail", fund_code="008975")

    fees = detail.fees
    assert (fees.subscribe_rate_pct, fees.purchase_rate_pct) == (1.0, 1.2)
    assert fees.redeem_rate_pct == 1.5
    assert (fees.purchase_discount, fees.subscribe_discount) == (0.1, 1.0)
    assert [(tier.value, tier.unit) for tier in fees.purchase_tiers] == [
        (1.2, "2"),
        (0.8, "2"),
        (1000.0, "1"),
    ]
    assert [tier.tier for tier in fees.other_fees] == ["基金管理费", "基金托管费"]
    days = detail.trade_days
    assert (days.buy_confirm_days, days.buy_query_days, days.buy_total_days) == (1, 1, 2)
    assert (days.sale_confirm_days, days.sale_query_days, days.sale_total_days) == (1, 3, 4)
    [manager] = detail.managers
    assert (manager.manager_id, manager.name, manager.work_years) == ("100000001", "示例经理", 16)
    assert manager.funds[0].fund_code == "021254"
    # A string date here, milliseconds in fund_manager.
    assert manager.funds[0].start_date == date(2026, 3, 17)
    assert manager.funds[0].tenure_return_pct == -8.8768


def test_fund_detail_leaves_out_quotes_and_duplicates(make: Callable[..., Harness]) -> None:
    kept = replayed(make).call("fund_detail", fund_code="008975").model_dump()

    position = kept["position"]
    for key in ("chart_list", "important_list_show", "end_date_str"):
        assert key not in position
    for key in ("current_price", "change_percentage", "xq_url", "percent_double"):
        assert key not in position["stocks"][0]
    assert "fd_code" not in kept["fees"]
    assert "fd_code" not in kept["trade_days"]


def test_fund_detail_renders_fee_tables_by_default_and_prose_on_request(
    make: Callable[..., Harness],
) -> None:
    h = replayed(make)

    text = text_of(h, "fund_detail", fund_code="008975")
    prose = text_of(h, "fund_detail", ["manager_name", "fund_name"], fund_code="008975")
    resume = text_of(h, "fund_detail", ["resume", "company_intro"], fund_code="008975")

    assert "# info: report_date=2026-09-30 total_assets=1379824964.74 " in text
    assert "buy_confirm_days=1 sale_total_days=4\n" in text
    assert "# section: purchase_tiers\ntier,value,unit\n0.0万<买入金额<100.0万,1.2,2\n" in text
    assert "# section: other_fees\ntier,value,unit\n基金管理费,0.5,2\n" in text
    assert "tables with none of these columns are left out: stocks (rows=3" in text
    assert "合成履历" not in text and "合成公司简介" not in text
    assert "manager_name,fund_name\n示例经理,示例基金1\n" in prose
    assert "合成履历" in resume and "合成公司简介" in resume
    assert "# untrusted upstream text begin" in resume


@pytest.mark.sample("fund_growth")
def test_fund_growth_is_columnar_in_percent_points_and_oldest_first(
    make: Callable[..., Harness],
) -> None:
    growth = replayed(make).call("fund_growth", fund_code="008975")

    assert isinstance(growth, FundGrowth)
    assert REGISTRY["fund_growth"].order == "oldest_first"
    assert (growth.fund_code, growth.period, growth.index_name) == ("008975", "ty", "中证消费50")
    assert growth.dates == (date(2025, 12, 31), date(2026, 1, 5), date(2026, 1, 6))
    assert growth.columns["nav"] == (1.2629, 1.2764, 1.2831)
    assert growth.columns["daily_pct"] == (-0.5042, 1.069, 0.5249)
    # The upstream sends fractions: 0.01069 is 1.069 percent points.
    assert growth.columns["cum_return_pct"] == (0.0, 1.069, 1.5995)
    assert growth.columns["index_cum_return_pct"] == (0.0, 1.1188, 1.6733)
    assert growth.columns["benchmark_cum_return_pct"] == (0.0, 1.0631, 1.5896)


@pytest.mark.parametrize("period", ["ty", "all"])
def test_fund_growth_cumulative_return_is_the_nav_ratio(
    make: Callable[..., Harness], period: str
) -> None:
    growth = replayed(make).call("fund_growth", fund_code="008975", period=period)

    navs = growth.columns["nav"]
    returns = growth.columns["cum_return_pct"]
    assert returns[0] == 0.0
    assert all(nav > 0 for nav in navs)
    for nav, value in zip(navs, returns, strict=True):
        assert value == pytest.approx((nav / navs[0] - 1) * 100, abs=1e-3)
    assert list(growth.dates) == sorted(set(growth.dates))


def test_fund_growth_daily_change_and_benchmark_hold_on_the_daily_window(
    make: Callable[..., Harness],
) -> None:
    growth = replayed(make).call("fund_growth", fund_code="008975", period="ty")

    navs = growth.columns["nav"]
    for (older, newer), pct in zip(pairwise(navs), growth.columns["daily_pct"][1:], strict=True):
        assert pct == pytest.approx((newer / older - 1) * 100, abs=1e-3)
    index = growth.columns["index_cum_return_pct"]
    benchmark = growth.columns["benchmark_cum_return_pct"]
    for value, line in zip(index, benchmark, strict=True):
        assert line == pytest.approx(0.95 * value, abs=1e-3)


def test_fund_growth_all_has_sampled_rows_and_no_change_on_the_oldest(
    make: Callable[..., Harness],
) -> None:
    growth = replayed(make).call("fund_growth", fund_code="008975", period="all")

    assert growth.period == "all"
    assert growth.dates[:2] == (date(2020, 3, 18), date(2020, 3, 27))
    assert growth.columns["daily_pct"][0] is None
    # The day's own change, though the row before is nine days older.
    assert growth.columns["daily_pct"][1] == 1.0256
    assert growth.columns["cum_return_pct"][-1] == 20.48


def test_fund_growth_says_long_windows_are_sampled() -> None:
    doc = FundGrowth.__doc__ or ""

    assert "sampled" in REGISTRY["fund_growth"].doc
    assert "抽样" in REGISTRY["fund_growth"].agent.summary
    assert "497 rows for 1603" in doc
    assert "drawdown" in doc


def test_fund_growth_takes_the_windows_the_upstream_lists(h: Harness) -> None:
    assert get_args(FundGrowthPeriod) == ("ty", "1m", "3m", "6m", "1y", "3y", "5y", "all")

    with pytest.raises(InvalidParams) as caught:
        h.call("fund_growth", fund_code="008975", period="2y")

    assert caught.value.field == "period"
    assert "all" in caught.value.valid_values
    assert h.upstream.sent == []


def test_fund_growth_renders_stats_over_the_whole_window(make: Callable[..., Harness]) -> None:
    text = text_of(replayed(make), "fund_growth", fund_code="008975")

    assert "# info: index_name=中证消费50\n" in text
    assert "# stats: points=3 first_date=2025-12-31 last_date=2026-01-06 nav_last=1.2831 " in text
    assert "date,nav,daily_pct,cum_return_pct,index_cum_return_pct,benchmark_cum_return_pct" in text
    assert "2026-01-05,1.2764,1.069,1.069,1.1188,1.0631\n" in text


def test_fund_growth_without_an_index_line_has_no_index_name(h: Harness) -> None:
    body = {"fund_nav_growth": [{"date": "2026-11-04", "nav": "1.1", "value": "0.0"}]}
    h.upstream.route(DANJUAN, GROWTH, json_reply(data(body)))

    growth = h.call("fund_growth", fund_code="008975")

    assert growth.index_name is None
    assert growth.header() == {}
    assert growth.columns["index_cum_return_pct"] == (None,)


@pytest.mark.sample("fund_manager")
def test_fund_manager_parses_tenure(make: Callable[..., Harness]) -> None:
    [manager] = replayed(make).call("fund_manager", fund_code="008975")

    assert isinstance(manager, FundManager)
    assert REGISTRY["fund_manager"].order is None
    assert (manager.manager_id, manager.name, manager.work_years) == ("100000001", "示例经理", 16)
    # 1584460800000 ms is midnight of 2020-03-18 in Shanghai, still 03-17 in UTC.
    assert manager.start_date == date(2020, 3, 18)
    assert (manager.status, manager.role, manager.tenure_text) == (1, 1, "6年233天")
    assert (manager.tenure_return_pct, manager.annualized_return_pct) == (20.48, 2.78)
    assert manager.managed_assets == 361.85


@pytest.mark.sample("fund_rank")
def test_fund_rank_reads_one_page_best_first(make: Callable[..., Harness]) -> None:
    page = replayed(make).call("fund_rank", fund_type="1", order_by="1m", page_size=3)

    assert REGISTRY["fund_rank"].order == "rank"
    assert (page.page, page.page_size, page.total, page.last_page) == (1, 3, 1000, 334)
    assert page.capped is False
    assert page.next_params == {"fund_type": "1", "order_by": "1m", "page": 2, "page_size": 3}
    top = page.items[0]
    assert isinstance(top, FundRankItem)
    # A leading zero survives: the code is never read as a number.
    assert (top.fund_code, top.name) == ("001628", "招商体育文化休闲")
    assert (top.return_pct, top.nav, top.fund_type, top.product) == (10.0188, 1.757, "1", "fund")
    returns = [row.return_pct for row in page]
    assert returns == sorted(returns, reverse=True)


def rank_reply(request: httpx2.Request) -> httpx2.Response:
    """A ranking of 1000 funds, cut into pages by the requested size, as measured."""
    page, size = int(request.url.params["page"]), int(request.url.params["size"])
    rows = [
        {"fd_code": f"{index:06d}", "fd_name": f"基金{index}", "yield": "1.0", "unit_nav": "1.0"}
        for index in range((page - 1) * size, page * size)
    ]
    pages = 1000 // size
    body = {
        "items": rows,
        "current_page": page,
        "size": size,
        "total_items": 1000,
        "total_pages": pages,
    }
    return httpx2.Response(200, json=data(body))


def test_fund_rank_pages_walk_all_1000_rows_with_no_limit(h: Harness) -> None:
    h.upstream.route(DANJUAN, RANK, rank_reply)

    pages, error = h.walk(REGISTRY["fund_rank"], fund_type="3", page_size=100)

    assert error is None
    assert [len(page) for page in pages] == [100] * 10
    assert not any(page.capped for page in pages)
    assert pages[-1].next_params is None
    assert len({row.fund_code for page in pages for row in page}) == 1000
    sent = h.upstream.requests(DANJUAN, RANK)
    assert [request.params["page"] for request in sent] == [str(n) for n in range(1, 11)]
    assert {request.params["size"] for request in sent} == {"100"}


def test_fund_rank_pages_drop_a_fund_repeated_across_pages(h: Harness) -> None:
    def page(number: int, codes: list[str]) -> dict[str, Any]:
        rows = [{"fd_code": code, "yield": "1.0"} for code in codes]
        return data(
            {
                "items": rows,
                "current_page": number,
                "size": 2,
                "total_items": 4,
                "total_pages": 2,
            }
        )

    h.upstream.route(
        DANJUAN,
        RANK,
        json_reply(page(1, ["000001", "000002"])),
        json_reply(page(2, ["000002", "000003"])),
    )

    pages, error = h.walk(REGISTRY["fund_rank"], page_size=2)

    assert error is None
    assert [row.fund_code for walked in pages for row in walked] == ["000001", "000002", "000003"]


def test_fund_rank_says_it_serves_at_most_1000_rows() -> None:
    spec = REGISTRY["fund_rank"]

    assert spec.pagination is not None and spec.pagination.total_cap is None
    assert "at most 1000 rows" in spec.doc
    assert "1000" in spec.agent.summary


def test_fund_rank_takes_the_types_and_windows_the_upstream_lists(h: Harness) -> None:
    assert get_args(FundRankType) == ("1", "3", "2", "5", "11")
    assert get_args(FundRankOrder) == (
        "td",
        "1w",
        "1m",
        "3m",
        "6m",
        "ty",
        "1y",
        "2y",
        "3y",
        "5y",
        "base",
    )

    with pytest.raises(InvalidParams) as wrong_type:
        h.call("fund_rank", fund_type="4")
    with pytest.raises(InvalidParams) as wrong_window:
        h.call("fund_rank", order_by="1d")
    with pytest.raises(InvalidParams) as too_large:
        h.call("fund_rank", page_size=101)

    assert wrong_type.value.field == "fund_type"
    assert wrong_window.value.field == "order_by"
    assert too_large.value.field == "page_size"
    assert h.upstream.sent == []


def test_fund_rank_renders_a_page_with_the_next_call(make: Callable[..., Harness]) -> None:
    text = text_of(replayed(make), "fund_rank", order_by="1m", page_size=3)

    assert "# page: page=1 page_size=3 total=1000 last_page=334\n" in text
    assert "fund_code,name,return_pct,nav\n001628,招商体育文化休闲,10.0188,1.757\n" in text
    assert text.endswith('# next: {"fund_type":"1","order_by":"1m","page":2,"page_size":3}')


@pytest.mark.sample("fund_rank_options")
def test_fund_rank_options_lists_codes_fund_rank_takes(make: Callable[..., Harness]) -> None:
    options = replayed(make).call("fund_rank_options")

    assert isinstance(options, FundRankOptions)
    assert REGISTRY["fund_rank_options"].order is None
    assert [option.code for option in options.fund_types] == list(get_args(FundRankType))
    assert {option.code for option in options.order_by} <= set(get_args(FundRankOrder))
    assert len(options) == len(options.fund_types) + len(options.order_by)
    stock = options.fund_types[0]
    assert (stock.name, stock.code, stock.default) == ("股票型", "1", True)
    [default_window] = [option for option in options.order_by if option.default]
    assert default_window.code == "2y"


def test_fund_rank_options_render_as_two_tables(make: Callable[..., Harness]) -> None:
    text = text_of(replayed(make), "fund_rank_options")

    assert "# section: fund_types\ncode,name,default\n1,股票型,true\n" in text
    assert "# section: order_by\ncode,name,default\ntd,日涨幅,false\n" in text
    assert text.endswith("# next: none")


@pytest.mark.sample("fund_search")
def test_fund_search_finds_funds_by_name(make: Callable[..., Harness]) -> None:
    rows = replayed(make).call("fund_search", query="富国中证消费50")

    assert all(isinstance(row, FundSearchResult) for row in rows)
    assert REGISTRY["fund_search"].order == "rank"
    assert [(row.code, row.kind) for row in rows] == [("008975", "fund"), ("008976", "fund")]
    assert (rows[0].name, rows[0].result_id) == ("富国中证消费50ETF联接A", 8695)
    assert "store_status" not in rows[0].model_dump()


def test_fund_search_can_bring_advisory_portfolios(make: Callable[..., Harness]) -> None:
    rows = replayed(make).call("fund_search", query="消费")

    assert {row.kind for row in rows} == {"ic"}
    assert rows[0].code == "TIAA026061"


def test_fund_search_missing_parameter_answer_is_an_upstream_error(h: Harness) -> None:
    h.upstream.route(DANJUAN, SEARCH, danjuan_error_reply(200007, "api参数缺少"))

    with pytest.raises(UpstreamError) as caught:
        h.call("fund_search", query="消费")

    assert caught.value.upstream_code == "200007"
    assert "api参数缺少" in caught.value.message


@pytest.mark.parametrize("query", ["", "   ", "基" * 51])
def test_fund_search_refuses_an_empty_or_long_query(h: Harness, query: str) -> None:
    with pytest.raises(InvalidParams) as caught:
        h.call("fund_search", query=query)

    assert caught.value.field == "query"
    assert h.upstream.sent == []


@pytest.mark.sample("fund_trade_date")
def test_fund_trade_date_keeps_month_day_text(make: Callable[..., Harness]) -> None:
    dates = replayed(make).call("fund_trade_date", fund_code="008975")

    assert isinstance(dates, FundTradeDate)
    assert REGISTRY["fund_trade_date"].order is None
    assert (dates.buy_confirm, dates.buy_query) == ("11-06", "11-09")
    assert dates.sale_confirm == "11-06"
    assert (dates.sale_query, dates.sale_to_cash_query) == ("11-11 12:00前", "11-09 18:00前")
    assert dates.can_pay_with_wallet is True
    assert dates.can_sell_to_wallet is True
    assert dates.can_convert is True
    assert dates.can_sell_to_cash is True
    assert "no year" in REGISTRY["fund_trade_date"].doc
    assert "no year" in (FundTradeDate.__doc__ or "")


@pytest.mark.sample("fund_auto_invest_return")
def test_fund_auto_invest_return_parses_settings_and_windows(
    make: Callable[..., Harness],
) -> None:
    result = replayed(make).call("fund_auto_invest_return", fund_code="008975")

    assert isinstance(result, FundAutoInvestReturn)
    assert REGISTRY["fund_auto_invest_return"].order is None
    assert (result.duration, result.strategy, result.smart_strategy) == ("3Y", "normal", "mv")
    assert (result.cycle, result.amount) == ("1", 100.0)
    assert [(w.window, w.return_pct, w.smart_return_max_pct) for w in result.windows] == [
        ("近1年", 3.2, 4.11),
        ("近2年", 0.68, 1.74),
        ("近3年", 1.84, 3.39),
    ]
    assert result.model_extra is not None and "fd_code" not in result.model_extra


def test_fund_auto_invest_return_renders_the_settings_before_the_table(
    make: Callable[..., Harness],
) -> None:
    text = text_of(replayed(make), "fund_auto_invest_return", fund_code="008975")

    assert "# info: duration=3Y strategy=normal smart_strategy=mv cycle=1 amount=100\n" in text
    assert "window,return_pct,smart_return_max_pct\n近1年,3.2,4.11\n" in text


def test_fund_asset_renders_the_classes_before_the_tables(make: Callable[..., Harness]) -> None:
    text = text_of(replayed(make), "fund_asset", fund_code="008975")

    assert "# info: report_date=2026-09-30 stock_pct_net_assets=0.72 " in text
    assert "# section: stocks\nname,symbol,pct_net_assets,industry,change_vs_prev_quarter" in text
    assert "贵州茅台,SH600519,0.17,食品饮料,0.05\n" in text
    assert "# section: bonds\nname,symbol,pct_net_assets\n25国债19,019792,5.79\n" in text


def test_a_fund_record_renders_its_default_fields_without_prose(
    make: Callable[..., Harness],
) -> None:
    text = text_of(replayed(make), "fund_info", fund_code="008975")

    assert "total_shares: 755000000\n" in text
    assert "nav_date: 2026-11-04\n" in text
    assert "合成的投资策略文本" not in text


def test_the_nav_history_fixtures_hold_the_value_and_date_checks(
    make: Callable[..., Harness],
) -> None:
    pages, error = replayed(make).walk(REGISTRY["fund_nav_history"], fund_code="008975")

    assert error is None
    rows = [row for page in pages for row in page]
    assert all(row.nav > 0 for row in rows)
    assert all(newer.date > older.date for newer, older in pairwise(rows))
    for newer, older in pairwise(pages[0].items):
        assert newer.daily_pct == round((newer.nav / older.nav - 1) * 100, 2)
