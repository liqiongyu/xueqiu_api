"""Share changes, executives, top holders and the revenue breakdown, on both clients."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from xueqiu_api import REGISTRY, InvalidParams, SymbolNotFound, UpstreamChanged
from xueqiu_api._render import compact_json, render
from xueqiu_api.endpoints.f10_executive_trades import ExecutiveTrade
from xueqiu_api.endpoints.f10_executives import Executive
from xueqiu_api.endpoints.f10_share_changes import ShareChange
from xueqiu_api.endpoints.f10_top_holders import TopHolder, TopHolders
from xueqiu_api.endpoints.finance_business import RevenueBreakdown

from .harness import Harness, fixture_reply, json_reply

STOCK = "stock.xueqiu.com"
SHARE_CHANGES = "/v5/stock/f10/cn/shareschg.json"
EXECUTIVES = "/v5/stock/f10/cn/skholder.json"
EXECUTIVE_TRADES = "/v5/stock/f10/cn/skholderchg.json"
TOP_HOLDERS = "/v5/stock/f10/cn/top_holders.json"
BUSINESS_CN = "/v5/stock/finance/cn/business.json"
BUSINESS_US = "/v5/stock/finance/us/business.json"
LISTS = {
    "f10_share_changes": SHARE_CHANGES,
    "f10_executives": EXECUTIVES,
    "f10_executive_trades": EXECUTIVE_TRADES,
}
CN_ONLY = [*LISTS, "f10_top_holders"]
SHANGHAI = ZoneInfo("Asia/Shanghai")


def ok(data: Any) -> Any:
    return json_reply({"data": data, "error_code": 0, "error_description": ""})


def rendered(
    h: Harness, name: str, *, fields: list[str] | None = None, limit: int = 100, **params: Any
) -> str:
    spec = REGISTRY[name]
    return render(
        spec,
        spec.validate(params),
        h.call(name, **params),
        fields=fields,
        limit=limit,
        max_chars=4000,
        fetched_at=datetime(2026, 11, 5, 2, 0, tzinfo=SHANGHAI),
        next_call=lambda following, offset: compact_json({**following, "offset": offset}),
    )


@pytest.mark.parametrize(
    ("name", "symbol"),
    [(name, symbol) for name in CN_ONLY for symbol in ("00700", "AAPL")]
    + [("finance_business", "00700")],
)
def test_a_symbol_of_another_market_is_rejected_without_a_request(
    h: Harness, name: str, symbol: str
) -> None:
    with pytest.raises(InvalidParams) as caught:
        h.call(name, symbol=symbol)

    assert caught.value.field == "symbol"
    assert h.upstream.sent == []


@pytest.mark.parametrize("name", list(LISTS))
def test_a_list_endpoint_without_items_is_upstream_changed(h: Harness, name: str) -> None:
    h.upstream.route(STOCK, LISTS[name], ok({}))

    with pytest.raises(UpstreamChanged):
        h.call(name, symbol="SH600000")


@pytest.mark.parametrize("name", list(LISTS))
def test_a_list_endpoint_with_no_rows_returns_an_empty_list(h: Harness, name: str) -> None:
    h.upstream.route(STOCK, LISTS[name], ok({"items": []}))

    assert h.call(name, symbol="SH600000") == []


@pytest.mark.sample("f10_share_changes")
def test_f10_share_changes_parses_dates_and_whole_shares(h: Harness) -> None:
    h.upstream.route(STOCK, SHARE_CHANGES, fixture_reply("f10_share_changes/sh600000"))

    rows = h.call("f10_share_changes", symbol="600000", count=3)

    [sent] = h.upstream.requests(STOCK)
    assert "shareschg" in sent.path
    assert sent.params == {"symbol": "SH600000", "count": "3"}
    assert all(isinstance(row, ShareChange) for row in rows)
    assert REGISTRY["f10_share_changes"].order == "newest_first"
    # Midnight in Shanghai: read in UTC, the first date would be 2025-10-26.
    assert [row.chg_date for row in rows] == [
        date(2025, 10, 27),
        date(2025, 10, 24),
        date(2025, 10, 23),
    ]
    assert rows[0].total_shares == 33305838300
    assert rows[0].float_shares == 33305838300
    assert rows[0].chg_reason == "可转债转股"


def test_f10_share_changes_sends_50_by_default_and_refuses_more_than_100(h: Harness) -> None:
    h.upstream.route(STOCK, SHARE_CHANGES, fixture_reply("f10_share_changes/sh600000"))

    h.call("f10_share_changes", symbol="SH600000")

    assert h.upstream.requests(STOCK)[0].params["count"] == "50"
    with pytest.raises(InvalidParams):
        h.call("f10_share_changes", symbol="SH600000", count=101)


def test_f10_share_changes_needs_restricts_to_be_a_list(h: Harness) -> None:
    h.upstream.route(STOCK, SHARE_CHANGES, ok({"items": [], "restricts": {"unexpected": 1}}))

    with pytest.raises(UpstreamChanged):
        h.call("f10_share_changes", symbol="SH600000")


@pytest.mark.sample("f10_executives")
def test_f10_executives_parses_dates_and_keeps_the_upstream_order(h: Harness) -> None:
    h.upstream.route(STOCK, EXECUTIVES, fixture_reply("f10_executives/sh600000"))

    rows = h.call("f10_executives", symbol="SH600000")

    [sent] = h.upstream.requests(STOCK)
    assert sent.params == {"symbol": "SH600000"}
    assert all(isinstance(row, Executive) for row in rows)
    assert REGISTRY["f10_executives"].order == "rank"
    assert [row.personal_name for row in rows] == ["示例董事长", "示例副董事长", "示例独立董事"]
    assert rows[0].position_name == "董事长、执行董事"
    # Midnight in Shanghai: read in UTC, this would be 2024-02-06.
    assert rows[0].employ_date == date(2024, 2, 7)
    assert rows[0].employ_ed is None
    assert rows[0].annual_salary == 900000.0
    assert rows[1].held_num == 200000
    assert rows[0].resume_cn == "合成简历一"


def test_f10_executives_render_the_resume_only_when_asked_and_fenced(h: Harness) -> None:
    h.upstream.route(STOCK, EXECUTIVES, fixture_reply("f10_executives/sh600000"))

    by_default = rendered(h, "f10_executives", symbol="SH600000")
    asked = rendered(h, "f10_executives", fields=["personal_name", "resume_cn"], symbol="SH600000")

    assert "合成简历一" not in by_default
    assert "personal_name,position_name,employ_date,held_num,annual_salary" in by_default
    assert "示例董事长,董事长、执行董事,2024-02-07,,900000" in by_default
    assert "# untrusted upstream text begin\npersonal_name,resume_cn" in asked
    assert "示例董事长,合成简历一" in asked


@pytest.mark.sample("f10_executive_trades")
def test_f10_executive_trades_parses_shares_prices_and_dates(h: Harness) -> None:
    h.upstream.route(STOCK, EXECUTIVE_TRADES, fixture_reply("f10_executive_trades/sh600000"))

    rows = h.call("f10_executive_trades", symbol="SH600000")

    [sent] = h.upstream.requests(STOCK)
    assert sent.params == {"symbol": "SH600000"}
    assert all(isinstance(row, ExecutiveTrade) for row in rows)
    assert REGISTRY["f10_executive_trades"].order == "newest_first"
    # Midnight in Shanghai: read in UTC, the first date would be 2026-11-02.
    assert [row.chg_date for row in rows] == [
        date(2026, 11, 3),
        date(2026, 11, 3),
        date(2026, 11, 2),
    ]
    assert rows[0].manage_name == "示例高管一"
    assert rows[0].chg_shares_num == 22500
    assert rows[0].trans_avg_price == 9.92


def test_f10_executive_trades_keeps_a_negative_change_and_new_keys(h: Harness) -> None:
    row = {"chg_date": 1793635200000, "manage_name": "示例高管", "chg_shares_num": -100, "new": 1}
    h.upstream.route(STOCK, EXECUTIVE_TRADES, ok({"items": [row]}))

    [trade] = h.call("f10_executive_trades", symbol="SH600000")

    assert trade.chg_shares_num == -100
    assert trade.model_extra == {"new": 1}


def test_f10_executive_trades_explains_an_empty_list(h: Harness) -> None:
    h.upstream.route(STOCK, EXECUTIVE_TRADES, ok({"items": []}))

    assert "# note: no rows: no share change" in rendered(
        h, "f10_executive_trades", symbol="SH600000"
    )


@pytest.mark.sample("f10_top_holders")
def test_f10_top_holders_reads_the_latest_period_and_percent_points(h: Harness) -> None:
    h.upstream.route(STOCK, TOP_HOLDERS, fixture_reply("f10_top_holders/sh600000"))

    result = h.call("f10_top_holders", symbol="600000")

    [sent] = h.upstream.requests(STOCK)
    assert sent.params == {"symbol": "SH600000", "circula": "1"}
    assert isinstance(result, TopHolders)
    assert REGISTRY["f10_top_holders"].order == "rank"
    assert result.symbol == "SH600000"
    assert len(result) == 3
    assert result.report_date == date(2026, 9, 30)
    assert result.report_name == "2026三季报"
    assert [option.report_date for option in result.report_periods] == [
        date(2026, 9, 30),
        date(2026, 6, 30),
        date(2026, 3, 31),
    ]
    first = result.holders[0]
    assert (first.holder_name, first.held_num) == ("上海国际集团有限公司", 7086834641)
    # Percent points as sent, not a fraction to multiply.
    assert first.held_ratio == 21.28
    assert first.held_chg_pct == 0.0
    assert result.total is not None
    assert result.total.held_num == 24873575418
    assert result.total.held_ratio == 74.6823
    assert result.total.held_chg_pct == -0.25
    assert result.quit == ()


@pytest.mark.sample("f10_top_holders")
def test_f10_top_holders_keeps_new_and_departed_holders(h: Harness) -> None:
    h.upstream.route(STOCK, TOP_HOLDERS, fixture_reply("f10_top_holders/sh688517"))

    result = h.call("f10_top_holders", symbol="SH688517")

    assert [holder.held_chg_pct for holder in result.holders] == [0.0, 55.04, None]
    assert [(gone.holder_name, gone.holder_rank) for gone in result.quit] == [
        ("示例退出股东一", 5),
        ("示例退出股东二", 8),
    ]


@pytest.mark.parametrize("param", [{"scope": "all"}, {"report_date": "2026-06-30"}])
def test_f10_top_holders_offers_no_scope_and_no_report_date(
    h: Harness, param: dict[str, str]
) -> None:
    with pytest.raises(InvalidParams):
        h.call("f10_top_holders", symbol="SH600000", **param)

    assert h.upstream.sent == []


def test_f10_top_holders_doc_says_the_denominator_is_unverified() -> None:
    spec = REGISTRY["f10_top_holders"]

    assert "unverified" in spec.doc
    assert "未经核实" in spec.agent.summary
    assert "unverified" in (TopHolder.model_fields["held_ratio"].description or "")


def test_f10_top_holders_without_rows_is_falsy(h: Harness) -> None:
    h.upstream.route(STOCK, TOP_HOLDERS, ok({"times": [], "total": None, "quit": [], "items": []}))

    result = h.call("f10_top_holders", symbol="SH600000")

    assert not result
    assert result.report_date is None
    assert result.report_name is None


def test_f10_top_holders_without_items_is_upstream_changed(h: Harness) -> None:
    h.upstream.route(STOCK, TOP_HOLDERS, ok({"times": [], "quit": []}))

    with pytest.raises(UpstreamChanged):
        h.call("f10_top_holders", symbol="SH600000")


def test_f10_top_holders_renders_totals_and_periods_in_the_header(h: Harness) -> None:
    h.upstream.route(STOCK, TOP_HOLDERS, fixture_reply("f10_top_holders/sh600000"))

    lines = rendered(h, "f10_top_holders", symbol="SH600000").splitlines()

    assert lines[1] == (
        "# info: report_date=2026-09-30 report_name=2026三季报 total_held_num=24873575418 "
        'total_held_ratio=74.6823 total_held_chg_pct=-0.25 report_periods=["2026三季报","2026中报",'
        '"2026一季报"]'
    )
    assert "holder_name,held_num,held_ratio,held_chg_pct" in lines
    assert "上海国际集团有限公司,7086834641,21.28,0" in lines
    assert not any("empty sections" in line or "# section" in line for line in lines)


def test_f10_top_holders_renders_departed_holders_as_their_own_table(h: Harness) -> None:
    h.upstream.route(STOCK, TOP_HOLDERS, fixture_reply("f10_top_holders/sh688517"))

    lines = rendered(h, "f10_top_holders", symbol="SH688517").splitlines()

    gone = lines.index("# section: quit")
    assert lines.index("# section: holders") < gone
    assert lines[gone + 1 : gone + 3] == [
        "holder_name,held_num,held_ratio",
        "示例退出股东一,1838534,1.35",
    ]


def test_f10_top_holders_limit_reaches_the_departed_holders_with_an_offset(h: Harness) -> None:
    h.upstream.route(STOCK, TOP_HOLDERS, fixture_reply("f10_top_holders/sh688517"))

    lines = rendered(h, "f10_top_holders", limit=3, symbol="SH688517").splitlines()

    assert "示例退出股东一" not in "\n".join(lines)
    assert lines[-1] == '# next: {"symbol":"SH688517","offset":3}'


@pytest.mark.sample("finance_business")
def test_finance_business_turns_fractions_into_percent_points(h: Harness) -> None:
    h.upstream.route(STOCK, BUSINESS_CN, fixture_reply("finance_business/sh600000"))

    result = h.call("finance_business", symbol="600000", count=2)

    [sent] = h.upstream.requests(STOCK)
    assert sent.params == {"symbol": "SH600000", "count": "2"}
    assert isinstance(result, RevenueBreakdown)
    assert REGISTRY["finance_business"].order == "newest_first"
    assert (result.symbol, result.name, result.currency) == ("SH600000", "浦发银行", "CNY")
    assert len(result) == 2
    latest, annual = result.periods
    # Midnight in Shanghai: read in UTC, this would be 2026-06-29.
    assert (latest.report_date, latest.report_name) == (date(2026, 6, 30), "2026中报")
    assert [group.class_standard for group in latest.groups] == [1, 2, 3]
    first = latest.groups[0].segments[0]
    assert first.project_announced_name == "贷款利息收入"
    assert first.prime_operating_income == 90819000000.0
    assert first.income_ratio == 55.75
    assert first.gross_profit_rate is None
    assert latest.groups[2].segments[0].gross_profit_rate == pytest.approx(58.84, abs=0.01)
    # The denominator differs between groups, so a share can exceed 100.
    assert annual.groups[0].segments[0].income_ratio == 107.05


def test_finance_business_of_an_unknown_code_is_symbol_not_found(h: Harness) -> None:
    body: dict[str, Any] = {"data": {"quote_name": None, "list": []}, "error_code": 0}
    h.upstream.route(STOCK, BUSINESS_CN, json_reply(body))

    with pytest.raises(SymbolNotFound) as caught:
        h.call("finance_business", symbol="SH999999")

    assert caught.value.endpoint == "finance_business"
    assert "SH999999" in caught.value.message


@pytest.mark.sample("finance_business")
def test_finance_business_reads_us_report_dates_in_shanghai(h: Harness) -> None:
    h.upstream.route(STOCK, BUSINESS_US, fixture_reply("finance_business/aapl"))

    result = h.call("finance_business", symbol="AAPL", count=2)

    fiscal_year, fourth_quarter = result.periods
    # 2026-09-26 at midnight in Shanghai is still 09-25 in New York.
    assert fiscal_year.report_date == fourth_quarter.report_date == date(2026, 9, 26)
    assert (fiscal_year.report_name, fourth_quarter.report_name) == ("2026年FY", "2026年Q4")
    assert result.currency == "USD"
    assert fiscal_year.model_extra is not None
    assert fiscal_year.model_extra["ed"] == "1790352000006"
    assert fiscal_year.model_extra["report_annual"] == 2026
    assert fiscal_year.groups[1].segments[0].income_ratio == 53.07
    assert fiscal_year.groups[1].segments[0].gross_profit_rate is None


def test_finance_business_keeps_the_prose_out_of_the_text(h: Harness) -> None:
    h.upstream.route(STOCK, BUSINESS_CN, fixture_reply("finance_business/sh600000"))

    result = h.call("finance_business", symbol="SH600000")
    text = rendered(h, "finance_business", symbol="SH600000")

    assert result.model_extra["main_operation_business"] == "合成主营业务"
    assert result.model_extra["currency_code"] == "人民币"
    assert "合成主营业务" not in text
    assert "合成经营范围" not in text


def test_finance_business_renders_one_row_per_segment(h: Harness) -> None:
    h.upstream.route(STOCK, BUSINESS_CN, fixture_reply("finance_business/sh600000"))

    lines = rendered(h, "finance_business", symbol="SH600000").splitlines()

    assert lines[1] == "# info: name=浦发银行 currency=CNY industry=银行"
    assert any(line.endswith("rows=18") for line in lines if line.startswith("# meta:"))
    assert (
        "report_date,report_name,class_standard,project_announced_name,prime_operating_income,"
        "income_ratio,gross_profit_rate"
    ) in lines
    assert "2026-06-30,2026中报,1,贷款利息收入,90819000000,55.75," in lines
    assert "2026-06-30,2026中报,3,总行,29190000000,31.13,58.84" in lines
    assert "2025-12-31,2025年报,1,贷款利息收入,186233000000,107.05," in lines


def test_finance_business_limit_continues_with_an_offset(h: Harness) -> None:
    h.upstream.route(STOCK, BUSINESS_CN, fixture_reply("finance_business/sh600000"))

    lines = rendered(h, "finance_business", limit=5, symbol="SH600000").splitlines()

    assert lines[-1] == '# next: {"symbol":"SH600000","count":2,"offset":5}'


def test_finance_business_with_no_periods_is_falsy(h: Harness) -> None:
    h.upstream.route(STOCK, BUSINESS_CN, ok({"quote_name": "示例", "list": []}))

    assert not h.call("finance_business", symbol="SH600000")


def test_finance_business_without_a_list_is_upstream_changed(h: Harness) -> None:
    h.upstream.route(STOCK, BUSINESS_CN, ok({"quote_name": "示例"}))

    with pytest.raises(UpstreamChanged):
        h.call("finance_business", symbol="SH600000")


def test_finance_business_caps_count_at_20(h: Harness) -> None:
    with pytest.raises(InvalidParams):
        h.call("finance_business", symbol="SH600000", count=21)

    assert h.upstream.sent == []
