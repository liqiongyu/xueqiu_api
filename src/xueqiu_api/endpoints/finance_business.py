"""finance_business: revenue breakdown of one company by report period, latest first."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

from .._f10 import ExchangeDate
from .._finance import MAX_COUNT, REPORT_DATE_ZONE
from .._model import UpstreamModel
from .._values import FractionPoints, Number
from ..errors import SymbolNotFound
from ..registry import AgentInfo, Endpoint, Pagination
from ..symbols import SymbolCode, normalize_symbol


class BusinessParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    symbol: SymbolCode
    """An A-share or US stock; the request goes to the region of its market."""
    count: int = Field(default=2, ge=1, le=MAX_COUNT)
    """Report periods to fetch, latest first."""


class RevenueSegment(UpstreamModel):
    """One line of a breakdown."""

    project_announced_name: str | None = None
    """Name of the business, product or region."""
    prime_operating_income: Number = None
    """Revenue of the segment, in the currency of the breakdown."""
    income_ratio: FractionPoints = None
    """Share of revenue, in percent points (55.75 means 55.75%). The denominator differs between
    groups and periods: a value can exceed 100, and a group need not add up to 100."""
    gross_profit_rate: FractionPoints = None
    """Gross margin, in percent points. The upstream sends it for some groups only."""


class RevenueGroup(UpstreamModel):
    """One way of splitting the revenue of a period."""

    class_standard: int
    """Upstream code of the split, as sent. By region was 3 on SH600000 and 2 on AAPL, measured
    2026-11-04, so one code can mean different splits."""
    segments: tuple[RevenueSegment, ...] = Field(validation_alias="business_list")


class BusinessPeriod(UpstreamModel):
    """One report period. Other upstream fields such as ``ctime``, and for US ``ed``,
    ``report_annual`` and ``report_type_code``, stay in ``model_extra`` as sent."""

    report_date: ExchangeDate
    """Period end date, not the disclosure date, read in Asia/Shanghai for every market. A US
    fiscal year and its fourth quarter have the same date."""
    report_name: str
    """Upstream period name, such as 2026中报 (January to June), 2026年FY or 2026年Q4 (the quarter
    alone)."""
    groups: tuple[RevenueGroup, ...] = Field(validation_alias="class_list")


class RevenueBreakdown(UpstreamModel):
    """Revenue breakdown by report period, latest first.

    The company's own prose, ``main_operation_business`` and ``operation_range``, stays in
    ``model_extra`` with the other upstream fields, and is not rendered.
    """

    symbol: str
    name: str | None = Field(default=None, validation_alias="quote_name")
    """Security name (upstream ``quote_name``)."""
    currency: str | None = None
    """Currency of the amounts, such as CNY or USD."""
    industry: dict[str, Any] | None = None
    """Xueqiu industry: ``ind_name`` and ``ind_code``, such as 银行 and BK0055."""
    periods: tuple[BusinessPeriod, ...] = Field(validation_alias="list")

    def header(self) -> Mapping[str, Any]:
        industry = (self.industry or {}).get("ind_name")
        return {"name": self.name, "currency": self.currency, "industry": industry}

    def sections(self) -> Mapping[str, list[dict[str, Any]]]:
        """One row per segment, with its period and the code of its group."""
        rows = [
            {
                "report_date": period.report_date,
                "report_name": period.report_name,
                "class_standard": group.class_standard,
                **segment.model_dump(),
            }
            for period in self.periods
            for group in period.groups
            for segment in group.segments
        ]
        return {"segments": rows}

    def __len__(self) -> int:
        return len(self.periods)


def _build_query(params: BusinessParams, now: datetime) -> dict[str, str]:
    return {
        "region": normalize_symbol(params.symbol).region,
        "symbol": params.symbol,
        "count": str(params.count),
    }


def _parse(payload: Any, params: BusinessParams) -> RevenueBreakdown:
    fields = cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
    if fields.get("quote_name") is None and not fields.get("list"):
        # What an unknown code gets, measured 2026-11-05.
        raise SymbolNotFound(f"the upstream has no revenue breakdown for {params.symbol}")
    return RevenueBreakdown.model_validate(
        {**payload, "symbol": params.symbol}, context={"tz": REPORT_DATE_ZONE}
    )


ENDPOINT = Endpoint(
    name="finance_business",
    doc="Revenue by business, product or region of one A-share or US company, latest period first.",
    host="stock.xueqiu.com",
    path="/v5/stock/finance/{region}/business.json",
    params=BusinessParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    pagination=Pagination(style="latest"),
    # The hk path answers 404, measured 2026-11-04.
    markets=("CN", "US"),
    order="newest_first",
    agent=AgentInfo(
        category="finance",
        group="xq_financials",
        section="business",
        title_zh="主营构成",
        title_en="Revenue breakdown",
        summary=(
            "取 A 股或美股公司按报告期划分的各业务、产品或地区的主营收入、收入占比和毛利率，"
            "class_standard 相同的行属于同一种拆分口径，各口径的占比分母不同。"
        ),
        keywords=(
            "主营构成",
            "收入构成",
            "分产品收入",
            "分地区收入",
            "业务构成",
            "营收构成",
            "主营收入",
            "毛利率",
            "赚钱",
            "revenue breakdown",
            "segments",
            "business mix",
            "revenue by product",
        ),
        default_fields=(
            "report_date",
            "report_name",
            "class_standard",
            "project_announced_name",
            "prime_operating_income",
            "income_ratio",
            "gross_profit_rate",
        ),
        row_default=50,
        row_cap=100,
        upstream_cap=MAX_COUNT,
        examples=({"symbol": "SH600000", "count": 2}, {"symbol": "AAPL", "count": 2}),
        precision=2,
    ),
)
