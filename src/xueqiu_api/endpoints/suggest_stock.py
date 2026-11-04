"""suggest_stock: look up securities by code, pinyin initials or name."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from ..registry import AgentInfo, Endpoint

RESULT_CAP = 10
"""Results one request returns. Not measured: the survey saw at most 9."""
QUERY_CHARS = 50


class SuggestStockParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    query: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=QUERY_CHARS)
    ]
    """Code, pinyin initials or name, such as 600000, pfyh, 浦发银行 or AAPL."""


class StockSuggestion(BaseModel):
    """One search result. Unknown upstream fields (``label``, ``state``, ``type``) are kept in
    ``model_extra``."""

    model_config = ConfigDict(extra="allow", frozen=True, use_attribute_docstrings=True)

    symbol: str = Field(validation_alias="code")
    """Xueqiu symbol, such as SH600000 or AAPL (upstream ``code``)."""
    name: str = Field(validation_alias="query")
    """Security name, such as 浦发银行 (upstream ``query``)."""
    stock_type: int | None = None
    """Xueqiu security type code; the survey saw 11 for an A share and 0 for a US stock."""


_SUGGESTIONS = TypeAdapter(list[StockSuggestion])


def _build_query(params: SuggestStockParams, now: datetime) -> dict[str, str]:
    return {"q": params.query}


def _parse(payload: Any, params: SuggestStockParams) -> list[StockSuggestion]:
    return _SUGGESTIONS.validate_python(payload)


ENDPOINT = Endpoint(
    name="suggest_stock",
    host="www.xueqiu.com",
    path="/query/v1/suggest_stock.json",
    params=SuggestStockParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="static",
    envelope="query",
    verified_on=date(2026, 11, 4),
    # The survey got results with every cookie left out.
    cookies=False,
    agent=AgentInfo(
        title_zh="证券搜索",
        title_en="Security search",
        summary="按代码、拼音首字母或名称查证券的雪球代码和名称，只知道名称时先用它。",
        keywords=(
            "证券搜索",
            "股票代码",
            "代码查询",
            "拼音",
            "名称",
            "search",
            "symbol lookup",
            "ticker",
            "suggest",
        ),
        default_fields=("symbol", "name", "stock_type"),
        row_default=RESULT_CAP,
        row_cap=RESULT_CAP,
        upstream_cap=RESULT_CAP,
        examples=({"query": "pfyh"},),
        mcp_tool="xq_search",
    ),
)
