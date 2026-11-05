"""f10_indicator: key figures of the latest report of one A-share, valued at the latest price."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BeforeValidator, Field

from .._f10 import report_date_from_label
from .._model import UpstreamModel
from .._values import Number, WholeNumber
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolParams


class MainIndicator(UpstreamModel):
    """Figures of the latest report, and valuation at the latest price.

    Flows and returns are year to date and not annualized, so a third-quarter value covers nine
    months. Growth rates are already in percent points (4.42 means 4.42%).
    """

    report_name: str | None = Field(default=None, validation_alias="report_date")
    """Report period label, such as 2026三季报 (upstream ``report_date``)."""
    report_date: Annotated[date | None, BeforeValidator(report_date_from_label)] = None
    """Period end, derived from the label; not a disclosure date, which the upstream does not
    send."""
    currency: str | None = None
    total_revenue: Number = None
    """Operating revenue, year to date, in ``currency``."""
    operating_income_yoy: Number = None
    """Revenue growth on the same period a year earlier, in percent points."""
    net_profit_atsopc: Number = None
    """Net profit attributable to the parent's shareholders, year to date, in ``currency``."""
    net_profit_atsopc_yoy: Number = None
    """Growth of that profit on the same period a year earlier, in percent points."""
    basic_eps: Number = None
    """Basic earnings per share, year to date, in ``currency``."""
    np_per_share: Number = None
    """Net assets per share, in ``currency``, despite the name; equal to quote_detail's navps,
    measured 2026-11-04."""
    avg_roe: Number = None
    """Return on equity, year to date and not annualized, in percent points. Equal to
    finance_indicator's avg_roe, but f10_industry_compare's differs for the same period (5.2 and
    4.8994 for SH600000, measured 2026-11-04), so do not compare it across those endpoints."""
    gross_selling_rate: Number = None
    """Gross margin, in percent points; None for banks."""
    net_selling_rate: Number = None
    """Net margin, in percent points."""
    asset_liab_ratio: Number = None
    """Liabilities over assets, in percent points."""
    goodwill_in_net_assets: Number = None
    """Goodwill over net assets attributable to the parent's shareholders, in percent points."""
    pledge_ratio: Number = None
    """Share pledge ratio; percent points going by its size, not measured against a source."""
    total_shares: WholeNumber = None
    """Shares outstanding."""
    float_shares: WholeNumber = None
    """Tradable shares."""
    market_capital: Number = None
    """``total_shares`` at the latest close, in ``currency``."""
    float_market_capital: Number = None
    """``float_shares`` at the latest close, in ``currency``."""
    pe_ttm: Number = None
    """Trailing twelve months price to earnings, as a multiple."""
    pb: Number = None
    """Price to book, as a multiple."""
    dividend: Number = None
    """Dividend per share, in ``currency``: 0.42 for the plan 10派4.2元, measured 2026-11-04."""
    dividend_yield: Number = None
    """``dividend`` over the latest close, in percent points."""


class _Payload(UpstreamModel):
    items: tuple[MainIndicator, ...]


def _build_query(params: SymbolParams, now: datetime) -> dict[str, str]:
    return {"symbol": params.symbol}


def _parse(payload: Any, params: SymbolParams) -> list[MainIndicator]:
    return list(_Payload.model_validate(payload).items)


ENDPOINT = Endpoint(
    name="f10_indicator",
    doc="Key figures of the latest report of one A-share, valued at the latest price.",
    host="stock.xueqiu.com",
    path="/v5/stock/f10/cn/indicator.json",
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
        section="indicator",
        title_zh="主要指标",
        title_en="Key indicators",
        summary=(
            "取 A 股公司最新一期报告的主要指标快照，含营收、归母净利润及同比、每股收益、"
            "净资产收益率、资产负债率，以及按最新股价算的市值、市盈率、市净率和股息率。"
        ),
        keywords=(
            "主要指标",
            "最新指标",
            "估值",
            "市盈率",
            "市净率",
            "股息率",
            "质押比例",
            "商誉占比",
            "key indicators",
            "snapshot",
            "valuation",
            "dividend yield",
        ),
        default_fields=(
            "report_name",
            "report_date",
            "total_revenue",
            "operating_income_yoy",
            "net_profit_atsopc",
            "net_profit_atsopc_yoy",
            "basic_eps",
            "avg_roe",
            "asset_liab_ratio",
            "pe_ttm",
            "pb",
            "dividend_yield",
            "market_capital",
        ),
        row_default=1,
        row_cap=1,
        upstream_cap=1,
        examples=({"symbol": "SH600000"},),
    ),
)
