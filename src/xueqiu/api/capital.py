from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from xueqiu.api._base import AsyncRequester, SyncRequester
from xueqiu.api.urls import (
    CAPITAL_ASSORT_PATH,
    CAPITAL_BLOCKTRANS_PATH,
    CAPITAL_FLOW_PATH,
    CAPITAL_HISTORY_PATH,
    CAPITAL_MARGIN_PATH,
)
from xueqiu.models import XueqiuResponse
from xueqiu.parsing import parse_datetime


class MarginItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    margin_trading_amt_balance: float | None = None
    short_selling_amt_balance: float | None = None
    margin_trading_balance: float | None = None
    trade_date: datetime | None = Field(default=None, validation_alias="td_date")

    @field_validator("trade_date", mode="before")
    @classmethod
    def _parse_trade_date(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class MarginData(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: list[MarginItem] = Field(default_factory=list)


class BlocktransItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    volume: float | None = Field(default=None, validation_alias="vol")
    sell_branch_org_name: str | None = None
    premium_rate: float | None = Field(default=None, validation_alias="premium_rat")
    transaction_amount: float | None = Field(default=None, validation_alias="trans_amt")
    trade_date: datetime | None = Field(default=None, validation_alias="td_date")
    buy_branch_org_name: str | None = None
    transaction_price: float | None = Field(default=None, validation_alias="trans_price")

    @field_validator("trade_date", mode="before")
    @classmethod
    def _parse_trade_date(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class BlocktransData(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: list[BlocktransItem] = Field(default_factory=list)


class CapitalAssortData(BaseModel):
    model_config = ConfigDict(extra="allow")

    sell_large: float | None = None
    sell_medium: float | None = None
    sell_small: float | None = None
    sell_total: float | None = None
    buy_large: float | None = None
    buy_medium: float | None = None
    buy_small: float | None = None
    buy_total: float | None = None

    timestamp: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("timestamp", "created_at", "updated_at", mode="before")
    @classmethod
    def _parse_times(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class CapitalFlowItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    timestamp: datetime | None = None
    amount: float | None = None
    type: str | None = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class CapitalFlowData(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str | None = None
    items: list[CapitalFlowItem] = Field(default_factory=list)


class CapitalHistoryItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    amount: float | None = None
    timestamp: datetime | None = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class CapitalHistoryData(BaseModel):
    model_config = ConfigDict(extra="allow")

    sum_3d: float | None = Field(default=None, validation_alias="sum3")
    sum_5d: float | None = Field(default=None, validation_alias="sum5")
    sum_10d: float | None = Field(default=None, validation_alias="sum10")
    sum_20d: float | None = Field(default=None, validation_alias="sum20")

    items: list[CapitalHistoryItem] = Field(default_factory=list)


class CapitalAPI:
    def __init__(self, client: SyncRequester) -> None:
        self._client = client

    def margin(self, symbol: str, *, page: int = 1, size: int = 180) -> XueqiuResponse[MarginData]:
        params: dict[str, Any] = {"symbol": symbol, "page": int(page), "size": int(size)}
        return self._client.request_model(
            "GET",
            CAPITAL_MARGIN_PATH,
            params=params,
            require_auth=True,
            model=XueqiuResponse[MarginData],
        )

    def blocktrans(
        self, symbol: str, *, page: int = 1, size: int = 30
    ) -> XueqiuResponse[BlocktransData]:
        params: dict[str, Any] = {"symbol": symbol, "page": int(page), "size": int(size)}
        return self._client.request_model(
            "GET",
            CAPITAL_BLOCKTRANS_PATH,
            params=params,
            require_auth=True,
            model=XueqiuResponse[BlocktransData],
        )

    def assort(self, symbol: str) -> XueqiuResponse[CapitalAssortData]:
        return self._client.request_model(
            "GET",
            CAPITAL_ASSORT_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[CapitalAssortData],
        )

    def flow(self, symbol: str) -> XueqiuResponse[CapitalFlowData]:
        return self._client.request_model(
            "GET",
            CAPITAL_FLOW_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[CapitalFlowData],
        )

    def history(self, symbol: str, *, count: int = 20) -> XueqiuResponse[CapitalHistoryData]:
        return self._client.request_model(
            "GET",
            CAPITAL_HISTORY_PATH,
            params={"symbol": symbol, "count": int(count)},
            require_auth=True,
            model=XueqiuResponse[CapitalHistoryData],
        )


class AsyncCapitalAPI:
    def __init__(self, client: AsyncRequester) -> None:
        self._client = client

    async def margin(
        self, symbol: str, *, page: int = 1, size: int = 180
    ) -> XueqiuResponse[MarginData]:
        params: dict[str, Any] = {"symbol": symbol, "page": int(page), "size": int(size)}
        return await self._client.request_model(
            "GET",
            CAPITAL_MARGIN_PATH,
            params=params,
            require_auth=True,
            model=XueqiuResponse[MarginData],
        )

    async def blocktrans(
        self, symbol: str, *, page: int = 1, size: int = 30
    ) -> XueqiuResponse[BlocktransData]:
        params: dict[str, Any] = {"symbol": symbol, "page": int(page), "size": int(size)}
        return await self._client.request_model(
            "GET",
            CAPITAL_BLOCKTRANS_PATH,
            params=params,
            require_auth=True,
            model=XueqiuResponse[BlocktransData],
        )

    async def assort(self, symbol: str) -> XueqiuResponse[CapitalAssortData]:
        return await self._client.request_model(
            "GET",
            CAPITAL_ASSORT_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[CapitalAssortData],
        )

    async def flow(self, symbol: str) -> XueqiuResponse[CapitalFlowData]:
        return await self._client.request_model(
            "GET",
            CAPITAL_FLOW_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[CapitalFlowData],
        )

    async def history(self, symbol: str, *, count: int = 20) -> XueqiuResponse[CapitalHistoryData]:
        return await self._client.request_model(
            "GET",
            CAPITAL_HISTORY_PATH,
            params={"symbol": symbol, "count": int(count)},
            require_auth=True,
            model=XueqiuResponse[CapitalHistoryData],
        )
