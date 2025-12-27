from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from xueqiu.api._base import AsyncRequester, SyncRequester
from xueqiu.api.urls import EASTMONEY_CONVERTIBLE_BOND_QUOTE_COLUMNS, EASTMONEY_DATACENTER_URL


class EastmoneyResponse(BaseModel):
    """Loose model for Eastmoney datacenter responses.

    These endpoints are not part of Xueqiu; keep parsing permissive and preserve raw payload.
    """

    model_config = ConfigDict(extra="allow")

    result: Any | None = None
    success: bool | None = None
    message: str | None = None


class EastmoneyAPI:
    """Eastmoney datacenter endpoints (no Xueqiu auth)."""

    def __init__(self, client: SyncRequester) -> None:
        self._client = client

    def convertible_bond(self, page_size: int, page_number: int) -> EastmoneyResponse:
        return self._client.request_model(
            "GET",
            EASTMONEY_DATACENTER_URL,
            params={
                "pageSize": int(page_size),
                "pageNumber": int(page_number),
                "sortColumns": "PUBLIC_START_DATE",
                "sortTypes": -1,
                "reportName": "RPT_BOND_CB_LIST",
                "columns": "ALL",
                "quoteColumns": EASTMONEY_CONVERTIBLE_BOND_QUOTE_COLUMNS,
                "source": "WEB",
                "client": "WEB",
            },
            require_auth=False,
            check_api_error=False,
            model=EastmoneyResponse,
        )


class AsyncEastmoneyAPI:
    """Async Eastmoney datacenter endpoints (no Xueqiu auth)."""

    def __init__(self, client: AsyncRequester) -> None:
        self._client = client

    async def convertible_bond(self, page_size: int, page_number: int) -> EastmoneyResponse:
        return await self._client.request_model(
            "GET",
            EASTMONEY_DATACENTER_URL,
            params={
                "pageSize": int(page_size),
                "pageNumber": int(page_number),
                "sortColumns": "PUBLIC_START_DATE",
                "sortTypes": -1,
                "reportName": "RPT_BOND_CB_LIST",
                "columns": "ALL",
                "quoteColumns": EASTMONEY_CONVERTIBLE_BOND_QUOTE_COLUMNS,
                "source": "WEB",
                "client": "WEB",
            },
            require_auth=False,
            check_api_error=False,
            model=EastmoneyResponse,
        )
