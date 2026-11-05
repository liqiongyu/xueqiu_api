"""Static surface checks: pyright strict reads this file, pytest never runs it."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import assert_type

from xueqiu_api import AsyncClient, Client, Page
from xueqiu_api.models import (
    BlockTrade,
    Company,
    CubeNav,
    Financials,
    FundNav,
    Kline,
    Quote,
    QuoteDetail,
    StockSuggestion,
)


def sync_surface(c: Client) -> None:
    assert_type(c.quotec("SH600000"), list[Quote])
    assert_type(c.batch_quote("SH600000,00700"), list[QuoteDetail])
    assert_type(c.batch_quote(["SH600000"]), list[QuoteDetail])
    assert_type(c.quote_detail("SH600000"), QuoteDetail)
    assert_type(c.kline("SH600000", period="week", adjust="after"), Kline)
    assert_type(c.kline_history("SH600000"), Kline)
    assert_type(c.suggest_stock(query="pfyh"), list[StockSuggestion])
    assert_type(c.f10_company("SH600000"), Company)
    assert_type(c.finance_income("SH600000", report_type="annual"), Financials)
    assert_type(c.capital_block_trades("SH600000"), Page[BlockTrade])
    assert_type(c.capital_block_trades_pages("SH600000", max_items=5), Iterator[Page[BlockTrade]])
    assert_type(c.fund_nav_history("008975", page=2), Page[FundNav])
    assert_type(c.fund_nav_history_pages("008975", page_size=None), Iterator[Page[FundNav]])
    assert_type(c.cube_nav_daily("ZH2567925"), CubeNav)
    for row in c.fund_nav_history("008975"):
        assert_type(row, FundNav)
    c.kline("SH600000", period="daily")  # pyright: ignore[reportArgumentType]
    c.kline("SH600000", "day")  # pyright: ignore[reportCallIssue]
    c.kline(symbol="SH600000", begins=None)  # pyright: ignore[reportCallIssue]
    c.suggest_stock("pfyh")  # pyright: ignore[reportCallIssue]
    c.finance_income("SH600000", type="all")  # pyright: ignore[reportCallIssue]
    c.capital_block_trades("SH600000", page="1")  # pyright: ignore[reportArgumentType]
    c.capital_block_trades("SH600000", page_size=None)  # pyright: ignore[reportArgumentType]
    c.capital_block_trades_pages("SH600000", max_pages="1")  # pyright: ignore[reportArgumentType]
    c.fund_nav_history(8975)  # pyright: ignore[reportArgumentType]
    c.cube_nav_daily("ZH2567925", page=1)  # pyright: ignore[reportCallIssue]


async def async_surface(c: AsyncClient) -> None:
    assert_type(await c.quotec("SH600000"), list[Quote])
    assert_type(await c.batch_quote(["SH600000"]), list[QuoteDetail])
    assert_type(await c.kline("SH600000", period="week"), Kline)
    assert_type(await c.kline_history("SH600000"), Kline)
    assert_type(await c.finance_income("SH600000"), Financials)
    assert_type(await c.capital_block_trades("SH600000"), Page[BlockTrade])
    assert_type(c.capital_block_trades_pages("SH600000"), AsyncIterator[Page[BlockTrade]])
    assert_type(c.fund_nav_history_pages("008975"), AsyncIterator[Page[FundNav]])
    assert_type(await c.cube_nav_daily("ZH2567925"), CubeNav)
    async for page in c.fund_nav_history_pages("008975", max_pages=2):
        assert_type(page, Page[FundNav])
    await c.kline("SH600000", period="daily")  # pyright: ignore[reportArgumentType]
    await c.capital_block_trades("SH600000", page="1")  # pyright: ignore[reportArgumentType]
