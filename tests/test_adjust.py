"""Adjustment factors: generated series, the checks against corporate actions, and real series.

Series are generated here, each with the events that made it, so steps can be compared one by one.
Real series are local and uncommitted: see ``test_quality_samples``.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple, cast
from zoneinfo import ZoneInfo

import pytest

from xueqiu_api import REGISTRY, InvalidParams, UpstreamChanged
from xueqiu_api.adjust import (
    AdjustFactors,
    CorporateAction,
    corporate_actions,
    derive_factors,
    reconcile_factors,
)
from xueqiu_api.endpoints.f10_bonus import BonusPage, Dividend
from xueqiu_api.endpoints.f10_holders import Holders, Split
from xueqiu_api.endpoints.f10_share_changes import ShareChange
from xueqiu_api.endpoints.kline import ENDPOINT as KLINE
from xueqiu_api.endpoints.kline import Adjust, Kline
from xueqiu_api.quality import CheckResult, QualityReport

from .test_quality_samples import parse_bars, recorded_responses, samples_root

SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")
FETCHED_AT = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)
PRICES = ("open", "high", "low", "close")
EPSILON = 0.55e-4
FLOAT_NOISE = 1e-9
ShockOf = Callable[[random.Random, float], "Shock"]


class Shock(NamedTuple):
    """What one corporate action does per share."""

    cash: float = 0.0
    bonus: float = 0.0
    rights: float = 0.0
    rights_price: float = 0.0

    def ratio(self, close: float) -> float:
        adjusted = close - self.cash + self.rights * self.rights_price
        return close * (1 + self.bonus + self.rights) / adjusted


class TrueStep(NamedTuple):
    day: date
    ratio: float
    previous_close: float


@dataclass
class Generated:
    normal: Kline
    after: Kline
    before: Kline
    steps: list[TrueStep]
    factors: list[float]


def weekdays(first: date, count: int) -> list[date]:
    days = (first + timedelta(days=offset) for offset in range(count * 2))
    return [day for day in days if day.weekday() < 5][:count]


def series(
    symbol: str,
    adjust: Adjust,
    days: Sequence[date],
    rows: Sequence[Sequence[float | None]],
    *,
    zone: ZoneInfo = SHANGHAI,
    **extra: tuple[float | int | None, ...],
) -> Kline:
    stamps = tuple(datetime(day.year, day.month, day.day, tzinfo=zone) for day in days)
    columns = {name: tuple(row[index] for row in rows) for index, name in enumerate(PRICES)}
    return Kline(symbol, "day", adjust, stamps, {**columns, **extra})


def simulate(
    seed: int,
    shock_of: ShockOf,
    *,
    start: float = 20.0,
    tick: float = 0.01,
    floor: float = 1.0,
    first_factor: float = 1.0,
    size: int = 2000,
    events: int = 40,
) -> Generated:
    """A random walk with events at least 30 bars apart, on the upstream's rounding."""
    rng = random.Random(seed)
    ex_bars = {spot * 30 for spot in rng.sample(range(1, size // 30), events)}
    days = weekdays(date(2015, 1, 5), size)

    def on_tick(value: float) -> float:
        return max(tick, round(round(value / tick) * tick, 6))

    rows: list[tuple[float, float, float, float]] = []
    steps: list[TrueStep] = []
    factors: list[float] = []
    factor, close = first_factor, start
    for index, day in enumerate(days):
        level = close
        if index in ex_bars:
            ratio = shock_of(rng, close).ratio(close)
            steps.append(TrueStep(day, ratio, close))
            factor *= ratio
            level = close / ratio
        level = max(floor, level * (1 + rng.gauss(0.0003, 0.02)))
        close = on_tick(level)
        open_ = on_tick(level * (1 + rng.gauss(0, 0.005)))
        high = max(open_, close, on_tick(level * 1.01))
        low = min(open_, close, on_tick(level * 0.99))
        rows.append((open_, high, low, close))
        factors.append(factor)
    last = factors[-1]
    return Generated(
        normal=series("SH600000", "normal", days, rows),
        after=series(
            "SH600000",
            "after",
            days,
            [[round(price * a, 4) for price in row] for row, a in zip(rows, factors, strict=True)],
        ),
        before=series(
            "SH600000",
            "before",
            days,
            [
                [round(price * a / last, 4) for price in row]
                for row, a in zip(rows, factors, strict=True)
            ],
        ),
        steps=steps,
        factors=factors,
    )


def cash_only(rng: random.Random, close: float) -> Shock:
    return Shock(cash=max(0.01, round(close * rng.uniform(0.005, 0.05), 2)))


def bonus_and_cash(rng: random.Random, close: float) -> Shock:
    return Shock(cash=max(0.01, round(close * 0.01, 2)), bonus=rng.choice((0.1, 0.3, 0.5, 1.0)))


def rights_issue(rng: random.Random, close: float) -> Shock:
    return Shock(rights=rng.choice((0.1, 0.2, 0.3)), rights_price=round(close * 0.8, 2))


def split(rng: random.Random, close: float) -> Shock:
    return Shock(bonus=rng.choice((1.0, 3.0, 6.0)))


def penny_cash(rng: random.Random, close: float) -> Shock:
    return Shock(cash=max(0.001, round(close * rng.uniform(0.02, 0.05), 3)))


CASES: list[tuple[str, ShockOf, dict[str, Any]]] = [
    ("cash", cash_only, {}),
    ("bonus", bonus_and_cash, {"events": 12}),
    ("rights", rights_issue, {}),
    ("split", split, {"start": 500.0, "events": 6}),
    ("low_price", penny_cash, {"start": 0.5, "tick": 0.001, "floor": 0.05}),
]


def generated_cases() -> list[Any]:
    return [
        pytest.param(seed, shock_of, options, id=f"{name}-{seed}")
        for name, shock_of, options in CASES
        for seed in range(3)
    ]


@pytest.mark.parametrize(("seed", "shock_of", "options"), generated_cases())
def test_steps_are_the_events_and_factors_are_within_the_rounding_bound(
    seed: int, shock_of: ShockOf, options: dict[str, Any]
) -> None:
    made = simulate(seed, shock_of, **options)
    factors = derive_factors(made.normal, made.after, fetched_at=FETCHED_AT)

    assert factors.symbol == "SH600000"
    assert factors.fetched_at == FETCHED_AT
    assert list(factors.dates) == sorted(factors.dates)
    assert len(factors.dates) == len(factors.factors) == len(factors.errors) == len(made.normal)
    assert [(step.date, step.previous_close) for step in factors.steps] == [
        (step.day, step.previous_close) for step in made.steps
    ]
    for derived, true, error in zip(factors.factors, made.factors, factors.errors, strict=True):
        assert abs(derived - true) <= error + FLOAT_NOISE
    relative = {
        day: error / factor
        for day, factor, error in zip(factors.dates, factors.factors, factors.errors, strict=True)
    }
    for index, (step, true_step) in enumerate(zip(factors.steps, made.steps, strict=True)):
        before_step = factors.dates[factors.dates.index(step.date) - 1]
        bound = relative[step.date] + relative[before_step] + FLOAT_NOISE
        assert abs(step.ratio / true_step.ratio - 1) <= bound, index


@pytest.mark.parametrize(("seed", "shock_of", "options"), generated_cases())
def test_local_prices_stay_within_two_epsilon_of_the_upstream_chain(
    seed: int, shock_of: ShockOf, options: dict[str, Any]
) -> None:
    made = simulate(seed, shock_of, **options)
    factors = derive_factors(made.normal, made.after, fetched_at=FETCHED_AT)

    for adjust, upstream in (("before", made.before), ("after", made.after)):
        local = factors.apply(made.normal, cast(Any, adjust))
        assert local.adjust == adjust
        for name in PRICES:
            gaps = [
                abs(cast(float, ours) - cast(float, theirs))
                for ours, theirs in zip(local.columns[name], upstream.columns[name], strict=True)
            ]
            assert max(gaps) <= 2 * EPSILON, (adjust, name)
    report = reconcile_factors(factors, (), normal=made.normal, before=made.before)
    assert result(report, "factor.before_matches").status == "pass"


def test_the_plan_prices_give_the_factors_and_the_prediction() -> None:
    days = (date(2024, 6, 3), date(2026, 11, 4))
    normal = series("SH600000", "normal", days, [[8.28] * 4, [10.09] * 4])
    after = series("SH600000", "after", days, [[128.7202] * 4, [175.4816] * 4])

    factors = derive_factors(normal, after, fetched_at=FETCHED_AT)

    assert factors.factors[0] == pytest.approx(15.545918, abs=1e-6)
    # The upstream's 7.4013 has 4 decimals, so 7.4013 / 8.28 = 0.893877 is good to about 7e-6.
    assert factors.forward()[0] == pytest.approx(0.893877, abs=7e-6)
    assert factors.apply(normal, "before").columns["close"][0] == pytest.approx(7.4013, abs=2e-4)
    # From before and after of 2024-06-03 alone: measured 175.4816 against a predicted 175.4809.
    assert round(10.09 * 128.7202 / 7.4013, 2) == 175.48
    assert round(cast(float, factors.apply(normal, "after").columns["close"][1]), 2) == 175.48


def test_the_first_factor_need_not_be_one() -> None:
    made = simulate(0, cash_only, first_factor=2.18927)

    factors = derive_factors(made.normal, made.after, fetched_at=FETCHED_AT)

    assert factors.factors[0] == pytest.approx(2.18927, abs=factors.errors[0] + FLOAT_NOISE)
    assert len(factors.steps) == len(made.steps)


def test_an_index_factor_stays_one() -> None:
    made = simulate(1, cash_only, events=0)
    days = [stamp.date() for stamp in made.normal.timestamps]
    index = series("SH000001", "normal", days, rows_of(made.normal))
    same = series("SH000001", "after", days, rows_of(made.normal))

    factors = derive_factors(index, same, fetched_at=FETCHED_AT)

    assert factors.steps == ()
    assert factors.factors == pytest.approx((1.0,) * len(index), abs=1e-12)
    assert factors.forward() == pytest.approx((1.0,) * len(index), abs=1e-12)


def rows_of(kline: Kline) -> list[list[float | None]]:
    return [
        [cast(float | None, kline.columns[name][index]) for name in PRICES]
        for index in range(len(kline))
    ]


DAYS = tuple(weekdays(date(2026, 7, 13), 6))
"""2026-07-13 to 2026-07-20; DAYS[3] is 2026-07-16."""


def pair(
    closes: Sequence[float], factors: Sequence[float], *, days: Sequence[date] = DAYS
) -> tuple[Kline, Kline]:
    """Unadjusted and adjusted bars, each bar with four equal prices."""
    normal = series("SH600000", "normal", days, [[close] * 4 for close in closes])
    after = series(
        "SH600000",
        "after",
        days,
        [[round(close * a, 4)] * 4 for close, a in zip(closes, factors, strict=True)],
    )
    return normal, after


# 10派4.2元 on a close of 9.31, measured for SH600000 on 2026-07-16.
CLOSES = (9.2, 9.3, 9.31, 8.85, 8.9, 8.8)
STEPPED = (17.0, 17.0, 17.0, 17.0 * 9.31 / 8.89, 17.0 * 9.31 / 8.89, 17.0 * 9.31 / 8.89)


def test_the_error_of_a_factor_is_half_its_interval() -> None:
    normal, after = pair((10.0, 20.0), (1.5, 1.5), days=DAYS[:2])

    factors = derive_factors(normal, after, fetched_at=FETCHED_AT)

    assert factors.factors == pytest.approx((1.5, 1.5))
    assert factors.errors == pytest.approx((EPSILON / 20.0, EPSILON / 20.0))


def test_series_are_joined_by_timestamp_and_not_by_position() -> None:
    normal, after = pair(CLOSES, STEPPED)
    # Dropping a leading bar of one series and an inner bar of the other shifts every position.
    short_normal = cut(normal, 1, len(normal))
    short_after = glue(cut(after, 0, 1), cut(after, 2, len(after)))

    factors = derive_factors(short_normal, short_after, fetched_at=FETCHED_AT)

    assert factors.dates == (DAYS[2], DAYS[3], DAYS[4], DAYS[5])
    assert [step.date for step in factors.steps] == [DAYS[3]]
    assert factors.factors[0] == pytest.approx(17.0, abs=1e-4)
    assert factors.steps[0].previous_close == 9.31


def test_the_order_of_the_bars_does_not_matter() -> None:
    normal, after = pair(CLOSES, STEPPED)
    reversed_after = glue(cut(after, 3, len(after)), cut(after, 0, 3))

    factors = derive_factors(normal, reversed_after, fetched_at=FETCHED_AT)

    assert list(factors.dates) == sorted(factors.dates)
    assert [step.date for step in factors.steps] == [DAYS[3]]


def test_a_last_bar_only_in_after_makes_no_step() -> None:
    normal, after = pair(CLOSES[:-1], STEPPED[:-1], days=DAYS[:-1])
    _, moving = pair(CLOSES, (*STEPPED[:-1], 30.0))

    factors = derive_factors(normal, moving, fetched_at=FETCHED_AT)

    assert factors.dates == DAYS[:-1]
    assert [step.date for step in factors.steps] == [DAYS[3]]
    assert derive_factors(normal, after, fetched_at=FETCHED_AT).steps == factors.steps


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param([None, 9.0, 9.0, 9.0], id="null"),
        pytest.param([0.0, 9.0, 9.0, 9.0], id="zero"),
    ],
)
def test_a_bar_without_four_positive_prices_gets_no_factor_and_no_step(
    broken: list[float | None],
) -> None:
    normal, after = pair(CLOSES, (17.0,) * 6)
    rows = rows_of(normal)
    rows[2] = broken
    holed = series("SH600000", "normal", DAYS, rows)

    factors = derive_factors(holed, after, fetched_at=FETCHED_AT)

    assert DAYS[2] not in factors.dates
    assert factors.steps == ()


def test_a_bar_whose_prices_disagree_gets_no_factor_and_no_step() -> None:
    normal, after = pair(CLOSES, (17.0,) * 6)
    rows = rows_of(after)
    rows[2] = [cast(float, rows[2][0]) * 1.5, *rows[2][1:]]
    dirty = series("SH600000", "after", DAYS, rows)

    factors = derive_factors(normal, dirty, fetched_at=FETCHED_AT)

    assert DAYS[2] not in factors.dates
    assert factors.steps == ()
    check = result(reconcile_factors(factors, (), normal=normal), "factor.bars_have_factor")
    assert [finding.where for finding in check.findings] == [DAYS[2].isoformat()]


@pytest.mark.parametrize(
    "emptied",
    [
        pytest.param("normal", id="no_unadjusted_bars"),
        pytest.param("after", id="no_adjusted_bars"),
        pytest.param("apart", id="no_common_bars"),
    ],
)
def test_no_usable_bar_raises(emptied: str) -> None:
    normal, after = pair(CLOSES, STEPPED)
    if emptied == "normal":
        normal = cut(normal, 0, 0)
    elif emptied == "after":
        after = cut(after, 0, 0)
    else:
        normal, after = cut(normal, 0, 3), cut(after, 3, 6)

    with pytest.raises(UpstreamChanged, match="none of which gives a factor"):
        derive_factors(normal, after, fetched_at=FETCHED_AT)


def glue(head: Kline, tail: Kline) -> Kline:
    return Kline(
        head.symbol,
        head.period,
        head.adjust,
        head.timestamps + tail.timestamps,
        {name: values + tail.columns[name] for name, values in head.columns.items()},
    )


def cut(kline: Kline, first: int, last: int) -> Kline:
    return Kline(
        kline.symbol,
        kline.period,
        kline.adjust,
        kline.timestamps[first:last],
        {name: values[first:last] for name, values in kline.columns.items()},
    )


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"symbol": "SH600001"}, id="other_symbol"),
        pytest.param({"adjust": "before"}, id="other_adjustment"),
        pytest.param({"period": "week"}, id="other_period"),
    ],
)
def test_other_bars_are_refused(change: dict[str, str]) -> None:
    normal, after = pair(CLOSES, STEPPED)
    other = Kline(
        change.get("symbol", after.symbol),
        cast(Any, change.get("period", after.period)),
        cast(Any, change.get("adjust", after.adjust)),
        after.timestamps,
        after.columns,
    )

    with pytest.raises(InvalidParams):
        derive_factors(normal, other, fetched_at=FETCHED_AT)


