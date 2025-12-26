from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from xueqiu.api._base import AsyncRequester, SyncRequester
from xueqiu.api.urls import (
    KLINE_PATH,
    REALTIME_PANKOU_PATH,
    REALTIME_QUOTE_DETAIL_PATH,
    REALTIME_QUOTEC_PATH,
)
from xueqiu.models import XueqiuResponse
from xueqiu.parsing import parse_datetime


def _join_symbols(symbols: str | Iterable[str]) -> str:
    if isinstance(symbols, str):
        return symbols
    return ",".join(symbols)


class Quote(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str
    current: float | None = None
    percent: float | None = None
    chg: float | None = None
    timestamp: datetime | None = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class MarketStatus(BaseModel):
    model_config = ConfigDict(extra="allow")

    status_id: int | None = None
    region: str | None = None
    status: str | None = None
    time_zone: str | None = None
    time_zone_desc: str | None = None
    delay_tag: int | None = None


class QuoteTag(BaseModel):
    model_config = ConfigDict(extra="allow")

    description: str | None = None
    value: int | None = None


class QuoteDetailQuote(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str | None = None
    code: str | None = None
    name: str | None = None
    exchange: str | None = None
    currency: str | None = None

    current: float | None = None
    percent: float | None = None
    chg: float | None = None

    open: float | None = None
    last_close: float | None = None
    high: float | None = None
    low: float | None = None
    avg_price: float | None = None

    volume: float | None = None
    amount: float | None = None
    turnover_rate: float | None = None

    market_capital: float | None = None
    float_market_capital: float | None = None

    pe_ttm: float | None = None
    pe_lyr: float | None = None
    pb: float | None = None
    ps: float | None = None
    pcf: float | None = None

    dividend: float | None = None
    dividend_yield: float | None = None

    timestamp: datetime | None = None
    time: datetime | None = None
    issue_date: datetime | None = None

    @field_validator("timestamp", "time", "issue_date", mode="before")
    @classmethod
    def _parse_times(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class QuoteDetailData(BaseModel):
    model_config = ConfigDict(extra="allow")

    market: MarketStatus | None = None
    quote: QuoteDetailQuote | None = None
    others: dict[str, Any] | None = None
    tags: list[QuoteTag] | None = None


class KlineData(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str | None = None
    column: list[str] | None = None
    item: list[list[Any]] | None = None

    def bars(self) -> list[KlineBar]:
        if not self.column or not self.item:
            return []
        bars: list[KlineBar] = []
        for row in self.item:
            row_map = {
                col: row[idx] if idx < len(row) else None for idx, col in enumerate(self.column)
            }
            bars.append(KlineBar.model_validate(row_map))
        return bars


class KlineBar(BaseModel):
    model_config = ConfigDict(extra="allow")

    timestamp: datetime | None = None
    volume: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    chg: float | None = None
    percent: float | None = None
    turnoverrate: float | None = None
    amount: float | None = None
    pe: float | None = None
    pb: float | None = None
    ps: float | None = None
    pcf: float | None = None
    market_capital: float | None = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class OrderBookLevel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    price: float | None = None
    count: float | None = None


class Pankou(BaseModel):
    """Real-time order book snapshot.

    Upstream format uses flat keys: bp1/bc1/... and sp1/sc1/...
    This model normalizes them into `bids` / `asks`.
    """

    model_config = ConfigDict(extra="allow")

    symbol: str | None = None
    timestamp: datetime | None = None
    current: float | None = None

    buypct: float | None = None
    sellpct: float | None = None
    diff: float | None = None
    ratio: float | None = None

    bids: list[OrderBookLevel] = []
    asks: list[OrderBookLevel] = []

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, value: Any) -> datetime | None:
        return parse_datetime(value)

    @classmethod
    def _extract_levels(
        cls, raw: dict[str, Any], prefix_p: str, prefix_c: str
    ) -> list[OrderBookLevel]:
        levels: list[OrderBookLevel] = []
        for i in range(1, 11):
            price = raw.get(f"{prefix_p}{i}")
            count = raw.get(f"{prefix_c}{i}")
            if price in (None, 0) and count in (None, 0):
                continue
            levels.append(OrderBookLevel(price=price, count=count))
        return levels

    @classmethod
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> Pankou:  # type: ignore[override]
        if isinstance(obj, dict):
            obj = dict(obj)
            obj.setdefault("bids", cls._extract_levels(obj, "bp", "bc"))
            obj.setdefault("asks", cls._extract_levels(obj, "sp", "sc"))
        return super().model_validate(obj, *args, **kwargs)


class RealtimeAPI:
    def __init__(self, client: SyncRequester) -> None:
        self._client = client

    def quotec(self, symbols: str | Iterable[str]) -> XueqiuResponse[list[Quote]]:
        return self._client.request_model(
            "GET",
            REALTIME_QUOTEC_PATH,
            params={"symbol": _join_symbols(symbols)},
            require_auth=False,
            model=XueqiuResponse[list[Quote]],
        )

    def quote_detail(self, symbol: str) -> XueqiuResponse[QuoteDetailData]:
        return self._client.request_model(
            "GET",
            REALTIME_QUOTE_DETAIL_PATH,
            params={"extend": "detail", "symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[QuoteDetailData],
        )

    def pankou(self, symbol: str) -> XueqiuResponse[Pankou]:
        return self._client.request_model(
            "GET",
            REALTIME_PANKOU_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[Pankou],
        )

    def kline(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 284,
        begin_ms: int | None = None,
        indicator: str = "kline,pe,pb,ps,pcf,market_capital,agt,ggt,balance",
    ) -> XueqiuResponse[KlineData]:
        begin_ms = int(begin_ms if begin_ms is not None else time.time() * 1000)
        return self._client.request_model(
            "GET",
            KLINE_PATH,
            params={
                "symbol": symbol,
                "begin": begin_ms,
                "period": period,
                "type": "before",
                "count": -abs(int(count)),
                "indicator": indicator,
            },
            require_auth=True,
            model=XueqiuResponse[KlineData],
        )


class AsyncRealtimeAPI:
    def __init__(self, client: AsyncRequester) -> None:
        self._client = client

    async def quotec(self, symbols: str | Iterable[str]) -> XueqiuResponse[list[Quote]]:
        return await self._client.request_model(
            "GET",
            REALTIME_QUOTEC_PATH,
            params={"symbol": _join_symbols(symbols)},
            require_auth=False,
            model=XueqiuResponse[list[Quote]],
        )

    async def quote_detail(self, symbol: str) -> XueqiuResponse[QuoteDetailData]:
        return await self._client.request_model(
            "GET",
            REALTIME_QUOTE_DETAIL_PATH,
            params={"extend": "detail", "symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[QuoteDetailData],
        )

    async def pankou(self, symbol: str) -> XueqiuResponse[Pankou]:
        return await self._client.request_model(
            "GET",
            REALTIME_PANKOU_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[Pankou],
        )

    async def kline(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 284,
        begin_ms: int | None = None,
        indicator: str = "kline,pe,pb,ps,pcf,market_capital,agt,ggt,balance",
    ) -> XueqiuResponse[KlineData]:
        begin_ms = int(begin_ms if begin_ms is not None else time.time() * 1000)
        return await self._client.request_model(
            "GET",
            KLINE_PATH,
            params={
                "symbol": symbol,
                "begin": begin_ms,
                "period": period,
                "type": "before",
                "count": -abs(int(count)),
                "indicator": indicator,
            },
            require_auth=True,
            model=XueqiuResponse[KlineData],
        )
