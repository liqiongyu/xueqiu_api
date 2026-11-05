"""capital_history: main-force net inflow of one A-share per day, with its running sums."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .._capital import HISTORY_DAYS, history_query, validate_for
from .._f10 import ExchangeDate
from .._model import UpstreamModel
from .._values import Number
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolParams


class CapitalHistoryDay(UpstreamModel):
    """Main-force net inflow of one trading day."""

    trade_date: ExchangeDate = Field(validation_alias="timestamp")
    """Trading day."""
    net_inflow: Number = Field(default=None, validation_alias="amount")
    """Main-force net inflow, in CNY: the extra-large and large order nets of
    ``capital_history_detail`` added up."""


class CapitalHistory(UpstreamModel):
    """The latest 20 trading days of one A-share, oldest first, and the sums of the latest ones.

    ``sum3`` equalled the latest three days of ``capital_history_detail`` added up, to the cent,
    measured 2026-11-04; the longer sums are taken to work the same way.
    """

    symbol: str
    sum3: Number = None
    """Sum of ``net_inflow`` over the latest 3 trading days, in CNY."""
    sum5: Number = None
    """The same over 5 trading days."""
    sum10: Number = None
    """The same over 10 trading days."""
    sum20: Number = None
    """The same over 20 trading days."""
    items: tuple[CapitalHistoryDay, ...]

    @field_validator("items")
    @classmethod
    def _oldest_first(cls, rows: tuple[CapitalHistoryDay, ...]) -> tuple[CapitalHistoryDay, ...]:
        return tuple(sorted(rows, key=lambda row: row.trade_date or date.min))

    def header(self) -> Mapping[str, Any]:
        return {"sum3": self.sum3, "sum5": self.sum5, "sum10": self.sum10, "sum20": self.sum20}

    def sections(self) -> Mapping[str, Sequence[BaseModel]]:
        return {"items": self.items}

    def __len__(self) -> int:
        return len(self.items)


def _parse(payload: Any, params: SymbolParams) -> CapitalHistory:
    return validate_for(CapitalHistory, payload, params.symbol)


ENDPOINT = Endpoint(
    name="capital_history",
    doc="Main-force net inflow of one A-share per day for 20 trading days, with running sums.",
    host="stock.xueqiu.com",
    path="/v5/stock/capital/history.json",
    params=SymbolParams,
    build_query=history_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    markets=("CN",),
    order="oldest_first",
    agent=AgentInfo(
        category="capital",
        group="xq_capital",
        section="history",
        title_zh="历史资金流向",
        title_en="Capital flow history",
        summary="取一只 A 股最近 20 个交易日每天的主力资金净流入，以及近 3、5、10、20 日的合计。",
        keywords=(
            "历史资金流向",
            "近20日主力资金",
            "主力净流入合计",
            "资金流出",
            "capital flow history",
            "money flow history",
            "net inflow",
        ),
        default_fields=("trade_date", "net_inflow"),
        row_default=HISTORY_DAYS,
        row_cap=HISTORY_DAYS,
        upstream_cap=HISTORY_DAYS,
        examples=({"symbol": "SH600000"},),
        precision=2,
    ),
)
