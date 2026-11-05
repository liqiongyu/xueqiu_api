"""batch_quote: quotes of several symbols in one request, with names and market status."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter

from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolCode, split_symbols
from .quote_detail import QuoteDetail

BATCH_CAP = 20
"""Symbols per request, kept low because it is not measured: the survey sent 3 and 6."""


class BatchQuoteParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    symbols: Annotated[
        tuple[SymbolCode, ...],
        BeforeValidator(split_symbols),
        Field(min_length=1, max_length=BATCH_CAP),
    ]
    """Symbols, as a sequence or one comma-separated string."""


_DETAILS = TypeAdapter(list[QuoteDetail])


def _build_query(params: BatchQuoteParams, now: datetime) -> dict[str, str]:
    return {"symbol": ",".join(params.symbols), "extend": "detail"}


def _parse(payload: Any, params: BatchQuoteParams) -> list[QuoteDetail]:
    items: Any = cast(dict[str, Any], payload).get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("batch quote data needs an items list")
    return _DETAILS.validate_python(items)


ENDPOINT = Endpoint(
    name="batch_quote",
    doc="Quotes of up to 20 symbols in one request, with names and market status.",
    host="stock.xueqiu.com",
    path="/v5/stock/batch/quote.json",
    params=BatchQuoteParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="realtime",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    markets=("CN", "HK", "US"),
    agent=AgentInfo(
        category="quote",
        title_zh="批量行情",
        title_en="Batch quotes",
        summary="一次请求取多只股票或指数的名称、最新价、涨跌幅、成交额、市值和市场交易状态。",
        keywords=(
            "批量行情",
            "实时行情",
            "股票名称",
            "最新价",
            "涨跌幅",
            "市值",
            "quote",
            "batch",
            "realtime",
            "price",
            "market status",
        ),
        default_fields=(
            "symbol",
            "name",
            "current",
            "percent",
            "chg",
            "volume",
            "amount",
            "market_capital",
            "market_status_id",
            "timestamp",
        ),
        row_default=BATCH_CAP,
        row_cap=BATCH_CAP,
        upstream_cap=BATCH_CAP,
        examples=({"symbols": ["SH600000", "00700", "AAPL"]},),
        mcp_tool="xq_quote",
    ),
)
