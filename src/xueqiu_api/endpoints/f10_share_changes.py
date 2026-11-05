"""f10_share_changes: share capital changes of one A-share, latest first."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .._f10 import ExchangeDate
from .._model import UpstreamModel
from .._values import WholeNumber
from ..registry import AgentInfo, Endpoint, Pagination
from ..symbols import SymbolCode, normalize_symbol

MAX_COUNT = 100
"""Most rows per request. Not measured: the survey sent count=3 and got 3 rows."""


class ShareChangeParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    symbol: SymbolCode
    """An A-share symbol."""
    count: int = Field(default=50, ge=1, le=MAX_COUNT)
    """Latest changes to fetch."""


class ShareChange(UpstreamModel):
    """One change of the share capital."""

    chg_date: ExchangeDate
    """Date of the change."""
    chg_reason: str | None = None
    """Reason as a short upstream label, such as 可转债转股."""
    total_shares: WholeNumber = None
    """Total shares after the change."""
    float_shares: WholeNumber = None
    """Floating shares after the change."""


class _Payload(UpstreamModel):
    items: tuple[ShareChange, ...]
    restricts: tuple[Any, ...] = ()
    """Not returned: empty in the one response seen, so its rows are unknown."""


def _build_query(params: ShareChangeParams, now: datetime) -> dict[str, str]:
    return {"symbol": params.symbol, "count": str(params.count)}


def _parse(payload: Any, params: ShareChangeParams) -> list[ShareChange]:
    tz = normalize_symbol(params.symbol).timezone
    return list(_Payload.model_validate(payload, context={"tz": tz}).items)


ENDPOINT = Endpoint(
    name="f10_share_changes",
    doc="Share capital changes of one A-share, latest first.",
    host="stock.xueqiu.com",
    path="/v5/stock/f10/cn/shareschg.json",
    params=ShareChangeParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    pagination=Pagination(style="latest"),
    markets=("CN",),
    order="newest_first",
    agent=AgentInfo(
        category="f10",
        group="xq_company",
        section="share_changes",
        title_zh="股本变动",
        title_en="Share capital changes",
        summary="取一只 A 股历次股本变动的日期、原因、变动后总股本和流通股本，最新的在前。",
        keywords=(
            "股本变动",
            "总股本",
            "流通股本",
            "股本结构",
            "股本变化",
            "可转债转股",
            "share capital",
            "shares outstanding",
            "float shares",
            "share changes",
        ),
        default_fields=("chg_date", "chg_reason", "total_shares", "float_shares"),
        row_default=50,
        row_cap=MAX_COUNT,
        upstream_cap=MAX_COUNT,
        examples=({"symbol": "SH600000", "count": 3},),
    ),
)
