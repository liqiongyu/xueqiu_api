"""index_stock_bond_percentile: Danjuan's stock-bond percentile over time, oldest first."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .._index import in_range
from .._model import UpstreamModel
from .._values import FractionPoints, Number
from ..registry import AgentInfo, Endpoint
from .kline import BarDate, check_range


class StockBondParams(BaseModel):
    """One request returns about 5 years; ``begin`` and ``end`` choose the rows kept of it."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    begin: BarDate | None = None
    """First date, inclusive."""
    end: BarDate | None = None
    """Last date, inclusive."""

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        check_range(self.begin, self.end, None)
        return self


@dataclass(frozen=True, slots=True)
class StockBondPercentile:
    """Danjuan's stock-bond percentile in columns, oldest first.

    ``columns`` holds ``percentile`` (percent points; how Danjuan computes it is not documented)
    and ``benchmark`` (index points; which index it follows is not known). The window had 1210
    points from 2021-11-05 to 2026-11-03 when measured on 2026-11-04.
    """

    dates: tuple[date, ...]
    columns: Mapping[str, tuple[float | None, ...]]

    def index(self) -> tuple[str, tuple[date, ...]]:
        return "date", self.dates

    def header(self) -> Mapping[str, Any]:
        return {}

    def __len__(self) -> int:
        return len(self.dates)


class _Point(UpstreamModel):
    day: date = Field(validation_alias="date")
    percentile: FractionPoints = Field(default=None, validation_alias="quantile")
    benchmark: Number = Field(default=None, validation_alias="performance_benchmark")


_POINTS = TypeAdapter(list[_Point])


def _build_query(params: StockBondParams, now: datetime) -> dict[str, str]:
    return {"time": "60"}  # the only value measured


def _parse(payload: Any, params: StockBondParams) -> StockBondPercentile:
    points = sorted(
        (
            point
            for point in _POINTS.validate_python(payload)
            if in_range(point.day, params.begin, params.end)
        ),
        key=lambda point: point.day,
    )
    return StockBondPercentile(
        dates=tuple(point.day for point in points),
        columns={
            "percentile": tuple(point.percentile for point in points),
            "benchmark": tuple(point.benchmark for point in points),
        },
    )


ENDPOINT = Endpoint(
    name="index_stock_bond_percentile",
    doc="Danjuan's stock-bond percentile over about 5 years, oldest first.",
    host="danjuanfunds.com",
    path="/djapi/fundx/base/index/stock/bond/line",
    params=StockBondParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="danjuan",
    verified_on=date(2026, 11, 4),
    order="oldest_first",
    agent=AgentInfo(
        category="index",
        group="xq_market",
        section="index_stock_bond_percentile",
        title_zh="股债性价比",
        title_en="Stock-bond percentile",
        summary="取蛋卷近 5 年的股债性价比百分位和基准指数点位，最早的在前。",
        keywords=(
            "股债性价比",
            "股债利差",
            "风险溢价",
            "百分位",
            "stock bond",
            "equity risk premium",
        ),
        default_fields=("date", "percentile", "benchmark"),
        row_default=100,
        row_cap=500,
        upstream_cap=1300,
        examples=({},),
    ),
)