def test_a_series_in_the_wrong_slot_is_refused() -> None:
    normal, after = pair(CLOSES, STEPPED)

    with pytest.raises(InvalidParams):
        derive_factors(after, normal, fetched_at=FETCHED_AT)


@pytest.fixture
def stepped() -> tuple[AdjustFactors, Kline, Kline]:
    normal, after = pair(CLOSES, STEPPED)
    return derive_factors(normal, after, fetched_at=FETCHED_AT), normal, after


def test_forward_ends_at_one_and_scales_earlier_days(
    stepped: tuple[AdjustFactors, Kline, Kline],
) -> None:
    factors, _, _ = stepped

    forward = factors.forward()

    assert forward[-1] == 1.0
    assert forward[0] == pytest.approx(8.89 / 9.31, abs=1e-5)


def test_apply_scales_prices_and_keeps_what_the_adjustment_leaves_alone(
    stepped: tuple[AdjustFactors, Kline, Kline],
) -> None:
    factors, normal, _ = stepped
    extra: dict[str, tuple[float | int | None, ...]] = {
        "volume": (1, 2, 3, 4, 5, 6),
        "turnoverrate": (0.1,) * 6,
        "chg": (0.5,) * 6,
        "percent": (1.0,) * 6,
    }
    bars = Kline(normal.symbol, "day", "normal", normal.timestamps, {**normal.columns, **extra})

    local = factors.apply(bars, "before")

    assert local.adjust == "before"
    assert local.symbol == bars.symbol
    assert local.columns["volume"] == extra["volume"]
    assert local.columns["turnoverrate"] == extra["turnoverrate"]
    assert "chg" not in local.columns
    assert "percent" not in local.columns
    assert local.columns["close"][-1] == 8.8
    assert local.columns["close"][0] == round(9.2 * factors.forward()[0], 4)
    assert normal.columns["close"][0] == 9.2


