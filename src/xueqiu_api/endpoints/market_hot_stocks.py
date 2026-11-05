"""market_hot_stocks: the Xueqiu ranking of the most popular stocks, hottest first."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from .._model import QuoteRow, UpstreamModel
from .._values import Number, WholeNumber
from ..registry import AgentInfo, Endpoint

HotStockRegion = Literal["global", "us", "cn", "hk"]
"""Which stocks are ranked. Every region has data in both windows, measured 2026-11-05."""
HotStockWindow = Literal["hour", "day"]
"""The window of the heat."""

COUNT_CAP = 50
"""Rows per request. Not measured: the survey sent size=3."""
_REGION_DIGIT: Mapping[HotStockRegion, int] = MappingProxyType(
    {"global": 0, "us": 1, "cn": 2, "hk": 3}
)
_WINDOW_BASE: Mapping[HotStockWindow, int] = MappingProxyType({"hour": 10, "day": 20})


class MarketHotStocksParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    region: HotStockRegion = "cn"
    """Ranking to read: global, us, cn or hk. Global day had only A-shares on top, measured
    2026-11-05, once during Shanghai trading."""
    window: HotStockWindow = "hour"
    """hour or day."""
    count: int = Field(default=10, ge=1, le=COUNT_CAP)
    """How many rows from the top to fetch."""


class HotStock(QuoteRow):
    """One stock of the ranking. Prices are in its own quote currency; the promotion references
    and the session's watchlist state of a row stay out."""

    drop: ClassVar[frozenset[str]] = frozenset(
        {"code", "type", "has_exist", "ad_id", "content_id", "page", "model", "location"}
    )

    value: Number = None
    """Heat, in no known unit. The day ranking runs far larger than the hour one, so compare
    values within one window only."""
    increment: Number = None
    """Change of the heat, going by the upstream's name."""
    rank_change: WholeNumber = None
    """Change of rank; which sign means a rise is not confirmed."""
    exchange: str | None = None
    """Listing venue, such as SZ, SH or NASDAQ."""
    stock_type: WholeNumber = None
    """Xueqiu security type code; 11 is an A share, 82 a STAR Market share and 0 a US stock."""
    sub_type: str | None = None
    """Upstream code, as sent; its values are of mixed kinds."""
    ad: WholeNumber = None
    """0 on every row seen; by its name, not 0 could mark a promoted row."""
    current_ext: Number = None
    """US pre-market or after-hours price, in USD; null on A-share and Hong Kong rows."""
    percent_ext: Number = None
    """Change of ``current_ext`` from the regular close before it, in percent points (1.23 means
    1.23%): from ``current`` after the session, from ``current - chg`` before it, measured
    2026-11-04 and 2026-11-05."""


class _Payload(UpstreamModel):
    items: tuple[HotStock, ...]


def _build_query(params: MarketHotStocksParams, now: datetime) -> dict[str, str]:
    digit = _REGION_DIGIT[params.region]
    return {
        "type": str(_WINDOW_BASE[params.window] + digit),
        "_type": str(10 + digit),
        "size": str(params.count),
    }


def _parse(payload: Any, params: MarketHotStocksParams) -> list[HotStock]:
    return list(_Payload.model_validate(payload).items)


ENDPOINT = Endpoint(
    name="market_hot_stocks",
    doc="The most popular stocks on Xueqiu by region and window, hottest first, with their quotes.",
    host="stock.xueqiu.com",
    path="/v5/stock/hot_stock/list.json",
    params=MarketHotStocksParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="intraday",
    envelope="xueqiu",
    verified_on=date(2026, 11, 5),
    order="rank",
    agent=AgentInfo(
        category="market",
        group="xq_market",
        section="hot_stocks",
        title_zh="热股榜",
        title_en="Hot stocks ranking",
        summary="按市场和时间窗取雪球热股榜上最热门的股票及其热度值、热度变化和行情。",
        keywords=(
            "热股榜",
            "人气榜",
            "热门股票",
            "热度榜",
            "榜单",
            "hot stocks",
            "trending",
            "popular stocks",
        ),
        default_fields=(
            "symbol",
            "name",
            "current",
            "percent",
            "value",
            "increment",
            "rank_change",
        ),
        row_default=10,
        row_cap=COUNT_CAP,
        upstream_cap=COUNT_CAP,
        examples=({"region": "cn", "window": "hour", "count": 3},),
    ),
)
