"""Adjustment factors from unadjusted and backward-adjusted daily bars, checked against dividends.

The factor of a day is A = after / raw, a step function that changes only on ex-dates. The upstream
sends after = round(raw * A, 4): that held for all 25684 prices of SH600000 and all 30032 of AAPL
(measured 2026-11-05). So each price gives an interval for A, a bar the intersection of its four,
and consecutive bars whose intervals overlap share one factor: the midpoint of their intersection.
A bar that overlaps nothing before it starts a new factor.

Forward-adjusted prices are computed locally as raw * B, with B = A / A(T) and T the last bar. They
came within 5.0e-5 of the upstream's for all prices of SH600000 (measured 2026-11-05).
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from itertools import pairwise
from types import MappingProxyType
from typing import Literal

from .endpoints.f10_bonus import Dividend
from .endpoints.f10_holders import Split
from .endpoints.f10_share_changes import ShareChange
from .endpoints.kline import Adjust, Kline
from .errors import InvalidParams, UpstreamChanged
from .quality import CheckResult, CheckTally, QualityReport
from .symbols import Market, normalize_symbol

__all__ = [
    "AdjustFactors",
    "CorporateAction",
    "FactorStep",
    "corporate_actions",
    "derive_factors",
    "reconcile_factors",
]

_EPSILON = 0.55e-4
"""Half of the upstream's fourth decimal, plus a margin: with exactly 0.5e-4, float error at the
edges of an interval split factors in simulation."""
_PRICES = ("open", "high", "low", "close")
_DEPENDS_ON_ADJUSTMENT = ("chg", "percent")
_RATIO_TOLERANCE = 0.005
"""The largest gap for SH600000's 26 dividends was 1.7e-7 (measured 2026-11-05), so this is wide."""
_SHARE_RATIO_TOLERANCE = 0.01
_SHARE_CHANGE_SLACK = timedelta(days=31)
"""How far a share change may lie from its ex-date. Their relation has not been measured."""
_BONUS_REASONS = ("送", "转增")
"""Share change reasons that a bonus issue explains. 可转债转股 is not one: it has no step."""
_QUOTE_CURRENCY: Mapping[Market, str] = MappingProxyType({"CN": "CNY", "HK": "HKD", "US": "USD"})

_NUMBER = r"(\d+(?:\.\d+)?)"
_CN_PLAN = re.compile(rf"10(?:[送转]{_NUMBER}股|股)?(?:派{_NUMBER}元)?\(实施方案\)")
_HK_PLAN = re.compile(rf"末期派息每股股息{_NUMBER}元（([A-Z]+) ?）")
_US_PLAN = re.compile(rf"每股分配{_NUMBER}美元")
_SPLIT = re.compile(rf"每?{_NUMBER}股拆为{_NUMBER}股")


@dataclass(frozen=True, slots=True)
class FactorStep:
    """One change of the factor."""

    date: date
    """First bar with the new factor."""
    ratio: float
    """New factor over old: p * (1 + s + r) / (p - c + r * q), with p the previous close, c the
    cash, s the bonus shares, r the allotted shares, all per share, and q the allotment price."""
    previous_close: float
    """Unadjusted close of the bar before, the p of ``ratio``."""