def test_apply_gives_null_prices_on_a_day_without_a_factor(
    stepped: tuple[AdjustFactors, Kline, Kline],
) -> None:
    factors, normal, _ = stepped
    later_day = (*DAYS, date(2026, 7, 21))
    rows = [*rows_of(normal), [None, 9.0, 9.0, 9.0]]

    local = factors.apply(series("SH600000", "normal", later_day, rows), "after")

    assert local.columns["high"][-1] is None
    assert local.columns["open"][-1] is None
    assert local.columns["high"][-2] is not None


@pytest.mark.parametrize(
    "bars",
    [
        pytest.param(series("SH600000", "after", DAYS, [[9.0] * 4] * 6), id="adjusted"),
        pytest.param(series("SH600001", "normal", DAYS, [[9.0] * 4] * 6), id="other_symbol"),
    ],
)
def test_apply_refuses_other_bars(bars: Kline, stepped: tuple[AdjustFactors, Kline, Kline]) -> None:
    factors, _, _ = stepped

    with pytest.raises(InvalidParams):
        factors.apply(bars, "before")


def dividend(zone: ZoneInfo = SHANGHAI, **fields: Any) -> Dividend:
    return Dividend.model_validate(fields, context={"tz": zone})


def on(day: date, zone: ZoneInfo = SHANGHAI) -> int:
    midnight = datetime(day.year, day.month, day.day, tzinfo=zone)
    return int(midnight.timestamp() * 1000)


@pytest.mark.parametrize(
    ("text", "cash", "bonus", "currency"),
    [
        ("10派4.2元(实施方案)", 0.42, 0.0, "CNY"),
        ("10派280.2423元(实施方案)", 28.02423, 0.0, "CNY"),
        ("10送3股派1.6元(实施方案)", 0.16, 0.3, "CNY"),
        ("10转3股派2元(实施方案)", 0.2, 0.3, "CNY"),
        ("10股派1元(实施方案)", 0.1, 0.0, "CNY"),
        ("10转10股(实施方案)", 0.0, 1.0, None),
        ("末期派息每股股息5.3元（HKD ）", 5.3, 0.0, "HKD"),
        ("每股分配0.27美元", 0.27, 0.0, "USD"),
    ],
)
def test_the_measured_plan_forms_are_read(
    text: str, cash: float, bonus: float, currency: str | None
) -> None:
    action = corporate_actions([dividend(plan_explain=text)])[0]

    assert action.cash_per_share == pytest.approx(cash)
    assert action.bonus_per_share == pytest.approx(bonus)
    assert action.currency == currency
    assert action.text == text


