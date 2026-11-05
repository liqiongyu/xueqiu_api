"""cube_rank_list: one page of the portfolio leaderboard."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .._cube import CubeFigures, CubePageBody, PlatformTime
from .._pages import Page
from ..registry import AgentInfo, Endpoint, Pagination

PAGE_SIZE = 20
"""The upstream's page size: it answered size=3 with 20 rows, measured 2026-11-04."""


class CubeRankListParams(BaseModel):
    """Upstream category 14, the only one measured, which held 100 portfolios in 5 pages on
    2026-11-04. How it ranks them is not documented."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    page: int = Field(default=1, ge=1, le=5)
    """Page number, from 1 to 5."""


class CubeSummary(CubeFigures):
    """One portfolio on the leaderboard. Undocumented upstream keys, such as ``bb_rate`` and
    ``style``, stay in ``model_extra`` as sent."""

    cube_id: int = Field(validation_alias="id")
    """Numeric id of the portfolio."""
    follower_count: int | None = None
    """Followers of the portfolio."""
    created_at: PlatformTime = None
    """When the portfolio was created."""
    updated_at: PlatformTime = None
    """When the portfolio last changed."""
    last_rb_id: int | None = None
    """Id of its latest rebalancing."""


def _build_query(params: CubeRankListParams, now: datetime) -> dict[str, str]:
    return {"category": "14", "page": str(params.page), "size": str(PAGE_SIZE)}


def _parse(payload: Any, params: CubeRankListParams) -> Page[CubeSummary]:
    body = CubePageBody[CubeSummary].model_validate(payload)
    return Page(
        body.items,
        page=body.page,
        page_size=body.page_size,
        total=body.total,
        last_page=body.last_page,
    )


ENDPOINT = Endpoint(
    name="cube_rank_list",
    doc="One page of the portfolio leaderboard, with each portfolio's net value and gains.",
    host="www.xueqiu.com",
    path="/cubes/discover/rank/cube/list.json",
    params=CubeRankListParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="bare",
    verified_on=date(2026, 11, 4),
    pagination=Pagination(
        style="page", end="total", size_cap=PAGE_SIZE, key=lambda row: row.symbol
    ),
    order="rank",
    agent=AgentInfo(
        category="cube",
        group="xq_cube",
        section="rank",
        title_zh="组合排行榜",
        title_en="Portfolio leaderboard",
        summary="按页取雪球组合排行榜（上游分类 14，共 100 个）中各组合的代码、名称、净值和收益。",
        keywords=(
            "组合排行",
            "排行榜",
            "组合榜单",
            "雪球组合",
            "cube rank",
            "leaderboard",
            "portfolio ranking",
        ),
        default_fields=(
            "symbol",
            "name",
            "net_value",
            "daily_gain",
            "monthly_gain",
            "total_gain",
            "annualized_gain",
            "follower_count",
            "updated_at",
        ),
        row_default=PAGE_SIZE,
        row_cap=PAGE_SIZE,
        upstream_cap=PAGE_SIZE,
        examples=({"page": 1},),
        labels=("name",),
    ),
)
