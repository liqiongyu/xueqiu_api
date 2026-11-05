"""f10_org_holding_change: institutional ownership by report period or by trading day."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from .._f10 import ExchangeDate, region_query
from .._model import UpstreamModel
from .._values import Number, WholeNumber
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolParams, normalize_symbol


class OrgHoldingPeriod(UpstreamModel):
    """Institutional ownership of an A-share at one report period, latest first.

    Compare like with like: half-year and annual reports cover far more institutions than the
    quarterly ones (521 against 74 for SH600000, measured 2026-11-04).
    """

    report_name: str | None = Field(default=None, validation_alias="chg_date")
    """Report period label, such as 2026三季报 (upstream ``chg_date``)."""
    report_date: ExchangeDate = Field(default=None, validation_alias="timestamp")
    """Period end; not a disclosure date, which the upstream does not send."""
    institution_num: WholeNumber = None
    """Institutions holding the stock."""
    held_ratio: Number = None
    """Their share of the tradable shares, in percent points (75.19 means 75.19%). The denominator
    is inferred: for SH600000 it equalled the ten largest tradable holders plus the funds, 74.6823
    + 0.51, measured 2026-11-04."""
    held_ratio_chg_pct: Number = Field(default=None, validation_alias="chg")
    """Relative change of ``held_ratio`` from the period before, in percent points, not a
    difference of ratios: (75.192 / 76.2369 - 1) * 100 = -1.37. Measured 2026-11-05."""
    price: Number = None
    """Price at the period end, in CNY; equal to f10_holders' price."""


class OrgHoldingDay(UpstreamModel):
    """Institutional ownership of a US stock on one trading day, latest first."""

    trade_date: ExchangeDate = Field(default=None, validation_alias="chg_date")
    """Trading day."""
    institution_num: WholeNumber = Field(default=None, validation_alias="holder_num")
    """Institutions holding the stock."""
    held_ratio: Number = Field(default=None, validation_alias="org_postion_calc")
    """Their share of the shares, in percent points; the upstream calls it calculated."""
    net_trade_vol: Number = None
    """Shares the institutions bought on the day, negative when they sold. Within 1% of the change
    of ``held_ratio`` times the shares outstanding, measured 2026-11-04."""
    price: Number = None
    """Close, in USD."""


class OrgHoldingChange(UpstreamModel):
    """Rows of one stock: ``periods`` for an A-share, ``days`` for a US stock."""

    symbol: str
    """The requested symbol."""
    periods: tuple[OrgHoldingPeriod, ...] = ()
    days: tuple[OrgHoldingDay, ...] = ()

    def header(self) -> Mapping[str, Any]:
        return {}

    def sections(self) -> Mapping[str, Sequence[BaseModel]]:
        # The other table depends on the market, so it is not reported as empty.
        if normalize_symbol(self.symbol).market == "US":
            return {"days": self.days}
        return {"periods": self.periods}

    def __len__(self) -> int:
        return len(self.periods) + len(self.days)


class _Periods(UpstreamModel):
    items: tuple[OrgHoldingPeriod, ...]


class _Days(UpstreamModel):
    items: tuple[OrgHoldingDay, ...]


def _parse(payload: Any, params: SymbolParams) -> OrgHoldingChange:
    symbol = normalize_symbol(params.symbol)
    context = {"tz": symbol.timezone}
    if symbol.market == "US":
        days = _Days.model_validate(payload, context=context).items
        return OrgHoldingChange(symbol=params.symbol, days=days)
    periods = _Periods.model_validate(payload, context=context).items
    return OrgHoldingChange(symbol=params.symbol, periods=periods)


ENDPOINT = Endpoint(
    name="f10_org_holding_change",
    doc="Institutional ownership of one stock, by report period for A-shares and by day for US.",
    host="stock.xueqiu.com",
    path="/v5/stock/f10/{region}/org_holding/change.json",
    params=SymbolParams,
    build_query=region_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="xueqiu",
    verified_on=date(2026, 11, 5),
    # The hk path answers HTTP 404, measured 2026-11-04.
    markets=("CN", "US"),
    order="newest_first",
    agent=AgentInfo(
        category="f10",
        group="xq_company",
        section="org_holding_change",
        title_zh="机构持仓变动",
        title_en="Institutional ownership changes",
        summary=(
            "取 A 股各报告期或美股逐日的机构持股比例、持股机构数和价格，"
            "A 股另有持股比例的变化，美股另有机构净买卖股数。"
        ),
        keywords=(
            "机构持仓",
            "机构持股",
            "持股机构数",
            "机构持股比例",
            "持仓变动",
            "institutional ownership",
            "institutional holdings",
            "institution count",
        ),
        default_fields=(
            "report_name",
            "report_date",
            "trade_date",
            "institution_num",
            "held_ratio",
            "held_ratio_chg_pct",
            "net_trade_vol",
            "price",
        ),
        row_default=10,
        row_cap=10,
        upstream_cap=10,
        examples=({"symbol": "SH600000"},),
    ),
)
