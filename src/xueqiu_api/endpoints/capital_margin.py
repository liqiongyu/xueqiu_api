"""capital_margin: margin trading and short selling of one A-share per day, latest first."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .._capital import validate_for
from .._f10 import ExchangeDate
from .._model import UpstreamModel
from .._values import Number
from ..registry import AgentInfo, Endpoint, Pagination
from ..symbols import SymbolCode

MAX_COUNT = 120
"""size=200 returned 120 rows, about half a year, and page 2 was empty, measured 2026-11-05."""


class CapitalMarginParams(BaseModel):
    """Older days than the latest 120 are out of reach."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    symbol: SymbolCode
    """An A-share symbol."""
    count: int = Field(default=50, ge=1, le=MAX_COUNT)
    """Trading days to fetch, latest first."""


class MarginDay(UpstreamModel):
    """Margin balances of one trading day."""

    trade_date: ExchangeDate = Field(validation_alias="td_date")
    """Trading day. Published the next trading day: the latest row was the day before on both
    2026-11-04 and 2026-11-05."""
    margin_trading_balance: Number = None
    """Financing balance (融资余额), in CNY."""
    short_selling_amt_balance: Number = None
    """Short-selling balance by amount (融券余额), in CNY."""
    total_balance: Number = Field(default=None, validation_alias="margin_trading_amt_balance")
    """Financing plus short-selling balance (融资融券余额), in CNY; exact on all 120 rows measured
    2026-11-05. Upstream ``margin_trading_amt_balance``, which reads like the financing balance."""
    margin_trading_buy_amt: Number = None
    """Bought on financing (融资买入额), in CNY."""
    margin_trading_net_buy_amt: Number = None
    """Net bought on financing (融资净买入额), in CNY. Close to the day's change of the financing
    balance but not always equal to it: 7 of 119 days were over 10 yuan off, up to 841901,
    measured 2026-11-05."""


class MarginHistory(UpstreamModel):
    """The latest trading days of one A-share, latest first."""

    symbol: str
    inclusion_date: ExchangeDate = None
    """The upstream's ``inclusion_date``, apparently when the stock became eligible for margin
    trading: 2010-03-31, the start of the pilot, for SH600000."""
    items: tuple[MarginDay, ...]

    def header(self) -> Mapping[str, Any]:
        return {"inclusion_date": self.inclusion_date}

    def sections(self) -> Mapping[str, Sequence[BaseModel]]:
        return {"items": self.items}

    def __len__(self) -> int:
        return len(self.items)


def _build_query(params: CapitalMarginParams, now: datetime) -> dict[str, str]:
    return {"symbol": params.symbol, "page": "1", "size": str(params.count)}


def _parse(payload: Any, params: CapitalMarginParams) -> MarginHistory:
    return validate_for(MarginHistory, payload, params.symbol)


ENDPOINT = Endpoint(
    name="capital_margin",
    doc="Margin trading and short selling of one A-share per day, latest first, up to 120 days.",
    host="stock.xueqiu.com",
    path="/v5/stock/capital/margin.json",
    params=CapitalMarginParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="xueqiu",
    verified_on=date(2026, 11, 5),
    pagination=Pagination(style="latest"),
    markets=("CN",),
    order="newest_first",
    agent=AgentInfo(
        category="capital",
        group="xq_capital",
        section="margin",
        title_zh="融资融券",
        title_en="Margin trading and short selling",
        summary=(
            "取一只 A 股最近最多 120 个交易日的融资余额、融券余额、两融余额、融资买入额和"
            "融资净买入额，最新的在前。"
        ),
        keywords=(
            "融资融券",
            "两融余额",
            "融资余额",
            "融券余额",
            "融资买入",
            "融资净买入",
            "margin trading",
            "short selling",
            "margin balance",
        ),
        default_fields=(
            "trade_date",
            "margin_trading_balance",
            "short_selling_amt_balance",
            "total_balance",
            "margin_trading_buy_amt",
            "margin_trading_net_buy_amt",
        ),
        row_default=50,
        row_cap=MAX_COUNT,
        upstream_cap=MAX_COUNT,
        examples=({"symbol": "SH600000", "count": 20},),
        precision=2,
        empty_hint="no rows: this stock may not be eligible for margin trading.",
    ),
)