@pytest.mark.parametrize(
    "text",
    [
        "中期现金分红金额占比不高于三分之一(股东大会预案)",
        "10派4.2元(股东大会预案)",
        "10(实施方案)",
        "特别派息每股股息1元（HKD ）",
        "每股分配0.27港元",
        "",
    ],
)
def test_any_other_plan_is_not_guessed_at(text: str) -> None:
    action = corporate_actions([dividend(plan_explain=text)])[0]

    assert action.cash_per_share is None
    assert action.bonus_per_share is None
    assert action.currency is None


def test_a_dividend_keeps_its_dates_and_its_cancellation() -> None:
    day = date(2026, 7, 16)
    upstream_rows = [
        dividend(plan_explain="10派4.2元(实施方案)", ashare_ex_dividend_date=on(day)),
        dividend(
            plan_explain="10派4.2元(实施方案)",
            ashare_ex_dividend_date=on(day),
            cancle_dividend_date=on(day),
        ),
        dividend(plan_explain="10派4.2元(实施方案)", ashare_ex_dividend_date=None),
    ]

    actions = corporate_actions(upstream_rows)

    assert [action.ex_date for action in actions] == [day, day, None]
    assert [action.cancelled for action in actions] == [False, True, False]


@pytest.mark.parametrize(
    ("row", "zone", "ex_date", "bonus"),
    [
        pytest.param(
            {
                "explain": "每1.0股拆为4.0股",
                "split_valid_date": on(date(2020, 8, 28), NEW_YORK),
                "exright_date": on(date(2020, 8, 31), NEW_YORK),
            },
            NEW_YORK,
            date(2020, 8, 31),
            3.0,
            id="us_uses_the_ex_date",
        ),
        pytest.param(
            {"explain": "1股拆为5股", "sdatetpatr": on(date(2014, 5, 15))},
            SHANGHAI,
            date(2014, 5, 15),
            4.0,
            id="hk_uses_the_effective_date",
        ),
        pytest.param(
            {"explain": "每2.0股拆为3.0股", "exright_date": on(date(2020, 8, 31), NEW_YORK)},
            NEW_YORK,
            date(2020, 8, 31),
            0.5,
            id="the_ratio_is_new_over_old",
        ),
    ],
)
def test_a_split_reads_as_added_shares(
    row: dict[str, Any], zone: ZoneInfo, ex_date: date, bonus: float
) -> None:
    split_row = Split.model_validate(row, context={"tz": zone})

    action = corporate_actions((), splits=[split_row])[0]

    assert action.ex_date == ex_date
    assert action.cash_per_share == 0.0
    assert action.bonus_per_share == pytest.approx(bonus)
    assert action.currency is None
    assert not action.cancelled


@pytest.mark.parametrize("text", ["合股", "0股拆为5股", "每1股拆为0股"])
def test_a_split_text_that_cannot_be_read_gives_null_amounts(text: str) -> None:
    split_row = Split.model_validate(
        {"explain": text, "exright_date": on(date(2020, 8, 31))}, context={"tz": SHANGHAI}
    )

    action = corporate_actions((), splits=[split_row])[0]

    assert action.cash_per_share is None
    assert action.bonus_per_share is None


def action(
    day: date | None = DAYS[3],
    *,
    cash: float | None = 0.42,
    bonus: float | None = 0.0,
    currency: str | None = "CNY",
    text: str = "10派4.2元(实施方案)",
    cancelled: bool = False,
) -> CorporateAction:
    return CorporateAction(day, cash, bonus, currency, text, cancelled)


def result(report: QualityReport, check: str) -> CheckResult:
    return next(item for item in report.results if item.check == check)


def test_a_clean_reconciliation_passes_every_check_it_can_run(
    stepped: tuple[AdjustFactors, Kline, Kline],
) -> None:
    factors, normal, _ = stepped
    prices = [close * scale for close, scale in zip(CLOSES, factors.forward(), strict=True)]

    report = reconcile_factors(factors, [action()], normal=normal, before=before_series(prices))

    assert report.subject == "SH600000 factors"
    assert report.ok
    assert {item.check: item.status for item in report.results} == {
        "factor.bars_have_factor": "pass",
        "factor.step_has_action": "pass",
        "factor.ratio_matches_action": "pass",
        "factor.action_has_step": "pass",
        "factor.before_matches": "pass",
        "factor.shares_match_bonus": "skipped",
    }


def test_a_bar_without_a_factor_is_a_warning(stepped: tuple[AdjustFactors, Kline, Kline]) -> None:
    factors, normal, _ = stepped
    later = (*DAYS, date(2026, 7, 21))
    rows = [*rows_of(normal), [9.0] * 4]
    holed = [*rows_of(normal), [None, 9.0, 9.0, 9.0]]

    check = result(
        reconcile_factors(factors, [action()], normal=series("SH600000", "normal", later, rows)),
        "factor.bars_have_factor",
    )
    null_bar = result(
        reconcile_factors(factors, [action()], normal=series("SH600000", "normal", later, holed)),
        "factor.bars_have_factor",
    )

    assert check.status == "fail"
    assert [finding.severity for finding in check.findings] == ["warning"]
    assert check.findings[0].where == "2026-07-21"
    assert null_bar.status == "pass"
    assert null_bar.skipped_rows == 1


def test_a_step_without_a_dividend_row_is_a_warning_and_the_report_stays_ok() -> None:
    # SH600000 stepped by 1.3 on 2006-05-12 and has no dividend row for it (measured 2026-11-05).
    days = (date(2006, 3, 17), date(2006, 5, 12), date(2006, 5, 15))
    normal, after = pair((13.0, 10.0, 10.1), (2.0, 2.6, 2.6), days=days)
    factors = derive_factors(normal, after, fetched_at=FETCHED_AT)

    report = reconcile_factors(factors, [], normal=normal)

    check = result(report, "factor.step_has_action")
    assert report.ok
    assert check.status == "fail"
    assert [(finding.severity, finding.where) for finding in check.findings] == [
        ("warning", "2006-05-12")
    ]
    assert check.findings[0].observed == pytest.approx(1.3, abs=1e-4)
    ratio = result(report, "factor.ratio_matches_action")
    assert ratio.status == "skipped"
    assert ratio.skipped_rows == 1


def test_a_series_without_steps_has_nothing_to_match() -> None:
    normal, after = pair(CLOSES, (1.0,) * 6)
    factors = derive_factors(normal, after, fetched_at=FETCHED_AT)

    report = reconcile_factors(factors, (), normal=normal)

    assert result(report, "factor.step_has_action").status == "skipped"
    assert result(report, "factor.ratio_matches_action").status == "skipped"
    assert result(report, "factor.action_has_step").status == "skipped"
    assert result(report, "factor.before_matches").status == "skipped"
    assert result(report, "factor.shares_match_bonus").status == "skipped"


