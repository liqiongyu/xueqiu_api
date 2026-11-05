"""Models and params shared by the index valuation endpoints.

Dates and times are read in Asia/Shanghai for every index, also Hong Kong and US ones: the 63
indexes of one list shared one midnight timestamp, measured 2026-11-05.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, ClassVar, Literal, Self, cast

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from ._finance import REPORT_DATE_ZONE
from ._model import UpstreamModel
from ._result import Fetched
from ._time import date_from_ms, from_ms
from ._values import FractionPoints, Number
from .endpoints.kline import BarDate, check_range
from .symbols import IndexCode

ValuationWindow = Literal["all", "3y", "5y"]
"""History windows: all, 3y and 5y work, 1y and 10y get error 999001, measured 2026-11-05."""


class IndexValuation(UpstreamModel):
    """Valuation of one index on its latest date, as Danjuan computes it."""

    drop: ClassVar[frozenset[str]] = frozenset({"date", "created_at"})
    """``date`` is a month and a day with no year, and ``created_at`` repeats ``updated_at``."""

    index_code: str
    """Danjuan's code, such as SH000300, CSIH30269, NDX or 935600."""
    name: str | None = None
    trade_date: date | None = Field(default=None, validation_alias="ts")
    """Date of the values."""
    begin_at: date | None = None
    """Upstream name kept, because its meaning is unconfirmed: perhaps where the history behind
    the percentiles starts. It differs between indexes: 14 dates among the 63 measured 2026-11-05,
    2016-07-20 on 44 of them and 2020-07-27 on HKHSTECH."""
    pe: Number = None
    """Price to earnings, as a multiple; None when the upstream has none and sends 0."""
    pb: Number = None
    """Price to book, as a multiple; None when the upstream has none and sends 0."""
    pe_percentile: FractionPoints = None
    """Where ``pe`` stands in the index's own history, in percent points (69.36 means 69.36%)."""
    pb_percentile: FractionPoints = None
    """Where ``pb`` stands in the index's own history, in percent points."""
    pe_over_history: FractionPoints = None
    """In percent points; equalled 100 minus ``pe_percentile`` on all 63 indexes measured
    2026-11-05."""
    pb_over_history: FractionPoints = None
    """In percent points; equalled 100 minus ``pb_percentile`` on all 63 indexes measured
    2026-11-05."""
    roe: FractionPoints = None
    """Return on equity, in percent points; None when it is sent as 0 next to a placeholder
    ``pe``, the one row of 63 measured 2026-11-05 with a ``roe`` of 0."""
    dividend_yield: FractionPoints = Field(default=None, validation_alias="yeild")
    """Dividend yield, in percent points."""
    bond_yield: FractionPoints = Field(default=None, validation_alias="bond_yeild")
    """Bond yield Danjuan compares with, in percent points; 5 on all 63 indexes measured
    2026-11-05."""
    peg: Number = None
    """PE to growth, as a multiple; 8 of 63 indexes had none, measured 2026-11-05."""
    eva_type: str | None = None
    """Danjuan's verdict: low, mid or high were measured."""
    pb_flag: bool | None = None
    """Upstream flag, whose meaning is unconfirmed; 国证地产 sets it, 沪深300 does not."""
    updated_at: datetime | None = None
    """When Danjuan wrote the row, in Asia/Shanghai; rows of one list differ by hours."""

    @model_validator(mode="before")
    @classmethod
    def _no_placeholders(cls, value: Any) -> Any:
        """A multiple of 0 means no value, and so do the percentiles sent next to it and the
        return on equity sent next to a ``pe`` of 0."""
        if not isinstance(value, dict):
            return value
        fields = cast(dict[str, Any], value)
        blank = {
            f"{metric}{suffix}": None
            for metric in ("pe", "pb")
            if fields.get(metric) == 0
            for suffix in ("", "_percentile", "_over_history")
        }
        if fields.get("pe") == 0:
            blank["roe"] = None
        return {**fields, **blank}

    @field_validator("trade_date", "begin_at", mode="before")
    @classmethod
    def _shanghai_date(cls, value: Any) -> Any:
        return date_from_ms(value, REPORT_DATE_ZONE)

    @field_validator("updated_at", mode="before")
    @classmethod
    def _shanghai_time(cls, value: Any) -> Any:
        return None if value is None else from_ms(value, REPORT_DATE_ZONE)


class ValuationHistoryParams(BaseModel):
    """One request returns the whole window; ``begin`` and ``end`` choose the rows kept of it."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    index_code: IndexCode
    """An index from index_valuation_list, such as SH000300 or CSIH30269. Case-insensitive."""
    period: ValuationWindow = "all"
    """Window: all, 3y or 5y. all starts about 10 years back."""
    begin: BarDate | None = None
    """First date, inclusive."""
    end: BarDate | None = None
    """Last date, inclusive."""

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        check_range(self.begin, self.end, None)
        return self


@dataclass(frozen=True, slots=True)
class ValuationHistory(Fetched):
    """PE or PB of one index over time in columns, oldest first.

    ``columns`` holds one column, ``pe`` or ``pb``, as a multiple; a 0 from the upstream is None.
    Points are not daily: about 7 days apart on the 157 to 517 points of each window measured.
    ``lines`` holds the reference values Danjuan draws, by its names 30分位值, 中位值 and 70分位值.
    They belong to the window requested, not to ``begin`` and ``end``.
    """

    index_code: str
    dates: tuple[date, ...]
    columns: Mapping[str, tuple[float | None, ...]]
    lines: Mapping[str, float]

    def index(self) -> tuple[str, tuple[date, ...]]:
        return "date", self.dates

    def header(self) -> Mapping[str, Any]:
        return self.lines

    def __len__(self) -> int:
        return len(self.dates)


class _Point(UpstreamModel):
    day: date = Field(validation_alias="ts")
    value: Number = Field(default=None, validation_alias=AliasChoices("pe", "pb"))

    @field_validator("day", mode="before")
    @classmethod
    def _shanghai_date(cls, value: Any) -> Any:
        return date_from_ms(value, REPORT_DATE_ZONE)


class _Line(UpstreamModel):
    line_name: str
    line_value: float


class _History(UpstreamModel):
    points: tuple[_Point, ...] = Field(
        validation_alias=AliasChoices("index_eva_pe_growths", "index_eva_pb_growths")
    )
    lines: tuple[_Line, ...] = Field(validation_alias="horizontal_lines")


def in_range(day: date, begin: date | None, end: date | None) -> bool:
    return (begin is None or day >= begin) and (end is None or day <= end)


def history_query(params: ValuationHistoryParams, now: datetime) -> dict[str, str]:
    return {"index_code": params.index_code, "day": params.period}


def parse_history(payload: Any, params: ValuationHistoryParams, metric: str) -> ValuationHistory:
    data = _History.model_validate(payload)
    points = sorted(
        (point for point in data.points if in_range(point.day, params.begin, params.end)),
        key=lambda point: point.day,
    )
    return ValuationHistory(
        index_code=params.index_code,
        dates=tuple(point.day for point in points),
        columns={metric: tuple(point.value or None for point in points)},
        lines={line.line_name: line.line_value for line in data.lines},
    )
