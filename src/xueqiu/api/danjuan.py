from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from xueqiu.api._base import AsyncRequester, SyncRequester
from xueqiu.api.urls import (
    DANJUAN_FUND_ACHIEVEMENT_URL,
    DANJUAN_FUND_ASSET_URL,
    DANJUAN_FUND_DERIVED_URL,
    DANJUAN_FUND_DETAIL_URL,
    DANJUAN_FUND_GROWTH_URL,
    DANJUAN_FUND_INFO_URL,
    DANJUAN_FUND_MANAGER_URL,
    DANJUAN_FUND_NAV_HISTORY_URL,
    DANJUAN_FUND_TRADE_DATE_URL,
)


class DanjuanResponse(BaseModel):
    """Loose model for Danjuan (蛋卷基金) responses.

    These endpoints are not part of Xueqiu; keep parsing permissive and preserve raw payload.
    """

    model_config = ConfigDict(extra="allow")

    data: Any | None = None
    code: int | None = None
    message: str | None = None


class DanjuanAPI:
    """Danjuan (蛋卷基金) endpoints (no Xueqiu auth)."""

    def __init__(self, client: SyncRequester) -> None:
        self._client = client

    def fund_detail(self, fund_code: str) -> DanjuanResponse:
        return self._client.request_model(
            "GET",
            f"{DANJUAN_FUND_DETAIL_URL}/{fund_code}",
            require_auth=False,
            check_api_error=False,
            model=DanjuanResponse,
        )

    def fund_info(self, fund_code: str) -> DanjuanResponse:
        return self._client.request_model(
            "GET",
            f"{DANJUAN_FUND_INFO_URL}/{fund_code}",
            require_auth=False,
            check_api_error=False,
            model=DanjuanResponse,
        )

    def fund_growth(self, fund_code: str, *, day: str = "ty") -> DanjuanResponse:
        return self._client.request_model(
            "GET",
            f"{DANJUAN_FUND_GROWTH_URL}/{fund_code}",
            params={"day": day},
            require_auth=False,
            check_api_error=False,
            model=DanjuanResponse,
        )

    def fund_nav_history(self, fund_code: str, *, page: int = 1, size: int = 10) -> DanjuanResponse:
        return self._client.request_model(
            "GET",
            f"{DANJUAN_FUND_NAV_HISTORY_URL}/{fund_code}",
            params={"page": int(page), "size": int(size)},
            require_auth=False,
            check_api_error=False,
            model=DanjuanResponse,
        )

    def fund_achievement(self, fund_code: str) -> DanjuanResponse:
        return self._client.request_model(
            "GET",
            f"{DANJUAN_FUND_ACHIEVEMENT_URL}/{fund_code}",
            require_auth=False,
            check_api_error=False,
            model=DanjuanResponse,
        )

    def fund_asset(self, fund_code: str) -> DanjuanResponse:
        return self._client.request_model(
            "GET",
            DANJUAN_FUND_ASSET_URL,
            params={"fund_code": fund_code},
            require_auth=False,
            check_api_error=False,
            model=DanjuanResponse,
        )

    def fund_manager(self, fund_code: str, *, post_status: int = 1) -> DanjuanResponse:
        return self._client.request_model(
            "GET",
            DANJUAN_FUND_MANAGER_URL,
            params={"fund_code": fund_code, "post_status": int(post_status)},
            require_auth=False,
            check_api_error=False,
            model=DanjuanResponse,
        )

    def fund_trade_date(self, fund_code: str) -> DanjuanResponse:
        return self._client.request_model(
            "GET",
            DANJUAN_FUND_TRADE_DATE_URL,
            params={"fd_code": fund_code},
            require_auth=False,
            check_api_error=False,
            model=DanjuanResponse,
        )

    def fund_derived(self, fund_code: str) -> DanjuanResponse:
        return self._client.request_model(
            "GET",
            f"{DANJUAN_FUND_DERIVED_URL}/{fund_code}",
            require_auth=False,
            check_api_error=False,
            model=DanjuanResponse,
        )


class AsyncDanjuanAPI:
    """Async Danjuan (蛋卷基金) endpoints (no Xueqiu auth)."""

    def __init__(self, client: AsyncRequester) -> None:
        self._client = client

    async def fund_detail(self, fund_code: str) -> DanjuanResponse:
        return await self._client.request_model(
            "GET",
            f"{DANJUAN_FUND_DETAIL_URL}/{fund_code}",
            require_auth=False,
            check_api_error=False,
            model=DanjuanResponse,
        )

    async def fund_info(self, fund_code: str) -> DanjuanResponse:
        return await self._client.request_model(
            "GET",
            f"{DANJUAN_FUND_INFO_URL}/{fund_code}",
            require_auth=False,
            check_api_error=False,
            model=DanjuanResponse,
        )

    async def fund_growth(self, fund_code: str, *, day: str = "ty") -> DanjuanResponse:
        return await self._client.request_model(
            "GET",
            f"{DANJUAN_FUND_GROWTH_URL}/{fund_code}",
            params={"day": day},
            require_auth=False,
            check_api_error=False,
            model=DanjuanResponse,
        )

    async def fund_nav_history(
        self, fund_code: str, *, page: int = 1, size: int = 10
    ) -> DanjuanResponse:
        return await self._client.request_model(
            "GET",
            f"{DANJUAN_FUND_NAV_HISTORY_URL}/{fund_code}",
            params={"page": int(page), "size": int(size)},
            require_auth=False,
            check_api_error=False,
            model=DanjuanResponse,
        )

    async def fund_achievement(self, fund_code: str) -> DanjuanResponse:
        return await self._client.request_model(
            "GET",
            f"{DANJUAN_FUND_ACHIEVEMENT_URL}/{fund_code}",
            require_auth=False,
            check_api_error=False,
            model=DanjuanResponse,
        )

    async def fund_asset(self, fund_code: str) -> DanjuanResponse:
        return await self._client.request_model(
            "GET",
            DANJUAN_FUND_ASSET_URL,
            params={"fund_code": fund_code},
            require_auth=False,
            check_api_error=False,
            model=DanjuanResponse,
        )

    async def fund_manager(self, fund_code: str, *, post_status: int = 1) -> DanjuanResponse:
        return await self._client.request_model(
            "GET",
            DANJUAN_FUND_MANAGER_URL,
            params={"fund_code": fund_code, "post_status": int(post_status)},
            require_auth=False,
            check_api_error=False,
            model=DanjuanResponse,
        )

    async def fund_trade_date(self, fund_code: str) -> DanjuanResponse:
        return await self._client.request_model(
            "GET",
            DANJUAN_FUND_TRADE_DATE_URL,
            params={"fd_code": fund_code},
            require_auth=False,
            check_api_error=False,
            model=DanjuanResponse,
        )

    async def fund_derived(self, fund_code: str) -> DanjuanResponse:
        return await self._client.request_model(
            "GET",
            f"{DANJUAN_FUND_DERIVED_URL}/{fund_code}",
            require_auth=False,
            check_api_error=False,
            model=DanjuanResponse,
        )