def test_an_ex_date_during_a_suspension_falls_on_the_bar_that_ends_it() -> None:
    days = (date(2026, 7, 13), date(2026, 7, 14), date(2026, 7, 20), date(2026, 7, 21))
    normal, after = pair((9.31, 9.31, 8.85, 8.9), (17.0, 17.0, 17.8031, 17.8031), days=days)
    factors = derive_factors(normal, after, fetched_at=FETCHED_AT)

    report = reconcile_factors(factors, [action(date(2026, 7, 16))], normal=normal)

    assert result(report, "factor.step_has_action").status == "pass"
    assert result(report, "factor.ratio_matches_action").status == "pass"
    assert result(report, "factor.action_has_step").status == "pass"


def test_a_ratio_too_far_from_the_action_is_a_warning(
    stepped: tuple[AdjustFactors, Kline, Kline],
) -> None:
    factors, normal, _ = stepped

    near = reconcile_factors(factors, [action(cash=0.42 * (1 + 1e-6))], normal=normal)
    far = reconcile_factors(factors, [action(cash=0.42 * 1.001)], normal=normal)

    assert result(near, "factor.ratio_matches_action").status == "pass"
    check = result(far, "factor.ratio_matches_action")
    assert check.status == "fail"
    assert check.findings[0].severity == "warning"
    assert check.findings[0].where == DAYS[3].isoformat()
    assert check.findings[0].expected == pytest.approx(9.31 / (9.31 - 0.42 * 1.001))


@pytest.mark.parametrize("cash", [0.0, 0.54, 1.35])
def test_a_wrong_amount_of_a_small_dividend_is_a_warning(cash: float) -> None:
    # AAPL paid 0.27 on a close of 313.33, a step of under 0.1% (measured 2026-11-05).
    closes = (313.0, 313.2, 313.33, 313.06, 314.0, 315.0)
    step = 313.33 / (313.33 - 0.27)
    normal, after = pair(closes, (292.0, 292.0, 292.0, 292.0 * step, 292.0 * step, 292.0 * step))
    factors = derive_factors(normal, after, fetched_at=FETCHED_AT)

    right = reconcile_factors(factors, [action(cash=0.27)], normal=normal)
    wrong = reconcile_factors(factors, [action(cash=cash)], normal=normal)

    assert result(right, "factor.ratio_matches_action").status == "pass"
    assert result(wrong, "factor.ratio_matches_action").status == "fail"


@pytest.mark.parametrize(
    ("seed", "shock_of", "options"),
    [case for case in generated_cases() if case.id.startswith(("cash", "low_price"))],
)
def test_the_ratio_check_tells_the_right_cash_from_twice_as_much(
    seed: int, shock_of: ShockOf, options: dict[str, Any]
) -> None:
    made = simulate(seed, shock_of, **options)
    factors = derive_factors(made.normal, made.after, fetched_at=FETCHED_AT)

    def ratio_check(scale: float) -> CheckResult:
        rows = [
            action(step.day, cash=scale * step.previous_close * (1 - 1 / step.ratio))
            for step in made.steps
        ]
        report = reconcile_factors(factors, rows, normal=made.normal)
        return result(report, "factor.ratio_matches_action")

    right, doubled = ratio_check(1.0), ratio_check(2.0)

    assert (right.status, right.checked, right.total_findings) == ("pass", len(made.steps), 0)
    assert doubled.total_findings == len(made.steps)


def test_an_action_on_the_second_bar_is_compared() -> None:
    step = 9.2 / (9.2 - 0.42)
    normal, after = pair(CLOSES, (1.0, step, step, step, step, step))
    factors = derive_factors(normal, after, fetched_at=FETCHED_AT)

    report = reconcile_factors(factors, [action(DAYS[1])], normal=normal)

    assert result(report, "factor.ratio_matches_action").checked == 1
    assert result(report, "factor.action_has_step").checked == 1


def test_actions_on_one_bar_count_as_one(stepped: tuple[AdjustFactors, Kline, Kline]) -> None:
    factors, normal, _ = stepped
    # SH600519 has both 10转10股 and 10派3元 on 2006-05-19 (measured 2026-11-05).
    rows = [
        action(cash=0.21, text="10派2.1元(实施方案)"),
        action(cash=0.21, text="10派2.1元(实施方案)"),
    ]

    report = reconcile_factors(factors, rows, normal=normal)

    check = result(report, "factor.ratio_matches_action")
    assert check.status == "pass"
    assert check.checked == 1
    assert result(report, "factor.action_has_step").checked == 1


@pytest.mark.parametrize(
    ("row", "ratio"),
    [
        pytest.param(action(cash=0.16, bonus=0.3), 1.3 * 9.31 / 9.15, id="bonus_and_cash"),
        pytest.param(action(cash=0.0, bonus=0.3, currency=None), 1.3, id="bonus_only"),
    ],
)
def test_bonus_shares_enter_the_expected_ratio(row: CorporateAction, ratio: float) -> None:
    normal, after = pair(CLOSES, (1.0, 1.0, 1.0, ratio, ratio, ratio))
    factors = derive_factors(normal, after, fetched_at=FETCHED_AT)

    report = reconcile_factors(factors, [row], normal=normal)

    check = result(report, "factor.ratio_matches_action")
    assert check.status == "pass"
    assert check.checked == 1


@pytest.mark.parametrize(
    "row",
    [
        pytest.param(action(cash=None, bonus=None, currency=None, text="预案"), id="unread_text"),
        pytest.param(action(cash=1.0, currency="USD"), id="cash_in_another_currency"),
        pytest.param(action(cash=9.31), id="cash_as_large_as_the_price"),
    ],
)
def test_a_ratio_that_cannot_be_compared_is_skipped(
    row: CorporateAction, stepped: tuple[AdjustFactors, Kline, Kline]
) -> None:
    factors, normal, _ = stepped

    report = reconcile_factors(factors, [row], normal=normal)

    check = result(report, "factor.ratio_matches_action")
    assert check.status == "skipped"
    assert check.skipped_rows == 1
    assert result(report, "factor.step_has_action").status == "pass"


def test_an_action_without_a_step_is_a_warning() -> None:
    normal, after = pair(CLOSES, (17.0,) * 6)
    factors = derive_factors(normal, after, fetched_at=FETCHED_AT)

    check = result(
        reconcile_factors(factors, [action(cash=0.42)], normal=normal), "factor.action_has_step"
    )

    assert check.status == "fail"
    assert [(finding.severity, finding.where) for finding in check.findings] == [
        ("warning", DAYS[3].isoformat())
    ]
    assert check.findings[0].expected == pytest.approx(9.31 / 8.89)


def test_a_missing_step_is_expected_from_the_close_before() -> None:
    rows = [[close, close + 1, close - 1, close] for close in CLOSES]
    normal = series("SH600000", "normal", DAYS, rows)
    after = series("SH600000", "after", DAYS, [[17.0 * price for price in row] for row in rows])
    factors = derive_factors(normal, after, fetched_at=FETCHED_AT)

    check = result(reconcile_factors(factors, [action()], normal=normal), "factor.action_has_step")

    assert check.findings[0].expected == pytest.approx(9.31 / 8.89)


