from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from xueqiu.api._base import AsyncRequester, SyncRequester
from xueqiu.api.urls import (
    CUBE_NAV_DAILY_URL,
    CUBE_QUOTE_URL,
    CUBE_REBALANCING_CURRENT_URL,
    CUBE_REBALANCING_HISTORY_URL,
)
from xueqiu.models import XueqiuResponse
from xueqiu.parsing import parse_datetime


class CubeNavPoint(BaseModel):
    model_config = ConfigDict(extra="allow")

    time: datetime | None = None
    date: str | None = None
    value: float | None = None
    percent: float | None = None

    @field_validator("time", mode="before")
    @classmethod
    def _parse_time(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class CubeNavSeries(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str | None = None
    name: str | None = None
    items: list[CubeNavPoint] = Field(default_factory=list, alias="list")


class CubeRebalancingHistoryItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int | None = None
    rebalancing_id: int | None = None
    stock_id: int | None = None
    stock_name: str | None = None
    stock_symbol: str | None = None

    volume: float | None = None
    price: float | None = None
    net_value: float | None = None
    weight: float | None = None
    target_weight: float | None = None
    prev_weight: float | None = None
    proactive: bool | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _parse_times(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class CubeHolding(BaseModel):
    model_config = ConfigDict(extra="allow")

    stock_id: int | None = None
    weight: float | None = None
    segment_name: str | None = None
    segment_id: int | None = None
    stock_name: str | None = None
    stock_symbol: str | None = None
    segment_color: str | None = None
    proactive: bool | None = None
    volume: float | None = None


class CubeRebalancing(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int | None = None
    status: str | None = None
    cube_id: int | None = None
    prev_rebalancing_id: int | None = Field(
        default=None, validation_alias=AliasChoices("prev_bebalancing_id", "prev_rebalancing_id")
    )
    category: str | None = None
    exe_strategy: str | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    cash: float | None = None
    cash_value: float | None = None
    error_code: int | None = None
    error_message: str | None = None
    error_status: str | None = None

    holdings: list[CubeHolding] | None = None
    rebalancing_histories: list[CubeRebalancingHistoryItem] = Field(default_factory=list)

    comment: str | None = None
    diff: float | None = None
    new_buy_count: int | None = None

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _parse_times(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class CubeRebalancingHistoryData(BaseModel):
    model_config = ConfigDict(extra="allow")

    count: int | None = None
    page: int | None = None
    total_count: int | None = Field(
        default=None, validation_alias=AliasChoices("totalCount", "total_count")
    )
    items: list[CubeRebalancing] = Field(default_factory=list, alias="list")
    max_page: int | None = Field(default=None, validation_alias=AliasChoices("maxPage", "max_page"))


class CubeRebalancingCurrentData(BaseModel):
    model_config = ConfigDict(extra="allow")

    last_rb: CubeRebalancing | None = None


class CubeQuote(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str | None = None
    market: str | None = None
    name: str | None = None

    net_value: float | None = None
    daily_gain: float | None = None
    monthly_gain: float | None = None
    total_gain: float | None = None
    annualized_gain: float | None = None

    hasexist: bool | None = None
    badges_exist: bool | None = None
    game_id: int | None = None

    closed_at: datetime | None = None

    @field_validator("closed_at", mode="before")
    @classmethod
    def _parse_closed_at(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class CubeAPI:
    def __init__(self, client: SyncRequester) -> None:
        self._client = client

    def nav_daily(self, cube_symbol: str) -> XueqiuResponse[list[CubeNavSeries]]:
        return self._client.request_model(
            "GET",
            CUBE_NAV_DAILY_URL,
            params={"cube_symbol": cube_symbol},
            require_auth=True,
            model=XueqiuResponse[list[CubeNavSeries]],
        )

    def rebalancing_history(
        self, cube_symbol: str, *, count: int = 20, page: int = 1
    ) -> XueqiuResponse[CubeRebalancingHistoryData]:
        params: dict[str, Any] = {
            "cube_symbol": cube_symbol,
            "count": int(count),
            "page": int(page),
        }
        return self._client.request_model(
            "GET",
            CUBE_REBALANCING_HISTORY_URL,
            params=params,
            require_auth=True,
            model=XueqiuResponse[CubeRebalancingHistoryData],
        )

    def rebalancing_current(self, cube_symbol: str) -> XueqiuResponse[CubeRebalancingCurrentData]:
        return self._client.request_model(
            "GET",
            CUBE_REBALANCING_CURRENT_URL,
            params={"cube_symbol": cube_symbol},
            require_auth=True,
            model=XueqiuResponse[CubeRebalancingCurrentData],
        )

    def quote(self, code: str) -> XueqiuResponse[dict[str, CubeQuote]]:
        return self._client.request_model(
            "GET",
            CUBE_QUOTE_URL,
            params={"code": code},
            require_auth=True,
            model=XueqiuResponse[dict[str, CubeQuote]],
        )


class AsyncCubeAPI:
    def __init__(self, client: AsyncRequester) -> None:
        self._client = client

    async def nav_daily(self, cube_symbol: str) -> XueqiuResponse[list[CubeNavSeries]]:
        return await self._client.request_model(
            "GET",
            CUBE_NAV_DAILY_URL,
            params={"cube_symbol": cube_symbol},
            require_auth=True,
            model=XueqiuResponse[list[CubeNavSeries]],
        )

    async def rebalancing_history(
        self, cube_symbol: str, *, count: int = 20, page: int = 1
    ) -> XueqiuResponse[CubeRebalancingHistoryData]:
        params: dict[str, Any] = {
            "cube_symbol": cube_symbol,
            "count": int(count),
            "page": int(page),
        }
        return await self._client.request_model(
            "GET",
            CUBE_REBALANCING_HISTORY_URL,
            params=params,
            require_auth=True,
            model=XueqiuResponse[CubeRebalancingHistoryData],
        )

    async def rebalancing_current(
        self, cube_symbol: str
    ) -> XueqiuResponse[CubeRebalancingCurrentData]:
        return await self._client.request_model(
            "GET",
            CUBE_REBALANCING_CURRENT_URL,
            params={"cube_symbol": cube_symbol},
            require_auth=True,
            model=XueqiuResponse[CubeRebalancingCurrentData],
        )

    async def quote(self, code: str) -> XueqiuResponse[dict[str, CubeQuote]]:
        return await self._client.request_model(
            "GET",
            CUBE_QUOTE_URL,
            params={"code": code},
            require_auth=True,
            model=XueqiuResponse[dict[str, CubeQuote]],
        )
