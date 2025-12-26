from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from xueqiu.api._base import AsyncRequester, SyncRequester
from xueqiu.api.urls import PORTFOLIO_LIST_PATH, PORTFOLIO_STOCK_LIST_PATH
from xueqiu.models import XueqiuResponse
from xueqiu.parsing import parse_datetime


def _bool_str(value: bool) -> str:
    return "true" if value else "false"


class PortfolioListItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int | None = None
    name: str | None = None
    order_id: int | None = None
    category: int | None = None
    include: bool | None = None
    symbol_count: int | None = None
    type: int | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _parse_times(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class PortfolioListData(BaseModel):
    model_config = ConfigDict(extra="allow")

    cubes: list[PortfolioListItem] = Field(default_factory=list)
    funds: list[PortfolioListItem] = Field(default_factory=list)
    stocks: list[PortfolioListItem] = Field(default_factory=list)
    mutual_funds: list[PortfolioListItem] = Field(
        default_factory=list,
        validation_alias=AliasChoices("mutualFunds", "mutual_funds"),
    )


class PortfolioStockItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str | None = None
    name: str | None = None
    type: int | None = None
    remark: str | None = None
    exchange: str | None = None
    created: datetime | None = None

    @field_validator("created", mode="before")
    @classmethod
    def _parse_created(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class PortfolioStocksData(BaseModel):
    model_config = ConfigDict(extra="allow")

    pid: int | None = None
    category: int | None = None
    stocks: list[PortfolioStockItem] = Field(default_factory=list)


class PortfolioAPI:
    def __init__(self, client: SyncRequester) -> None:
        self._client = client

    def list(self, *, system: bool = True) -> XueqiuResponse[PortfolioListData]:
        return self._client.request_model(
            "GET",
            PORTFOLIO_LIST_PATH,
            params={"system": _bool_str(system)},
            require_auth=True,
            model=XueqiuResponse[PortfolioListData],
        )

    def stocks(
        self, pid: int, *, size: int = 1000, category: int = 1
    ) -> XueqiuResponse[PortfolioStocksData]:
        return self._client.request_model(
            "GET",
            PORTFOLIO_STOCK_LIST_PATH,
            params={"size": int(size), "category": int(category), "pid": int(pid)},
            require_auth=True,
            model=XueqiuResponse[PortfolioStocksData],
        )


class AsyncPortfolioAPI:
    def __init__(self, client: AsyncRequester) -> None:
        self._client = client

    async def list(self, *, system: bool = True) -> XueqiuResponse[PortfolioListData]:
        return await self._client.request_model(
            "GET",
            PORTFOLIO_LIST_PATH,
            params={"system": _bool_str(system)},
            require_auth=True,
            model=XueqiuResponse[PortfolioListData],
        )

    async def stocks(
        self, pid: int, *, size: int = 1000, category: int = 1
    ) -> XueqiuResponse[PortfolioStocksData]:
        return await self._client.request_model(
            "GET",
            PORTFOLIO_STOCK_LIST_PATH,
            params={"size": int(size), "category": int(category), "pid": int(pid)},
            require_auth=True,
            model=XueqiuResponse[PortfolioStocksData],
        )
