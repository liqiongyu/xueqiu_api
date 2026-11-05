"""cube_score: the score card of one portfolio, with its total, tier and sub-scores."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .._cube import CubeParams, PlatformTime
from .._model import UpstreamModel
from .._values import Number
from ..registry import AgentInfo, Endpoint


class SubScore(UpstreamModel):
    """One part of the total score."""

    name: str | None = None
    """Platform label, such as 盈利能力."""
    param: str | None = None
    """Upstream id of the part, such as maxDrawDown."""
    score: Number = None
    """Points of this part."""
    max_score: int | None = None
    """Most points of this part."""


class CubeScore(UpstreamModel):
    """Score card of one portfolio. ``cube_id`` and ``badges``, seen only as 0 and an empty list,
    stay in ``model_extra`` as sent."""

    symbol: str
    """Portfolio code, such as ZH2567925."""
    market: str | None = None
    """Market of the portfolio, cn on all those measured."""
    total_score: Number = None
    """Points out of ``max_total_score``."""
    max_total_score: int | None = None
    """Most points, 100 where measured."""
    level: int | None = None
    """Tier; what its values mean is unknown."""
    score_rank: int | None = None
    """Upstream ``score_rank``, seen only as 0; meaning unknown."""
    score_updated_time: PlatformTime = None
    """When the score was last computed."""
    report_updated_time: PlatformTime = None
    """Upstream ``report_updated_time``; it moves on a closed portfolio too."""
    sub_scores: tuple[SubScore, ...] = ()
    """The parts of the total score."""


def _build_query(params: CubeParams, now: datetime) -> dict[str, str]:
    return {"symbol": params.cube_symbol, "ua": "web"}


def _parse(payload: Any, params: CubeParams) -> CubeScore:
    return CubeScore.model_validate(payload)


ENDPOINT = Endpoint(
    name="cube_score",
    doc="The score card of one portfolio: total score, tier and sub-scores.",
    host="www.xueqiu.com",
    path="/cubes/rank/summary.json",
    params=CubeParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="bare",
    verified_on=date(2026, 11, 4),
    agent=AgentInfo(
        category="cube",
        group="xq_cube",
        section="score",
        title_zh="组合评分",
        title_en="Portfolio score",
        summary="取一个雪球组合的综合评分、等级和盈利能力、抗风险能力、稳定性等分项得分。",
        keywords=("组合评分", "评分", "等级", "盈利能力", "抗风险", "cube score", "rating"),
        default_fields=(
            "symbol",
            "total_score",
            "max_total_score",
            "level",
            "score_rank",
            "score_updated_time",
            "sub_scores",
        ),
        row_default=1,
        row_cap=1,
        upstream_cap=1,
        examples=({"cube_symbol": "ZH2567925"},),
    ),
)
