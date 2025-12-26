from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from xueqiu.api._base import AsyncRequester, SyncRequester
from xueqiu.api.urls import REPORT_EARNING_FORECAST_PATH, REPORT_LATEST_PATH
from xueqiu.models import XueqiuResponse
from xueqiu.parsing import parse_datetime


class EarningForecastItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    forecast_year: str | None = None
    eps: float | None = None
    pe: float | None = None
    pb: float | None = None
    roe: float | None = None


class EarningForecastData(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: list[EarningForecastItem] = Field(default_factory=list, alias="list")


class InstitutionRatingItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str | None = None
    rpt_comp: str | None = None
    rating_desc: str | None = None
    target_price_min: float | None = None
    target_price_max: float | None = None
    pub_date: datetime | None = None
    status_id: int | None = None
    retweet_count: int | None = None
    reply_count: int | None = None
    like_count: int | None = None
    liked: bool | None = None

    @field_validator("pub_date", mode="before")
    @classmethod
    def _parse_pub_date(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class InstitutionRatingData(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: list[InstitutionRatingItem] = Field(default_factory=list, alias="list")


class ReportAPI:
    def __init__(self, client: SyncRequester) -> None:
        self._client = client

    def latest(self, symbol: str) -> XueqiuResponse[InstitutionRatingData]:
        return self._client.request_model(
            "GET",
            REPORT_LATEST_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[InstitutionRatingData],
        )

    def earning_forecast(self, symbol: str) -> XueqiuResponse[EarningForecastData]:
        return self._client.request_model(
            "GET",
            REPORT_EARNING_FORECAST_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[EarningForecastData],
        )


class AsyncReportAPI:
    def __init__(self, client: AsyncRequester) -> None:
        self._client = client

    async def latest(self, symbol: str) -> XueqiuResponse[InstitutionRatingData]:
        return await self._client.request_model(
            "GET",
            REPORT_LATEST_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[InstitutionRatingData],
        )

    async def earning_forecast(self, symbol: str) -> XueqiuResponse[EarningForecastData]:
        return await self._client.request_model(
            "GET",
            REPORT_EARNING_FORECAST_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[EarningForecastData],
        )