@dataclass(frozen=True, slots=True)
class AdjustFactors:
    """Factors of one symbol in columns, oldest first. The first need not be 1: AAPL's is 2.18927
    (measured 2026-11-05)."""

    symbol: str
    dates: tuple[date, ...]
    """Days with a factor: those both series have, with four positive prices that agree."""
    factors: tuple[float, ...]
    """A of each day."""
    errors: tuple[float, ...]
    """Largest error of each factor from the upstream's rounding."""
    steps: tuple[FactorStep, ...]
    fetched_at: datetime

    def forward(self) -> tuple[float, ...]:
        """B of each day: raw * B is the forward-adjusted price, and B of the last bar is 1."""
        last = self.factors[-1]
        return tuple(factor / last for factor in self.factors)

    def apply(self, bars: Kline, adjust: Literal["before", "after"]) -> Kline:
        """Adjusted copy of unadjusted daily ``bars``, with prices rounded to 4 decimals as the
        upstream's.

        A bar on a day without a factor gets null prices, because a later day may be an ex-date.
        Volume, amount and turnover carry over, being equal in the three series (measured
        2026-11-05). ``chg`` and ``percent`` are left out: they depend on the adjustment, and the
        upstream's do not always follow from its closes.
        """
        _require_daily(bars, "normal", self.symbol)
        scale_of_day = self.forward() if adjust == "before" else self.factors
        by_day = dict(zip(self.dates, scale_of_day, strict=True))
        scales = [by_day.get(stamp.date()) for stamp in bars.timestamps]
        columns = {
            name: values
            for name, values in bars.columns.items()
            if name not in _DEPENDS_ON_ADJUSTMENT
        }
        for name in _PRICES:
            if name in columns:
                columns[name] = tuple(
                    None if value is None or scale is None else round(value * scale, 4)
                    for value, scale in zip(columns[name], scales, strict=True)
                )
        return replace(bars, adjust=adjust, columns=columns)


@dataclass(slots=True)
class _Segment:
    """Consecutive bars sharing one factor."""

    first: int
    count: int
    low: float
    high: float

    @property
    def factor(self) -> float:
        return (self.low + self.high) / 2


def derive_factors(normal: Kline, after: Kline, *, fetched_at: datetime) -> AdjustFactors:
    """The factors of a symbol from its unadjusted and backward-adjusted daily bars.

    Bars are joined by timestamp, so a bar only one series has gets no factor and makes no step:
    the requests are separate, and the latest bar can change between them. Give both the same end.

    Raises ``InvalidParams`` for other bars, and ``UpstreamChanged`` when no bar gives a factor:
    the upstream can answer with 0 bars and no error.
    """
    _require_daily(normal, "normal", normal.symbol)
    _require_daily(after, "after", normal.symbol)
    raw_rows = _price_rows(normal)
    after_rows = _price_rows(after)
    days: list[date] = []
    closes: list[float] = []
    segments: list[_Segment] = []
    for stamp in sorted(raw_rows.keys() & after_rows.keys()):
        raw = raw_rows[stamp]
        low = max((y - _EPSILON) / x for x, y in zip(raw, after_rows[stamp], strict=True))
        high = min((y + _EPSILON) / x for x, y in zip(raw, after_rows[stamp], strict=True))
        if low > high:
            continue
        current = segments[-1] if segments else None
        if current is not None and max(current.low, low) <= min(current.high, high):
            current.low = max(current.low, low)
            current.high = min(current.high, high)
            current.count += 1
        else:
            segments.append(_Segment(len(days), 1, low, high))
        days.append(stamp.date())
        closes.append(raw[-1])
    if not days:
        raise UpstreamChanged(
            f"{normal.symbol} has {len(normal)} unadjusted and {len(after)} adjusted bars, "
            "none of which gives a factor",
            endpoint="kline",
            params={"symbol": normal.symbol},
        )
    return AdjustFactors(
        symbol=normal.symbol,
        dates=tuple(days),
        factors=tuple(item.factor for item in segments for _ in range(item.count)),
        errors=tuple((item.high - item.low) / 2 for item in segments for _ in range(item.count)),
        steps=tuple(
            FactorStep(days[item.first], item.factor / earlier.factor, closes[item.first - 1])
            for earlier, item in pairwise(segments)
        ),
        fetched_at=fetched_at,
    )


def _require_daily(kline: Kline, adjust: Adjust, symbol: str) -> None:
    if (kline.symbol, kline.adjust, kline.period) != (symbol, adjust, "day"):
        raise InvalidParams(
            f"expected {symbol} {adjust} daily bars, "
            f"not {kline.symbol} {kline.adjust} {kline.period} bars"
        )


