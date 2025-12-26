from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from xueqiu.api._base import AsyncRequester, SyncRequester
from xueqiu.api.urls import SUGGEST_STOCK_URL


class SuggestStockItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str | None = Field(default=None, validation_alias=AliasChoices("code", "symbol"))
    label: str | None = None
    query: str | None = None
    state: int | None = None
    stock_type: int | None = None
    type: int | None = None


class SuggestStockMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    count: int | None = None
    feedback: int | None = None
    has_next_page: bool | None = None
    max_page: int | None = Field(default=None, validation_alias=AliasChoices("maxPage", "max_page"))
    page: int | None = None
    query_id: int | None = None
    size: int | None = None


class SuggestStockResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: int | None = None
    message: str | None = None
    success: bool | None = None
    data: list[SuggestStockItem] = Field(default_factory=list)
    meta: SuggestStockMeta | None = None

    @field_validator("data", mode="before")
    @classmethod
    def _unwrap_data(cls, value: Any) -> Any:
        # Some variants return: {"data": {"items": [...]}}
        if isinstance(value, dict) and isinstance(value.get("items"), list):
            return value["items"]
        return value


class SuggestAPI:
    def __init__(self, client: SyncRequester) -> None:
        self._client = client

    def stock(self, keyword: str) -> SuggestStockResponse:
        return self._client.request_model(
            "GET",
            SUGGEST_STOCK_URL,
            params={"q": keyword},
            require_auth=True,
            model=SuggestStockResponse,
        )


class AsyncSuggestAPI:
    def __init__(self, client: AsyncRequester) -> None:
        self._client = client

    async def stock(self, keyword: str) -> SuggestStockResponse:
        return await self._client.request_model(
            "GET",
            SUGGEST_STOCK_URL,
            params={"q": keyword},
            require_auth=True,
            model=SuggestStockResponse,
        )
