"""fund_achievement: returns, drawdowns and ranks of one fund by year and by recent period."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field, model_validator

from .._fund import FundParams, fund_code_query, split_ranks
from .._model import UpstreamModel
from .._values import PercentPoints
from ..registry import AgentInfo, Endpoint

FundPerformanceGroup = Literal["annual", "stage"]
"""annual: since inception, this year and each calendar year. stage: the recent periods."""
_RANK = {"self_nav_rank": "rank"}
RESULT_CAP = 50
"""14 rows were measured for a fund of six years; each calendar year adds one."""


class FundPeriodPerformance(UpstreamModel):
    """One period of one fund. All percentages are in percent points (1.23 means 1.23%)."""

    group: FundPerformanceGroup
    period: str = Field(validation_alias="period_time")
    """Upstream label, such as 成立以来, 2025 or 近1月."""
    return_pct: PercentPoints = Field(default=None, validation_alias="self_nav")
    """Cumulative return over the period."""
    max_drawdown_pct: PercentPoints = Field(default=None, validation_alias="self_max_draw_down")
    """Largest drawdown in the period, as a positive number; some rows have none."""
    standard_index_return_pct: PercentPoints = Field(
        default=None, validation_alias="standard_index_nav"
    )
    """Return of what the upstream calls the standard index. Which series that is stays
    unconfirmed: since inception 008975 had 12.79 here, measured 2026-11-04, while fund_growth had
    4.70 for the tracked index and 5.37 for the benchmark at the same net value date, measured
    2026-11-05."""
    standard_index_max_drawdown_pct: PercentPoints = Field(
        default=None, validation_alias="standard_index_max_draw_down"
    )
    rank: int | None = None
    """Place among peers."""
    rank_total: int | None = None
    """Number of peers."""

    @model_validator(mode="before")
    @classmethod
    def _split_rank(cls, value: Any) -> Any:
        return split_ranks(value, _RANK)


class _Payload(UpstreamModel):
    annual_performance_list: tuple[dict[str, Any], ...] = ()
    stage_performance_list: tuple[dict[str, Any], ...] = ()


def _parse(payload: Any, params: FundParams) -> list[FundPeriodPerformance]:
    data = _Payload.model_validate(payload)
    return [
        FundPeriodPerformance.model_validate({**row, "group": group})
        for group, rows in (
            ("annual", data.annual_performance_list),
            ("stage", data.stage_performance_list),
        )
        for row in rows
    ]


ENDPOINT = Endpoint(
    name="fund_achievement",
    doc="Returns, largest drawdowns and peer ranks of one fund by year and by recent period.",
    host="danjuanfunds.com",
    path="/djapi/fundx/base/fund/achievement/{fund_code}",
    params=FundParams,
    build_query=fund_code_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="danjuan",
    verified_on=date(2026, 11, 4),
    agent=AgentInfo(
        category="fund",
        group="xq_fund",
        section="achievement",
        title_zh="基金阶段业绩",
        title_en="Fund performance by period",
        summary=(
            "取一只基金各年度和近一月到成立以来各阶段的收益率、最大回撤、标准指数收益和同类排名。"
        ),
        keywords=(
            "基金业绩",
            "年度业绩",
            "阶段业绩",
            "最大回撤",
            "同类排名",
            "fund performance",
            "max drawdown",
            "annual return",
        ),
        default_fields=(
            "group",
            "period",
            "return_pct",
            "max_drawdown_pct",
            "standard_index_return_pct",
            "standard_index_max_drawdown_pct",
            "rank",
            "rank_total",
        ),
        row_default=RESULT_CAP,
        row_cap=RESULT_CAP,
        upstream_cap=RESULT_CAP,
        examples=({"fund_code": "008975"},),
    ),
)
