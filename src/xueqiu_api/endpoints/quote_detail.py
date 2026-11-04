"""quote_detail: the full quote of one symbol, with its name and market status."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, cast

from pydantic import model_validator

from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolParams
from .quotec import Quote


class QuoteDetail(Quote):
    """Quote of one symbol, as ``quote.json`` and ``batch/quote.json`` send it with
    ``extend=detail``.

    The upstream item is ``{"market": ..., "quote": ..., "others": ..., "tags": ...}``. Fields of
    ``quote`` sit at the top level; ``market``, ``others``, ``tags`` and every quote field not
    declared here, such as ``pe_ttm``, ``pb`` or ``currency``, are kept in ``model_extra``.
    """

    name: str | None = None
    """Security name, such as 浦发银行."""
    market_status_id: int | None = None
    """``market.status_id``: 5 (trading) and 7 (closed) were measured on 2026-11-04; values on
    holidays and before the open are not known yet."""

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        item = cast(dict[str, Any], value)
        quote: Any = item.get("quote")
        if not isinstance(quote, dict):
            return item
        market: Any = item.get("market")
        status = cast(dict[str, Any], market).get("status_id") if isinstance(market, dict) else None
        rest = {key: part for key, part in item.items() if key != "quote"}
        return {**rest, **cast(dict[str, Any], quote), "market_status_id": status}


def _build_query(params: SymbolParams, now: datetime) -> dict[str, str]:
    return {"symbol": params.symbol, "extend": "detail"}


def _parse(payload: Any, params: SymbolParams) -> QuoteDetail:
    return QuoteDetail.model_validate(payload)


ENDPOINT = Endpoint(
    name="quote_detail",
    host="stock.xueqiu.com",
    path="/v5/stock/quote.json",
    params=SymbolParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="realtime",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    agent=AgentInfo(
        title_zh="行情详情",
        title_en="Quote detail",
        summary="取一只股票或指数的完整行情，包括名称、市值、市盈率、市净率、股息率和市场交易状态。",
        keywords=(
            "行情详情",
            "个股行情",
            "市盈率",
            "市净率",
            "股息率",
            "交易状态",
            "quote detail",
            "valuation",
            "pe",
            "pb",
        ),
        default_fields=(
            "symbol",
            "name",
            "current",
            "percent",
            "chg",
            "open",
            "high",
            "low",
            "last_close",
            "volume",
            "amount",
            "turnover_rate",
            "market_capital",
            "market_status_id",
            "timestamp",
        ),
        row_default=1,
        row_cap=1,
        upstream_cap=1,
        examples=({"symbol": "SH600000"},),
    ),
)
