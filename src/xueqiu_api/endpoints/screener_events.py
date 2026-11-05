"""screener_events: notices about one A-share, such as block trades and reports, latest first."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .._finance import REPORT_DATE_ZONE
from .._model import UpstreamModel
from .._pages import Page
from .._time import date_from_ms
from ..registry import AgentInfo, Endpoint, Pagination
from ..symbols import SymbolCode

PAGE_SIZE_CAP = 50
"""size=100 gave pages of 50 and the next page went on from row 51, measured 2026-11-05."""


class ScreenerEventsParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    symbol: SymbolCode
    """An A-share symbol."""
    page: int = Field(default=1, ge=1)
    """Page number, from 1; page 1 holds the latest rows."""
    page_size: int = Field(default=50, ge=1, le=PAGE_SIZE_CAP)
    """Rows per page."""


class StockEvent(UpstreamModel):
    """One notice Xueqiu generates about a stock. ``ext`` and ``tags`` stay in ``model_extra``."""

    symbol: str
    event_date: date = Field(validation_alias="timestamp")
    """Date of the event, sent as midnight in Asia/Shanghai."""
    subtype: int | None = None
    """Event type code. Measured 2026-11-05: 2 预计财报发布, 3 业绩快报, 8 股东增持, 14 股东大会,
    16 大宗交易, 29 股东人数, 30 财报发布, 34 分配方案, 35 股权登记, 36 除权除息."""
    title: str | None = None
    """Name of the event type, such as 大宗交易."""
    message: str | None = None
    """One sentence on the event, generated upstream."""
    priority: int | None = None
    """Upstream ordering hint, whose meaning is unconfirmed."""

    @field_validator("event_date", mode="before")
    @classmethod
    def _shanghai_date(cls, value: Any) -> Any:
        return date_from_ms(value, REPORT_DATE_ZONE)


class _Payload(UpstreamModel):
    count: int
    items: tuple[StockEvent, ...]


def _build_query(params: ScreenerEventsParams, now: datetime) -> dict[str, str]:
    return {"symbol": params.symbol, "page": str(params.page), "size": str(params.page_size)}


def _parse(payload: Any, params: ScreenerEventsParams) -> Page[StockEvent]:
    data = _Payload.model_validate(payload)
    return Page(data.items, page=params.page, page_size=params.page_size, total=data.count)


ENDPOINT = Endpoint(
    name="screener_events",
    doc="Notices about one A-share, such as block trades and reports, latest first.",
    host="stock.xueqiu.com",
    path="/v5/stock/screener/event/list.json",
    params=ScreenerEventsParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="xueqiu",
    verified_on=date(2026, 11, 5),
    # One day can hold two events of one subtype with different messages, measured 2026-11-05, so
    # the message is part of the identity. Identical rows on one page both stay.
    pagination=Pagination(
        style="page",
        end="total",
        size_cap=PAGE_SIZE_CAP,
        key=lambda row: (row.event_date, row.subtype, row.message),
    ),
    markets=("CN",),
    order="newest_first",
    agent=AgentInfo(
        category="screener",
        group="xq_market",
        section="screener_events",
        title_zh="个股事件",
        title_en="Stock events",
        summary="按页取一只 A 股的事件提醒，如大宗交易、财报发布和除权除息，最新的在前。",
        keywords=(
            "个股事件",
            "大事提醒",
            "事件提醒",
            "财报发布",
            "除权除息",
            "stock events",
            "corporate events",
        ),
        default_fields=("event_date", "title", "subtype"),
        row_default=PAGE_SIZE_CAP,
        row_cap=PAGE_SIZE_CAP,
        upstream_cap=PAGE_SIZE_CAP,
        examples=({"symbol": "SH600000", "page_size": 3},),
        free_text=("message",),
    ),
)