def test_a_text_that_was_not_read_and_has_no_step_is_a_warning() -> None:
    normal, after = pair(CLOSES, (17.0,) * 6)
    factors = derive_factors(normal, after, fetched_at=FETCHED_AT)
    row = action(cash=None, bonus=None, currency=None, text="预案")

    check = result(reconcile_factors(factors, [row], normal=normal), "factor.action_has_step")

    assert check.status == "fail"
    assert check.findings[0].severity == "warning"
    assert check.findings[0].expected is None


def test_an_action_below_the_resolution_is_only_info() -> None:
    # At a price of 0.05 a cash amount of 0.00002 does not move an adjusted price by a tick.
    closes = (0.05,) * 6
    normal, after = pair(closes, (1.0,) * 6)
    factors = derive_factors(normal, after, fetched_at=FETCHED_AT)

    check = result(
        reconcile_factors(factors, [action(cash=0.00002)], normal=normal), "factor.action_has_step"
    )

    assert check.status == "pass"
    assert [finding.severity for finding in check.findings] == ["info"]


@pytest.mark.parametrize(
    ("cash", "severity"),
    [(0.000002, "info"), (0.000008, "warning"), (0.001, "warning")],
)
def test_the_resolution_is_relative_to_the_factor(cash: float, severity: str) -> None:
    # A factor of 17 at a price of 0.05 has an error of 1.1e-3, so a change above 1.3e-4 could show.
    normal, after = pair((0.05,) * 6, (17.0,) * 6)
    factors = derive_factors(normal, after, fetched_at=FETCHED_AT)

    check = result(
        reconcile_factors(factors, [action(cash=cash)], normal=normal), "factor.action_has_step"
    )

    assert [finding.severity for finding in check.findings] == [severity]


def test_future_cancelled_and_undated_actions_are_left_out(
    stepped: tuple[AdjustFactors, Kline, Kline],
) -> None:
    factors, normal, _ = stepped
    rows = [
        action(),
        action(date(2026, 11, 12), text="future"),
        action(date(2026, 7, 13), text="on the first bar"),
        action(date(2020, 1, 2), text="before the first bar"),
        action(date(2026, 7, 15), cancelled=True, text="cancelled"),
        action(None, text="not carried out"),
        action(None, cancelled=True, text="cancelled plan"),
    ]

    report = reconcile_factors(factors, rows, normal=normal)

    has_step = result(report, "factor.action_has_step")
    assert has_step.status == "pass"
    assert has_step.findings == ()
    assert has_step.checked == 1
    assert has_step.skipped_rows == 4  # after, on and before the first bar, and without a date
    assert result(report, "factor.ratio_matches_action").checked == 1


def test_only_future_or_cancelled_actions_leave_the_checks_skipped() -> None:
    normal, after = pair(CLOSES, (1.0,) * 6)
    factors = derive_factors(normal, after, fetched_at=FETCHED_AT)
    rows = [action(date(2026, 11, 12)), action(DAYS[3], cancelled=True)]

    check = result(reconcile_factors(factors, rows, normal=normal), "factor.action_has_step")

    assert check.status == "skipped"
    assert check.reason == "no action applies within the bars"


def test_a_cancelled_action_explains_no_step(stepped: tuple[AdjustFactors, Kline, Kline]) -> None:
    factors, normal, _ = stepped

    report = reconcile_factors(factors, [action(cancelled=True)], normal=normal)

    assert result(report, "factor.step_has_action").status == "fail"


def before_series(prices: Sequence[float], *, days: Sequence[date] = DAYS) -> Kline:
    return series("SH600000", "before", days, [[price] * 4 for price in prices])


def test_a_before_series_off_the_local_prices_is_an_error(
    stepped: tuple[AdjustFactors, Kline, Kline],
) -> None:
    factors, normal, _ = stepped
    prices = [
        round(close * scale, 4) for close, scale in zip(CLOSES, factors.forward(), strict=True)
    ]
    prices[1] += 0.01

    report = reconcile_factors(factors, [action()], normal=normal, before=before_series(prices))

    check = result(report, "factor.before_matches")
    assert not report.ok
    assert check.status == "fail"
    assert [(finding.severity, finding.where) for finding in check.findings] == [
        ("error", DAYS[1].isoformat())
    ]
    assert check.findings[0].expected == prices[1]


@pytest.mark.parametrize(("gap", "status"), [(1e-4, "pass"), (1.4e-4, "fail"), (2e-4, "fail")])
def test_a_before_series_may_differ_by_two_epsilon(
    gap: float, status: str, stepped: tuple[AdjustFactors, Kline, Kline]
) -> None:
    factors, normal, _ = stepped
    prices = [close * scale for close, scale in zip(CLOSES, factors.forward(), strict=True)]
    prices[1] += gap

    report = reconcile_factors(factors, [action()], normal=normal, before=before_series(prices))

    assert result(report, "factor.before_matches").status == status


def test_a_few_cheap_bars_after_an_ex_date_do_not_read_as_a_before_mismatch() -> None:
    # The stock fell tenfold, and the one bar after the ex-date only gives its factor to about
    # 1e-5. A flat two epsilon would flag the old, higher prices though nothing is wrong.
    days = DAYS[:4]
    true_factors = (1.0, 1.0, 1.0, 1.0123)
    closes = (30.0, 30.0, 30.0, 3.03)
    normal, after = pair(closes, true_factors, days=days)
    factors = derive_factors(normal, after, fetched_at=FETCHED_AT)
    upstream = before_series(
        [round(close * a / 1.0123, 4) for close, a in zip(closes, true_factors, strict=True)],
        days=days,
    )
    flat_gap = abs(30.0 * factors.forward()[0] - 30.0 / 1.0123)

    check = result(
        reconcile_factors(factors, (), normal=normal, before=upstream), "factor.before_matches"
    )

    assert flat_gap > 2 * EPSILON
    assert check.status == "pass"


def test_a_before_series_ending_on_another_day_is_skipped(
    stepped: tuple[AdjustFactors, Kline, Kline],
) -> None:
    factors, normal, _ = stepped
    short = before_series([1.0] * 5, days=DAYS[:5])

    check = result(
        reconcile_factors(factors, [action()], normal=normal, before=short),
        "factor.before_matches",
    )

    assert check.status == "skipped"
    assert check.reason == "the before series ends on another day, so it has another base"


def test_before_bars_without_prices_on_either_side_are_skipped_rows(
    stepped: tuple[AdjustFactors, Kline, Kline],
) -> None:
    factors, normal, _ = stepped
    prices = [
        round(close * scale, 4) for close, scale in zip(CLOSES, factors.forward(), strict=True)
    ]
    rows: list[list[float | None]] = [[price] * 4 for price in prices]
    rows[2] = [None, None, None, None]

    check = result(
        reconcile_factors(
            factors, [action()], normal=normal, before=series("SH600000", "before", DAYS, rows)
        ),
        "factor.before_matches",
    )

    assert check.status == "pass"
    assert check.checked == 5
    assert check.skipped_rows == 1