def _price_rows(kline: Kline) -> dict[datetime, list[float]]:
    """The prices of each bar, in the order of ``_PRICES``; a bar with one missing or not positive
    is left out, since a factor cannot be read from it and a null is never read as 0."""
    blank = (None,) * len(kline)
    columns = [kline.columns.get(name, blank) for name in _PRICES]
    rows: dict[datetime, list[float]] = {}
    for stamp, *prices in zip(kline.timestamps, *columns, strict=True):
        present = [price for price in prices if price is not None and price > 0]
        if len(present) == len(_PRICES):
            rows[stamp] = present
    return rows


@dataclass(frozen=True, slots=True)
class CorporateAction:
    """One dividend, bonus issue or split, with amounts per share held before it."""

    ex_date: date | None
    """First day it applies; None for a plan not carried out yet."""
    cash_per_share: float | None
    """Cash, 0 when there is none; None when ``text`` could not be read."""
    bonus_per_share: float | None
    """Bonus and capitalization shares, or the shares a split adds, 0 when there are none; None
    when ``text`` could not be read."""
    currency: str | None
    """Currency of the cash; None when there is no cash or ``text`` could not be read."""
    text: str
    """The upstream's text."""
    cancelled: bool


def corporate_actions(
    dividends: Sequence[Dividend], *, splits: Sequence[Split] = ()
) -> tuple[CorporateAction, ...]:
    """Actions from the rows of f10_bonus and f10_holders.

    Only the text forms measured on 2026-11-05 are read, each with its exact wording. Any other
    text gives null amounts and is not guessed at, so its ratio is reported as skipped.

    Allotments are left out: the upstream sends no ratio and no ex-rights date, so a rights issue
    shows as a step without an action.
    """
    return (*map(_from_dividend, dividends), *map(_from_split, splits))


def _from_dividend(dividend: Dividend) -> CorporateAction:
    text = dividend.plan or ""
    cash, bonus, currency = _read_plan(text)
    return CorporateAction(
        ex_date=dividend.ex_date,
        cash_per_share=cash,
        bonus_per_share=bonus,
        currency=currency if cash else None,
        text=text,
        cancelled=dividend.cancelled_date is not None,
    )


def _read_plan(text: str) -> tuple[float | None, float | None, str | None]:
    """Cash per share, bonus shares per share and currency of a dividend plan."""
    if (match := _CN_PLAN.fullmatch(text)) and (match[1] or match[2]):
        return float(match[2] or 0) / 10, float(match[1] or 0) / 10, "CNY"
    if match := _HK_PLAN.fullmatch(text):
        return float(match[1]), 0.0, match[2]
    if match := _US_PLAN.fullmatch(text):
        return float(match[1]), 0.0, "USD"
    return None, None, None


def _from_split(split: Split) -> CorporateAction:
    text = split.explain or ""
    match = _SPLIT.fullmatch(text)
    ratio = float(match[2]) / float(match[1]) if match and float(match[1]) > 0 else 0.0
    return CorporateAction(
        # US rows have an ex-date. Hong Kong rows do not, and 00700's effective date of 2014-05-15
        # is the day its unadjusted price falls (measured 2026-11-05).
        ex_date=split.ex_date or split.effective_date,
        cash_per_share=0.0 if ratio else None,
        bonus_per_share=ratio - 1 if ratio else None,
        currency=None,
        text=text,
        cancelled=False,
    )


def reconcile_factors(
    factors: AdjustFactors,
    actions: Sequence[CorporateAction],
    *,
    normal: Kline,
    before: Kline | None = None,
    share_changes: Sequence[ShareChange] | None = None,
) -> QualityReport:
    """Check factors against the corporate actions and the bars they came from.

    An action applies to the first bar on or after its ex-date, so an ex-date during a suspension
    falls on the bar that ends it, and all actions on one bar count as one. Cancelled actions are
    left out, and so are ones that apply to the first bar or after the last, because no step could
    show them. A step without an action is a warning: SH600000's step of 1.3 on 2006-05-12 has no
    dividend row, and neither has SH601166's rights issue of 2010-06-02 (measured 2026-11-05).

    ``normal`` holds the bars the factors came from. ``before``, if given, is the upstream's
    forward-adjusted series as fetched with them: it ends on the same day and uses the same base.
    ``share_changes`` turns on a comparison whose findings are only informational, because how the
    date of a share change relates to the ex-date has not been measured.
    """
    _require_daily(normal, "normal", factors.symbol)
    if before is not None:
        _require_daily(before, "before", factors.symbol)
    applied = _actions_by_bar(factors, actions)
    currency = _QUOTE_CURRENCY[normalize_symbol(factors.symbol).market]
    return QualityReport(
        subject=f"{factors.symbol} factors",
        results=(
            _bars_have_factor(factors, normal),
            _step_has_action(factors, applied),
            _ratio_matches_action(factors, applied, currency),
            _action_has_step(factors, actions, applied, normal, currency),
            _before_matches(factors, normal, before),
            _shares_match_bonus(actions, share_changes),
        ),
    )


