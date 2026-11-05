"""capital_history_detail: net inflow of one A-share per day, broken down by order size."""

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


class CapitalDetailDay(UpstreamModel):
    """Net inflow of one trading day by order size.

    ``net_inflow`` equalled ``xlarge_net + large_net``, and the four size nets summed to zero, to
    the cent on every row measured 2026-11-04.
    """

    trade_date: ExchangeDate = Field(validation_alias="timestamp")
    """Trading day."""
    close: Number = None
    """Unadjusted close, in CNY."""
    percent: Number = None
    """Change of the day, in percent points (1.23 means 1.23%)."""
    net_inflow: Number = Field(default=None, validation_alias="amount")
    """Main-force net inflow, in CNY."""
    xlarge_net: Number = Field(default=None, validation_alias="xlarge")
    """Net inflow of extra-large orders, in CNY."""
    large_net: Number = Field(default=None, validation_alias="large")
    """Net inflow of large orders, in CNY."""
    medium_net: Number = Field(default=None, validation_alias="medium")
    """Net inflow of medium orders, in CNY."""
    small_net: Number = Field(default=None, validation_alias="small")
    """Net inflow of small orders, in CNY."""


class CapitalHistoryDetail(UpstreamModel):
    """The latest 20 trading days of one A-share by order size, oldest first."""

    symbol: str
    items: tuple[CapitalDetailDay, ...]

    @field_validator("items")
    @classmethod
    def _oldest_first(cls, rows: tuple[CapitalDetailDay, ...]) -> tuple[CapitalDetailDay, ...]:
        return tuple(sorted(rows, key=lambda row: row.trade_date or date.min))

    def header(self) -> Mapping[str, Any]:
        return {}

    def sections(self) -> Mapping[str, Sequence[BaseModel]]:
        return {"items": self.items}

    def __len__(self) -> int:
        return len(self.items)


def _parse(payload: Any, params: SymbolParams) -> CapitalHistoryDetail:
    return validate_for(CapitalHistoryDetail, payload, params.symbol)


ENDPOINT = Endpoint(
    name="capital_history_detail",
    doc="Net inflow of one A-share per day for 20 trading days, broken down by order size.",
    host="stock.xueqiu.com",
    path="/v5/stock/capital/query.json",
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
        section="history_detail",
        title_zh="历史资金流向明细",
        title_en="Daily capital flow by order size",
        summary=(
            "取一只 A 股最近 20 个交易日每天按超大单、大单、中单、小单拆分的资金净流入，"
            "附收盘价和涨跌幅。"
        ),
        keywords=(
            "资金流向明细",
            "超大单",
            "大单",
            "中单",
            "小单",
            "散户资金",
            "主力资金",
            "order size",
            "money flow by order size",
            "daily capital flow",
        ),
        default_fields=(
            "trade_date",
            "close",
            "percent",
            "net_inflow",
            "xlarge_net",
            "large_net",
            "medium_net",
            "small_net",
        ),
        row_default=HISTORY_DAYS,
        row_cap=HISTORY_DAYS,
        upstream_cap=HISTORY_DAYS,
        examples=({"symbol": "SH600000"},),
        precision=2,
    ),
)