def share_change(day: date, reason: str | None, total: int | None) -> ShareChange:
    return ShareChange.model_validate(
        {"chg_date": on(day), "chg_reason": reason, "total_shares": total},
        context={"tz": SHANGHAI},
    )


SHARES_START = share_change(date(2026, 7, 1), "可转债转股", 100)
BONUS = action(bonus=0.3, cash=0.0, currency=None, text="10送3股(实施方案)")


@pytest.mark.parametrize("reason", ["送股", "资本公积转增"])
def test_share_changes_of_a_bonus_issue_fit_it(
    reason: str, stepped: tuple[AdjustFactors, Kline, Kline]
) -> None:
    factors, normal, _ = stepped
    # Latest first, as the upstream sends them; the date of the change need not be the ex-date.
    changes = [share_change(DAYS[5], reason, 130), SHARES_START]

    check = result(
        reconcile_factors(factors, [BONUS], normal=normal, share_changes=changes),
        "factor.shares_match_bonus",
    )

    assert check.status == "pass"
    assert check.checked == 1
    assert check.findings == ()


def test_share_changes_that_miss_the_bonus_issue_are_only_info(
    stepped: tuple[AdjustFactors, Kline, Kline],
) -> None:
    factors, normal, _ = stepped
    changes = [SHARES_START, share_change(DAYS[3], "送股", 110)]

    report = reconcile_factors(factors, [BONUS], normal=normal, share_changes=changes)

    check = result(report, "factor.shares_match_bonus")
    assert report.ok
    assert check.status == "pass"
    assert [finding.severity for finding in check.findings] == ["info"]
    assert check.findings[0].observed == pytest.approx(1.1)
    assert check.findings[0].expected == pytest.approx(1.3)


def test_a_bonus_share_change_far_from_any_action_is_only_info(
    stepped: tuple[AdjustFactors, Kline, Kline],
) -> None:
    factors, normal, _ = stepped
    changes = [SHARES_START, share_change(date(2026, 10, 20), "送股", 130)]

    check = result(
        reconcile_factors(factors, [BONUS], normal=normal, share_changes=changes),
        "factor.shares_match_bonus",
    )

    assert [finding.severity for finding in check.findings] == ["info"]
    assert "no bonus issue near" in check.findings[0].message


def test_other_share_changes_are_not_compared(
    stepped: tuple[AdjustFactors, Kline, Kline],
) -> None:
    factors, normal, _ = stepped
    changes = [
        share_change(DAYS[3], "可转债转股", 150),
        SHARES_START,
        share_change(DAYS[4], "配股", None),
        share_change(DAYS[5], "送股", None),
        ShareChange.model_validate(
            {"chg_date": None, "chg_reason": "送股", "total_shares": 130},
            context={"tz": SHANGHAI},
        ),
    ]

    check = result(
        reconcile_factors(factors, [BONUS], normal=normal, share_changes=changes),
        "factor.shares_match_bonus",
    )

    assert check.status == "skipped"
    assert check.checked == 0
    assert check.skipped_rows == 1


def test_a_cancelled_bonus_issue_explains_no_share_change(
    stepped: tuple[AdjustFactors, Kline, Kline],
) -> None:
    factors, normal, _ = stepped
    changes = [SHARES_START, share_change(DAYS[3], "送股", 130)]
    cancelled = action(bonus=0.3, cash=0.0, currency=None, text="取消", cancelled=True)

    check = result(
        reconcile_factors(factors, [cancelled], normal=normal, share_changes=changes),
        "factor.shares_match_bonus",
    )

    assert [finding.severity for finding in check.findings] == ["info"]


def test_two_bonus_rows_on_one_day_add_up(stepped: tuple[AdjustFactors, Kline, Kline]) -> None:
    factors, normal, _ = stepped
    rows = [
        action(bonus=0.1, cash=0.0, currency=None, text="10送1股(实施方案)"),
        action(bonus=0.2, cash=0.0, currency=None, text="10转2股(实施方案)"),
    ]
    changes = [SHARES_START, share_change(DAYS[3], "送股、转增", 130)]

    check = result(
        reconcile_factors(factors, rows, normal=normal, share_changes=changes),
        "factor.shares_match_bonus",
    )

    assert check.findings == ()
    assert check.checked == 1


def test_reconciling_other_bars_is_refused(stepped: tuple[AdjustFactors, Kline, Kline]) -> None:
    factors, normal, after = stepped

    with pytest.raises(InvalidParams):
        reconcile_factors(factors, (), normal=after)
    with pytest.raises(InvalidParams):
        reconcile_factors(factors, (), normal=normal, before=after)


REAL_BONUS_PATH = re.compile(r"/v5/stock/f10/[a-z]+/bonus\.json")
REAL_HOLDERS_PATH = re.compile(r"/v5/stock/f10/[a-z]+/holders\.json")
REAL_SHARE_CHANGES_PATH = "/v5/stock/f10/cn/shareschg.json"
KNOWN_STEPS_WITHOUT_ACTION = {
    # A step of 1.3 with no dividend row, during a suspension from 2006-03-20 (measured 2026-11-05).
    "SH600000": ["2006-05-12"],
    # A rights issue, whose rows have no ex-rights date (measured 2026-11-05).
    "SH601166": ["2010-06-02"],
}
"""Steps of the real series that no recorded action explains."""


@dataclass
class RealSamples:
    bars: dict[tuple[str, str], Kline] = field(default_factory=dict[tuple[str, str], Kline])
    dividends: dict[str, BonusPage] = field(default_factory=dict[str, BonusPage])
    holders: dict[str, Holders] = field(default_factory=dict[str, Holders])
    share_changes: dict[str, list[ShareChange]] = field(
        default_factory=dict[str, list[ShareChange]]
    )

    def factors(self, symbol: str) -> AdjustFactors:
        return derive_factors(
            self.bars[symbol, "normal"], self.bars[symbol, "after"], fetched_at=FETCHED_AT
        )

    def actions(self, symbol: str) -> tuple[CorporateAction, ...]:
        holders = self.holders.get(symbol)
        return corporate_actions(
            self.dividends[symbol].items, splits=holders.splits if holders else ()
        )

    def report(self, symbol: str) -> QualityReport:
        return reconcile_factors(
            self.factors(symbol),
            self.actions(symbol),
            normal=self.bars[symbol, "normal"],
            before=self.bars.get((symbol, "before")),
            share_changes=self.share_changes.get(symbol),
        )


