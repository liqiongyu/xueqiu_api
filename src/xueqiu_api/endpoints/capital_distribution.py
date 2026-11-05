"""capital_distribution: today's buy and sell turnover of one A-share by order size."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, cast

from pydantic import AliasPath, Field, ValidationInfo, field_validator

from .._capital import symbol_query, validate_for
from .._model import UpstreamModel
from .._values import Number
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolParams
from .quotec import exchange_time


def _amount(side: str, size: str) -> Any:
    return Field(default=None, validation_alias=AliasPath("distribution", side, size))


class CapitalDistribution(UpstreamModel):
    """Turnover of one session by order size, gross on each side.

    Both sides summed to the same amount, 813911761.76 yuan against a day's turnover of
    835024728, measured 2026-11-04 on a snapshot stamped 14:56:59, so the closing auction seems to
    be left out.
    """

    symbol: str
    timestamp: datetime | None = Field(validation_alias=AliasPath("distribution", "timestamp"))
    """Time of the snapshot, timezone-aware in the exchange timezone."""
    net_inflow: Number = None
    """Main-force net inflow, in CNY: buys minus sells of extra-large and large orders."""
    buy_xlarge: Number = _amount("buy", "xlarge")
    """Bought in extra-large orders, in CNY."""
    buy_large: Number = _amount("buy", "large")
    """Bought in large orders, in CNY."""
    buy_medium: Number = _amount("buy", "medium")
    """Bought in medium orders, in CNY."""
    buy_small: Number = _amount("buy", "small")
    """Bought in small orders, in CNY."""
    sell_xlarge: Number = _amount("sell", "xlarge")
    """Sold in extra-large orders, in CNY."""
    sell_large: Number = _amount("sell", "large")
    """Sold in large orders, in CNY."""
    sell_medium: Number = _amount("sell", "medium")
    """Sold in medium orders, in CNY."""
    sell_small: Number = _amount("sell", "small")
    """Sold in small orders, in CNY."""
    analysis: str | None = None
    """The upstream's generated sentences about the flow, joined with ；."""

    @field_validator("timestamp", mode="before")
    @classmethod
    def _exchange_time(cls, value: Any, info: ValidationInfo) -> Any:
        return exchange_time(value, info)

    @field_validator("analysis", mode="before")
    @classmethod
    def _joined(cls, value: Any) -> Any:
        return "；".join(cast(list[str], value)) if isinstance(value, list) else value


def _parse(payload: Any, params: SymbolParams) -> CapitalDistribution:
    return validate_for(CapitalDistribution, payload, params.symbol)


ENDPOINT = Endpoint(
    name="capital_distribution",
    doc="Today's buy and sell turnover of one A-share by order size, and its main-force net.",
    host="stock.xueqiu.com",
    path="/v5/stock/capital/distribution.json",
    params=SymbolParams,
    build_query=symbol_query,
    parse=_parse,
    auth="anonymous",
    freshness="intraday",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    markets=("CN",),
    agent=AgentInfo(
        category="capital",
        group="xq_capital",
        section="distribution",
        title_zh="资金分布",
        title_en="Capital distribution by order size",
        summary="取一只 A 股当天超大单、大单、中单、小单的买入和卖出成交额，以及主力净流入。",
        keywords=(
            "资金分布",
            "今日资金",
            "主力净流入",
            "超大单",
            "买卖成交额",
            "capital distribution",
            "order size",
            "buy sell turnover",
        ),
        default_fields=(
            "timestamp",
            "net_inflow",
            "buy_xlarge",
            "buy_large",
            "buy_medium",
            "buy_small",
            "sell_xlarge",
            "sell_large",
            "sell_medium",
            "sell_small",
        ),
        row_default=1,
        row_cap=1,
        upstream_cap=1,
        examples=({"symbol": "SH600000"},),
        precision=2,
        free_text=("analysis",),
    ),
)
