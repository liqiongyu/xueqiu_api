from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from xueqiu.api._base import AsyncRequester, SyncRequester
from xueqiu.api.urls import (
    F10_BONUS_PATH,
    F10_BUSINESS_ANALYSIS_PATH,
    F10_HOLDERS_PATH,
    F10_INDICATOR_PATH,
    F10_INDUSTRY_COMPARE_PATH,
    F10_INDUSTRY_PATH,
    F10_ORG_HOLDING_CHANGE_PATH,
    F10_SHARESCHG_PATH,
    F10_SKHOLDER_PATH,
    F10_SKHOLDERCHG_PATH,
    F10_TOP_HOLDERS_PATH,
)
from xueqiu.models import XueqiuResponse
from xueqiu.parsing import parse_datetime


class F10TimePoint(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    value: datetime | None = None

    @field_validator("value", mode="before")
    @classmethod
    def _parse_value(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class F10TopHolderItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    change: float | None = Field(default=None, validation_alias=AliasChoices("chg", "change"))
    held_shares: float | None = Field(
        default=None, validation_alias=AliasChoices("held_num", "held_shares")
    )
    held_ratio: float | None = None
    shareholder_name: str | None = Field(
        default=None, validation_alias=AliasChoices("holder_name", "shareholder_name")
    )

    @property
    def chg(self) -> float | None:
        return self.change

    @property
    def held_num(self) -> float | None:
        return self.held_shares

    @property
    def holder_name(self) -> str | None:
        return self.shareholder_name


class F10TopHoldersData(BaseModel):
    model_config = ConfigDict(extra="allow")

    times: list[F10TimePoint] = Field(default_factory=list)
    items: list[F10TopHolderItem] = Field(default_factory=list)


class F10MainIndicatorItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    asset_liab_ratio: float | None = None
    net_profit_atsopc_yoy: float | None = None
    operating_income_yoy: float | None = None
    basic_eps: float | None = None
    net_selling_rate: float | None = None
    avg_roe: float | None = None
    gross_selling_rate: float | None = None
    float_shares: float | None = None
    pb: float | None = None
    np_per_share: float | None = None
    float_market_capital: float | None = None
    total_revenue: float | None = None
    market_capital: float | None = None
    pe_ttm: float | None = None
    dividend: float | None = None
    currency: str | None = None
    dividend_yield: float | None = None
    net_profit_atsopc: float | None = None
    total_shares: float | None = None
    report_name: str | None = Field(
        default=None, validation_alias=AliasChoices("report_date", "report_name")
    )

    @property
    def report_date(self) -> str | None:
        return self.report_name


class F10MainIndicatorData(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: list[F10MainIndicatorItem] = Field(default_factory=list)


class F10ShareholderCountItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    change: float | None = Field(default=None, validation_alias=AliasChoices("chg", "change"))
    price: float | None = None
    a_share_holders: int | None = Field(
        default=None, validation_alias=AliasChoices("ashare_holder", "a_share_holders")
    )
    timestamp: datetime | None = None

    @property
    def chg(self) -> float | None:
        return self.change

    @property
    def ashare_holder(self) -> int | None:
        return self.a_share_holders

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class F10ShareholderCountData(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: list[F10ShareholderCountItem] = Field(default_factory=list)


class F10OrgHoldingChangeItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    report_name: str | None = Field(
        default=None, validation_alias=AliasChoices("chg_date", "report_name")
    )
    institution_count: str | None = Field(
        default=None, validation_alias=AliasChoices("institution_num", "institution_count")
    )
    change: float | None = Field(default=None, validation_alias=AliasChoices("chg", "change"))
    held_ratio: float | None = None
    price: float | None = None
    timestamp: datetime | None = None

    @property
    def chg_date(self) -> str | None:
        return self.report_name

    @property
    def institution_num(self) -> str | None:
        return self.institution_count

    @property
    def chg(self) -> float | None:
        return self.change

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class F10OrgHoldingChangeData(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: list[F10OrgHoldingChangeItem] = Field(default_factory=list)


class F10BonusAddition(BaseModel):
    model_config = ConfigDict(extra="allow")

    actual_issue_vol: float | None = None
    actual_issue_price: float | None = None
    listing_at: datetime | None = Field(
        default=None, validation_alias=AliasChoices("listing_ad", "listing_at")
    )
    actual_raised_net_amount: float | None = Field(
        default=None, validation_alias=AliasChoices("actual_rc_net_amt", "actual_raised_net_amount")
    )

    @field_validator("listing_at", mode="before")
    @classmethod
    def _parse_listing(cls, value: Any) -> datetime | None:
        return parse_datetime(value)

    @property
    def listing_ad(self) -> datetime | None:
        return self.listing_at

    @property
    def actual_rc_net_amt(self) -> float | None:
        return self.actual_raised_net_amount


class F10BonusDividendItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    dividend_year: str | None = None
    ashare_ex_dividend_date: datetime | None = None
    plan_explain: str | None = None
    cancel_dividend_date: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("cancel_dividend_date", "cancle_dividend_date"),
    )

    @field_validator("ashare_ex_dividend_date", "cancel_dividend_date", mode="before")
    @classmethod
    def _parse_dates(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class F10BonusData(BaseModel):
    model_config = ConfigDict(extra="allow")

    additions: list[F10BonusAddition] = Field(
        default_factory=list,
        validation_alias=AliasChoices("additions", "addtions"),
    )
    allots: list[dict[str, Any]] = Field(default_factory=list)
    items: list[F10BonusDividendItem] = Field(default_factory=list)


class F10IndustryCompareStats(BaseModel):
    model_config = ConfigDict(extra="allow")

    pe_ttm: float | None = None
    basic_eps: float | None = None
    avg_roe: float | None = None
    gross_selling_rate: float | None = None
    total_revenue: float | None = None
    net_profit_atsopc: float | None = None
    np_per_share: float | None = None
    operate_cash_flow_ps: float | None = None
    total_assets: float | None = None
    total_shares: float | None = None


class F10IndustryCompareItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str | None = None
    name: str | None = None

    basic_eps: float | None = None
    total_revenue: float | None = None
    gross_selling_rate: float | None = None
    net_profit_atsopc: float | None = None
    np_per_share: float | None = None
    avg_roe: float | None = None
    pe_ttm: float | None = None
    total_assets: float | None = None
    operate_cash_flow_ps: float | None = None
    total_shares: float | None = None


class F10IndustryCompareData(BaseModel):
    model_config = ConfigDict(extra="allow")

    industry_name: str | None = Field(
        default=None, validation_alias=AliasChoices("ind_name", "industry_name")
    )
    quote_at: datetime | None = Field(
        default=None, validation_alias=AliasChoices("quote_time", "quote_at")
    )
    avg: F10IndustryCompareStats | None = None
    min: F10IndustryCompareStats | None = None
    max: F10IndustryCompareStats | None = None
    count: int | None = None
    industry_code: str | None = Field(
        default=None, validation_alias=AliasChoices("ind_code", "industry_code")
    )
    industry_class: str | None = Field(
        default=None, validation_alias=AliasChoices("ind_class", "industry_class")
    )
    report_name: str | None = None
    items: list[F10IndustryCompareItem] = Field(default_factory=list)

    @field_validator("quote_at", mode="before")
    @classmethod
    def _parse_quote_time(cls, value: Any) -> datetime | None:
        return parse_datetime(value)

    @property
    def ind_name(self) -> str | None:
        return self.industry_name

    @property
    def quote_time(self) -> datetime | None:
        return self.quote_at

    @property
    def ind_code(self) -> str | None:
        return self.industry_code

    @property
    def ind_class(self) -> str | None:
        return self.industry_class


class F10GenericItems(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: list[dict[str, Any]] = Field(
        default_factory=list, validation_alias=AliasChoices("items", "list")
    )


class F10IndustryTag(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str | None = Field(default=None, validation_alias=AliasChoices("ind_code", "code"))
    name: str | None = Field(default=None, validation_alias=AliasChoices("ind_name", "name"))


class F10IndustryCompanyInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    classification_name: str | None = Field(
        default=None, validation_alias=AliasChoices("classi_name", "classification_name")
    )
    provincial_name: str | None = None
    listed_at: datetime | None = Field(
        default=None, validation_alias=AliasChoices("listed_date", "listed_at")
    )
    main_operation_business: str | None = None
    org_name_cn: str | None = None
    actual_controller: str | None = None

    @field_validator("listed_at", mode="before")
    @classmethod
    def _parse_listed_at(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class F10IndustryData(BaseModel):
    model_config = ConfigDict(extra="allow")

    concepts: list[F10IndustryTag] = Field(
        default_factory=list, validation_alias=AliasChoices("concept", "concepts")
    )
    concept_class: str | None = None
    industries: list[F10IndustryTag] = Field(
        default_factory=list, validation_alias=AliasChoices("industry", "industries")
    )
    industry_class: str | None = None
    company: F10IndustryCompanyInfo | None = None


class F10BusinessAnalysisItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    report_name: str | None = Field(
        default=None, validation_alias=AliasChoices("report_date", "report_name")
    )
    operating_analysis_explain: str | None = None


class F10BusinessAnalysisData(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: list[F10BusinessAnalysisItem] = Field(default_factory=list)


class F10SkholderItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    person_name: str | None = Field(
        default=None, validation_alias=AliasChoices("personal_name", "person_name")
    )
    position: str | None = Field(
        default=None, validation_alias=AliasChoices("position_name", "position")
    )
    employment_start: datetime | None = Field(
        default=None, validation_alias=AliasChoices("employ_date", "employment_start")
    )
    employment_end: datetime | None = Field(
        default=None, validation_alias=AliasChoices("employ_ed", "employment_end")
    )
    resume: str | None = Field(default=None, validation_alias=AliasChoices("resume_cn", "resume"))
    held_shares: float | None = Field(
        default=None, validation_alias=AliasChoices("held_num", "held_shares")
    )
    annual_salary: float | None = None

    @field_validator("employment_start", "employment_end", mode="before")
    @classmethod
    def _parse_employment_times(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class F10SkholderData(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: list[F10SkholderItem] = Field(default_factory=list)


class F10SkholderChangeItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    manager_name: str | None = Field(
        default=None, validation_alias=AliasChoices("manage_name", "manager_name")
    )
    change_date: datetime | None = Field(
        default=None, validation_alias=AliasChoices("chg_date", "change_date")
    )
    transaction_avg_price: float | None = Field(
        default=None, validation_alias=AliasChoices("trans_avg_price", "transaction_avg_price")
    )
    change_shares: float | None = Field(
        default=None, validation_alias=AliasChoices("chg_shares_num", "change_shares")
    )

    @field_validator("change_date", mode="before")
    @classmethod
    def _parse_change_date(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class F10SkholderChangeData(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: list[F10SkholderChangeItem] = Field(default_factory=list)


class F10SharesChangeItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    change_date: datetime | None = Field(
        default=None, validation_alias=AliasChoices("chg_date", "change_date")
    )
    change_reason: str | None = Field(
        default=None, validation_alias=AliasChoices("chg_reason", "change_reason")
    )
    float_shares: float | None = None
    total_shares: float | None = None

    @field_validator("change_date", mode="before")
    @classmethod
    def _parse_change_date(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class F10SharesRestrictionItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    release_time: datetime | None = Field(
        default=None, validation_alias=AliasChoices("ft_time", "release_time")
    )
    release_ratio: float | None = Field(
        default=None, validation_alias=AliasChoices("ft_ratio", "release_ratio")
    )
    release_shares: float | None = Field(
        default=None, validation_alias=AliasChoices("ft_nums", "release_shares")
    )
    release_type: str | None = Field(
        default=None, validation_alias=AliasChoices("ft_type", "release_type")
    )

    @field_validator("release_time", mode="before")
    @classmethod
    def _parse_release_time(cls, value: Any) -> datetime | None:
        return parse_datetime(value)


class F10SharesChangeData(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: list[F10SharesChangeItem] = Field(default_factory=list)
    restrictions: list[F10SharesRestrictionItem] = Field(
        default_factory=list, validation_alias=AliasChoices("restricts", "restrictions")
    )


class F10API:
    def __init__(self, client: SyncRequester) -> None:
        self._client = client

    def skholderchg(self, symbol: str) -> XueqiuResponse[F10SkholderChangeData]:
        return self._client.request_model(
            "GET",
            F10_SKHOLDERCHG_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[F10SkholderChangeData],
        )

    def skholder(self, symbol: str) -> XueqiuResponse[F10SkholderData]:
        return self._client.request_model(
            "GET",
            F10_SKHOLDER_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[F10SkholderData],
        )

    def industry(self, symbol: str) -> XueqiuResponse[F10IndustryData]:
        return self._client.request_model(
            "GET",
            F10_INDUSTRY_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[F10IndustryData],
        )

    def holders(self, symbol: str) -> XueqiuResponse[F10ShareholderCountData]:
        return self._client.request_model(
            "GET",
            F10_HOLDERS_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[F10ShareholderCountData],
        )

    def bonus(self, symbol: str, *, page: int = 1, size: int = 10) -> XueqiuResponse[F10BonusData]:
        params: dict[str, Any] = {"symbol": symbol, "page": int(page), "size": int(size)}
        return self._client.request_model(
            "GET",
            F10_BONUS_PATH,
            params=params,
            require_auth=True,
            model=XueqiuResponse[F10BonusData],
        )

    def org_holding_change(self, symbol: str) -> XueqiuResponse[F10OrgHoldingChangeData]:
        return self._client.request_model(
            "GET",
            F10_ORG_HOLDING_CHANGE_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[F10OrgHoldingChangeData],
        )

    def industry_compare(
        self, symbol: str, *, type: str = "single"
    ) -> XueqiuResponse[F10IndustryCompareData]:
        return self._client.request_model(
            "GET",
            F10_INDUSTRY_COMPARE_PATH,
            params={"type": type, "symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[F10IndustryCompareData],
        )

    def business_analysis(self, symbol: str) -> XueqiuResponse[F10BusinessAnalysisData]:
        return self._client.request_model(
            "GET",
            F10_BUSINESS_ANALYSIS_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[F10BusinessAnalysisData],
        )

    def shareschg(self, symbol: str, *, count: int = 5) -> XueqiuResponse[F10SharesChangeData]:
        return self._client.request_model(
            "GET",
            F10_SHARESCHG_PATH,
            params={"symbol": symbol, "count": int(count)},
            require_auth=True,
            model=XueqiuResponse[F10SharesChangeData],
        )

    def top_holders(self, symbol: str, *, circula: int = 1) -> XueqiuResponse[F10TopHoldersData]:
        return self._client.request_model(
            "GET",
            F10_TOP_HOLDERS_PATH,
            params={"symbol": symbol, "circula": int(circula)},
            require_auth=True,
            model=XueqiuResponse[F10TopHoldersData],
        )

    def indicator(self, symbol: str) -> XueqiuResponse[F10MainIndicatorData]:
        return self._client.request_model(
            "GET",
            F10_INDICATOR_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[F10MainIndicatorData],
        )


class AsyncF10API:
    def __init__(self, client: AsyncRequester) -> None:
        self._client = client

    async def skholderchg(self, symbol: str) -> XueqiuResponse[F10SkholderChangeData]:
        return await self._client.request_model(
            "GET",
            F10_SKHOLDERCHG_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[F10SkholderChangeData],
        )

    async def skholder(self, symbol: str) -> XueqiuResponse[F10SkholderData]:
        return await self._client.request_model(
            "GET",
            F10_SKHOLDER_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[F10SkholderData],
        )

    async def industry(self, symbol: str) -> XueqiuResponse[F10IndustryData]:
        return await self._client.request_model(
            "GET",
            F10_INDUSTRY_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[F10IndustryData],
        )

    async def holders(self, symbol: str) -> XueqiuResponse[F10ShareholderCountData]:
        return await self._client.request_model(
            "GET",
            F10_HOLDERS_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[F10ShareholderCountData],
        )

    async def bonus(
        self, symbol: str, *, page: int = 1, size: int = 10
    ) -> XueqiuResponse[F10BonusData]:
        params: dict[str, Any] = {"symbol": symbol, "page": int(page), "size": int(size)}
        return await self._client.request_model(
            "GET",
            F10_BONUS_PATH,
            params=params,
            require_auth=True,
            model=XueqiuResponse[F10BonusData],
        )

    async def org_holding_change(self, symbol: str) -> XueqiuResponse[F10OrgHoldingChangeData]:
        return await self._client.request_model(
            "GET",
            F10_ORG_HOLDING_CHANGE_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[F10OrgHoldingChangeData],
        )

    async def industry_compare(
        self, symbol: str, *, type: str = "single"
    ) -> XueqiuResponse[F10IndustryCompareData]:
        return await self._client.request_model(
            "GET",
            F10_INDUSTRY_COMPARE_PATH,
            params={"type": type, "symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[F10IndustryCompareData],
        )

    async def business_analysis(self, symbol: str) -> XueqiuResponse[F10BusinessAnalysisData]:
        return await self._client.request_model(
            "GET",
            F10_BUSINESS_ANALYSIS_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[F10BusinessAnalysisData],
        )

    async def shareschg(
        self, symbol: str, *, count: int = 5
    ) -> XueqiuResponse[F10SharesChangeData]:
        return await self._client.request_model(
            "GET",
            F10_SHARESCHG_PATH,
            params={"symbol": symbol, "count": int(count)},
            require_auth=True,
            model=XueqiuResponse[F10SharesChangeData],
        )

    async def top_holders(
        self, symbol: str, *, circula: int = 1
    ) -> XueqiuResponse[F10TopHoldersData]:
        return await self._client.request_model(
            "GET",
            F10_TOP_HOLDERS_PATH,
            params={"symbol": symbol, "circula": int(circula)},
            require_auth=True,
            model=XueqiuResponse[F10TopHoldersData],
        )

    async def indicator(self, symbol: str) -> XueqiuResponse[F10MainIndicatorData]:
        return await self._client.request_model(
            "GET",
            F10_INDICATOR_PATH,
            params={"symbol": symbol},
            require_auth=True,
            model=XueqiuResponse[F10MainIndicatorData],
        )
