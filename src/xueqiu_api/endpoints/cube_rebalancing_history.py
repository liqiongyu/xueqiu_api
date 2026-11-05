"""cube_rebalancing_history: one page of a portfolio's rebalancings; needs a login."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .._cube import CubePageBody, PlatformTime
from .._model import UnverifiedModel
from .._pages import Page
from .._values import Number
from ..registry import AgentInfo, Endpoint, Pagination
from ..symbols import CubeCode

PAGE_SIZE_CAP = 50
"""Rebalancings per page; not measured."""


class CubeRebalancingParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    cube_symbol: CubeCode
    """Portfolio code, such as ZH2567925."""
    page: int = Field(default=1, ge=1)
    """Page number, from 1."""
    page_size: int = Field(default=50, ge=1, le=PAGE_SIZE_CAP)
    """Rebalancings per page."""


class RebalancingChange(UnverifiedModel):
    """One security a rebalancing changed. The structure is unverified, see ``Rebalancing``."""

    symbol: str = Field(validation_alias="stock_symbol")
    """Xueqiu symbol of the security."""
    name: str | None = Field(default=None, validation_alias="stock_name")
    """Security name."""
    prev_weight_adjusted: Number = None
    """Weight before, after prices moved it, in percent points (1.23 means 1.23%)."""
    prev_weight: Number = None
    """Previous target weight, in percent points; None for a new position."""
    target_weight: Number = None
    """Weight asked for, in percent points."""
    weight: Number = None
    """Weight reached, in percent points."""
    price: Number = None
    """Price of the change, in the quote currency."""
    prev_price: Number = None
    """Price of the previous change, in the quote currency; None for a new position."""


class Rebalancing(UnverifiedModel):
    """One rebalancing, in the order the upstream sends them.

    The structure is unverified: no real response has been seen, only the keys needed to parse
    are declared, others are not kept, and this model is outside the semantic versioning promise.
    """

    rebalancing_id: int = Field(validation_alias="id")
    """Id of this rebalancing."""
    status: str | None = None
    """Status of this rebalancing."""
    category: str | None = None
    """Upstream category of this rebalancing."""
    created_at: PlatformTime = None
    """When it was submitted."""
    updated_at: PlatformTime = None
    """When it last changed."""
    cash_weight: Number = Field(default=None, validation_alias="cash")
    """Share of cash after it, in percent points."""
    prev_rebalancing_id: int | None = Field(default=None, validation_alias="prev_bebalancing_id")
    """Id of the rebalancing before it."""
    changes: tuple[RebalancingChange, ...] = Field(validation_alias="rebalancing_histories")
    """The securities it changed."""


class RebalancingPage(Page[Rebalancing]):
    """One page of rebalancings: ``items`` holds them, and it renders one row per change."""

    def header(self) -> Mapping[str, Any]:
        return {}

    def sections(self) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        return {
            "changes": [
                {
                    "rebalancing_id": rebalancing.rebalancing_id,
                    "updated_at": rebalancing.updated_at,
                    "status": rebalancing.status,
                    "cash_weight": rebalancing.cash_weight,
                    **change.model_dump(),
                }
                for rebalancing in self.items
                for change in rebalancing.changes
            ]
        }


def _build_query(params: CubeRebalancingParams, now: datetime) -> dict[str, str]:
    return {
        "cube_symbol": params.cube_symbol,
        "page": str(params.page),
        "count": str(params.page_size),
    }


def _parse(payload: Any, params: CubeRebalancingParams) -> RebalancingPage:
    body = CubePageBody[Rebalancing].model_validate(payload)
    return RebalancingPage(
        body.items,
        page=body.page,
        page_size=body.page_size,
        total=body.total,
        last_page=body.last_page,
    )


ENDPOINT = Endpoint(
    name="cube_rebalancing_history",
    doc="One page of a portfolio's rebalancings; needs a login, structure unverified.",
    host="www.xueqiu.com",
    path="/cubes/rebalancing/history.json",
    params=CubeRebalancingParams,
    build_query=_build_query,
    parse=_parse,
    auth="required",
    freshness="daily",
    envelope="bare",
    verified_on=None,
    pagination=Pagination(
        style="page", end="total", size_cap=PAGE_SIZE_CAP, key=lambda row: row.rebalancing_id
    ),
    agent=AgentInfo(
        category="cube",
        group="xq_cube",
        section="rebalancing",
        title_zh="组合调仓记录",
        title_en="Portfolio rebalancing history",
        summary="按页取一个雪球组合的历次调仓，每行是一只股票调仓前后的权重和成交价，需要登录。",
        keywords=(
            "调仓记录",
            "调仓",
            "历史调仓",
            "买入卖出",
            "rebalancing",
            "trades",
            "cube history",
        ),
        default_fields=(
            "updated_at",
            "status",
            "symbol",
            "name",
            "prev_weight_adjusted",
            "target_weight",
            "price",
        ),
        row_default=50,
        row_cap=100,
        upstream_cap=PAGE_SIZE_CAP,
        examples=({"cube_symbol": "ZH2567925", "page_size": 20},),
    ),
)
