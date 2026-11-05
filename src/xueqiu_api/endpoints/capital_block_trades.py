"""capital_block_trades: disclosed block trades of one A-share, latest first."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import Field

from .._capital import PAGE_SIZE_CAP, CapitalPageParams, page_query
from .._f10 import ExchangeDate
from .._model import UpstreamModel
from .._pages import Page
from .._values import Number
from ..registry import AgentInfo, Endpoint, Pagination
from ..symbols import normalize_symbol


class BlockTrade(UpstreamModel):
    """One disclosed block trade."""

    trade_date: ExchangeDate = Field(validation_alias="td_date")
    """Trade date. Only days with block trades appear; one day can have several rows."""
    trans_price: Number = None
    """Trade price, in CNY per share."""
    vol: Number = None
    """Shares traded."""
    trans_amt: Number = None
    """Turnover, in CNY, as sent: within 0.01% of price times shares, measured 2026-11-05."""
    premium_pct: Number = Field(default=None, validation_alias="premium_rat")
    """Premium over the unadjusted close of the day, in percent points (1.23 means 1.23%)."""
    buy_branch_org_name: str | None = None
    """Buyer's seat as disclosed, such as 机构专用."""
    sell_branch_org_name: str | None = None
    """Seller's seat as disclosed."""


class _Payload(UpstreamModel):
    items: tuple[BlockTrade, ...]


def _parse(payload: Any, params: CapitalPageParams) -> Page[BlockTrade]:
    tz = normalize_symbol(params.symbol).timezone
    rows = _Payload.model_validate(payload, context={"tz": tz}).items
    return Page(rows, page=params.page, page_size=params.page_size)


ENDPOINT = Endpoint(
    name="capital_block_trades",
    doc="Disclosed block trades of one A-share, latest first.",
    host="stock.xueqiu.com",
    path="/v5/stock/capital/blocktrans.json",
    params=CapitalPageParams,
    build_query=page_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="xueqiu",
    verified_on=date(2026, 11, 5),
    # Pages 1 and 2 were full at size=100, measured 2026-11-05. No short last page was seen: that
    # it ends the list follows from the honoured page size.
    pagination=Pagination(style="page", end="short", size_cap=PAGE_SIZE_CAP),
    markets=("CN",),
    order="newest_first",
    agent=AgentInfo(
        category="capital",
        group="xq_capital",
        section="blocktrans",
        title_zh="大宗交易",
        title_en="Block trades",
        summary=(
            "取一只 A 股的大宗交易记录，包括成交价、成交量、成交额、溢价率和买卖营业部，"
            "最新的在前。"
        ),
        keywords=(
            "大宗交易",
            "大宗",
            "溢价率",
            "折价率",
            "营业部",
            "机构专用",
            "block trade",
            "block trades",
            "premium",
        ),
        default_fields=(
            "trade_date",
            "trans_price",
            "vol",
            "trans_amt",
            "premium_pct",
            "buy_branch_org_name",
            "sell_branch_org_name",
        ),
        row_default=50,
        row_cap=PAGE_SIZE_CAP,
        upstream_cap=PAGE_SIZE_CAP,
        examples=({"symbol": "SH600000", "page_size": 20},),
        precision=2,
    ),
)
