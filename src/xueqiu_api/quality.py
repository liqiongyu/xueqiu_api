"""Data quality checks of bars and financial statements.

The checks are pure: they return a report, never raise on bad data and never change it. A check
that could not run reports ``skipped``. A null is never read as 0: its row counts as skipped.
Envelope and structure errors stay with the endpoint's parser.

The statement identities use the line items a bank, a non-financial company, an insurer and a
broker all report: 2 report periods of each had residuals of 0 (measured 2026-11-05).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from itertools import groupby, pairwise
from types import MappingProxyType
from typing import Literal, NamedTuple

from ._finance import Financials, LineItem
from .endpoints.kline import Kline
from .symbols import Market, normalize_symbol
from .trading_calendar import TradingCalendar

__all__ = [
    "MAX_FINDINGS",
    "CheckResult",
    "CheckTally",
    "Finding",
    "QualityReport",
    "SecurityKind",
    "Severity",
    "Statement",
    "Status",
    "check_financials",
    "check_kline",
]

Severity = Literal["error", "warning", "info"]
Status = Literal["pass", "fail", "skipped"]
SecurityKind = Literal["stock", "index"]
Statement = Literal["balance", "income", "cash_flow"]

MAX_FINDINGS = 20
"""Most findings a check keeps, errors first; ``total_findings`` counts all of them."""

_OHLC_TOLERANCE = 0.01
"""Largest order gap taken as rounding: index closes have 2 decimals and their highs and lows 4,
and SH000001 has 21 such bars (measured 2026-11-05)."""
_FLOAT_SLACK = 1e-9
_VWAP_MARGIN = 0.01
_CHG_TOLERANCE = 0.0051
_PERCENT_TOLERANCE = 0.0101
_RELATIVE_TOLERANCE = 1e-4
_RATIO_TOLERANCE = 0.01
_INDEX_PREFIXES = ("SH000", "SZ399", "BJ899", ".")
_REQUIRED_COLUMNS = ("open", "high", "low", "close")
_FILLED_LATE_COLUMNS = ("balance", "hold_volume_cn", "hold_ratio_cn", "net_volume_cn")
_DAILY_ONLY = "only daily bars are checked"
_SEVERITY_ORDER: Mapping[Severity, int] = MappingProxyType({"error": 0, "warning": 1, "info": 2})


@dataclass(frozen=True, slots=True)
class Finding:
    check: str
    severity: Severity
    where: str
    """Where it is: a date, a time, a range of dates or a report period name."""
    message: str
    observed: float | str | None = None
    expected: float | str | None = None


@dataclass(frozen=True, slots=True)
class CheckResult:
    check: str
    status: Status
    """fail when there is an error or warning finding; info findings alone are a pass."""
    checked: int
    """Rows or comparisons really checked."""
    skipped_rows: int
    """Rows left out because a value they need is null."""
    reason: str | None
    """Why the check was skipped."""
    findings: tuple[Finding, ...]
    """At most ``MAX_FINDINGS``, errors first."""
    total_findings: int


@dataclass(frozen=True, slots=True)
class QualityReport:
    subject: str
    results: tuple[CheckResult, ...]

    @property
    def ok(self) -> bool:
        """Whether no check found an error; warnings and skipped checks do not count."""
        return not any(
            finding.severity == "error" for result in self.results for finding in result.findings
        )


class CheckTally:
    """Counts and findings of one check while it runs."""

    def __init__(self, check: str) -> None:
        self.check = check
        self.checked = 0
        self.skipped_rows = 0
        self._findings: list[Finding] = []

    def found(
        self,
        severity: Severity,
        where: str,
        message: str,
        *,
        observed: float | str | None = None,
        expected: float | str | None = None,
    ) -> None:
        self._findings.append(Finding(self.check, severity, where, message, observed, expected))

    def result(self, reason_if_nothing_checked: str = "nothing to check") -> CheckResult:
        ranked = sorted(self._findings, key=lambda finding: _SEVERITY_ORDER[finding.severity])
        failed = any(finding.severity != "info" for finding in self._findings)
        status: Status = "skipped" if not self.checked else "fail" if failed else "pass"
        return CheckResult(
            check=self.check,
            status=status,
            checked=self.checked,
            skipped_rows=self.skipped_rows,
            reason=reason_if_nothing_checked if status == "skipped" else None,
            findings=tuple(ranked[:MAX_FINDINGS]),
            total_findings=len(ranked),
        )


def check_kline(
    kline: Kline,
    *,
    kind: SecurityKind | None = None,
    calendar: TradingCalendar | None = None,
    as_of: datetime | None = None,
) -> QualityReport:
    """Check one series of bars.

    ``kind`` defaults to what the symbol looks like; an index is SH000, SZ399 or BJ899 plus digits,
    HK plus letters, or a leading dot. ``calendar`` turns on the check for missing and extra days.
    ``as_of``, an aware datetime, turns on the note that the last bar may be unfinished.
    """
    resolved: SecurityKind = _kind_of(kline.symbol) if kind is None else kind
    return QualityReport(
        subject=f"{kline.symbol} {kline.period} {kline.adjust}",
        results=(
            _timestamps_increasing(kline),
            _no_duplicate_day(kline),
            _daily_at_midnight(kline),
            _ohlc_order(kline),
            _price_positive(kline),
            _volume_nonneg(kline),
            _vwap_in_range(kline, resolved),
            _chg_matches_close(kline),
            _gaps_vs_calendar(kline, resolved, calendar),
            _last_bar_partial(kline, as_of),
            _null_policy(kline, resolved),
        ),
    )


def _kind_of(symbol: str) -> SecurityKind:
    if normalize_symbol(symbol).market == "HK":
        return "index" if symbol.startswith("HK") else "stock"
    return "index" if symbol.startswith(_INDEX_PREFIXES) else "stock"


def _column(kline: Kline, name: str) -> tuple[float | int | None, ...]:
    """A column by name; a column the upstream did not send reads as all null."""
    return kline.columns.get(name, (None,) * len(kline))


def _where(kline: Kline, index: int) -> str:
    stamp = kline.timestamps[index]
    return stamp.date().isoformat() if kline.period == "day" else stamp.isoformat()


def _timestamps_increasing(kline: Kline) -> CheckResult:
    tally = CheckTally("kline.timestamps_increasing")
    for index in range(1, len(kline)):
        tally.checked += 1
        before, moment = kline.timestamps[index - 1 : index + 1]
        if moment <= before:
            tally.found(
                "error",
                _where(kline, index),
                "the bar is not after the one before it",
                observed=moment.isoformat(),
                expected=f"after {before.isoformat()}",
            )
    return tally.result("fewer than two bars")


def _no_duplicate_day(kline: Kline) -> CheckResult:
    check = "kline.no_duplicate_day"
    if kline.period != "day":
        return CheckTally(check).result(_DAILY_ONLY)
    tally = CheckTally(check)
    seen: set[date] = set()
    for stamp in kline.timestamps:
        tally.checked += 1
        if stamp.date() in seen:
            tally.found("error", stamp.date().isoformat(), "more than one bar on this date")
        seen.add(stamp.date())
    return tally.result("no bars")


def _daily_at_midnight(kline: Kline) -> CheckResult:
    check = "kline.daily_at_midnight"
    if kline.period != "day":
        return CheckTally(check).result(_DAILY_ONLY)
    tally = CheckTally(check)
    for stamp in kline.timestamps:
        tally.checked += 1
        if stamp.time() != time():
            tally.found(
                "error",
                stamp.date().isoformat(),
                "a daily bar is not at midnight in the exchange timezone",
                observed=stamp.isoformat(),
            )
    return tally.result("no bars")


class _Prices(NamedTuple):
    position: int
    open: float
    high: float
    low: float
    close: float


def _price_rows(kline: Kline, tally: CheckTally) -> Iterator[_Prices]:
    """Bars with all four prices; the others count as skipped rows."""
    rows = zip(
        _column(kline, "open"),
        _column(kline, "high"),
        _column(kline, "low"),
        _column(kline, "close"),
        strict=True,
    )
    for index, (open_, high, low, close) in enumerate(rows):
        if open_ is None or high is None or low is None or close is None:
            tally.skipped_rows += 1
            continue
        tally.checked += 1
        yield _Prices(index, open_, high, low, close)


def _ohlc_order(kline: Kline) -> CheckResult:
    tally = CheckTally("kline.ohlc_order")
    for row in _price_rows(kline, tally):
        gap = max(row.low - min(row.open, row.close), max(row.open, row.close) - row.high)
        if gap > _OHLC_TOLERANCE + _FLOAT_SLACK:
            tally.found(
                "warning",
                _where(kline, row.position),
                "open or close lies outside low and high",
                observed=gap,
                expected=_OHLC_TOLERANCE,
            )
    return tally.result("no bar has all four prices")


def _price_positive(kline: Kline) -> CheckResult:
    tally = CheckTally("kline.price_positive")
    # Adjusting can legitimately take old prices to 0 or below.
    severity: Severity = "error" if kline.adjust == "normal" else "warning"
    for row in _price_rows(kline, tally):
        lowest = min(row.open, row.high, row.low, row.close)
        if lowest <= 0:
            tally.found(
                severity, _where(kline, row.position), "a price is not positive", observed=lowest
            )
    return tally.result("no bar has all four prices")


def _volume_nonneg(kline: Kline) -> CheckResult:
    tally = CheckTally("kline.volume_nonneg")
    rows = zip(_column(kline, "volume"), _column(kline, "amount"), strict=True)
    for index, values in enumerate(rows):
        present = [value for value in values if value is not None]
        if not present:
            tally.skipped_rows += 1
            continue
        tally.checked += 1
        if min(present) < 0:
            tally.found(
                "error", _where(kline, index), "volume or amount is negative", observed=min(present)
            )
    return tally.result("no bar has a volume or an amount")


def _vwap_in_range(kline: Kline, kind: SecurityKind) -> CheckResult:
    check = "kline.vwap_in_range"
    if kind != "stock":
        return CheckTally(check).result("an index's amount and volume do not give its level")
    if kline.adjust != "normal":
        return CheckTally(check).result("amount is not adjusted, so it only fits unadjusted prices")
    tally = CheckTally(check)
    rows = zip(
        _column(kline, "amount"),
        _column(kline, "volume"),
        _column(kline, "low"),
        _column(kline, "high"),
        strict=True,
    )
    for index, (amount, volume, low, high) in enumerate(rows):
        # Early AAPL bars have an amount of 0 with a real volume: that 0 means not known.
        if not amount or not volume or low is None or high is None:
            tally.skipped_rows += 1
            continue
        tally.checked += 1
        vwap = amount / volume
        if not low * (1 - _VWAP_MARGIN) <= vwap <= high * (1 + _VWAP_MARGIN):
            tally.found(
                "warning",
                _where(kline, index),
                "amount over volume lies outside low and high",
                observed=vwap,
                expected=f"{low} to {high}",
            )
    return tally.result("no bar has an amount, a volume, a low and a high")


def _chg_matches_close(kline: Kline) -> CheckResult:
    check = "kline.chg_matches_close"
    # Measured 2026-11-05: on SH600000 chg is the close difference on every day, ex-dates too, but
    # not on AAPL, nor on after-adjusted bars.
    if normalize_symbol(kline.symbol).market != "CN" or kline.adjust != "normal":
        return CheckTally(check).result("only unadjusted A-share series follow this rule")
    if kline.period != "day":
        return CheckTally(check).result(_DAILY_ONLY)
    tally = CheckTally(check)
    closes = _column(kline, "close")
    rows = zip(
        closes[:-1],
        closes[1:],
        _column(kline, "chg")[1:],
        _column(kline, "percent")[1:],
        strict=True,
    )
    for index, (previous, close, chg, percent) in enumerate(rows, start=1):
        if previous is None or close is None or chg is None or percent is None or not previous:
            tally.skipped_rows += 1
            continue
        tally.checked += 1
        difference = close - previous
        relative = 100 * difference / previous
        if abs(chg - difference) > _CHG_TOLERANCE:
            tally.found(
                "warning",
                _where(kline, index),
                "chg is not the difference of closes",
                observed=chg,
                expected=round(difference, 4),
            )
        elif abs(percent - relative) > _PERCENT_TOLERANCE:
            tally.found(
                "warning",
                _where(kline, index),
                "percent is not the relative difference of closes",
                observed=percent,
                expected=round(relative, 4),
            )
    return tally.result("fewer than two bars with close, chg and percent")


def _gaps_vs_calendar(
    kline: Kline, kind: SecurityKind, calendar: TradingCalendar | None
) -> CheckResult:
    check = "kline.gaps_vs_calendar"
    if kline.period != "day":
        return CheckTally(check).result(_DAILY_ONLY)
    if calendar is None:
        return CheckTally(check).result("no calendar was given")
    if calendar.market != normalize_symbol(kline.symbol).market:
        return CheckTally(check).result(f"the calendar is for {calendar.market}, not this market")
    days = {stamp.date() for stamp in kline.timestamps}
    if not days:
        return CheckTally(check).result("no bars")
    begin, end = max(min(days), calendar.first), min(max(days), calendar.last)
    sessions = calendar.sessions_between(begin, end)
    known = set(sessions)
    extras = sorted(day for day in days if begin <= day <= end and day not in known)
    tally = CheckTally(check)
    tally.checked = len(sessions) + len(extras)
    # A suspended stock has no bars, measured 2026-11-05 on SZ000002 in 2016 and SH600000 in 2006
    # and 2016. An index has no such reason.
    severity: Severity = "warning" if kind == "stock" else "error"
    for has_bar, run in groupby(sessions, key=days.__contains__):
        if not has_bar:
            missing = list(run)
            where = f"{missing[0]} to {missing[-1]}" if len(missing) > 1 else str(missing[0])
            tally.found(severity, where, "no bar on these sessions", observed=len(missing))
    for day in extras:
        tally.found("error", day.isoformat(), "a bar on a day that is not a session")
    return tally.result("the bars and the calendar do not overlap")


def _last_bar_partial(kline: Kline, as_of: datetime | None) -> CheckResult:
    check = "kline.last_bar_partial"
    if kline.period != "day":
        return CheckTally(check).result(_DAILY_ONLY)
    if as_of is None:
        return CheckTally(check).result("no as_of time was given")
    if not kline.timestamps:
        return CheckTally(check).result("no bars")
    tally = CheckTally(check)
    tally.checked = 1
    last = kline.timestamps[-1]
    if last.date() == as_of.astimezone(last.tzinfo).date():
        tally.found(
            "info",
            last.date().isoformat(),
            "the last bar is from the day of as_of, so it may be unfinished",
        )
    return tally.result()


def _null_policy(kline: Kline, kind: SecurityKind) -> CheckResult:
    tally = CheckTally("kline.null_policy")
    tally.checked = len(kline)
    required = (*_REQUIRED_COLUMNS, "volume") if kind == "stock" else _REQUIRED_COLUMNS
    for name in required:
        for index, value in enumerate(_column(kline, name)):
            if value is None:
                tally.found("warning", _where(kline, index), f"{name} is null")
    for name in _FILLED_LATE_COLUMNS:
        column = kline.columns.get(name, ())
        filled = [index for index, value in enumerate(column) if value is not None]
        if not filled:
            continue
        for index in range(filled[0], filled[-1]):
            if column[index] is None:
                tally.found("warning", _where(kline, index), f"{name} is null between values")
        unfilled = len(column) - 1 - filled[-1]
        if unfilled:
            # The upstream fills these columns in later; the last bars of a sample had none.
            tally.found(
                "info",
                _where(kline, filled[-1] + 1),
                f"{name} is null in the last {unfilled} bars",
                observed=unfilled,
            )
    return tally.result("no bars")


class _Identity(NamedTuple):
    """A line item and the terms that add up to it. In a term, a leading ``-`` subtracts, a leading
    ``?`` counts a null as nothing, and ``a|b`` takes ``b`` when ``a`` is null."""

    total: str
    terms: tuple[str, ...]


class _IdentityCheck(NamedTuple):
    check: str
    severity: Severity
    statement: Statement
    identities: Mapping[Market, tuple[_Identity, ...]]


_IDENTITY_CHECKS = (
    _IdentityCheck(
        "fin.balance_identity",
        "error",
        "balance",
        {
            "CN": (
                _Identity("total_assets", ("total_liab", "total_holders_equity")),
                _Identity("total_assets", ("total_liab_and_holders_equity",)),
            ),
            "HK": (_Identity("ta", ("tlia", "teqy")),),
            "US": (_Identity("total_assets", ("total_liab", "total_equity|total_holders_equity")),),
        },
    ),
    _IdentityCheck(
        "fin.balance_subtotals",
        "warning",
        "balance",
        {
            # Banks, insurers and brokers have no current and non-current subtotals.
            "CN": (
                _Identity("total_assets", ("total_current_assets", "total_noncurrent_assets")),
                _Identity("total_liab", ("total_current_liab", "total_noncurrent_liab")),
            ),
            "HK": (
                _Identity("ta", ("ca", "tnca")),
                _Identity("tlia", ("clia", "tnclia")),
            ),
            "US": (
                _Identity("total_assets", ("total_current_assets", "total_noncurrent_assets")),
                _Identity("total_liab", ("total_current_liab", "total_noncurrent_liab")),
            ),
        },
    ),
    _IdentityCheck(
        "fin.equity_split",
        "warning",
        "balance",
        {
            "CN": (_Identity("total_holders_equity", ("total_quity_atsopc", "minority_equity")),),
            "HK": (_Identity("teqy", ("shhfd", "miint")),),
        },
    ),
    _IdentityCheck(
        "fin.income_identity",
        "warning",
        "income",
        {
            "CN": (
                _Identity("net_profit", ("net_profit_atsopc", "minority_gal")),
                _Identity("net_profit", ("profit_total_amt", "-income_tax_expenses")),
            ),
            "HK": (
                _Identity("plocyr", ("amteqyhdcom", "amtmiint")),
                _Identity("plocyr", ("plobtx", "-tx")),
            ),
            "US": (_Identity("gross_profit", ("revenue", "-sales_cost")),),
        },
    ),
    _IdentityCheck(
        "fin.cash_flow_identity",
        "warning",
        "cash_flow",
        {
            "CN": (
                _Identity(
                    "net_increase_in_cce",
                    ("ncf_from_oa", "ncf_from_ia", "ncf_from_fa", "effect_of_exchange_chg_on_cce"),
                ),
                _Identity(
                    "final_balance_of_cce", ("initial_balance_of_cce", "net_increase_in_cce")
                ),
            ),
            "HK": (
                _Identity("icdccceq", ("nocf", "ninvcf", "nfcgcf")),
                _Identity("cceqeyr", ("cceqbegyr", "icdccceq", "ncfdchexrateot")),
            ),
            "US": (
                _Identity(
                    "increase_in_cce",
                    (
                        "net_cash_provided_by_oa",
                        "net_cash_used_in_ia",
                        "net_cash_used_in_fa",
                        "?effect_of_exchange_chg_on_cce",
                    ),
                ),
                _Identity("cce_at_eoy", ("cce_at_boy", "increase_in_cce")),
            ),
        },
    ),
)
_DEBT_RATIO_KEYS: Mapping[Market, tuple[str, str, str]] = MappingProxyType(
    {
        "CN": ("asset_liab_ratio", "total_liab", "total_assets"),
        "HK": ("tlia_ta", "tlia", "ta"),
        "US": ("asset_liab_ratio", "total_liab", "total_assets"),
    }
)
"""Debt ratio in percent points, liabilities and assets. They catch a fraction read as percent."""


def check_financials(financials: Financials, statement: Statement) -> QualityReport:
    """Check the report periods of one statement of one company."""
    market = normalize_symbol(financials.symbol).market
    results = [
        _identities(financials, spec.check, spec.severity, spec.identities.get(market))
        for spec in _IDENTITY_CHECKS
        if spec.statement == statement
    ]
    if statement == "balance":
        results.append(_ratio_units(financials, market))
    results += [_period_dates(financials), _currency_present(financials)]
    return QualityReport(subject=f"{financials.symbol} {statement}", results=tuple(results))


def _value(items: Mapping[str, LineItem], names: str) -> float | None:
    """The first of the ``|``-separated line items with a value."""
    for name in names.split("|"):
        item = items.get(name)
        if item is not None and item.value is not None:
            return item.value
    return None


def _sum_terms(items: Mapping[str, LineItem], terms: tuple[str, ...]) -> float | None:
    """What the terms add up to, or None when a term that must have a value has none."""
    total = 0.0
    for term in terms:
        value = _value(items, term.lstrip("-?"))
        if value is not None:
            total += -value if term.startswith("-") else value
        elif not term.startswith("?"):
            return None
    return total


def _identities(
    financials: Financials,
    check: str,
    severity: Severity,
    identities: tuple[_Identity, ...] | None,
) -> CheckResult:
    if identities is None:
        return CheckTally(check).result("the statements of this market have no such items")
    tally = CheckTally(check)
    for period in financials.periods:
        for identity in identities:
            total = _value(period.items, identity.total)
            terms = _sum_terms(period.items, identity.terms)
            if total is None or terms is None:
                tally.skipped_rows += 1
                continue
            tally.checked += 1
            if abs(total - terms) > _RELATIVE_TOLERANCE * max(abs(total), abs(terms)):
                tally.found(
                    severity,
                    period.report_name,
                    f"{identity.total} does not equal {' + '.join(identity.terms)}",
                    observed=total,
                    expected=terms,
                )
    return tally.result("no report period has these items")


def _ratio_units(financials: Financials, market: Market) -> CheckResult:
    ratio_key, liabilities_key, assets_key = _DEBT_RATIO_KEYS[market]
    tally = CheckTally("fin.ratio_units")
    for period in financials.periods:
        ratio = _value(period.items, ratio_key)
        liabilities = _value(period.items, liabilities_key)
        assets = _value(period.items, assets_key)
        if ratio is None or liabilities is None or not assets:
            tally.skipped_rows += 1
            continue
        tally.checked += 1
        expected = 100 * liabilities / assets
        if abs(ratio - expected) > _RATIO_TOLERANCE:
            tally.found(
                "error",
                period.report_name,
                f"{ratio_key} is not liabilities over assets in percent points",
                observed=ratio,
                expected=round(expected, 4),
            )
    return tally.result("no report period has these items")


def _period_dates(financials: Financials) -> CheckResult:
    tally = CheckTally("fin.period_dates")
    for newer, older in pairwise(financials.periods):
        tally.checked += 1
        if older.report_date >= newer.report_date:
            tally.found(
                "error",
                older.report_name,
                "report dates are not strictly decreasing",
                observed=older.report_date.isoformat(),
                expected=f"before {newer.report_date.isoformat()}",
            )
    return tally.result("fewer than two report periods")


def _currency_present(financials: Financials) -> CheckResult:
    tally = CheckTally("fin.currency_present")
    tally.checked = 1
    if not financials.currency:
        tally.found("warning", financials.symbol, "the statement names no currency")
    return tally.result()
