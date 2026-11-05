"""f10_executive_trades: share changes of the executives of one A-share (upstream skholderchg)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .._f10 import ExchangeDate
from .._model import UpstreamModel
from .._values import Number, WholeNumber
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolParams, normalize_symbol


class ExecutiveTrade(UpstreamModel):
    """One change in the holding of a director, supervisor or executive."""

    chg_date: ExchangeDate
    """Date of the change; one day can have several rows."""
    manage_name: str
    """Name of the person."""
    chg_shares_num: WholeNumber = None
    """Shares changed; positive is an increase. No decrease has been seen, so its sign is
    unconfirmed."""
    trans_avg_price: Number = None
    """Average trade price, in CNY per share."""


class _Payload(UpstreamModel):
    items: tuple[ExecutiveTrade, ...]


def _build_query(params: SymbolParams, now: datetime) -> dict[str, str]:
    return {"symbol": params.symbol}


def _parse(payload: Any, params: SymbolParams) -> list[ExecutiveTrade]:
    tz = normalize_symbol(params.symbol).timezone
    return list(_Payload.model_validate(payload, context={"tz": tz}).items)


ENDPOINT = Endpoint(
    name="f10_executive_trades",
    doc="Share changes of the directors, supervisors and executives of one A-share, latest first.",
    host="stock.xueqiu.com",
    path="/v5/stock/f10/cn/skholderchg.json",
    params=SymbolParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    markets=("CN",),
    order="newest_first",
    agent=AgentInfo(
        category="f10",
        group="xq_company",
        section="executive_trades",
        title_zh="高管增减持",
        title_en="Executive share trades",
        summary="取一只 A 股董监高的持股变动记录，包括日期、姓名、变动股数和成交均价，最新的在前。",
        keywords=(
            "高管增减持",
            "董监高持股变动",
            "高管增持",
            "高管减持",
            "增持",
            "减持",
            "持股变动",
            "executive trades",
            "insider holdings",
            "director dealings",
        ),
        default_fields=("chg_date", "manage_name", "chg_shares_num", "trans_avg_price"),
        row_default=50,
        row_cap=100,
        upstream_cap=100,
        examples=({"symbol": "SH600000"},),
        empty_hint="no rows: no share change of an executive is on record for this stock.",
    ),
)
