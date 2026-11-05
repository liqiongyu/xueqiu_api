"""cube_nav_daily: daily net value of one portfolio and its benchmark, kept columnar."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .._model import UpstreamModel
from .._result import Fetched
from .._values import Number
from ..registry import AgentInfo, Endpoint
from ..symbols import CubeCode
from .kline import BarDate, check_range


class CubeNavParams(BaseModel):
    """One request returns the whole history; ``begin`` and ``end`` choose the rows kept of it."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    cube_symbol: CubeCode
    """Portfolio code, such as ZH2567925."""
    begin: BarDate | None = None
    """First date, inclusive."""
    end: BarDate | None = None
    """Last date, inclusive."""

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        check_range(self.begin, self.end, None)
        return self


@dataclass(frozen=True, slots=True)
class CubeNav(Fetched):
    """Daily values of one portfolio and its benchmark in columns, oldest first.

    ``columns`` holds ``nav`` (net value per unit; the first row is not always 1), ``total_gain``
    (percent points since inception, ``(nav - 1) * 100``), ``benchmark_value`` (index points) and
    ``benchmark_total_gain`` (percent points since the portfolio's base date). A date one series
    lacks is None in its columns. Dates are trading days: the 1389 rows of one portfolio matched
    the Shanghai sessions one by one, measured 2026-11-05, and that day's row was not there yet
    during trading.
    """

    symbol: str
    name: str | None
    """Portfolio name, written by its owner."""
    benchmark: str | None
    """Benchmark symbol, such as SH000300; None when the upstream sends only the portfolio."""
    benchmark_name: str | None
    dates: tuple[date, ...]
    columns: Mapping[str, tuple[float | None, ...]]

    def index(self) -> tuple[str, tuple[date, ...]]:
        return "date", self.dates

    def header(self) -> Mapping[str, Any]:
        if self.benchmark is None:
            return {}
        return {"benchmark": self.benchmark, "benchmark_name": self.benchmark_name}

    def __len__(self) -> int:
        return len(self.dates)


class _Point(UpstreamModel):
    day: date = Field(validation_alias="date")
    value: Number = None
    percent: Number = None


class _Line(UpstreamModel):
    symbol: str
    name: str | None = None
    points: tuple[_Point, ...] = Field(validation_alias="list")


_LINES = TypeAdapter(list[_Line])


def _build_query(params: CubeNavParams, now: datetime) -> dict[str, str]:
    return {"cube_symbol": params.cube_symbol}


def _parse(payload: Any, params: CubeNavParams) -> CubeNav:
    lines = _LINES.validate_python(payload)
    if not lines:
        raise ValueError("cube nav data has no series")
    cube = {point.day: point for point in lines[0].points}
    other = lines[1] if len(lines) > 1 else None
    benchmark = {point.day: point for point in other.points} if other else {}
    days = sorted(
        day
        for day in {*cube, *benchmark}
        if (params.begin is None or day >= params.begin)
        and (params.end is None or day <= params.end)
    )
    return CubeNav(
        symbol=lines[0].symbol,
        name=lines[0].name,
        benchmark=other.symbol if other else None,
        benchmark_name=other.name if other else None,
        dates=tuple(days),
        columns={
            "nav": tuple(cube[d].value if d in cube else None for d in days),
            "total_gain": tuple(cube[d].percent if d in cube else None for d in days),
            "benchmark_value": tuple(benchmark[d].value if d in benchmark else None for d in days),
            "benchmark_total_gain": tuple(
                benchmark[d].percent if d in benchmark else None for d in days
            ),
        },
    )


ENDPOINT = Endpoint(
    name="cube_nav_daily",
    doc="Daily net value of one portfolio and its benchmark, oldest first.",
    host="www.xueqiu.com",
    path="/cubes/nav_daily/all.json",
    params=CubeNavParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="bare",
    verified_on=date(2026, 11, 5),
    order="oldest_first",
    agent=AgentInfo(
        category="cube",
        group="xq_cube",
        section="nav",
        title_zh="组合净值走势",
        title_en="Portfolio daily NAV",
        summary=(
            "取一个雪球组合从创建到最近一个交易日的每日净值和累计收益，并附基准指数的同期累计涨跌。"
        ),
        keywords=(
            "组合净值",
            "净值走势",
            "历史净值",
            "累计收益",
            "基准",
            "cube nav",
            "nav history",
            "portfolio performance",
        ),
        default_fields=("date", "nav", "total_gain", "benchmark_value", "benchmark_total_gain"),
        row_default=100,
        row_cap=500,
        upstream_cap=10_000,
        examples=({"cube_symbol": "ZH2567925"},),
    ),
)
