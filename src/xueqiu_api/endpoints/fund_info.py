"""fund_info: profile, status and latest net value of one fund."""

from __future__ import annotations

from datetime import date
from typing import Any, ClassVar

from pydantic import AliasPath, Field

from .._fund import FundDate, FundParams, fund_code_query
from .._model import UpstreamModel
from .._values import CnAmount, Number, PercentPoints, WholeNumber
from ..errors import SymbolNotFound
from ..registry import AgentInfo, Endpoint, NotFound


class FundBenchmarkIndex(UpstreamModel):
    """An index the upstream compares a fund with."""

    symbol: str
    """Upstream symbol as sent, such as CSI931139 or SH000300."""
    name: str | None = Field(default=None, validation_alias="symbol_name")


class FundTag(UpstreamModel):
    """A label of a fund, such as 股票型-标准指数 or 中高风险."""

    name: str
    category: str | None = None
    """Upstream category code."""


class FundInfo(UpstreamModel):
    """Profile of one fund. A fund too new for a net value has None in those fields."""

    drop: ClassVar[frozenset[str]] = frozenset(
        {
            "base_data_tip",
            "display_annual_performance",
            "fir_header_base_data",
            "growth_default_period",
            "ipo_end_count_down",
            "ipo_start_count_down",
            "nav_tab_list",
            "sec_header_base_data",
            "trade_reason",
        }
    )
    """Prompts and UI fields; ``nav_tab_list`` repeats the returns of fund_derived."""

    fund_code: str = Field(validation_alias="fd_code")
    name: str | None = Field(default=None, validation_alias="fd_name")
    full_name: str | None = Field(default=None, validation_alias="fd_full_name")
    fund_type: str | None = Field(default=None, validation_alias="fd_type")
    """Upstream type code; ``type_desc`` says what it means."""
    type_desc: str | None = None
    """Such as 股票型-标准指数."""
    found_date: FundDate = None
    """Inception date."""
    ipo_start_date: FundDate = None
    ipo_end_date: FundDate = None
    company: str | None = Field(default=None, validation_alias="keeper_name")
    """Management company."""
    custodian: str | None = Field(default=None, validation_alias="trup_name")
    manager_name: str | None = None
    total_shares: CnAmount = Field(default=None, validation_alias="totshare")
    """Units outstanding: ``7.55亿`` gives 755000000.0, rounded as the upstream rounds it."""
    risk_level: WholeNumber = None
    """4 came with the tag 中高风险; a scale of 1 to 5 is inferred."""
    rating_desc: str | None = None
    purchase_status: WholeNumber = Field(default=None, validation_alias="declare_status")
    """Upstream status of 申购; 1 came with ``can_buy`` true."""
    redeem_status: WholeNumber = Field(default=None, validation_alias="withdraw_status")
    """Upstream status of 赎回; 1 was seen."""
    subscribe_status: WholeNumber = None
    """Upstream status of 认购, the initial offering; 0 was seen after it ended."""
    auto_invest_status: WholeNumber = None
    """Upstream status of 定投; 1 was seen."""
    can_buy: bool | None = None
    purchase_rate_pct: PercentPoints = Field(
        default=None, validation_alias=AliasPath("fund_rates", "declare_rate")
    )
    """List rate of the first purchase tier before any discount, in percent points."""
    purchase_discount: Number = Field(
        default=None, validation_alias=AliasPath("fund_rates", "declare_discount")
    )
    """Multiplier on the list rate; that it multiplies is inferred."""
    nav_date: FundDate = Field(default=None, validation_alias=AliasPath("fund_derived", "end_date"))
    unit_nav: Number = Field(default=None, validation_alias=AliasPath("fund_derived", "unit_nav"))
    """Net value per unit on ``nav_date``, in CNY."""
    daily_pct: PercentPoints = Field(
        default=None, validation_alias=AliasPath("fund_derived", "nav_grtd")
    )
    """Change on ``nav_date``, in percent points (1.23 means 1.23%)."""
    tags: tuple[FundTag, ...] = Field(
        default=(), validation_alias=AliasPath("op_fund", "fund_tags")
    )
    benchmark_indexes: tuple[FundBenchmarkIndex, ...] = Field(
        default=(), validation_alias="benchmark_index"
    )
    """The first is the tracked index, the others comparison indexes (inferred)."""
    benchmark_text: str | None = Field(default=None, validation_alias="performance_bench_mark")
    """The contract's benchmark formula."""
    invest_target: str | None = None
    invest_strategy: str | None = Field(default=None, validation_alias="invest_orientation")
    pension_fund: bool | None = None


def _parse(payload: Any, params: FundParams) -> FundInfo:
    if payload is None:
        raise SymbolNotFound(f"no fund {params.fund_code}")
    return FundInfo.model_validate(payload)


ENDPOINT = Endpoint(
    name="fund_info",
    doc="Profile, status and latest net value of one fund.",
    host="danjuanfunds.com",
    path="/djapi/fund/{fund_code}",
    params=FundParams,
    build_query=fund_code_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="danjuan",
    verified_on=date(2026, 11, 5),
    # 000000 got HTTP 200 with 600001 该基金暂不销售, measured 2026-11-05. That text may also fit a
    # fund that exists and is not on sale, so the error keeps it.
    not_found=NotFound(codes=("600001",)),
    agent=AgentInfo(
        category="fund",
        group="xq_fund",
        section="info",
        title_zh="基金资料",
        title_en="Fund profile",
        summary=(
            "取一只基金的基本资料：名称、类型、成立日、基金公司、托管人、基金经理、风险等级、"
            "份额、最新净值和申购状态。"
        ),
        keywords=(
            "基金资料",
            "基金基本信息",
            "基金类型",
            "基金公司",
            "基金经理",
            "最新净值",
            "fund info",
            "fund profile",
            "mutual fund",
        ),
        default_fields=(
            "fund_code",
            "name",
            "type_desc",
            "found_date",
            "company",
            "custodian",
            "manager_name",
            "risk_level",
            "total_shares",
            "nav_date",
            "unit_nav",
            "daily_pct",
            "can_buy",
        ),
        row_default=1,
        row_cap=1,
        upstream_cap=1,
        examples=({"fund_code": "008975"},),
        free_text=("invest_strategy", "invest_target", "benchmark_text"),
    ),
)
