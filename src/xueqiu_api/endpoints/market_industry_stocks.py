"""market_industry_stocks: the upstream's list of stocks in the board of one A-share."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from .._model import QuoteRow, UpstreamModel
from .._values import Number
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolCode

COUNT_CAP = 50
"""Rows per request. Not measured: the survey sent size=3."""


class MarketIndustryStocksParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    symbol: SymbolCode
    """An A-share, such as SH600000."""
    count: int = Field(default=10, ge=1, le=COUNT_CAP)
    """How many rows from the top to fetch."""


class IndustryPeer(QuoteRow):
    """One stock of the list. Prices and market cap are in the quote currency of this row's own
    market, which can differ from that of the requested stock."""

    drop: ClassVar[frozenset[str]] = frozenset({"code"})

    exchange: str | None = None
    """Market of this row, such as HK."""
    volume: Number = None
    """Volume, in shares going by its size."""
    pe_ttm: Number = None
    """Trailing price to earnings, a multiple."""
    market_capital: Number = Field(default=None, validation_alias="marketCapital")
    """Market capitalization, in the quote currency."""


@dataclass(frozen=True, slots=True)
class IndustryStocks:
    """The board of one A-share and stocks in it, as the upstream ranks them.

    The rows need not be A-shares or close peers: for SH600000, a bank, the upstream listed Hong
    Kong stocks including a game company, by percent from high to low, measured 2026-11-04.
    """

    symbol: str
    industry_name: str | None
    plate_name: str | None
    stocks: tuple[IndustryPeer, ...]
    extra: Mapping[str, Any]
    """Other upstream values of the response."""

    def header(self) -> Mapping[str, Any]:
        names = {"industry_name": self.industry_name, "plate_name": self.plate_name}
        return {key: value for key, value in names.items() if value}

    def sections(self) -> Mapping[str, Sequence[BaseModel]]:
        return {"stocks": self.stocks}

    def __len__(self) -> int:
        return len(self.stocks)


class _Payload(UpstreamModel):
    industry_name: str | None = Field(default=None, validation_alias="industryname")
    plate_name: str | None = Field(default=None, validation_alias="platename")
    stocks: tuple[IndustryPeer, ...] = Field(validation_alias="industrystocks")


def _build_query(params: MarketIndustryStocksParams, now: datetime) -> dict[str, str]:
    # type=1 is the only value ever sent; what it selects is not known.
    return {"code": params.symbol, "type": "1", "size": str(params.count)}


def _parse(payload: Any, params: MarketIndustryStocksParams) -> IndustryStocks:
    data = _Payload.model_validate(payload)
    return IndustryStocks(
        symbol=params.symbol,
        industry_name=data.industry_name,
        plate_name=data.plate_name,
        stocks=data.stocks,
        extra=data.model_extra or {},
    )


ENDPOINT = Endpoint(
    name="market_industry_stocks",
    doc="Stocks the upstream lists under the board of one A-share, with their quotes and size.",
    host="www.xueqiu.com",
    path="/stock/industry/stockList.json",
    params=MarketIndustryStocksParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="intraday",
    # The top-level code of the body echoes the requested symbol: it is no error code.
    envelope="bare",
    verified_on=date(2026, 11, 4),
    markets=("CN",),
    order="rank",
    agent=AgentInfo(
        category="market",
        group="xq_market",
        section="industry_stocks",
        title_zh="板块下股票",
        title_en="Stocks under a board",
        summary=(
            "取上游列在一只 A 股所属板块下的股票，含最新价、涨跌幅、市盈率和市值，行里可能是港股，"
            "也可能和所查股票不同行业；同业分析用 screener_quote_list 的 ind_code。"
        ),
        keywords=(
            "行业个股",
            "板块个股",
            "industry stocks",
            "board stocks",
            "sector",
        ),
        default_fields=(
            "symbol",
            "name",
            "exchange",
            "current",
            "percent",
            "pe_ttm",
            "market_capital",
        ),
        row_default=10,
        row_cap=COUNT_CAP,
        upstream_cap=COUNT_CAP,
        examples=({"symbol": "SH600000", "count": 3},),
    ),
)
