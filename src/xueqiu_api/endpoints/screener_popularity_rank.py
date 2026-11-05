"""screener_popularity_rank: every security of one market, most followed over 7 days first."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from .._model import UpstreamModel
from .._pages import Page
from .._values import Number
from ..registry import AgentInfo, Endpoint, Pagination

PAGE_SIZE_CAP = 100
"""size=100 gave full pages, measured 2026-11-05."""

RankMarket = Literal["CN", "HK", "US"]
"""Markets of the ranking; the generated methods import this name from this module."""


class PopularityRankParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    market: RankMarket = "CN"
    """CN, HK or US."""
    page: int = Field(default=1, ge=1)
    """Page number, from 1."""
    page_size: int = Field(default=50, ge=1, le=PAGE_SIZE_CAP)
    """Rows per page."""


class PopularStock(UpstreamModel):
    """One security of the ranking."""

    drop: ClassVar[frozenset[str]] = frozenset({"has_follow"})
    """Whether the session's own account follows the security."""

    symbol: str
    """Symbol as the upstream writes it, which ``normalize_symbol`` does not always accept."""
    name: str | None = None
    stock_type: int | None = Field(default=None, validation_alias="type")
    """Upstream security type: 11 SH and SZ, 82 STAR, 90 BJ, 30 HK and 0 US were measured."""
    current: Number = None
    """Last price, in the quote currency."""
    percent: Number = Field(default=None, validation_alias="pct")
    """Change from the previous close, in percent points (1.23 means 1.23%)."""
    follow7d: Number = None
    """Upstream's 7-day follow count, in users; its exact definition is unconfirmed. The tail of
    the list has none."""
    tick_size: Number = None
    """Smallest price step, in the quote currency."""


class _Payload(UpstreamModel):
    count: int
    rows: tuple[PopularStock, ...] = Field(validation_alias="list")


def _build_query(params: PopularityRankParams, now: datetime) -> dict[str, str]:
    return {
        "category": params.market,
        "order_by": "follow7d",  # the only value measured
        "order": "desc",  # the only value measured
        "only_count": "0",
        "page": str(params.page),
        "size": str(params.page_size),
    }


def _parse(payload: Any, params: PopularityRankParams) -> Page[PopularStock]:
    data = _Payload.model_validate(payload)
    return Page(data.rows, page=params.page, page_size=params.page_size, total=data.count)


ENDPOINT = Endpoint(
    name="screener_popularity_rank",
    doc="Every security of one market, most followed over 7 days first.",
    host="stock.xueqiu.com",
    path="/v5/stock/screener/screen.json",
    params=PopularityRankParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="intraday",
    envelope="xueqiu",
    verified_on=date(2026, 11, 5),
    # Not capped at 5000: CN page 51 at size 100 had full rows of 5656 and US page 92 had the last
    # 24 of 9124, measured 2026-11-05. Rows tied on follow7d can move between pages.
    pagination=Pagination(
        style="page", end="total", size_cap=PAGE_SIZE_CAP, key=lambda row: row.symbol
    ),
    order="rank",
    agent=AgentInfo(
        category="screener",
        group="xq_market",
        section="screener_popularity_rank",
        title_zh="关注度排行",
        title_en="Popularity rank",
        summary="按近 7 天关注数排出一个市场的全部证券，每行带最新价和涨跌幅。",
        keywords=(
            "关注度",
            "人气榜",
            "七日关注",
            "热度排行",
            "全部美股",
            "popularity",
            "most followed",
            "follow7d",
        ),
        default_fields=("symbol", "name", "current", "percent", "follow7d"),
        row_default=50,
        row_cap=PAGE_SIZE_CAP,
        upstream_cap=PAGE_SIZE_CAP,
        examples=({"market": "CN", "page_size": 3},),
    ),
)
