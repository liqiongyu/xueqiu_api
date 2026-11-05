"""watchlist_stocks: the members of one watchlist group of the logged-in account."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .._cube import PlatformTime
from .._model import UnverifiedModel
from ..registry import AgentInfo, Endpoint

WatchlistCategory = Literal["stock", "fund", "cube", "mutual_fund"]
"""The kind of a watchlist group. Whether the upstream accepts each one is unverified."""

_CATEGORY_CODES: Mapping[str, str] = MappingProxyType(
    {"stock": "1", "fund": "2", "cube": "3", "mutual_fund": "6"}
)
_SIZE = 1000
"""Sent as the page size; no way to page is known, so exactly this many rows may be a cut list."""


class WatchlistStocksParams(BaseModel):
    """One request with a page size of 1000, the most this method returns. No way to page is
    known, so a result of exactly 1000 rows may be cut."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    pid: int = -1
    """Group id, the ``pid`` of ``watchlist_groups``; -1 means every stock."""
    category: WatchlistCategory = "stock"
    """Kind of the group, the ``category`` of ``watchlist_groups``."""


class WatchlistItem(UnverifiedModel):
    """One member of a group, in the order the upstream sends them.

    The structure is unverified: no real response has been seen, only the keys needed to parse
    are declared, others are not kept, and this model is outside the semantic versioning promise.
    """

    symbol: str
    """Code as the upstream writes it, not normalized."""
    name: str | None = None
    """Name of the security or portfolio."""
    stock_type: int | None = Field(default=None, validation_alias="type")
    """Xueqiu security type code."""
    exchange: str | None = None
    """Exchange."""
    created: PlatformTime = None
    """Upstream ``created``, read as when it joined the list."""
    remark: str | None = None
    """The account owner's own note."""


class _Payload(UnverifiedModel):
    stocks: tuple[WatchlistItem, ...]


def _build_query(params: WatchlistStocksParams, now: datetime) -> dict[str, str]:
    return {
        "pid": str(params.pid),
        "category": _CATEGORY_CODES[params.category],
        "size": str(_SIZE),
    }


def _parse(payload: Any, params: WatchlistStocksParams) -> list[WatchlistItem]:
    return list(_Payload.model_validate(payload).stocks)


ENDPOINT = Endpoint(
    name="watchlist_stocks",
    doc="The members of one watchlist group of the logged-in account; structure unverified.",
    host="stock.xueqiu.com",
    path="/v5/stock/portfolio/stock/list.json",
    params=WatchlistStocksParams,
    build_query=_build_query,
    parse=_parse,
    auth="required",
    freshness="intraday",
    envelope="xueqiu",
    verified_on=None,
    needs_cookies=("u",),
    agent=AgentInfo(
        category="watchlist",
        group="xq_watchlist",
        section="stocks",
        title_zh="自选股",
        title_en="Watchlist stocks",
        summary=(
            "列出当前登录账号一个自选分组里的证券，默认是全部自选股，"
            "基金和组合分组要给 category 和 pid，需要登录。"
        ),
        keywords=(
            "自选股",
            "我的自选",
            "自选列表",
            "自选基金",
            "关注的股票",
            "watchlist",
            "watchlist stocks",
            "my stocks",
            "favorites",
        ),
        default_fields=("symbol", "name", "exchange", "stock_type", "created"),
        row_default=50,
        row_cap=100,
        upstream_cap=_SIZE,
        examples=({"pid": -1, "category": "stock"},),
        free_text=("remark",),
        labels=("name",),
        empty_hint="no rows: the group is empty, or the credentials lack a valid u cookie.",
    ),
)
