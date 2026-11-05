"""fund_growth: net value and cumulative return of one fund over a window, oldest first."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .._model import UpstreamModel
from .._values import FractionPoints, Number, PercentPoints
from ..registry import AgentInfo, Endpoint
from ..symbols import FundCode

FundGrowthPeriod = Literal["ty", "1m", "3m", "6m", "1y", "3y", "5y", "all"]
"""The windows the upstream lists for a fund, measured 2026-11-05."""
_COLUMNS = (
    "nav",
    "daily_pct",
    "cum_return_pct",
    "index_cum_return_pct",
    "benchmark_cum_return_pct",
)


class FundGrowthParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    fund_code: FundCode
    """Six digits, such as 008975."""
    period: FundGrowthPeriod = "ty"
    """Window: ty 今年以来, 1m 近1月, 3m 近3月, 6m 近6月, 1y 近1年, 3y 近3年, 5y 近5年,
    all 成立以来. Long windows come back as sampled points."""


@dataclass(frozen=True, slots=True)
class FundGrowth:
    """Values of one fund in columns, oldest first.

    ``columns`` holds ``nav`` (net value per unit, in CNY), ``daily_pct`` (that day's change in
    percent points, also on sampled rows; a fund's oldest value has none), ``cum_return_pct``
    (percent points since the first row, the base at 0: ``nav`` over the base ``nav``, minus 1,
    on a fund that paid no dividends; other funds were not measured), ``index_cum_return_pct``
    (the index named ``index_name``) and ``benchmark_cum_return_pct`` (the contract benchmark,
    inferred: near 95% of the index in a year of a fund whose formula says 95%).

    Long windows are sampled: ``all`` gave 497 rows for 1603 value dates, 1 to 4 dates apart,
    while ``ty`` gave 202 rows, one per session, measured 2026-11-05; where sampling starts among
    ``1y``, ``3y`` and ``5y`` was not measured. Do not compute a drawdown from these rows: the 497
    give 49.73%, every value 51.59%. Take ``max_drawdown_pct`` from fund_achievement, or walk
    fund_nav_history.
    """

    fund_code: str
    period: FundGrowthPeriod
    index_name: str | None
    dates: tuple[date, ...]
    columns: Mapping[str, tuple[float | None, ...]]

    def index(self) -> tuple[str, tuple[date, ...]]:
        return "date", self.dates

    def header(self) -> Mapping[str, Any]:
        return {} if self.index_name is None else {"index_name": self.index_name}

    def __len__(self) -> int:
        return len(self.dates)


class _Point(UpstreamModel):
    day: date = Field(validation_alias="date")
    nav: Number = None
    daily_pct: PercentPoints = Field(default=None, validation_alias="percentage")
    cum_return_pct: FractionPoints = Field(default=None, validation_alias="value")
    index_cum_return_pct: FractionPoints = Field(default=None, validation_alias="than_value")
    benchmark_cum_return_pct: FractionPoints = Field(
        default=None, validation_alias="performance_value"
    )


class _Line(UpstreamModel):
    line_key: str | None = None
    line_name: str | None = None


class _Payload(UpstreamModel):
    fund_nav_growth: tuple[_Point, ...]
    growth_lines: tuple[_Line, ...] = ()


def _build_query(params: FundGrowthParams, now: datetime) -> dict[str, str]:
    return {"fund_code": params.fund_code, "day": params.period}


def _parse(payload: Any, params: FundGrowthParams) -> FundGrowth:
    data = _Payload.model_validate(payload)
    points = data.fund_nav_growth
    return FundGrowth(
        fund_code=params.fund_code,
        period=params.period,
        index_name=next(
            (line.line_name for line in data.growth_lines if line.line_key == "than_value"), None
        ),
        dates=tuple(point.day for point in points),
        columns={name: tuple(getattr(point, name) for point in points) for name in _COLUMNS},
    )


ENDPOINT = Endpoint(
    name="fund_growth",
    doc=(
        "Net value and cumulative return of one fund against its index and benchmark, oldest "
        "first; long windows are sampled, so do not compute a drawdown from them."
    ),
    host="danjuanfunds.com",
    path="/djapi/fund/growth/{fund_code}",
    params=FundGrowthParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="danjuan",
    verified_on=date(2026, 11, 5),
    order="oldest_first",
    agent=AgentInfo(
        category="fund",
        group="xq_fund",
        section="growth",
        title_zh="基金业绩走势",
        title_en="Fund growth",
        summary=(
            "取一只基金在今年以来等区间内的单位净值、日涨跌和累计收益，并与跟踪指数、业绩比较基准"
            "对比，长区间是抽样点，不能用来算回撤。"
        ),
        keywords=(
            "基金业绩走势",
            "累计收益",
            "收益曲线",
            "对比指数",
            "业绩比较基准",
            "fund growth",
            "cumulative return",
            "performance chart",
        ),
        default_fields=(
            "date",
            "nav",
            "daily_pct",
            "cum_return_pct",
            "index_cum_return_pct",
            "benchmark_cum_return_pct",
        ),
        row_default=100,
        row_cap=500,
        # A bound: 497 rows was the most measured, for a fund of six and a half years.
        upstream_cap=1000,
        examples=({"fund_code": "008975", "period": "ty"},),
    ),
)
