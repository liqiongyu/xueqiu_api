"""cube_max_drawdown: the largest drawdown of one portfolio since inception."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import Field

from .._cube import CubeParams, PlatformTime
from .._model import UpstreamModel
from .._values import FractionPoints
from ..registry import AgentInfo, Endpoint


class CubeMaxDrawdown(UpstreamModel):
    """The largest drop from a peak to a later trough. Recomputing it from the daily net values
    gave the same dates and a difference of 2.1e-7, measured 2026-11-05."""

    symbol: str
    """Portfolio code, such as ZH2567925."""
    max_drawdown: FractionPoints = Field(default=None, validation_alias="max_draw")
    """In percent points and negative: -38.41 is a drop of 38.41%."""
    begin_date: date | None = None
    """Date of the peak."""
    end_date: date | None = None
    """Date of the trough."""
    begin_time: PlatformTime = None
    """When the net value of the peak was computed."""
    end_time: PlatformTime = None
    """When the net value of the trough was computed."""
    level: int | None = None
    """Upstream ``level``; meaning unknown."""


def _build_query(params: CubeParams, now: datetime) -> dict[str, str]:
    return {"cube_symbol": params.cube_symbol, "type": "max_draw"}


def _parse(payload: Any, params: CubeParams) -> CubeMaxDrawdown:
    return CubeMaxDrawdown.model_validate(payload)


ENDPOINT = Endpoint(
    name="cube_max_drawdown",
    doc="The largest drawdown of one portfolio since inception, with its dates.",
    host="www.xueqiu.com",
    path="/cubes/analyst/histo/stat.json",
    params=CubeParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="bare",
    verified_on=date(2026, 11, 5),
    agent=AgentInfo(
        category="cube",
        group="xq_cube",
        section="drawdown",
        title_zh="组合最大回撤",
        title_en="Portfolio max drawdown",
        summary="取一个雪球组合成立以来的最大回撤及其起止日期。",
        keywords=("最大回撤", "回撤", "风险", "组合", "max drawdown", "drawdown", "risk"),
        default_fields=("max_drawdown", "begin_date", "end_date"),
        row_default=1,
        row_cap=1,
        upstream_cap=1,
        examples=({"cube_symbol": "ZH2567925"},),
    ),
)
