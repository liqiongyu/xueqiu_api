from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from xueqiu.api._base import AsyncRequester, SyncRequester
from xueqiu.api.urls import (
    FINANCE_BALANCE_PATH,
    FINANCE_BUSINESS_PATH,
    FINANCE_CASH_FLOW_PATH,
    FINANCE_INCOME_PATH,
    FINANCE_INDICATOR_PATH,
)
from xueqiu.models import XueqiuResponse
from xueqiu.parsing import parse_datetime


def _is_number_like(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return False
        try:
            float(s)
        except ValueError:
            return False
        return True
    return False


class MetricValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float | None = None
    yoy: float | None = None


def _parse_metric_value(raw: Any) -> MetricValue | None:
    # Common pattern: "some_metric": [value, yoy]
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    if not (_is_number_like(raw[0]) and _is_number_like(raw[1])):
        return None
    return MetricValue(value=raw[0], yoy=raw[1])


class FinanceMetricPeriod(BaseModel):
    """One reporting period with a dynamic set of metrics."""

    model_config = ConfigDict(extra="allow")

    report_date: datetime | None = None
    report_name: str | None = None
    metrics: dict[str, MetricValue] = Field(default_factory=dict)

    @field_validator("report_date", mode="before")
    @classmethod
    def _parse_report_date(cls, value: Any) -> datetime | None:
        return parse_datetime(value)

    @model_validator(mode="before")
    @classmethod
    def _extract_metrics(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        raw = dict(data)
        metrics: dict[str, MetricValue] = {}

        for key, value in list(raw.items()):
            if key in {"report_date", "report_name"}:
                continue
            metric = _parse_metric_value(value)
            if metric is None:
                continue
            metrics[key] = metric
            raw.pop(key, None)

        raw["metrics"] = metrics
        return raw


class FinanceMetricStatementData(BaseModel):
    """Common shape for indicator/balance/income/cash_flow endpoints."""

    model_config = ConfigDict(extra="allow")

    quote_name: str | None = None
    currency_name: str | None = None
    org_type: int | None = None
    last_report_name: str | None = None
    currency: str | None = None

    periods: list[FinanceMetricPeriod] = Field(
        default_factory=list, validation_alias=AliasChoices("list", "items")
    )


class BusinessItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    project_announced_name: str | None = None
    prime_operating_income: float | None = None
    income_ratio: float | None = None
    gross_profit_rate: float | None = None


class BusinessClass(BaseModel):
    model_config = ConfigDict(extra="allow")

    class_standard: int | None = None
    business_list: list[BusinessItem] = Field(default_factory=list)


class BusinessPeriod(BaseModel):
    model_config = ConfigDict(extra="allow")

    report_date: datetime | None = None
    report_name: str | None = None
    class_list: list[BusinessClass] = Field(default_factory=list)

    @field_validator("report_date", mode="before")
    @classmethod
    def _parse_report_date(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class BusinessStatementData(BaseModel):
    model_config = ConfigDict(extra="allow")

    quote_name: str | None = None
    currency: str | None = None
    periods: list[BusinessPeriod] = Field(default_factory=list, validation_alias="list")


def _bool_str(value: bool) -> str:
    return "true" if value else "false"


def _finance_path_v2(*, region: str, endpoint: str) -> str:
    region = region.strip().lower()
    return f"/v5/stock/finance/{region}/{endpoint}.json"


class FinanceAPI:
    def __init__(self, client: SyncRequester) -> None:
        self._client = client

    def cash_flow(
        self, symbol: str, *, is_annals: bool = False, count: int = 10
    ) -> XueqiuResponse[FinanceMetricStatementData]:
        params: dict[str, Any] = {"symbol": symbol, "count": int(count)}
        if is_annals:
            params["type"] = "Q4"
        return self._client.request_model(
            "GET",
            FINANCE_CASH_FLOW_PATH,
            params=params,
            require_auth=True,
            model=XueqiuResponse[FinanceMetricStatementData],
        )

    def cash_flow_v2(
        self,
        symbol: str,
        *,
        count: int = 10,
        region: str = "cn",
        type: str = "all",
        is_detail: bool = True,
    ) -> XueqiuResponse[FinanceMetricStatementData]:
        params = {
            "symbol": symbol,
            "type": type,
            "is_detail": _bool_str(is_detail),
            "count": int(count),
        }
        return self._client.request_model(
            "GET",
            _finance_path_v2(region=region, endpoint="cash_flow"),
            params=params,
            require_auth=True,
            model=XueqiuResponse[FinanceMetricStatementData],
        )

    def indicator(
        self, symbol: str, *, is_annals: bool = False, count: int = 10
    ) -> XueqiuResponse[FinanceMetricStatementData]:
        params: dict[str, Any] = {"symbol": symbol, "count": int(count)}
        if is_annals:
            params["type"] = "Q4"
        return self._client.request_model(
            "GET",
            FINANCE_INDICATOR_PATH,
            params=params,
            require_auth=True,
            model=XueqiuResponse[FinanceMetricStatementData],
        )

    def indicator_v2(
        self,
        symbol: str,
        *,
        count: int = 10,
        region: str = "cn",
        type: str = "all",
        is_detail: bool = True,
    ) -> XueqiuResponse[FinanceMetricStatementData]:
        params = {
            "symbol": symbol,
            "type": type,
            "is_detail": _bool_str(is_detail),
            "count": int(count),
        }
        return self._client.request_model(
            "GET",
            _finance_path_v2(region=region, endpoint="indicator"),
            params=params,
            require_auth=True,
            model=XueqiuResponse[FinanceMetricStatementData],
        )

    def balance(
        self, symbol: str, *, is_annals: bool = False, count: int = 10
    ) -> XueqiuResponse[FinanceMetricStatementData]:
        params: dict[str, Any] = {"symbol": symbol, "count": int(count)}
        if is_annals:
            params["type"] = "Q4"
        return self._client.request_model(
            "GET",
            FINANCE_BALANCE_PATH,
            params=params,
            require_auth=True,
            model=XueqiuResponse[FinanceMetricStatementData],
        )

    def balance_v2(
        self,
        symbol: str,
        *,
        count: int = 10,
        region: str = "cn",
        type: str = "all",
        is_detail: bool = True,
    ) -> XueqiuResponse[FinanceMetricStatementData]:
        params = {
            "symbol": symbol,
            "type": type,
            "is_detail": _bool_str(is_detail),
            "count": int(count),
        }
        return self._client.request_model(
            "GET",
            _finance_path_v2(region=region, endpoint="balance"),
            params=params,
            require_auth=True,
            model=XueqiuResponse[FinanceMetricStatementData],
        )

    def income(
        self, symbol: str, *, is_annals: bool = False, count: int = 10
    ) -> XueqiuResponse[FinanceMetricStatementData]:
        params: dict[str, Any] = {"symbol": symbol, "count": int(count)}
        if is_annals:
            params["type"] = "Q4"
        return self._client.request_model(
            "GET",
            FINANCE_INCOME_PATH,
            params=params,
            require_auth=True,
            model=XueqiuResponse[FinanceMetricStatementData],
        )

    def income_v2(
        self,
        symbol: str,
        *,
        count: int = 10,
        region: str = "cn",
        type: str = "all",
        is_detail: bool = True,
    ) -> XueqiuResponse[FinanceMetricStatementData]:
        params = {
            "symbol": symbol,
            "type": type,
            "is_detail": _bool_str(is_detail),
            "count": int(count),
        }
        return self._client.request_model(
            "GET",
            _finance_path_v2(region=region, endpoint="income"),
            params=params,
            require_auth=True,
            model=XueqiuResponse[FinanceMetricStatementData],
        )

    def business(
        self, symbol: str, *, is_annals: bool = False, count: int = 10
    ) -> XueqiuResponse[BusinessStatementData]:
        params: dict[str, Any] = {"symbol": symbol, "count": int(count)}
        if is_annals:
            params["type"] = "Q4"
        return self._client.request_model(
            "GET",
            FINANCE_BUSINESS_PATH,
            params=params,
            require_auth=True,
            model=XueqiuResponse[BusinessStatementData],
        )


class AsyncFinanceAPI:
    def __init__(self, client: AsyncRequester) -> None:
        self._client = client

    async def cash_flow(
        self, symbol: str, *, is_annals: bool = False, count: int = 10
    ) -> XueqiuResponse[FinanceMetricStatementData]:
        params: dict[str, Any] = {"symbol": symbol, "count": int(count)}
        if is_annals:
            params["type"] = "Q4"
        return await self._client.request_model(
            "GET",
            FINANCE_CASH_FLOW_PATH,
            params=params,
            require_auth=True,
            model=XueqiuResponse[FinanceMetricStatementData],
        )

    async def cash_flow_v2(
        self,
        symbol: str,
        *,
        count: int = 10,
        region: str = "cn",
        type: str = "all",
        is_detail: bool = True,
    ) -> XueqiuResponse[FinanceMetricStatementData]:
        params = {
            "symbol": symbol,
            "type": type,
            "is_detail": _bool_str(is_detail),
            "count": int(count),
        }
        return await self._client.request_model(
            "GET",
            _finance_path_v2(region=region, endpoint="cash_flow"),
            params=params,
            require_auth=True,
            model=XueqiuResponse[FinanceMetricStatementData],
        )

    async def indicator(
        self, symbol: str, *, is_annals: bool = False, count: int = 10
    ) -> XueqiuResponse[FinanceMetricStatementData]:
        params: dict[str, Any] = {"symbol": symbol, "count": int(count)}
        if is_annals:
            params["type"] = "Q4"
        return await self._client.request_model(
            "GET",
            FINANCE_INDICATOR_PATH,
            params=params,
            require_auth=True,
            model=XueqiuResponse[FinanceMetricStatementData],
        )

    async def indicator_v2(
        self,
        symbol: str,
        *,
        count: int = 10,
        region: str = "cn",
        type: str = "all",
        is_detail: bool = True,
    ) -> XueqiuResponse[FinanceMetricStatementData]:
        params = {
            "symbol": symbol,
            "type": type,
            "is_detail": _bool_str(is_detail),
            "count": int(count),
        }
        return await self._client.request_model(
            "GET",
            _finance_path_v2(region=region, endpoint="indicator"),
            params=params,
            require_auth=True,
            model=XueqiuResponse[FinanceMetricStatementData],
        )

    async def balance(
        self, symbol: str, *, is_annals: bool = False, count: int = 10
    ) -> XueqiuResponse[FinanceMetricStatementData]:
        params: dict[str, Any] = {"symbol": symbol, "count": int(count)}
        if is_annals:
            params["type"] = "Q4"
        return await self._client.request_model(
            "GET",
            FINANCE_BALANCE_PATH,
            params=params,
            require_auth=True,
            model=XueqiuResponse[FinanceMetricStatementData],
        )

    async def balance_v2(
        self,
        symbol: str,
        *,
        count: int = 10,
        region: str = "cn",
        type: str = "all",
        is_detail: bool = True,
    ) -> XueqiuResponse[FinanceMetricStatementData]:
        params = {
            "symbol": symbol,
            "type": type,
            "is_detail": _bool_str(is_detail),
            "count": int(count),
        }
        return await self._client.request_model(
            "GET",
            _finance_path_v2(region=region, endpoint="balance"),
            params=params,
            require_auth=True,
            model=XueqiuResponse[FinanceMetricStatementData],
        )

    async def income(
        self, symbol: str, *, is_annals: bool = False, count: int = 10
    ) -> XueqiuResponse[FinanceMetricStatementData]:
        params: dict[str, Any] = {"symbol": symbol, "count": int(count)}
        if is_annals:
            params["type"] = "Q4"
        return await self._client.request_model(
            "GET",
            FINANCE_INCOME_PATH,
            params=params,
            require_auth=True,
            model=XueqiuResponse[FinanceMetricStatementData],
        )

    async def income_v2(
        self,
        symbol: str,
        *,
        count: int = 10,
        region: str = "cn",
        type: str = "all",
        is_detail: bool = True,
    ) -> XueqiuResponse[FinanceMetricStatementData]:
        params = {
            "symbol": symbol,
            "type": type,
            "is_detail": _bool_str(is_detail),
            "count": int(count),
        }
        return await self._client.request_model(
            "GET",
            _finance_path_v2(region=region, endpoint="income"),
            params=params,
            require_auth=True,
            model=XueqiuResponse[FinanceMetricStatementData],
        )

    async def business(
        self, symbol: str, *, is_annals: bool = False, count: int = 10
    ) -> XueqiuResponse[BusinessStatementData]:
        params: dict[str, Any] = {"symbol": symbol, "count": int(count)}
        if is_annals:
            params["type"] = "Q4"
        return await self._client.request_model(
            "GET",
            FINANCE_BUSINESS_PATH,
            params=params,
            require_auth=True,
            model=XueqiuResponse[BusinessStatementData],
        )
