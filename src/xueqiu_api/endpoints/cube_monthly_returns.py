"""cube_monthly_returns: calendar-month gains of one portfolio and its benchmark, latest first."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from pydantic import BeforeValidator, Field, TypeAdapter

from .._cube import CubeParams, cube_query
from .._model import UpstreamModel
from .._values import FractionPoints
from ..registry import AgentInfo, Endpoint


class CubeMonthlyReturn(UpstreamModel):
    """One complete calendar month: the month in progress is not listed, measured 2026-11-05."""

    month: date
    """First day of the month."""
    gain: float | None = None
    """Month-end net value over the previous month-end, minus 1, in percent points (1.23 means
    1.23%); the base of the first month is 1.0, measured 2026-11-05. None if not sent."""
    benchmark: str | None = None
    """Benchmark symbol, such as SH000300."""
    benchmark_gain: float | None = None
    """The benchmark's gain over the same month, in percent points. None if not sent."""


def _first_of_month(value: Any) -> Any:
    return f"{value}-01" if isinstance(value, str) else value


class _Point(UpstreamModel):
    month: Annotated[date, BeforeValidator(_first_of_month)] = Field(validation_alias="date")
    value: FractionPoints = None


class _Line(UpstreamModel):
    symbol: str
    points: tuple[_Point, ...] = Field(validation_alias="profit_list")


_LINES = TypeAdapter(list[_Line])


def _parse(payload: Any, params: CubeParams) -> list[CubeMonthlyReturn]:
    lines = _LINES.validate_python(payload)
    if not lines:
        raise ValueError("cube monthly returns have no series")
    cube = {point.month: point.value for point in lines[0].points}
    other = lines[1] if len(lines) > 1 else None
    benchmark = {point.month: point.value for point in other.points} if other else {}
    return [
        CubeMonthlyReturn(
            month=month,
            gain=cube.get(month),
            benchmark=other.symbol if other else None,
            benchmark_gain=benchmark.get(month),
        )
        for month in sorted({*cube, *benchmark}, reverse=True)
    ]


ENDPOINT = Endpoint(
    name="cube_monthly_returns",
    doc="Calendar-month gains of one portfolio and its benchmark, latest first.",
    host="www.xueqiu.com",
    path="/cubes/analyst/histo/benefit.json",
    params=CubeParams,
    build_query=cube_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="bare",
    verified_on=date(2026, 11, 5),
    order="newest_first",
    agent=AgentInfo(
        category="cube",
        group="xq_cube",
        section="monthly",
        title_zh="组合月度收益",
        title_en="Portfolio monthly returns",
        summary="取一个雪球组合每个完整自然月的收益率和同月基准指数的涨跌，最近的月份在前。",
        keywords=(
            "月度收益",
            "月收益",
            "组合收益",
            "收益统计",
            "monthly return",
            "cube",
            "benchmark",
        ),
        default_fields=("month", "gain", "benchmark_gain"),
        row_default=100,
        row_cap=500,
        upstream_cap=500,
        examples=({"cube_symbol": "ZH2567925"},),
        empty_hint="no rows: only complete calendar months are listed, and there may be none yet.",
    ),
)