def load_real_samples(root: Path) -> RealSamples:
    """The longest daily series and the fullest rows of each symbol recorded under ``root``."""
    real = RealSamples()
    for path, query, body in recorded_responses(root):
        envelope = cast(dict[str, Any], body) if isinstance(body, dict) else {}
        data = envelope.get("data")
        symbol = query.get("symbol")
        if not isinstance(data, dict) or not data or not isinstance(symbol, str):
            continue
        if path == KLINE.path:
            if query.get("period") == "day" and cast(dict[str, Any], data).get("item"):
                kline = parse_bars(query, data)
                known = real.bars.get((kline.symbol, kline.adjust))
                if known is None or len(kline) > len(known):
                    real.bars[kline.symbol, kline.adjust] = kline
        elif REAL_BONUS_PATH.fullmatch(path) and int(query.get("page", 1)) == 1:
            spec = REGISTRY["f10_bonus"]
            params = {"symbol": symbol, "page_size": int(query.get("size", 50))}
            page: BonusPage = spec.parse(data, spec.validate(params))
            known_page = real.dividends.get(symbol)
            if known_page is None or len(page.items) > len(known_page.items):
                real.dividends[symbol] = page
        elif REAL_HOLDERS_PATH.fullmatch(path):
            spec = REGISTRY["f10_holders"]
            real.holders[symbol] = spec.parse(data, spec.validate({"symbol": symbol}))
        elif path == REAL_SHARE_CHANGES_PATH:
            spec = REGISTRY["f10_share_changes"]
            real.share_changes[symbol] = spec.parse(data, spec.validate({"symbol": symbol}))
    return real


@pytest.fixture(scope="module")
def real() -> RealSamples:
    root = samples_root()
    if root is None:
        pytest.skip("XUEQIU_SAMPLES_DIR is not set")
    return load_real_samples(root)


@pytest.mark.parametrize(
    ("symbol", "steps", "last_factor"),
    [("SH600000", 27, 17.391634), ("SH601166", 22, 5.991199)],
)
def test_real_steps_are_the_dividend_rows_and_the_known_exceptions(
    real: RealSamples, symbol: str, steps: int, last_factor: float
) -> None:
    factors = real.factors(symbol)
    report = real.report(symbol)

    assert report.ok
    assert len(factors.steps) == steps
    assert factors.factors[-1] == pytest.approx(last_factor, abs=1e-6)
    without_action = result(report, "factor.step_has_action")
    assert [finding.where for finding in without_action.findings] == (
        KNOWN_STEPS_WITHOUT_ACTION[symbol]
    )
    ratios = result(report, "factor.ratio_matches_action")
    assert ratios.status == "pass"
    assert ratios.checked == steps - 1
    assert result(report, "factor.action_has_step").findings == ()
    assert result(report, "factor.bars_have_factor").findings == ()


def test_real_prices_rebuild_from_the_factors(real: RealSamples) -> None:
    for symbol in ("SH600000", "SH601166", "AAPL"):
        normal = real.bars[symbol, "normal"]
        factors = real.factors(symbol)
        local = factors.apply(normal, "after")
        upstream = real.bars[symbol, "after"]
        for name in PRICES:
            gaps = [
                abs(cast(float, ours) - cast(float, theirs))
                for ours, theirs in zip(local.columns[name], upstream.columns[name], strict=True)
            ]
            assert max(gaps) <= 2 * EPSILON, (symbol, name)


def test_real_before_series_matches_on_every_bar(real: RealSamples) -> None:
    check = result(real.report("SH600000"), "factor.before_matches")

    assert check.status == "pass"
    assert check.checked == len(real.bars["SH600000", "before"])


def test_the_real_plan_prices_fit_the_real_chain(real: RealSamples) -> None:
    factors = real.factors("SH600000")
    by_day = dict(zip(factors.dates, factors.factors, strict=True))
    forward = dict(zip(factors.dates, factors.forward(), strict=True))

    assert by_day[date(2024, 6, 3)] == pytest.approx(15.545918, abs=1e-5)
    assert forward[date(2024, 6, 3)] == pytest.approx(0.893877, abs=1e-5)


def test_the_real_rights_issue_step_fits_its_price(real: RealSamples) -> None:
    # 10配2 at 18.0: the upstream has no ratio field, so 0.2 comes from outside its rows.
    step = next(step for step in real.factors("SH601166").steps if step.date == date(2010, 6, 2))

    close = step.previous_close
    assert step.ratio == pytest.approx(close * 1.2 / (close + 0.2 * 18.0), rel=1e-6)
    assert real.dividends["SH601166"].allotments[0].allot_price == 18.0


def test_real_us_steps_include_splits_and_cash(real: RealSamples) -> None:
    factors = real.factors("AAPL")
    report = real.report("AAPL")

    assert len(factors.steps) == 61
    assert factors.factors[0] == pytest.approx(2.18927, abs=1e-5)
    assert factors.factors[-1] == pytest.approx(292.7847, abs=1e-4)
    assert {step.date: round(step.ratio, 6) for step in factors.steps if step.ratio > 1.5} == {
        date(2000, 6, 21): 2.0,
        date(2005, 2, 28): 2.0,
        date(2014, 6, 9): 7.0,
        date(2020, 8, 31): 4.0,
    }
    assert result(report, "factor.action_has_step").findings == ()
    ratios = result(report, "factor.ratio_matches_action")
    assert ratios.findings == ()
    # The recorded rows are 3 splits and 2 past dividends; the third has a future ex-date.
    assert ratios.checked == 5
    assert result(report, "factor.action_has_step").skipped_rows == 1


@pytest.mark.parametrize("symbol", ["SH600000", "SH601166", "AAPL"])
def test_a_wrong_amount_of_a_real_dividend_is_noticed(real: RealSamples, symbol: str) -> None:
    factors = real.factors(symbol)
    normal = real.bars[symbol, "normal"]
    actions = real.actions(symbol)
    steps = {step.date for step in factors.steps}
    cash_only = [
        row
        for row in actions
        if row.ex_date in steps and row.cash_per_share and not row.bonus_per_share
    ]
    assert cash_only

    def findings(*changed: CorporateAction) -> int:
        kept = [row for row in actions if row not in cash_only]
        report = reconcile_factors(factors, [*kept, *changed], normal=normal)
        return result(report, "factor.ratio_matches_action").total_findings

    assert findings(*cash_only) == 0
    for row in cash_only:
        others = [item for item in cash_only if item is not row]
        for scale in (0.0, 2.0):
            wrong = replace(row, cash_per_share=cast(float, row.cash_per_share) * scale)
            assert findings(wrong, *others) == 1, (row.ex_date, scale)


def test_real_samples_run_every_check_that_has_rows(real: RealSamples) -> None:
    checked: dict[str, int] = {}
    for symbol in ("SH600000", "SH601166", "AAPL"):
        for item in real.report(symbol).results:
            checked[item.check] = checked.get(item.check, 0) + item.checked

    # The one exception to every check having rows: the only recorded share changes are 可转债转股
    # (measured 2026-11-05), so this check has run on no real bonus issue, and it may read any
    # number of them once one is recorded.
    checked.pop("factor.shares_match_bonus")
    assert all(count > 0 for count in checked.values()), checked
    assert len(checked) == 5
