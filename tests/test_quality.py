"""Quality checks of bars and statements: each check passes and fails, nulls stay null."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from xueqiu_api._finance import Financials, LineItem, ReportPeriod
from xueqiu_api.endpoints.kline import ENDPOINT as KLINE
from xueqiu_api.endpoints.kline import Kline
from xueqiu_api.quality import (
    MAX_FINDINGS,
    CheckResult,
    QualityReport,
    Statement,
    check_financials,
    check_kline,
)
from xueqiu_api.trading_calendar import TradingCalendar

from .harness import replay_body

SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")
FETCHED_AT = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)
Cell = float | int | None


def replay_kline(name: str, **params: Any) -> Kline:
    data = replay_body(name)["data"]
    return KLINE.parse(data, KLINE.validate({"symbol": data["symbol"], **params}))


@pytest.fixture
def stock() -> Kline:
    """Three unadjusted bars of an A share, 2026-11-02 to 2026-11-04, without a defect."""
    return replay_kline("kline/sh600000_day", adjust="normal")


def with_cell(kline: Kline, column: str, position: int, value: Cell) -> Kline:
    cells = list(kline.columns[column])
    cells[position] = value
    return replace(kline, columns={**kline.columns, column: tuple(cells)})


def result(report: QualityReport, check: str) -> CheckResult:
    return next(item for item in report.results if item.check == check)


def calendar_of(*days: date, market: Any = "CN") -> TradingCalendar:
    return TradingCalendar(market, "SH000001", tuple(days), FETCHED_AT)


def daily_bars(symbol: str, days: Iterable[date], **columns: tuple[Cell, ...]) -> Kline:
    stamps = tuple(datetime(day.year, day.month, day.day, tzinfo=SHANGHAI) for day in days)
    return Kline(symbol, "day", "normal", stamps, columns)


def test_a_sound_series_passes_every_check_that_can_run(stock: Kline) -> None:
    days = tuple(stamp.date() for stamp in stock.timestamps)
    report = check_kline(stock, calendar=calendar_of(*days), as_of=FETCHED_AT)

    assert report.subject == "SH600000 day normal"
    assert report.ok
    assert {item.check: item.status for item in report.results} == {
        "kline.timestamps_increasing": "pass",
        "kline.no_duplicate_day": "pass",
        "kline.daily_at_midnight": "pass",
        "kline.ohlc_order": "pass",
        "kline.price_positive": "pass",
        "kline.volume_nonneg": "pass",
        "kline.vwap_in_range": "pass",
        "kline.chg_matches_close": "pass",
        "kline.gaps_vs_calendar": "pass",
        "kline.last_bar_partial": "pass",
        "kline.null_policy": "pass",
    }
    assert all(item.checked > 0 and not item.findings for item in report.results)


@pytest.mark.parametrize(
    ("check", "column", "position", "value", "severity"),
    [
        ("kline.ohlc_order", "close", 1, 10.25, "warning"),
        ("kline.ohlc_order", "low", 1, 10.2, "warning"),
        ("kline.price_positive", "low", 0, 0.0, "error"),
        ("kline.price_positive", "open", 2, -1.0, "error"),
        ("kline.volume_nonneg", "volume", 0, -1, "error"),
        ("kline.volume_nonneg", "amount", 2, -1.0, "error"),
        ("kline.vwap_in_range", "amount", 0, 2354245134.0, "warning"),
        ("kline.chg_matches_close", "chg", 1, 0.05, "warning"),
        ("kline.chg_matches_close", "percent", 1, 0.32, "warning"),
        ("kline.null_policy", "close", 0, None, "warning"),
        ("kline.null_policy", "volume", 0, None, "warning"),
    ],
)
def test_one_changed_cell_fails_its_check(
    stock: Kline, check: str, column: str, position: int, value: Cell, severity: str
) -> None:
    report = check_kline(with_cell(stock, column, position, value))
    failed = result(report, check)

    assert failed.status == "fail"
    assert failed.findings[0].severity == severity
    assert failed.findings[0].check == check
    assert failed.total_findings == 1
    assert report.ok is (severity != "error")


@pytest.mark.parametrize(
    ("check", "shift"),
    [
        ("kline.timestamps_increasing", timedelta(days=5)),
        ("kline.no_duplicate_day", timedelta(days=1, hours=1)),
        ("kline.daily_at_midnight", timedelta(hours=1)),
    ],
)
def test_a_changed_time_fails_its_check(stock: Kline, check: str, shift: timedelta) -> None:
    moved = replace(stock, timestamps=(stock.timestamps[0] + shift, *stock.timestamps[1:]))
    report = check_kline(moved)
    failed = result(report, check)

    assert failed.status == "fail"
    assert failed.findings[0].severity == "error"
    assert not report.ok


@pytest.mark.parametrize(
    ("position", "value", "status"),
    [(1, 10.19, "pass"), (2, 10.04, "pass"), (1, 10.2, "fail")],
)
def test_ohlc_order_tolerates_a_cent(
    stock: Kline, position: int, value: float, status: str
) -> None:
    # Bar 1 has a high of 10.18 and bar 2 a low of 10.05; 10.05 - 10.04 comes out above 0.01.
    checked = result(check_kline(with_cell(stock, "close", position, value)), "kline.ohlc_order")

    assert checked.status == status
    assert all(finding.severity == "warning" for finding in checked.findings)


@pytest.mark.parametrize(
    ("adjust", "severity"), [("normal", "error"), ("before", "warning"), ("after", "warning")]
)
def test_price_positive_is_only_an_error_on_unadjusted_prices(
    stock: Kline, adjust: Any, severity: str
) -> None:
    negative = with_cell(replace(stock, adjust=adjust), "open", 0, -0.5)
    checked = result(check_kline(negative), "kline.price_positive")

    assert checked.status == "fail"
    assert checked.findings[0].severity == severity


def test_vwap_stays_a_warning_and_skips_what_it_cannot_read(stock: Kline) -> None:
    breaking = with_cell(stock, "amount", 0, 2354245134.0)
    report = check_kline(breaking)
    assert report.ok
    assert result(report, "kline.vwap_in_range").findings[0].observed == pytest.approx(20.22, 0.01)

    unknown_amount = with_cell(stock, "amount", 0, 0.0)
    no_volume = with_cell(stock, "volume", 0, 0)
    for kline in (unknown_amount, no_volume):
        checked = result(check_kline(kline), "kline.vwap_in_range")
        assert (checked.status, checked.checked, checked.skipped_rows) == ("pass", 2, 1)


def test_vwap_is_skipped_for_adjusted_prices(stock: Kline) -> None:
    checked = result(check_kline(replace(stock, adjust="before")), "kline.vwap_in_range")
    assert checked.status == "skipped"
    assert checked.reason is not None and "adjusted" in checked.reason


@pytest.mark.parametrize(
    ("symbol", "kind"),
    [
        ("SH000001", "index"),
        ("SZ399001", "index"),
        ("BJ899050", "index"),
        ("HKHSI", "index"),
        (".INX", "index"),
        ("SH600000", "stock"),
        ("SZ000001", "stock"),
        ("BJ920000", "stock"),
        ("00700", "stock"),
        ("AAPL", "stock"),
        ("HK", "stock"),
    ],
)
def test_the_symbol_tells_an_index_from_a_stock(stock: Kline, symbol: str, kind: str) -> None:
    checked = result(check_kline(replace(stock, symbol=symbol)), "kline.vwap_in_range")
    assert checked.status == ("skipped" if kind == "index" else "pass")


def test_kind_can_be_given(stock: Kline) -> None:
    index = result(check_kline(stock, kind="index"), "kline.vwap_in_range")
    forced = result(
        check_kline(replace(stock, symbol="SH000001"), kind="stock"), "kline.vwap_in_range"
    )
    assert (index.status, forced.status) == ("skipped", "pass")


@pytest.mark.parametrize(
    ("symbol", "adjust", "period"),
    [
        ("00700", "normal", "day"),
        ("AAPL", "normal", "day"),
        ("SH600000", "after", "day"),
        ("SH600000", "before", "day"),
        ("SH600000", "normal", "week"),
    ],
)
def test_chg_rule_runs_for_unadjusted_a_share_daily_series_only(
    stock: Kline, symbol: str, adjust: Any, period: Any
) -> None:
    other = with_cell(replace(stock, symbol=symbol, adjust=adjust, period=period), "chg", 1, 9.0)
    checked = result(check_kline(other), "kline.chg_matches_close")
    assert (checked.status, checked.checked) == ("skipped", 0)


def test_a_suspension_is_a_merged_gap_and_nothing_else() -> None:
    # SZ000002 closed at 24.43 on 2015-12-18, the next bar is 21.99 on 2016-07-04 with chg -2.44
    # and percent -9.99, and every price of that bar is the same (measured 2026-11-05).
    days = [date(2015, 12, 18), date(2016, 7, 4), date(2016, 7, 5)]
    kline = daily_bars(
        "SZ000002",
        days,
        open=(24.2, 21.99, 19.79),
        high=(24.5, 21.99, 19.79),
        low=(24.0, 21.99, 19.79),
        close=(24.43, 21.99, 19.79),
        volume=(100, 4266300, 200),
        amount=(2440.0, 93815937.0, 3958.0),
        chg=(0.5, -2.44, -2.2),
        percent=(2.09, -9.99, -10.0),
    )
    sessions = (days[0], date(2015, 12, 21), date(2015, 12, 22), date(2016, 7, 1), *days[1:])
    report = check_kline(kline, calendar=calendar_of(*sessions))

    assert report.ok
    assert result(report, "kline.ohlc_order").status == "pass"
    assert result(report, "kline.price_positive").status == "pass"
    assert result(report, "kline.chg_matches_close").status == "pass"
    gaps = result(report, "kline.gaps_vs_calendar")
    assert gaps.status == "fail"
    assert gaps.total_findings == 1
    assert gaps.findings[0].severity == "warning"
    assert gaps.findings[0].where == "2015-12-21 to 2016-07-01"
    assert gaps.findings[0].observed == 3


def test_an_extra_day_is_an_error_for_a_stock(stock: Kline) -> None:
    days = [stamp.date() for stamp in stock.timestamps]
    report = check_kline(stock, calendar=calendar_of(days[0], days[2]))
    extra = result(report, "kline.gaps_vs_calendar")

    assert (extra.status, extra.checked) == ("fail", 3)
    assert [(item.severity, item.where) for item in extra.findings] == [("error", "2026-11-03")]
    assert not report.ok


def test_a_missing_day_is_an_error_for_an_index(stock: Kline) -> None:
    days = [stamp.date() for stamp in stock.timestamps]
    holed = replace(
        stock,
        symbol="SH000001",
        timestamps=(stock.timestamps[0], stock.timestamps[2]),
        columns={name: (cells[0], cells[2]) for name, cells in stock.columns.items()},
    )
    report = check_kline(holed, calendar=calendar_of(*days))
    missing = result(report, "kline.gaps_vs_calendar")

    assert (missing.status, missing.checked) == ("fail", 3)
    assert [(item.severity, item.where) for item in missing.findings] == [("error", "2026-11-03")]
    assert not report.ok


def test_the_calendar_only_counts_where_it_overlaps_the_bars(stock: Kline) -> None:
    days = [stamp.date() for stamp in stock.timestamps]
    wider = calendar_of(date(2026, 10, 30), *days, date(2026, 11, 5))
    checked = result(check_kline(stock, calendar=wider), "kline.gaps_vs_calendar")
    assert (checked.status, checked.checked) == ("pass", 3)

    later = calendar_of(date(2026, 11, 3), date(2026, 11, 4))
    partly = result(check_kline(stock, calendar=later), "kline.gaps_vs_calendar")
    assert (partly.status, partly.checked) == ("pass", 2)

    elsewhere = calendar_of(date(2020, 1, 2), date(2020, 1, 3))
    apart = result(check_kline(stock, calendar=elsewhere), "kline.gaps_vs_calendar")
    assert (apart.status, apart.checked) == ("skipped", 0)


def test_gaps_are_skipped_without_a_calendar_of_this_market_or_for_other_periods(
    stock: Kline,
) -> None:
    days = [stamp.date() for stamp in stock.timestamps]
    calendar = calendar_of(*days)
    cases = (
        check_kline(stock),
        check_kline(stock, calendar=calendar_of(*days, market="US")),
        check_kline(replace(stock, period="week"), calendar=calendar),
    )
    for report in cases:
        checked = result(report, "kline.gaps_vs_calendar")
        assert checked.status == "skipped"
        assert checked.reason


def test_last_bar_partial_is_a_note_in_the_exchange_timezone() -> None:
    bars = replay_kline("kline/aapl_day")
    last_bar_day = datetime(2026, 11, 4, 23, 0, tzinfo=NEW_YORK)

    # Already 11-05 in Shanghai, still 11-04 in New York.
    noted = result(check_kline(bars, as_of=last_bar_day), "kline.last_bar_partial")
    assert (noted.status, noted.checked, noted.total_findings) == ("pass", 1, 1)
    assert noted.findings[0].severity == "info"
    assert noted.findings[0].where == "2026-11-04"

    later = result(
        check_kline(bars, as_of=last_bar_day + timedelta(days=1)), "kline.last_bar_partial"
    )
    assert (later.status, later.total_findings) == ("pass", 0)

    skipped = result(check_kline(bars), "kline.last_bar_partial")
    assert skipped.status == "skipped"


def test_a_null_price_skips_the_row_and_is_not_read_as_0(stock: Kline) -> None:
    report = check_kline(with_cell(stock, "low", 1, None))

    for check in ("kline.ohlc_order", "kline.price_positive"):
        checked = result(report, check)
        assert (checked.status, checked.checked, checked.skipped_rows) == ("pass", 2, 1)
    assert result(report, "kline.vwap_in_range").skipped_rows == 1
    assert result(report, "kline.null_policy").findings[0].where == "2026-11-03"
    assert report.ok


def test_a_null_volume_or_chg_is_not_read_as_0(stock: Kline) -> None:
    no_volume = with_cell(with_cell(stock, "volume", 0, None), "amount", 0, None)
    checked = result(check_kline(no_volume), "kline.volume_nonneg")
    assert (checked.status, checked.checked, checked.skipped_rows) == ("pass", 2, 1)

    no_chg = with_cell(stock, "chg", 1, None)
    checked = result(check_kline(no_chg), "kline.chg_matches_close")
    assert (checked.status, checked.checked, checked.skipped_rows) == ("pass", 1, 1)

    no_close = with_cell(stock, "close", 1, None)
    checked = result(check_kline(no_close), "kline.chg_matches_close")
    assert (checked.status, checked.checked, checked.skipped_rows) == ("skipped", 0, 2)


def test_null_policy_of_columns_filled_in_later(stock: Kline) -> None:
    filled = replace(stock, columns={**stock.columns, "balance": (1.0, 2.0, None)})
    last_null = result(check_kline(filled), "kline.null_policy")
    assert last_null.status == "pass"
    assert [(item.severity, item.where, item.observed) for item in last_null.findings] == [
        ("info", "2026-11-04", 1)
    ]

    holed = replace(stock, columns={**stock.columns, "hold_ratio_cn": (1.0, None, 3.0)})
    between = result(check_kline(holed), "kline.null_policy")
    assert between.status == "fail"
    assert [(item.severity, item.where) for item in between.findings] == [("warning", "2026-11-03")]

    leading = replace(stock, columns={**stock.columns, "net_volume_cn": (None, 2.0, 3.0)})
    assert result(check_kline(leading), "kline.null_policy").findings == ()


def test_null_policy_allows_the_columns_that_can_be_null(stock: Kline) -> None:
    nulls = {
        "volume_post": (None, None, None),
        "amount_post": (None, None, None),
        "hold_volume_hk": (None, None, None),
        "balance": (None, None, None),
        "some_new_column": (None, None, None),
    }
    checked = result(
        check_kline(replace(stock, columns={**stock.columns, **nulls})), "kline.null_policy"
    )
    assert (checked.status, checked.checked, checked.findings) == ("pass", 3, ())


def test_an_index_may_have_no_volume(stock: Kline) -> None:
    index = with_cell(replace(stock, symbol="SH000001"), "volume", 0, None)
    stock_row = with_cell(stock, "volume", 0, None)
    assert result(check_kline(index), "kline.null_policy").findings == ()
    assert result(check_kline(stock_row), "kline.null_policy").status == "fail"


def test_missing_columns_read_as_null() -> None:
    bare = daily_bars("SH600000", [date(2026, 11, 3)])
    report = check_kline(bare)

    assert result(report, "kline.ohlc_order").skipped_rows == 1
    assert result(report, "kline.ohlc_order").status == "skipped"
    assert result(report, "kline.null_policy").total_findings == 5
    assert report.ok


def test_minute_bars_keep_their_time_and_skip_the_daily_checks() -> None:
    moment = datetime(2026, 11, 4, 10, 30, tzinfo=SHANGHAI)
    columns: dict[str, tuple[Cell, ...]] = {
        "open": (10.0, 10.0),
        "high": (10.0, 10.1),
        "low": (10.0, 10.0),
        "close": (10.0, 10.0),
        "volume": (-1, 1),
    }
    minute = Kline("SH600000", "60m", "normal", (moment, moment + timedelta(hours=1)), columns)
    report = check_kline(minute, as_of=moment)

    for check in (
        "kline.no_duplicate_day",
        "kline.daily_at_midnight",
        "kline.chg_matches_close",
        "kline.gaps_vs_calendar",
        "kline.last_bar_partial",
    ):
        assert result(report, check).status == "skipped"
    negative = result(report, "kline.volume_nonneg").findings[0]
    assert negative.where == moment.isoformat()


def test_empty_bars_give_a_report_of_skipped_checks() -> None:
    empty = Kline("SH600000", "day", "normal", (), {})
    report = check_kline(empty, calendar=calendar_of(date(2026, 11, 4)), as_of=FETCHED_AT)

    assert report.ok
    assert {item.status for item in report.results} == {"skipped"}
    assert all(item.reason for item in report.results)


def test_one_bar_cannot_be_compared_but_passes_the_rest(stock: Kline) -> None:
    single = replace(
        stock,
        timestamps=stock.timestamps[:1],
        columns={name: cells[:1] for name, cells in stock.columns.items()},
    )
    report = check_kline(single)

    assert result(report, "kline.timestamps_increasing").status == "skipped"
    assert result(report, "kline.chg_matches_close").status == "skipped"
    assert result(report, "kline.ohlc_order").status == "pass"


def test_findings_are_capped_and_the_total_is_kept() -> None:
    days = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(MAX_FINDINGS + 5)]
    count = len(days)
    kline = daily_bars("SH600000", days, volume=(-1,) * count)
    checked = result(check_kline(kline), "kline.volume_nonneg")

    assert len(checked.findings) == MAX_FINDINGS == 20
    assert checked.total_findings == count
    assert checked.findings[0].where == "2026-01-01"


def test_errors_are_kept_ahead_of_warnings_past_the_cap() -> None:
    # Sessions every other day and a bar on every second one: each hole is its own warning.
    sessions = [date(2026, 1, 1) + timedelta(days=2 * offset) for offset in range(60)]
    extra_day = sessions[-1] - timedelta(days=1)
    kline = daily_bars("SH600000", sorted((*sessions[::2], sessions[-1], extra_day)))
    report = check_kline(kline, calendar=calendar_of(*sessions))
    gaps = result(report, "kline.gaps_vs_calendar")

    assert gaps.total_findings == 30
    assert len(gaps.findings) == MAX_FINDINGS
    assert gaps.findings[0].severity == "error"
    assert gaps.findings[0].where == extra_day.isoformat()
    assert not report.ok


def test_checking_leaves_the_bars_as_they_were(stock: Kline) -> None:
    before = (stock.timestamps, dict(stock.columns))
    check_kline(stock, as_of=FETCHED_AT)
    assert (stock.timestamps, dict(stock.columns)) == before


def financials(
    symbol: str, *periods: Mapping[str, float | None], currency: str | None = "CNY"
) -> Financials:
    reports = tuple(
        ReportPeriod(
            date(2026 - offset, 9, 30),
            f"{2026 - offset}三季报",
            {key: LineItem(value, None) for key, value in items.items()},
            {},
        )
        for offset, items in enumerate(periods)
    )
    return Financials(symbol, "公司", currency, reports, {})


TOTALS_CN = {
    "total_assets": 100.0,
    "total_liab": 60.0,
    "total_holders_equity": 40.0,
    "total_liab_and_holders_equity": 100.0,
    "total_quity_atsopc": 35.0,
    "minority_equity": 5.0,
    "asset_liab_ratio": 60.0,
}
SUBTOTALS_CN = {
    "total_current_assets": 70.0,
    "total_noncurrent_assets": 30.0,
    "total_current_liab": 45.0,
    "total_noncurrent_liab": 15.0,
}
BALANCE_HK = {
    "ta": 100.0,
    "tlia": 60.0,
    "teqy": 40.0,
    "ca": 70.0,
    "tnca": 30.0,
    "clia": 45.0,
    "tnclia": 15.0,
    "shhfd": 35.0,
    "miint": 5.0,
    "tlia_ta": 60.0,
}
BALANCE_US = {
    "total_assets": 100.0,
    "total_liab": 60.0,
    "total_equity": 40.0,
    "total_holders_equity": 40.0,
    **SUBTOTALS_CN,
    "asset_liab_ratio": 60.0,
}
INCOME_CN = {
    "net_profit": 10.0,
    "net_profit_atsopc": 9.0,
    "minority_gal": 1.0,
    "profit_total_amt": 12.0,
    "income_tax_expenses": 2.0,
}
INCOME_HK = {"plocyr": 10.0, "amteqyhdcom": 9.0, "amtmiint": 1.0, "plobtx": 12.0, "tx": 2.0}
INCOME_US = {"gross_profit": 30.0, "revenue": 100.0, "sales_cost": 70.0}
CASH_CN = {
    "ncf_from_oa": 10.0,
    "ncf_from_ia": -3.0,
    "ncf_from_fa": -2.0,
    "effect_of_exchange_chg_on_cce": -1.0,
    "net_increase_in_cce": 4.0,
    "initial_balance_of_cce": 20.0,
    "final_balance_of_cce": 24.0,
}
CASH_HK = {
    "nocf": 10.0,
    "ninvcf": -3.0,
    "nfcgcf": -2.0,
    "icdccceq": 5.0,
    "cceqbegyr": 20.0,
    "ncfdchexrateot": -1.0,
    "cceqeyr": 24.0,
}
CASH_US = {
    "net_cash_provided_by_oa": 10.0,
    "net_cash_used_in_ia": -3.0,
    "net_cash_used_in_fa": -2.0,
    "effect_of_exchange_chg_on_cce": -1.0,
    "increase_in_cce": 4.0,
    "cce_at_boy": 20.0,
    "cce_at_eoy": 24.0,
}
BALANCE_CN = {**TOTALS_CN, **SUBTOTALS_CN}

IDENTITY_CASES = [
    ("fin.balance_identity", "balance", "SH600519", BALANCE_CN, "total_liab", "error"),
    (
        "fin.balance_identity",
        "balance",
        "SH600519",
        BALANCE_CN,
        "total_liab_and_holders_equity",
        "error",
    ),
    ("fin.balance_identity", "balance", "00700", BALANCE_HK, "teqy", "error"),
    ("fin.balance_identity", "balance", "AAPL", BALANCE_US, "total_equity", "error"),
    ("fin.balance_subtotals", "balance", "SH600519", BALANCE_CN, "total_current_assets", "warning"),
    (
        "fin.balance_subtotals",
        "balance",
        "SH600519",
        BALANCE_CN,
        "total_noncurrent_liab",
        "warning",
    ),
    ("fin.balance_subtotals", "balance", "00700", BALANCE_HK, "ca", "warning"),
    ("fin.balance_subtotals", "balance", "00700", BALANCE_HK, "tnclia", "warning"),
    ("fin.balance_subtotals", "balance", "AAPL", BALANCE_US, "total_current_liab", "warning"),
    ("fin.equity_split", "balance", "SH600519", BALANCE_CN, "minority_equity", "warning"),
    ("fin.equity_split", "balance", "00700", BALANCE_HK, "shhfd", "warning"),
    ("fin.income_identity", "income", "SH600519", INCOME_CN, "minority_gal", "warning"),
    ("fin.income_identity", "income", "SH600519", INCOME_CN, "income_tax_expenses", "warning"),
    ("fin.income_identity", "income", "00700", INCOME_HK, "amtmiint", "warning"),
    ("fin.income_identity", "income", "00700", INCOME_HK, "tx", "warning"),
    ("fin.income_identity", "income", "AAPL", INCOME_US, "sales_cost", "warning"),
    ("fin.cash_flow_identity", "cash_flow", "SH600519", CASH_CN, "ncf_from_ia", "warning"),
    ("fin.cash_flow_identity", "cash_flow", "SH600519", CASH_CN, "final_balance_of_cce", "warning"),
    ("fin.cash_flow_identity", "cash_flow", "00700", CASH_HK, "nocf", "warning"),
    ("fin.cash_flow_identity", "cash_flow", "00700", CASH_HK, "cceqeyr", "warning"),
    ("fin.cash_flow_identity", "cash_flow", "AAPL", CASH_US, "net_cash_used_in_fa", "warning"),
    ("fin.cash_flow_identity", "cash_flow", "AAPL", CASH_US, "cce_at_eoy", "warning"),
    ("fin.ratio_units", "balance", "SH600519", BALANCE_CN, "asset_liab_ratio", "error"),
    ("fin.ratio_units", "balance", "00700", BALANCE_HK, "tlia_ta", "error"),
    ("fin.ratio_units", "balance", "AAPL", BALANCE_US, "asset_liab_ratio", "error"),
]


@pytest.mark.parametrize(
    ("check", "statement", "symbol", "items", "key", "severity"),
    IDENTITY_CASES,
    ids=[f"{case[2]}-{case[4]}" for case in IDENTITY_CASES],
)
def test_statement_checks_pass_and_fail(
    check: str,
    statement: Statement,
    symbol: str,
    items: Mapping[str, float],
    key: str,
    severity: str,
) -> None:
    good = result(check_financials(financials(symbol, items), statement), check)
    assert (good.status, good.total_findings) == ("pass", 0)
    assert good.checked > 0

    report = check_financials(financials(symbol, {**items, key: items[key] + 7.0}), statement)
    bad = result(report, check)
    assert bad.status == "fail"
    assert bad.findings[0].severity == severity
    assert bad.findings[0].where == "2026三季报"
    assert report.ok is (severity != "error")


@pytest.mark.parametrize(
    ("extras", "subtotals"),
    [
        pytest.param({"savings_absorption": 50.0, "precious_metal": 1.0}, "skipped", id="bank"),
        pytest.param({"currency_funds": 1.0, **SUBTOTALS_CN}, "pass", id="non-financial"),
        pytest.param(
            {"premium_receivable": 1.0, "life_insurance_reserve": 2.0}, "skipped", id="insurer"
        ),
        pytest.param({"customer_fund_deposit": 1.0, "td_seat_fee": 2.0}, "skipped", id="broker"),
    ],
)
def test_the_identities_use_what_every_kind_of_company_reports(
    extras: Mapping[str, float], subtotals: str
) -> None:
    report = check_financials(financials("SH600000", {**TOTALS_CN, **extras}), "balance")

    assert report.ok
    assert result(report, "fin.balance_identity").status == "pass"
    assert result(report, "fin.equity_split").status == "pass"
    assert result(report, "fin.ratio_units").status == "pass"
    assert result(report, "fin.balance_subtotals").status == subtotals


def test_a_null_operand_skips_that_identity_and_is_not_read_as_0() -> None:
    items = {**INCOME_CN, "minority_gal": None}
    checked = result(
        check_financials(financials("SH600000", items), "income"), "fin.income_identity"
    )

    assert (checked.status, checked.checked, checked.skipped_rows) == ("pass", 1, 1)
    assert checked.total_findings == 0


def test_a_missing_key_counts_like_a_null() -> None:
    items = {key: value for key, value in INCOME_CN.items() if key != "net_profit"}
    checked = result(
        check_financials(financials("SH600000", items), "income"), "fin.income_identity"
    )

    assert (checked.status, checked.checked, checked.skipped_rows) == ("skipped", 0, 2)
    assert checked.reason


def test_null_exchange_effect_counts_as_nothing_only_where_it_is_optional() -> None:
    no_effect_us = {
        **CASH_US,
        "effect_of_exchange_chg_on_cce": None,
        "increase_in_cce": 5.0,
        "cce_at_eoy": 25.0,
    }
    us = result(
        check_financials(financials("AAPL", no_effect_us), "cash_flow"), "fin.cash_flow_identity"
    )
    assert (us.status, us.checked, us.skipped_rows) == ("pass", 2, 0)

    no_effect_cn = {**CASH_CN, "effect_of_exchange_chg_on_cce": None}
    cn = result(
        check_financials(financials("SH600000", no_effect_cn), "cash_flow"),
        "fin.cash_flow_identity",
    )
    assert (cn.status, cn.checked, cn.skipped_rows) == ("pass", 1, 1)


def test_us_equity_falls_back_to_holders_equity() -> None:
    items = {**BALANCE_US, "total_equity": None}
    checked = result(check_financials(financials("AAPL", items), "balance"), "fin.balance_identity")
    assert (checked.status, checked.checked) == ("pass", 1)


def test_us_statements_have_no_equity_split() -> None:
    checked = result(
        check_financials(financials("AAPL", BALANCE_US), "balance"), "fin.equity_split"
    )
    assert (checked.status, checked.checked) == ("skipped", 0)


def test_identities_allow_a_small_relative_difference() -> None:
    close = {**INCOME_US, "gross_profit": 30.002}
    assert (
        result(check_financials(financials("AAPL", close), "income"), "fin.income_identity").status
        == "pass"
    )
    off = {**INCOME_US, "gross_profit": 30.01}
    assert (
        result(check_financials(financials("AAPL", off), "income"), "fin.income_identity").status
        == "fail"
    )


def test_a_ratio_read_as_a_fraction_fails() -> None:
    items = {**TOTALS_CN, "asset_liab_ratio": 0.6}
    report = check_financials(financials("SH600000", items), "balance")
    checked = result(report, "fin.ratio_units")

    assert checked.findings[0].observed == 0.6
    assert checked.findings[0].expected == 60.0
    assert not report.ok


def test_ratio_skips_null_values_and_zero_assets() -> None:
    no_ratio = {**TOTALS_CN, "asset_liab_ratio": None}
    no_assets = {**TOTALS_CN, "total_assets": 0.0}
    for items in (no_ratio, no_assets):
        checked = result(
            check_financials(financials("SH600000", items), "balance"), "fin.ratio_units"
        )
        assert (checked.status, checked.checked, checked.skipped_rows) == ("skipped", 0, 1)


def test_report_dates_must_strictly_decrease() -> None:
    good = financials("SH600000", INCOME_CN, INCOME_CN, INCOME_CN)
    checked = result(check_financials(good, "income"), "fin.period_dates")
    assert (checked.status, checked.checked) == ("pass", 2)

    swapped = replace(good, periods=(good.periods[1], good.periods[0], good.periods[2]))
    report = check_financials(swapped, "income")
    assert result(report, "fin.period_dates").status == "fail"
    assert not report.ok

    repeated = replace(good, periods=(good.periods[0], good.periods[0]))
    assert result(check_financials(repeated, "income"), "fin.period_dates").status == "fail"

    single = financials("SH600000", INCOME_CN)
    assert result(check_financials(single, "income"), "fin.period_dates").status == "skipped"


def test_a_missing_currency_is_a_warning() -> None:
    report = check_financials(financials("SH600000", INCOME_CN, currency=None), "income")
    checked = result(report, "fin.currency_present")

    assert checked.status == "fail"
    assert checked.findings[0].severity == "warning"
    assert report.ok
    named = financials("SH600000", INCOME_CN)
    assert result(check_financials(named, "income"), "fin.currency_present").status == "pass"


@pytest.mark.parametrize(
    ("statement", "checks"),
    [
        (
            "balance",
            [
                "fin.balance_identity",
                "fin.balance_subtotals",
                "fin.equity_split",
                "fin.ratio_units",
                "fin.period_dates",
                "fin.currency_present",
            ],
        ),
        ("income", ["fin.income_identity", "fin.period_dates", "fin.currency_present"]),
        ("cash_flow", ["fin.cash_flow_identity", "fin.period_dates", "fin.currency_present"]),
    ],
)
def test_each_statement_runs_its_checks(statement: Statement, checks: list[str]) -> None:
    report = check_financials(financials("SH600000"), statement)

    assert report.subject == f"SH600000 {statement}"
    assert [item.check for item in report.results] == checks
    assert report.ok
    # With no report period there is nothing to compare, only the currency to read.
    assert [item.status for item in report.results][:-1] == ["skipped"] * (len(checks) - 1)
