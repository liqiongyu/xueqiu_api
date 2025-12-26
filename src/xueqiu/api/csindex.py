from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from xueqiu.api._base import AsyncRequester, SyncRequester
from xueqiu.api.urls import (
    CSINDEX_INDEX_BASIC_INFO_URL,
    CSINDEX_INDEX_DETAILS_DATA_URL,
    CSINDEX_INDEX_PERF_URL,
    CSINDEX_INDEX_WEIGHT_TOP10_URL,
)


def _format_yyyymmdd(value: str | date | datetime) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime("%Y%m%d")


class CSIndexResponse(BaseModel):
    """Loose model for CSIndex responses.

    These endpoints are not part of Xueqiu; we keep parsing permissive and store the raw payload.
    """

    model_config = ConfigDict(extra="allow")

    data: Any | None = None


class CSIndexAPI:
    """China Securities Index (中证指数) endpoints (no auth)."""

    def __init__(self, client: SyncRequester) -> None:
        self._client = client

    def index_basic_info(self, index_code: str) -> CSIndexResponse:
        return self._client.request_model(
            "GET",
            f"{CSINDEX_INDEX_BASIC_INFO_URL}/{index_code}",
            require_auth=False,
            check_api_error=False,
            model=CSIndexResponse,
        )

    def index_details_data(self, index_code: str, *, file_lang: int = 1) -> CSIndexResponse:
        return self._client.request_model(
            "GET",
            CSINDEX_INDEX_DETAILS_DATA_URL,
            params={"fileLang": int(file_lang), "indexCode": index_code},
            require_auth=False,
            check_api_error=False,
            model=CSIndexResponse,
        )

    def index_weight_top10(self, index_code: str) -> CSIndexResponse:
        return self._client.request_model(
            "GET",
            f"{CSINDEX_INDEX_WEIGHT_TOP10_URL}/{index_code}",
            require_auth=False,
            check_api_error=False,
            model=CSIndexResponse,
        )

    def index_perf(
        self,
        index_code: str,
        *,
        start_date: str | date | datetime,
        end_date: str | date | datetime,
    ) -> CSIndexResponse:
        return self._client.request_model(
            "GET",
            CSINDEX_INDEX_PERF_URL,
            params={
                "indexCode": index_code,
                "startDate": _format_yyyymmdd(start_date),
                "endDate": _format_yyyymmdd(end_date),
            },
            require_auth=False,
            check_api_error=False,
            model=CSIndexResponse,
        )


class AsyncCSIndexAPI:
    """Async China Securities Index (中证指数) endpoints (no auth)."""

    def __init__(self, client: AsyncRequester) -> None:
        self._client = client

    async def index_basic_info(self, index_code: str) -> CSIndexResponse:
        return await self._client.request_model(
            "GET",
            f"{CSINDEX_INDEX_BASIC_INFO_URL}/{index_code}",
            require_auth=False,
            check_api_error=False,
            model=CSIndexResponse,
        )

    async def index_details_data(self, index_code: str, *, file_lang: int = 1) -> CSIndexResponse:
        return await self._client.request_model(
            "GET",
            CSINDEX_INDEX_DETAILS_DATA_URL,
            params={"fileLang": int(file_lang), "indexCode": index_code},
            require_auth=False,
            check_api_error=False,
            model=CSIndexResponse,
        )

    async def index_weight_top10(self, index_code: str) -> CSIndexResponse:
        return await self._client.request_model(
            "GET",
            f"{CSINDEX_INDEX_WEIGHT_TOP10_URL}/{index_code}",
            require_auth=False,
            check_api_error=False,
            model=CSIndexResponse,
        )

    async def index_perf(
        self,
        index_code: str,
        *,
        start_date: str | date | datetime,
        end_date: str | date | datetime,
    ) -> CSIndexResponse:
        return await self._client.request_model(
            "GET",
            CSINDEX_INDEX_PERF_URL,
            params={
                "indexCode": index_code,
                "startDate": _format_yyyymmdd(start_date),
                "endDate": _format_yyyymmdd(end_date),
            },
            require_auth=False,
            check_api_error=False,
            model=CSIndexResponse,
        )

