"""fund_rank: funds of one type ranked by return over a window, at most 1000 rows."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .._model import UpstreamModel
from .._pages import Page
from .._values import Number, PercentPoints
from ..registry import AgentInfo, Endpoint, Pagination

FundRankType = Literal["1", "3", "2", "5", "11"]
"""The types fund_rank_options lists, measured 2026-11-05; requests went out with 1, 2 and 3."""
FundRankOrder = Literal["td", "1w", "1m", "3m", "6m", "ty", "1y", "2y", "3y", "5y", "base"]
"""The windows fund_rank_options lists, measured 2026-11-05; requests went out with 1m."""
PAGE_SIZE_CAP = 100
"""size=100 gave a full page 2 of 100 rows, measured 2026-11-05. Larger sizes were not tried."""


class FundRankParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    fund_type: FundRankType = "1"
    """Fund type: 1 股票型, 3 混合型, 2 债券型, 5 指数型, 11 QDII型."""
    # The upstream's own default is 2y; 1m is the one window requests were measured with.
    order_by: FundRankOrder = "1m"
    """Window of the return that ranks the funds: td 日涨幅, 1w 近一周, 1m 近一月 (a month here, a
    minute bar in kline), 3m 近三月, 6m 近六月, ty 今年以来, 1y 近一年, 2y 近两年, 3y 近三年,
    5y 近五年, base 成立以来."""
    page: int = Field(default=1, ge=1)
    """Page number, from 1."""
    page_size: int = Field(default=50, ge=1, le=PAGE_SIZE_CAP)
    """Rows per page."""


class FundRankItem(UpstreamModel):
    """One fund in the ranking. A fund with no value for the window is likely absent (inferred)."""

    fund_code: str = Field(validation_alias="fd_code")
    name: str | None = Field(default=None, validation_alias="fd_name")
    return_pct: PercentPoints = Field(default=None, validation_alias="yield")
    """Return over the ``order_by`` window, in percent points (1.23 means 1.23%)."""
    nav: Number = Field(default=None, validation_alias="unit_nav")
    """Latest net value per unit, in CNY; its date is not sent."""
    fund_type: str | None = Field(default=None, validation_alias="sf_type")
    """Upstream type code; it equalled the requested type where measured."""
    product: str | None = Field(default=None, validation_alias="f_type")
    """Upstream product kind; only ``fund`` was seen."""


class _Payload(UpstreamModel):
    items: tuple[FundRankItem, ...]
    current_page: int
    size: int
    total_items: int
    total_pages: int


def _build_query(params: FundRankParams, now: datetime) -> dict[str, str]:
    return {
        "type": params.fund_type,
        "order_by": params.order_by,
        "size": str(params.page_size),
        "page": str(params.page),
    }


def _parse(payload: Any, params: FundRankParams) -> Page[FundRankItem]:
    data = _Payload.model_validate(payload)
    return Page(
        data.items,
        page=data.current_page,
        page_size=data.size,
        total=data.total_items,
        last_page=data.total_pages,
    )


ENDPOINT = Endpoint(
    name="fund_rank",
    doc="Funds of one type ranked by return over a window, best first, at most 1000 rows per type.",
    host="danjuanfunds.com",
    path="/djapi/v3/filter/fund",
    params=FundRankParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="danjuan",
    verified_on=date(2026, 11, 5),
    # Types 1, 2 and 3 all report total_items 1000, measured 2026-11-05, so the walk takes that
    # as the end of the list, at total_pages. The funds ranked after it are out of reach.
    pagination=Pagination(
        style="page", end="total", size_cap=PAGE_SIZE_CAP, key=lambda row: row.fund_code
    ),
    order="rank",
    agent=AgentInfo(
        category="fund",
        group="xq_fund",
        section="rank",
        title_zh="基金排行",
        title_en="Fund ranking",
        summary=(
            "按基金类型和区间收益率排行列出基金，按页返回代码、名称、区间收益率和最新净值，"
            "每种类型最多前 1000 名。"
        ),
        keywords=(
            "基金排行",
            "基金排名",
            "收益排行",
            "股票型基金",
            "混合型基金",
            "fund ranking",
            "top funds",
            "best performing funds",
        ),
        default_fields=("fund_code", "name", "return_pct", "nav"),
        row_default=50,
        row_cap=PAGE_SIZE_CAP,
        upstream_cap=PAGE_SIZE_CAP,
        examples=({"fund_type": "1", "order_by": "1m", "page_size": 3},),
    ),
)
