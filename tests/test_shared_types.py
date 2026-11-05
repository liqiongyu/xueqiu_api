"""Models and value parsers the endpoint modules share."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, ClassVar, cast
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel, ValidationError

from xueqiu_api._f10 import ExchangeDate, ReportOption, region_query, report_date_from_label
from xueqiu_api._model import NoParams, QuoteRow, UnverifiedModel, UpstreamModel
from xueqiu_api._time import date_from_ms
from xueqiu_api._values import (
    CnAmount,
    FractionPoints,
    Number,
    PercentPoints,
    WholeNumber,
    cn_amount,
    fraction_to_points,
    number,
    percent_points,
    rank_total,
)
from xueqiu_api.symbols import SymbolParams

SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")
MIDNIGHT_IN_SHANGHAI = 1782748800000
"""2026-06-30 00:00 in Shanghai, still 06-29 in UTC and in New York."""


class _Row(UpstreamModel):
    symbol: str


class _RowWithDrop(UpstreamModel):
    drop: ClassVar[frozenset[str]] = frozenset({"prompt"})

    symbol: str


class _Unverified(UnverifiedModel):
    symbol: str


def test_upstream_model_removes_user_keys_at_every_depth() -> None:
    row = _Row.model_validate(
        {
            "symbol": "ZH0000001",
            "owner": {"id": 1},
            "owner_id": 1,
            "contractor_name": "someone",
            "user": {"id": 1},
            "kept": 1,
            "rows": [
                {"owner": {"id": 2}, "contractor_name": "someone", "value": 1},
                {"inner": [{"screen_name": "someone", "avatar_url": "x", "value": 2}]},
            ],
            "nested": {"contractor_id": 3, "value": 3},
        }
    )

    assert row.model_extra == {
        "kept": 1,
        "rows": [{"value": 1}, {"inner": [{"value": 2}]}],
        "nested": {"value": 3},
    }


def test_upstream_model_removes_the_keys_it_lists_and_keeps_others() -> None:
    row = _RowWithDrop.model_validate({"symbol": "x", "prompt": "p", "rows": [{"prompt": "q"}]})

    assert row.model_extra == {"rows": [{}]}


def test_upstream_model_is_frozen_and_keeps_unknown_fields() -> None:
    row = _Row.model_validate({"symbol": "x", "new_field": 1})

    assert row.model_extra == {"new_field": 1}
    with pytest.raises(ValidationError):
        cast(Any, row).symbol = "y"


def test_a_required_field_named_like_a_user_key_is_missing_after_the_filter() -> None:
    class Owned(UpstreamModel):
        owner: str

    with pytest.raises(ValidationError):
        Owned.model_validate({"owner": "someone"})


def test_unverified_model_keeps_no_undeclared_keys() -> None:
    row = _Unverified.model_validate({"symbol": "x", "other": 1, "owner": {"id": 1}})

    assert row.model_dump() == {"symbol": "x"}
    assert not row.model_extra


def test_no_params_takes_no_params() -> None:
    assert NoParams.model_validate({}).model_dump() == {}
    with pytest.raises(ValidationError):
        NoParams.model_validate({"symbol": "x"})


def test_quote_row_reads_both_upstream_spellings() -> None:
    plain = QuoteRow.model_validate(
        {"symbol": "BJ899050", "name": "n", "current": 894.08, "chg": 39.02, "percent": 4.56}
    )
    strings = QuoteRow.model_validate(
        {"code": "02888", "current": "219.4", "percentage": "2.81", "change": "6.0", "pe_ttm": ""}
    )
    empty = QuoteRow.model_validate({"code": "02888", "current": "", "percentage": "--"})

    assert (plain.symbol, plain.current, plain.chg, plain.percent) == (
        "BJ899050",
        894.08,
        39.02,
        4.56,
    )
    assert (strings.symbol, strings.current, strings.chg, strings.percent) == (
        "02888",
        219.4,
        6.0,
        2.81,
    )
    assert strings.model_extra == {"pe_ttm": ""}
    assert (empty.current, empty.percent) == (None, None)


@pytest.mark.parametrize(
    ("parse", "value", "expected"),
    [
        (number, 1, 1.0),
        (number, 1.5, 1.5),
        (number, "1.5", 1.5),
        (number, " -1.5 ", -1.5),
        (number, "+2", 2.0),
        (number, "4.75476923138E11", 475476923138.0),
        (number, None, None),
        (number, "", None),
        (number, "--", None),
        (percent_points, "51.59%", 51.59),
        (percent_points, "+20.48%", 20.48),
        (percent_points, "-3", -3.0),
        (percent_points, 1.5, 1.5),
        (percent_points, "--", None),
        (fraction_to_points, 0.5575, 55.75),
        (fraction_to_points, "0.07", 7.0),
        (fraction_to_points, -0.33452879, -33.452879),
        (fraction_to_points, "--", None),
        (cn_amount, "7.55亿", 755000000.0),
        (cn_amount, "1234.56万", 12345600.0),
        (cn_amount, "1.5万亿", 1500000000000.0),
        (cn_amount, "12", 12.0),
        (cn_amount, 12, 12.0),
        (cn_amount, "", None),
        (cn_amount, "--", None),
        (rank_total, "9/4521", (9, 4521)),
        (rank_total, "--", None),
        (rank_total, None, None),
    ],
)
def test_value_parsers(parse: Callable[[Any], Any], value: Any, expected: Any) -> None:
    assert parse(value) == expected


@pytest.mark.parametrize(
    ("parse", "value"),
    [
        (number, "n/a"),
        (number, "1%"),
        (number, "nan"),
        (number, "inf"),
        (number, float("nan")),
        (number, True),
        (number, [1]),
        (percent_points, "x%"),
        (fraction_to_points, "x"),
        (cn_amount, "3千"),
        (cn_amount, "亿"),
        (rank_total, "9"),
        (rank_total, "a/b"),
        (rank_total, 9),
    ],
)
def test_value_parsers_reject_anything_else(parse: Callable[[Any], Any], value: Any) -> None:
    with pytest.raises(ValueError):
        parse(value)


class _Values(BaseModel):
    plain: Number = None
    percent: PercentPoints = None
    fraction: FractionPoints = None
    amount: CnAmount = None
    whole: WholeNumber = None
    code: str | None = None


def test_value_types_apply_per_field_and_leave_other_fields_alone() -> None:
    values = _Values.model_validate(
        {
            "plain": "1.5",
            "percent": "2%",
            "fraction": 0.5575,
            "amount": "7.55亿",
            "whole": "934",
            "code": "008975",
        }
    )

    assert values.model_dump() == {
        "plain": 1.5,
        "percent": 2.0,
        "fraction": 55.75,
        "amount": 755000000.0,
        "whole": 934,
        "code": "008975",
    }
    assert _Values.model_validate({"whole": "--"}).whole is None
    with pytest.raises(ValidationError):
        _Values.model_validate({"plain": "n/a"})


def test_date_from_ms_reads_the_date_in_the_given_timezone() -> None:
    assert date_from_ms(MIDNIGHT_IN_SHANGHAI, SHANGHAI) == date(2026, 6, 30)
    assert date_from_ms(MIDNIGHT_IN_SHANGHAI, NEW_YORK) == date(2026, 6, 29)
    assert date_from_ms(MIDNIGHT_IN_SHANGHAI, UTC) == date(2026, 6, 29)
    assert date_from_ms("2026-06-30", SHANGHAI) == "2026-06-30"
    assert date_from_ms(None, SHANGHAI) is None
    assert date_from_ms(True, SHANGHAI) is True


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("2026一季报", date(2026, 3, 31)),
        ("2026中报", date(2026, 6, 30)),
        ("2026三季报", date(2026, 9, 30)),
        ("2025年报", date(2025, 12, 31)),
        ("2023-11-21", date(2023, 11, 21)),
        ("2026年FY", None),
        ("", None),
        (None, None),
        (20261231, None),
    ],
)
def test_report_date_from_label(label: object, expected: date | None) -> None:
    assert report_date_from_label(label) == expected


def test_region_query_follows_the_market_of_the_symbol() -> None:
    now = datetime(2026, 11, 5, tzinfo=UTC)
    for symbol, region in (("SH600000", "cn"), ("00700", "hk"), ("AAPL", "us")):
        params = SymbolParams.model_validate({"symbol": symbol})
        assert region_query(params, now) == {"region": region, "symbol": symbol}


class _Dated(BaseModel):
    day: ExchangeDate = None


def test_exchange_date_reads_the_timezone_from_the_validation_context() -> None:
    row = {"day": MIDNIGHT_IN_SHANGHAI}

    assert _Dated.model_validate(row, context={"tz": SHANGHAI}).day == date(2026, 6, 30)
    assert _Dated.model_validate(row, context={"tz": NEW_YORK}).day == date(2026, 6, 29)
    assert _Dated.model_validate({"day": None}, context={"tz": SHANGHAI}).day is None


def test_report_option_names_a_period_and_its_end() -> None:
    option = ReportOption.model_validate(
        {"name": "2026中报", "value": MIDNIGHT_IN_SHANGHAI}, context={"tz": SHANGHAI}
    )

    assert (option.report_name, option.report_date) == ("2026中报", date(2026, 6, 30))
