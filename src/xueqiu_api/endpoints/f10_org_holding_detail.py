"""f10_org_holding_detail: the funds holding one A-share at one report period."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from typing import Any

from pydantic import BaseModel, Field

from .._f10 import ExchangeDate, ReportOption
from .._model import UpstreamModel
from .._time import to_ms
from .._values import Number, WholeNumber
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolParams, normalize_symbol

TOTAL_ROW = "全部合计"
"""Name of the row holding the sum of the others; it came first and equalled their sum, measured
2026-11-05, but it is told by name."""


class OrgHoldingDetailParams(SymbolParams):
    report_date: date | None = None
    """Period end to list, one of ``report_periods``, such as 2026-06-30; by default the latest."""


class StockFundHolding(UpstreamModel):
    """One fund's position in the stock."""

    holder_name: str | None = Field(default=None, validation_alias="org_name_or_fund_name")
    """Full fund name."""
    held_num: WholeNumber = None
    """Shares held."""
    to_float_shares_ratio: Number = None
    """Share of the tradable shares, already in percent points: 0.51 means 0.51%."""


class OrgHoldingDetail(UpstreamModel):
    """The funds holding one A-share, largest first. Only funds: for all institutions together use
    f10_org_holding_change. Half-year and annual lists are much longer than quarterly ones: 513
    funds for SH600000's 2026 half-year report against 66 for the third quarter, measured
    2026-11-05."""

    symbol: str
    """The requested symbol."""
    report_name: str | None = Field(default=None, validation_alias="chg_date")
    """Report period label, such as 2026三季报 (upstream ``chg_date``)."""
    report_date: ExchangeDate = Field(default=None, validation_alias="timestamp")
    """Period end; not a disclosure date, which the upstream does not send."""
    holdings: tuple[StockFundHolding, ...] = Field(validation_alias="fund_items")
    """The funds, without the total row."""
    total: StockFundHolding | None = None
    """The upstream's total row, or None without one."""
    report_periods: tuple[ReportOption, ...] = Field(default=(), validation_alias="times")
    """The report periods the upstream lists, latest first."""

    def header(self) -> Mapping[str, Any]:
        return {
            "report_date": self.report_date,
            "report_name": self.report_name,
            "total_held_num": self.total.held_num if self.total else None,
            "total_to_float_shares_ratio": self.total.to_float_shares_ratio if self.total else None,
            "report_periods": len(self.report_periods),
        }

    def sections(self) -> Mapping[str, Sequence[BaseModel]]:
        return {"holdings": self.holdings}

    def __len__(self) -> int:
        return len(self.holdings)


def _build_query(params: OrgHoldingDetailParams, now: datetime) -> dict[str, str]:
    # extend=true is the only value measured.
    query = {"symbol": params.symbol, "extend": "true"}
    if params.report_date is not None:
        tz = normalize_symbol(params.symbol).timezone
        query["timestamp"] = str(to_ms(datetime.combine(params.report_date, time(), tz)))
    return query


def _parse(payload: Any, params: OrgHoldingDetailParams) -> OrgHoldingDetail:
    detail = OrgHoldingDetail.model_validate(
        {**payload, "symbol": params.symbol},
        context={"tz": normalize_symbol(params.symbol).timezone},
    )
    return detail.model_copy(
        update={
            "holdings": tuple(row for row in detail.holdings if row.holder_name != TOTAL_ROW),
            "total": next((row for row in detail.holdings if row.holder_name == TOTAL_ROW), None),
        }
    )


ENDPOINT = Endpoint(
    name="f10_org_holding_detail",
    doc="The funds holding one A-share at one report period, with their total.",
    host="stock.xueqiu.com",
    path="/v5/stock/f10/cn/org_holding/detail.json",
    params=OrgHoldingDetailParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="xueqiu",
    verified_on=date(2026, 11, 5),
    markets=("CN",),
    order="rank",
    agent=AgentInfo(
        category="f10",
        group="xq_company",
        section="org_holding_detail",
        title_zh="持股基金名单",
        title_en="Fund holders of a stock",
        summary=(
            "取一只 A 股某一报告期（默认最新一期）持有它的基金名单，"
            "含每只基金的持股数和占流通股比例，以及这些基金的合计。"
        ),
        keywords=(
            "持股基金",
            "持仓基金",
            "哪些基金持有",
            "基金持股明细",
            "机构持仓明细",
            "fund holders",
            "funds holding",
            "fund ownership",
        ),
        default_fields=("holder_name", "held_num", "to_float_shares_ratio"),
        row_default=50,
        row_cap=100,
        # 513 funds came back in one response, measured 2026-11-05; no cap was seen.
        upstream_cap=1000,
        examples=({"symbol": "SH600000"},),
    ),
)
