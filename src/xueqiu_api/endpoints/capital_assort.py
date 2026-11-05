"""capital_assort: today's buy and sell turnover of one A-share by trade size, per side."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import Field

from .._capital import symbol_query, validate_for
from .._f10 import ExchangeDate
from .._model import UpstreamModel
from .._values import Number
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolParams


class CapitalAssort(UpstreamModel):
    """Turnover of one session by size class.

    Another measure than ``capital_distribution``: here the two sides differ (478871068 bought,
    344478792 sold, measured 2026-11-04) and extra-large is null.
    """

    symbol: str
    trade_date: ExchangeDate = Field(validation_alias="timestamp")
    """Trading day."""
    buy_xlarge: Number = None
    """Bought in the extra-large class, in CNY; null wherever it was measured."""
    buy_large: Number = None
    """Bought in the large class, in CNY."""
    buy_medium: Number = None
    """Bought in the medium class, in CNY."""
    buy_small: Number = None
    """Bought in the small class, in CNY."""
    buy_total: Number = None
    """All bought, in CNY: the sum of the classes."""
    sell_xlarge: Number = None
    """Sold in the extra-large class, in CNY; null wherever it was measured."""
    sell_large: Number = None
    """Sold in the large class, in CNY."""
    sell_medium: Number = None
    """Sold in the medium class, in CNY."""
    sell_small: Number = None
    """Sold in the small class, in CNY."""
    sell_total: Number = None
    """All sold, in CNY: the sum of the classes."""


def _parse(payload: Any, params: SymbolParams) -> CapitalAssort:
    return validate_for(CapitalAssort, payload, params.symbol)


ENDPOINT = Endpoint(
    name="capital_assort",
    doc="Today's buy and sell turnover of one A-share by size class, with the totals per side.",
    host="stock.xueqiu.com",
    path="/v5/stock/capital/assort.json",
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
        section="assort",
        title_zh="成交分布",
        title_en="Turnover by order size",
        summary="取一只 A 股当天大单、中单、小单的买入和卖出成交额及合计，统计口径和资金分布不同。",
        keywords=(
            "成交分布",
            "大单成交",
            "中单",
            "小单成交",
            "买入合计",
            "turnover by order size",
            "trade size",
            "buy sell totals",
        ),
        default_fields=(
            "trade_date",
            "buy_large",
            "buy_medium",
            "buy_small",
            "buy_total",
            "sell_large",
            "sell_medium",
            "sell_small",
            "sell_total",
        ),
        row_default=1,
        row_cap=1,
        upstream_cap=1,
        examples=({"symbol": "SH600000"},),
        precision=2,
    ),
)