def _actions_by_bar(
    factors: AdjustFactors, actions: Sequence[CorporateAction]
) -> dict[date, list[CorporateAction]]:
    applied: dict[date, list[CorporateAction]] = {}
    for action in actions:
        if action.ex_date is None or action.cancelled:
            continue
        index = bisect_left(factors.dates, action.ex_date)
        if 0 < index < len(factors.dates):
            applied.setdefault(factors.dates[index], []).append(action)
    return applied


def _expected_ratio(
    actions: Sequence[CorporateAction], previous_close: float, currency: str
) -> float | None:
    """p * (1 + s) / (p - c) for the actions on one bar; None when something is not known."""
    cash = bonus = 0.0
    for action in actions:
        if action.cash_per_share is None or action.bonus_per_share is None:
            return None
        if action.cash_per_share and action.currency != currency:
            return None
        cash += action.cash_per_share
        bonus += action.bonus_per_share
    if not actions or cash >= previous_close:
        return None
    return previous_close * (1 + bonus) / (previous_close - cash)


def _bars_have_factor(factors: AdjustFactors, normal: Kline) -> CheckResult:
    tally = CheckTally("factor.bars_have_factor")
    rows = _price_rows(normal)
    tally.skipped_rows = len(normal) - len(rows)
    known = set(factors.dates)
    for stamp in rows:
        tally.checked += 1
        if stamp.date() not in known:
            tally.found(
                "warning",
                stamp.date().isoformat(),
                "the bar has prices but no factor: the adjusted series lacks it or disagrees",
            )
    return tally.result("no bar has all four prices")


def _step_has_action(
    factors: AdjustFactors, applied: Mapping[date, Sequence[CorporateAction]]
) -> CheckResult:
    tally = CheckTally("factor.step_has_action")
    for step in factors.steps:
        tally.checked += 1
        if step.date not in applied:
            tally.found(
                "warning",
                step.date.isoformat(),
                "the factor changes with no corporate action since the bar before",
                observed=step.ratio,
            )
    return tally.result("the factor never changes")


def _ratio_matches_action(
    factors: AdjustFactors, applied: Mapping[date, Sequence[CorporateAction]], currency: str
) -> CheckResult:
    tally = CheckTally("factor.ratio_matches_action")
    for step in factors.steps:
        actions = applied.get(step.date, ())
        expected = _expected_ratio(actions, step.previous_close, currency)
        if expected is None:
            tally.skipped_rows += 1
            continue
        tally.checked += 1
        if abs(step.ratio / expected - 1) > _RATIO_TOLERANCE:
            tally.found(
                "warning",
                step.date.isoformat(),
                f"the step does not fit {'; '.join(action.text for action in actions)}",
                observed=step.ratio,
                expected=expected,
            )
    return tally.result("no step has an action whose amounts are known")


