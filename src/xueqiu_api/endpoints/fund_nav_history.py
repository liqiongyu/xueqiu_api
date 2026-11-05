"""fund_nav_history: net asset value history of one fund, latest first."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .._model import UpstreamModel
from .._pages import Page
from .._values import Number, PercentPoints
from ..registry import AgentInfo, Endpoint, Pagination
from ..symbols import FundCode

PAGE_SIZE_CAP = 2000
"""size=2000 returned all 1603 rows of 008975 in one page and echoed 2000, measured 2026-11-05.
Larger sizes were not tried."""


class FundNavHistoryParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    fund_code: FundCode
    """Six digits, such as 008975."""
    page: int = Field(default=1, ge=1)
    """Page number, from 1; page 1 holds the latest rows."""
    page_size: int = Field(default=50, ge=1, le=PAGE_SIZE_CAP)
    """Rows per page."""


class FundNav(UpstreamModel):
    """Net asset value of one fund on one date."""

    date: date
    """Value date. It can fall on a weekend, so these dates are not a trading calendar."""
    nav: Number = None
    """Net value per unit, in CNY."""
    nav_value: Number = Field(default=None, validation_alias="value")
    """Upstream ``value``, whose meaning is unconfirmed: it equalled ``nav`` on every row measured
    on 2026-11-05, for a fund that paid dividends too, so it is not the accumulated net value."""
    daily_pct: PercentPoints = Field(default=None, validation_alias="percentage")
    """Change from the previous value, in percent points (1.23 means 1.23%); the oldest row of a
    fund has none."""


class _Payload(UpstreamModel):
    items: tuple[FundNav, ...]
    current_page: int
    size: int
    total_items: int
    total_pages: int


def _build_query(params: FundNavHistoryParams, now: datetime) -> dict[str, str]:
    return {"fund_code": params.fund_code, "page": str(params.page), "size": str(params.page_size)}


def _parse(payload: Any, params: FundNavHistoryParams) -> Page[FundNav]:
    data = _Payload.model_validate(payload)
    return Page(
        data.items,
        page=data.current_page,
        page_size=data.size,
        total=data.total_items,
        last_page=data.total_pages,
    )


ENDPOINT = Endpoint(
    name="fund_nav_history",
    doc="Net asset value history of one fund, latest first.",
    host="danjuanfunds.com",
    path="/djapi/fund/nav/history/{fund_code}",
    params=FundNavHistoryParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="danjuan",
    verified_on=date(2026, 11, 5),
    # A value published between two requests shifts every later page by one row.
    pagination=Pagination(
        style="page", end="total", size_cap=PAGE_SIZE_CAP, key=lambda row: row.date
    ),
    order="newest_first",
    agent=AgentInfo(
        category="fund",
        group="xq_fund",
        section="nav_history",
        title_zh="基金历史净值",
        title_en="Fund NAV history",
        summary="按页取一只基金的历史单位净值和日涨跌，最新的在前。",
        keywords=(
            "基金净值",
            "历史净值",
            "单位净值",
            "净值走势",
            "fund nav",
            "net asset value",
            "nav history",
        ),
        default_fields=("date", "nav", "daily_pct"),
        row_default=50,
        row_cap=500,
        upstream_cap=PAGE_SIZE_CAP,
        examples=({"fund_code": "008975", "page_size": 3},),
    ),
)
