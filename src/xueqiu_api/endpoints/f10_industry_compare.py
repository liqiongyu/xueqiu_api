"""f10_industry_compare: one A-share against the average, minimum and maximum of its industry."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import BeforeValidator, Field, field_validator

from .._f10 import report_date_from_label
from .._model import UpstreamModel
from .._time import from_ms
from .._values import Number
from ..registry import AgentInfo, Endpoint
from ..symbols import TIMEZONES, SymbolParams


class CompareMetrics(UpstreamModel):
    """Valuation at the quote time and figures of the report period.

    Flows and returns are year to date and not annualized. The minimum and maximum lack
    market_capital, pb, operating_income_yoy, net_profit_atsopc_yoy, asset_liab_ratio and
    net_selling_rate, measured 2026-11-04.
    """

    pe_ttm: Number = None
    """Trailing twelve months price to earnings, as a multiple; the upstream's industry statistics
    can include negative values."""
    pb: Number = None
    """Price to book, as a multiple."""
    basic_eps: Number = None
    """Basic earnings per share, year to date, in CNY."""
    avg_roe: Number = None
    """Return on equity, year to date and not annualized, in percent points. Not comparable with
    the avg_roe of f10_indicator and finance_indicator: 4.8994 against 5.2 for SH600000, measured
    2026-11-04."""
    gross_selling_rate: Number = None
    """Gross margin, in percent points. Returned as sent: for banks the stock, minimum and maximum
    are None while the average is 0.0, measured 2026-11-04, which is not a real average."""
    net_selling_rate: Number = None
    """Net margin, in percent points."""
    total_revenue: Number = None
    """Operating revenue, year to date, in CNY."""
    net_profit_atsopc: Number = None
    """Net profit attributable to the parent's shareholders, year to date, in CNY."""
    np_per_share: Number = None
    """Net assets per share, in CNY, despite the name."""
    operate_cash_flow_ps: Number = None
    """Operating cash flow per share, year to date, in CNY."""
    total_assets: Number = None
    """Total assets, in CNY."""
    total_shares: Number = None
    """Shares outstanding; a float, because the average is not a whole number."""
    market_capital: Number = None
    """Market value, in CNY."""
    operating_income_yoy: Number = None
    """Revenue growth on the same period a year earlier, in percent points."""
    net_profit_atsopc_yoy: Number = None
    """Growth of attributable net profit on the same period a year earlier, in percent points."""
    asset_liab_ratio: Number = None
    """Liabilities over assets, in percent points."""


class CompareItem(CompareMetrics):
    """The requested stock."""

    symbol: str | None = None
    name: str | None = None


class IndustryCompare(UpstreamModel):
    """One A-share against its industry. The upstream does not say how many peers the statistics
    cover, and its ``count`` is the number of ``items``."""

    symbol: str
    """The requested symbol."""
    ind_name: str | None = None
    """Industry name, such as 银行."""
    ind_code: str | None = None
    """Board code, such as BK0055; the same code as f10_industry."""
    ind_class: str | None = None
    """Classification standard, such as SW2014."""
    report_name: str | None = None
    """Report period as sent: an ISO date, such as 2026-09-30."""
    report_date: Annotated[date | None, BeforeValidator(report_date_from_label)] = Field(
        default=None, validation_alias="report_name"
    )
    """Period end of the figures; not a disclosure date, which the upstream does not send."""
    quote_time: datetime | None = None
    """Time of the quotes behind the valuation, in Asia/Shanghai."""
    items: tuple[CompareItem, ...]
    industry_avg: CompareMetrics | None = Field(default=None, validation_alias="avg")
    industry_min: CompareMetrics | None = Field(default=None, validation_alias="min")
    industry_max: CompareMetrics | None = Field(default=None, validation_alias="max")

    @field_validator("quote_time", mode="before")
    @classmethod
    def _quote_time(cls, value: Any) -> Any:
        return None if value is None else from_ms(value, ZoneInfo(TIMEZONES["CN"]))

    def header(self) -> Mapping[str, Any]:
        return {
            "ind_name": self.ind_name,
            "ind_code": self.ind_code,
            "ind_class": self.ind_class,
            "report_date": self.report_date,
            "quote_time": self.quote_time,
        }

    def sections(self) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        rows = [{"stat": "stock", **item.model_dump()} for item in self.items]
        stats = (("avg", self.industry_avg), ("min", self.industry_min), ("max", self.industry_max))
        rows += [{"stat": stat, **row.model_dump()} for stat, row in stats if row is not None]
        return {"comparison": rows}

    def __len__(self) -> int:
        return len(self.items)


def _build_query(params: SymbolParams, now: datetime) -> dict[str, str]:
    # single is the only type measured; with it items holds the requested stock alone.
    return {"symbol": params.symbol, "type": "single"}


def _parse(payload: Any, params: SymbolParams) -> IndustryCompare:
    return IndustryCompare.model_validate({**payload, "symbol": params.symbol})


ENDPOINT = Endpoint(
    name="f10_industry_compare",
    doc="One A-share against the average, minimum and maximum of its industry.",
    host="stock.xueqiu.com",
    path="/v5/stock/f10/cn/industry/compare.json",
    params=SymbolParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    # The valuation equalled the 15:00 quote_detail after the close, measured 2026-11-04. Whether
    # it moves during trading was not measured.
    freshness="intraday",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    markets=("CN",),
    agent=AgentInfo(
        category="f10",
        group="xq_company",
        section="industry_compare",
        title_zh="行业对比",
        title_en="Industry comparison",
        summary=(
            "取一只 A 股与所在行业的对比：该股的估值和最新报告期财务指标，"
            "以及全行业的平均值、最小值和最大值。"
        ),
        keywords=(
            "行业对比",
            "同行比较",
            "行业平均",
            "行业均值",
            "同业对比",
            "行业估值",
            "peer comparison",
            "industry average",
            "sector comparison",
        ),
        default_fields=(
            "stat",
            "symbol",
            "name",
            "pe_ttm",
            "pb",
            "avg_roe",
            "basic_eps",
            "gross_selling_rate",
            "net_selling_rate",
            "operating_income_yoy",
            "net_profit_atsopc_yoy",
            "asset_liab_ratio",
            "market_capital",
        ),
        row_default=4,
        row_cap=4,
        upstream_cap=1,
        examples=({"symbol": "SH600000"},),
    ),
)
