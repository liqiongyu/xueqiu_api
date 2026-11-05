"""watchlist_groups: the watchlist groups of the logged-in account."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from pydantic import Field

from .._cube import PlatformTime
from .._model import NoParams, UnverifiedModel
from ..registry import AgentInfo, Endpoint

_LIST_KEY = "list_key"
"""Under which name ``_parse`` hands a group the upstream list holding it."""
_CATEGORIES = {"stocks": "stock", "funds": "fund", "cubes": "cube", "mutualFunds": "mutual_fund"}


class WatchlistGroup(UnverifiedModel):
    """One group, in the order the upstream sends them.

    The structure is unverified: no real response has been seen, only the keys needed to parse
    are declared, others are not kept, and this model is outside the semantic versioning promise.
    """

    pid: int = Field(validation_alias="id")
    """Group id: the ``pid`` of ``watchlist_stocks``."""
    name: str
    """Group name; the account owner writes those of custom groups."""
    category: str = Field(validation_alias=_LIST_KEY)
    """The ``category`` of ``watchlist_stocks``: stock, fund, cube or mutual_fund; any other kind
    of list keeps its upstream name."""
    category_code: int | None = Field(default=None, validation_alias="category")
    """The upstream's number for the category."""
    symbol_count: int | None = None
    """Members of the group."""
    created_at: PlatformTime = None
    """When the group was created."""
    updated_at: PlatformTime = None
    """When the group last changed."""


def _build_query(params: NoParams, now: datetime) -> dict[str, str]:
    return {"system": "true"}


def _parse(payload: Any, params: NoParams) -> list[WatchlistGroup]:
    if not isinstance(payload, dict):
        raise ValueError("watchlist groups need an object of lists")
    return [
        WatchlistGroup.model_validate({**group, _LIST_KEY: _CATEGORIES.get(key, key)})
        for key, groups in cast(dict[str, Any], payload).items()
        if isinstance(groups, list)
        for group in cast(list[Any], groups)
    ]


ENDPOINT = Endpoint(
    name="watchlist_groups",
    doc="The watchlist groups of the logged-in account; structure unverified.",
    host="stock.xueqiu.com",
    path="/v5/stock/portfolio/list.json",
    params=NoParams,
    build_query=_build_query,
    parse=_parse,
    auth="required",
    freshness="intraday",
    envelope="xueqiu",
    verified_on=None,
    agent=AgentInfo(
        category="watchlist",
        group="xq_watchlist",
        section="groups",
        title_zh="自选分组",
        title_en="Watchlist groups",
        summary=(
            "列出当前登录账号的自选分组，给出股票、基金和组合分组的 pid、名称和成员数，需要登录。"
        ),
        keywords=(
            "自选分组",
            "自选股",
            "我的自选",
            "关注",
            "分组",
            "watchlist",
            "watchlist groups",
            "portfolio",
            "favorites",
        ),
        default_fields=("pid", "name", "category", "symbol_count"),
        row_default=50,
        row_cap=100,
        upstream_cap=100,
        examples=({},),
        labels=("name",),
    ),
)
