"""capital_flow: net inflow of one stock by the minute through one session, kept columnar."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .._capital import symbol_query
from .._model import UpstreamModel
from .._result import Fetched
from .._time import from_ms
from .._values import Number
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolParams, normalize_symbol


@dataclass(frozen=True, slots=True)
class CapitalFlow(Fetched):
    """One session of net inflow in columns, one point a minute, oldest first.

    ``columns`` holds ``net_inflow`` (upstream ``amount``, in the quote currency) and every other
    upstream key under its own name, such as ``type``, null wherever it was measured. Full days
    measured 2026-11-04: 242 points to 15:00 for SH600000 and 332 to 16:00 for 00700.

    Not measured: whether the value counts main-force orders only and whether it is cumulative
    since the open, though its size in the first minutes suggests both; and what comes back
    before the open or on a day without trading.
    """

    symbol: str
    timestamps: tuple[datetime, ...]
    """Times of the points, timezone-aware in the exchange timezone."""
    columns: Mapping[str, tuple[float | None, ...]]

    def index(self) -> tuple[str, tuple[datetime, ...]]:
        return "timestamp", self.timestamps

    def header(self) -> Mapping[str, Any]:
        return {}

    def __len__(self) -> int:
        return len(self.timestamps)


class _Point(UpstreamModel):
    timestamp: int
    amount: Number = None


class _Payload(UpstreamModel):
    items: tuple[_Point, ...]


def _parse(payload: Any, params: SymbolParams) -> CapitalFlow:
    points = _Payload.model_validate(payload).items
    tz = normalize_symbol(params.symbol).timezone
    others = dict.fromkeys(key for point in points for key in point.model_extra or {})
    return CapitalFlow(
        symbol=params.symbol,
        timestamps=tuple(from_ms(point.timestamp, tz) for point in points),
        columns={
            "net_inflow": tuple(point.amount for point in points),
            **{
                key: tuple((point.model_extra or {}).get(key) for point in points) for key in others
            },
        },
    )


ENDPOINT = Endpoint(
    name="capital_flow",
    doc="Net inflow of one A-share or Hong Kong stock by the minute through one session.",
    host="stock.xueqiu.com",
    path="/v5/stock/capital/flow.json",
    params=SymbolParams,
    build_query=symbol_query,
    parse=_parse,
    auth="anonymous",
    freshness="intraday",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    markets=("CN", "HK"),
    order="oldest_first",
    agent=AgentInfo(
        category="capital",
        group="xq_capital",
        section="flow",
        title_zh="分时资金流向",
        title_en="Intraday capital flow",
        summary="取一只 A 股或港股当天按分钟的主力资金净流入序列，A 股到 15:00，港股到 16:00。",
        keywords=(
            "资金流向",
            "主力资金",
            "主力净流入",
            "分时资金",
            "资金流入",
            "money flow",
            "capital flow",
            "net inflow",
            "intraday",
        ),
        default_fields=("timestamp", "net_inflow"),
        row_default=100,
        row_cap=500,
        upstream_cap=332,
        examples=({"symbol": "SH600000"},),
        precision=2,
    ),
)
