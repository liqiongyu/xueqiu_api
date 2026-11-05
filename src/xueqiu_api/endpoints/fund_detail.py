"""fund_detail: fees, settlement days, the latest report on total assets, and the managers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from .._fund import (
    REPORT_NOISE,
    FundBondHolding,
    FundDate,
    FundHolding,
    FundParams,
    fund_code_query,
)
from .._model import UpstreamModel
from .._values import Number, PercentPoints, WholeNumber
from ..registry import AgentInfo, Endpoint

RESULT_CAP = 500
"""Most rows of all tables; one manager alone had 58 funds, measured 2026-11-04."""


class FundPosition(UpstreamModel):
    """The latest report: class percentages on total assets, holdings on net assets.

    The classes sum to 100. The holdings do not add up to them: bonds of 6.48 against a class of
    5.48, measured 2026-11-04.
    """

    report_date: FundDate = Field(default=None, validation_alias="enddate")
    """Period end of the report."""
    report_name: str | None = Field(default=None, validation_alias="source_mark")
    """Such as 第三季度报."""
    report_code: str | None = Field(default=None, validation_alias="source")
    """Upstream code of the report, 4 for the third quarter."""
    total_assets: Number = Field(default=None, validation_alias="asset_tot")
    """In CNY."""
    net_assets: Number = Field(default=None, validation_alias="asset_val")
    """In CNY."""
    stock_pct_total_assets: PercentPoints = Field(default=None, validation_alias="stock_percent")
    """Stocks, in percent points of total assets (1.23 means 1.23%)."""
    bond_pct_total_assets: PercentPoints = Field(default=None, validation_alias="bond_percent")
    cash_pct_total_assets: PercentPoints = Field(default=None, validation_alias="cash_percent")
    other_pct_total_assets: PercentPoints = Field(default=None, validation_alias="other_percent")
    stocks: tuple[FundHolding, ...] = Field(default=(), validation_alias="stock_list")
    bonds: tuple[FundBondHolding, ...] = Field(default=(), validation_alias="bond_list")


class FundFeeRow(UpstreamModel):
    """One row of a fee table."""

    tier: str | None = Field(default=None, validation_alias="name")
    """Upstream label such as 0.0万<买入金额<100.0万; the bounds are not parsed."""
    value: Number = None
    """In the unit of ``unit``."""
    unit: str | None = None
    """2 for percent points, 1 for CNY per order; inferred from 1000.0 on the largest tier."""
    special: bool | None = None
    """Upstream flag of unknown meaning."""


class FundFees(UpstreamModel):
    """Fee rates in percent points (1.23 means 1.23%); discounts are multipliers."""

    subscribe_rate_pct: PercentPoints = Field(default=None, validation_alias="subscribe_rate")
    """Rate of 认购, during the initial offering."""
    purchase_rate_pct: PercentPoints = Field(default=None, validation_alias="declare_rate")
    """List rate of 申购 on the first tier, before any discount."""
    redeem_rate_pct: PercentPoints = Field(default=None, validation_alias="withdraw_rate")
    """Rate of 赎回 on the first holding tier."""
    purchase_discount: Number = Field(default=None, validation_alias="declare_discount")
    subscribe_discount: Number = None
    purchase_tiers: tuple[FundFeeRow, ...] = Field(
        default=(), validation_alias="declare_rate_table"
    )
    redeem_tiers: tuple[FundFeeRow, ...] = Field(default=(), validation_alias="withdraw_rate_table")
    other_fees: tuple[FundFeeRow, ...] = Field(default=(), validation_alias="other_rate_table")
    """Such as 基金管理费 and 基金托管费, per year (inferred)."""


class FundTradeDays(UpstreamModel):
    """Settlement offsets of one fund, in trading days."""

    buy_confirm_days: WholeNumber = Field(default=None, validation_alias="buy_confirm_date")
    buy_query_days: WholeNumber = Field(default=None, validation_alias="buy_query_date")
    buy_total_days: WholeNumber = Field(default=None, validation_alias="all_buy_days")
    sale_confirm_days: WholeNumber = Field(default=None, validation_alias="sale_confirm_date")
    sale_query_days: WholeNumber = Field(default=None, validation_alias="sale_query_date")
    sale_total_days: WholeNumber = Field(default=None, validation_alias="all_sale_days")


class FundManagedFund(UpstreamModel):
    """One fund a manager runs."""

    fund_code: str | None = None
    fund_name: str | None = Field(default=None, validation_alias="fundsname")
    start_date: FundDate = Field(default=None, validation_alias="post_date")
    tenure_return_pct: PercentPoints = Field(default=None, validation_alias="cp_rate")
    """Return since ``start_date``, in percent points."""


class FundManagerProfile(UpstreamModel):
    """A manager of the fund."""

    manager_id: str | None = Field(default=None, validation_alias="indi_id")
    """Holds only within this endpoint: fund_manager has another id for the same person."""
    name: str | None = None
    work_years: WholeNumber = Field(default=None, validation_alias="work_year")
    resume: str | None = None
    funds: tuple[FundManagedFund, ...] = Field(default=(), validation_alias="achievement_list")


class FundDetail(UpstreamModel):
    """Fees, settlement days, latest report and managers of one fund."""

    drop: ClassVar[frozenset[str]] = REPORT_NOISE | {"end_date_str", "fd_code"}
    """Quotes at fetch time, UI fields and duplicates."""

    company_intro: str | None = Field(default=None, validation_alias="fund_company")
    position: FundPosition = Field(default_factory=FundPosition, validation_alias="fund_position")
    fees: FundFees = Field(default_factory=FundFees, validation_alias="fund_rates")
    managers: tuple[FundManagerProfile, ...] = Field(default=(), validation_alias="manager_list")
    trade_days: FundTradeDays = Field(
        default_factory=FundTradeDays, validation_alias="fund_date_conf"
    )
    pension_fund: bool | None = None

    def header(self) -> Mapping[str, Any]:
        position, fees, days = self.position, self.fees, self.trade_days
        return {
            "report_date": position.report_date,
            "total_assets": position.total_assets,
            "net_assets": position.net_assets,
            "stock_pct_total_assets": position.stock_pct_total_assets,
            "bond_pct_total_assets": position.bond_pct_total_assets,
            "cash_pct_total_assets": position.cash_pct_total_assets,
            "other_pct_total_assets": position.other_pct_total_assets,
            "purchase_rate_pct": fees.purchase_rate_pct,
            "purchase_discount": fees.purchase_discount,
            "redeem_rate_pct": fees.redeem_rate_pct,
            "buy_confirm_days": days.buy_confirm_days,
            "sale_total_days": days.sale_total_days,
        }

    def sections(self) -> Mapping[str, Sequence[BaseModel | Mapping[str, Any]]]:
        return {
            "purchase_tiers": self.fees.purchase_tiers,
            "redeem_tiers": self.fees.redeem_tiers,
            "other_fees": self.fees.other_fees,
            "stocks": self.position.stocks,
            "bonds": self.position.bonds,
            "managers": [manager.model_dump(exclude={"funds"}) for manager in self.managers],
            "manager_funds": [
                {"manager_name": manager.name, **fund.model_dump()}
                for manager in self.managers
                for fund in manager.funds
            ],
            "company": [{"company_intro": self.company_intro}] if self.company_intro else [],
        }

    def __len__(self) -> int:
        return sum(len(table) for table in self.sections().values())


def _parse(payload: Any, params: FundParams) -> FundDetail:
    return FundDetail.model_validate(payload)


ENDPOINT = Endpoint(
    name="fund_detail",
    doc="Fees, settlement days, latest report on total assets and the managers of one fund.",
    host="danjuanfunds.com",
    path="/djapi/fund/detail/{fund_code}",
    params=FundParams,
    build_query=fund_code_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="danjuan",
    verified_on=date(2026, 11, 4),
    agent=AgentInfo(
        category="fund",
        group="xq_fund",
        section="detail",
        title_zh="基金费率和详情",
        title_en="Fund fees and details",
        summary=(
            "取一只基金的申购赎回费率分档、管理费托管费、申赎确认天数、按总资产计的资产配置"
            "和基金经理及其管理的其他基金。"
        ),
        keywords=(
            "基金费率",
            "申购费",
            "赎回费",
            "管理费",
            "托管费",
            "基金经理简历",
            "fund fees",
            "redemption fee",
            "management fee",
        ),
        default_fields=("tier", "value", "unit"),
        row_default=50,
        row_cap=RESULT_CAP,
        upstream_cap=RESULT_CAP,
        examples=({"fund_code": "008975"},),
        free_text=("company_intro", "resume"),
    ),
)