def _action_has_step(
    factors: AdjustFactors,
    actions: Sequence[CorporateAction],
    applied: Mapping[date, Sequence[CorporateAction]],
    normal: Kline,
    currency: str,
) -> CheckResult:
    tally = CheckTally("factor.action_has_step")
    tally.skipped_rows = sum(action.ex_date is None and not action.cancelled for action in actions)
    stepped = {step.date for step in factors.steps}
    closes = {stamp.date(): row[-1] for stamp, row in _price_rows(normal).items()}
    for day, group in applied.items():
        tally.checked += 1
        if day in stepped:
            continue
        index = bisect_left(factors.dates, day)
        previous_close = closes.get(factors.dates[index - 1])
        expected = (
            None if previous_close is None else _expected_ratio(group, previous_close, currency)
        )
        text = "; ".join(action.text for action in group)
        # A factor's interval spans all it could be, so a change no wider than that cannot show.
        resolution = 2 * factors.errors[index] / factors.factors[index]
        if expected is not None and abs(expected - 1) <= resolution:
            tally.found(
                "info", day.isoformat(), f"too small to show as a step: {text}", expected=expected
            )
        else:
            tally.found("warning", day.isoformat(), f"no step for {text}", expected=expected)
    return tally.result("no action applies within the bars")


def _before_matches(factors: AdjustFactors, normal: Kline, before: Kline | None) -> CheckResult:
    tally = CheckTally("factor.before_matches")
    if before is None:
        return tally.result("no before series given")
    if not before.timestamps or before.timestamps[-1].date() != factors.dates[-1]:
        return tally.result("the before series ends on another day, so it has another base")
    upstream = _price_rows(before)
    raw_rows = _price_rows(normal)
    tally.skipped_rows = len(normal) - len(raw_rows)
    scales = dict(zip(factors.dates, factors.forward(), strict=True))
    errors = {
        day: error / factor
        for day, factor, error in zip(factors.dates, factors.factors, factors.errors, strict=True)
    }
    for stamp, raw in raw_rows.items():
        day = stamp.date()
        if stamp not in upstream or day not in scales:
            tally.skipped_rows += 1
            continue
        tally.checked += 1
        # Rounding gives a local price an error too, proportional to it, and it is large when the
        # base comes from a few cheap bars just after an ex-date.
        relative_error = errors[day] + errors[factors.dates[-1]]
        for price, reference in zip(raw, upstream[stamp], strict=True):
            local = price * scales[day]
            if abs(local - reference) > 2 * _EPSILON + reference * relative_error:
                tally.found(
                    "error",
                    day.isoformat(),
                    "the local forward-adjusted price differs from the upstream's",
                    observed=local,
                    expected=reference,
                )
                break
    return tally.result("no bar has a factor and prices in both series")


def _shares_match_bonus(
    actions: Sequence[CorporateAction], share_changes: Sequence[ShareChange] | None
) -> CheckResult:
    tally = CheckTally("factor.shares_match_bonus")
    if share_changes is None:
        return tally.result("no share changes given")
    bonuses: dict[date, float] = {}
    for action in actions:
        if action.ex_date is not None and action.bonus_per_share and not action.cancelled:
            bonuses[action.ex_date] = bonuses.get(action.ex_date, 0.0) + action.bonus_per_share
    dated = [(change.chg_date, change) for change in share_changes if change.chg_date is not None]
    dated.sort(key=lambda pair: pair[0])
    for (_, earlier), (day, change) in pairwise(dated):
        reason = change.chg_reason or ""
        if not any(word in reason for word in _BONUS_REASONS):
            continue
        if not earlier.total_shares or change.total_shares is None:
            tally.skipped_rows += 1
            continue
        tally.checked += 1
        ratio = change.total_shares / earlier.total_shares
        nearest = min(bonuses, key=lambda ex_date: abs(ex_date - day), default=None)
        if nearest is None or abs(nearest - day) > _SHARE_CHANGE_SLACK:
            tally.found(
                "info", day.isoformat(), f"no bonus issue near this: {reason}", observed=ratio
            )
        elif abs(ratio / (1 + bonuses[nearest]) - 1) > _SHARE_RATIO_TOLERANCE:
            tally.found(
                "info",
                day.isoformat(),
                f"total shares do not fit the bonus issue of {nearest.isoformat()}",
                observed=ratio,
                expected=1 + bonuses[nearest],
            )
    return tally.result("no share change comes from a bonus issue")
