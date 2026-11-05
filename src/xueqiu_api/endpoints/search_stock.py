"""search_stock: look up one security by code or name, with its industry and last price."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from .._model import QuoteRow, UpstreamModel
from .._values import WholeNumber, no_data
from ..registry import AgentInfo, Endpoint

RESULT_CAP = 10
"""Rows one request returns: the upstream echoes size=10; it never sent that many."""
QUERY_CHARS = 50


class SearchStockParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    query: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=QUERY_CHARS)
    ]
    """Code or name, such as SH600000 or 浦发银行; the name form was measured 2026-11-05."""


class StockHit(QuoteRow):
    """One security found. The session's watchlist state and the upstream's highlight, generated
    text and promotion fields stay out; other upstream fields stay in ``model_extra``."""

    drop: ClassVar[frozenset[str]] = frozenset(
        {"hasexist", "highlight_name", "ai_result", "promotion_reason"}
    )

    name_en: Annotated[str | None, BeforeValidator(no_data)] = Field(
        default=None, validation_alias="enName"
    )
    """English name, when the upstream has one."""
    exchange: str | None = None
    """Listing exchange, such as SH."""
    stock_type: WholeNumber = Field(default=None, validation_alias="type")
    """Xueqiu security type code; 11 is an A share."""
    ind_name: str | None = None
    """Industry, such as 银行."""


class _Payload(UpstreamModel):
    stocks: tuple[StockHit, ...]


def _build_query(params: SearchStockParams, now: datetime) -> dict[str, str]:
    return {"code": params.query}


def _parse(payload: Any, params: SearchStockParams) -> list[StockHit]:
    return list(_Payload.model_validate(payload).stocks)


ENDPOINT = Endpoint(
    name="search_stock",
    doc="Securities matching a code or name, with their exchange, industry and last price.",
    host="www.xueqiu.com",
    path="/query/v1/search/stock.json",
    params=SearchStockParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="intraday",
    envelope="bare",
    verified_on=date(2026, 11, 5),
    agent=AgentInfo(
        category="search",
        title_zh="证券检索",
        title_en="Security lookup",
        summary="按代码或名称查证券的英文名、交易所、所属行业和最新价；只要代码时用证券搜索。",
        keywords=(
            "证券检索",
            "股票查询",
            "所属行业",
            "按名称查",
            "stock lookup",
            "security search",
            "industry",
        ),
        default_fields=(
            "symbol",
            "name",
            "name_en",
            "exchange",
            "stock_type",
            "ind_name",
            "current",
            "percent",
        ),
        row_default=RESULT_CAP,
        row_cap=RESULT_CAP,
        upstream_cap=RESULT_CAP,
        examples=({"query": "浦发银行"},),
        empty_hint="no rows: nothing matches this code or name; suggest_stock also takes pinyin.",
    ),
)
