"""quote_minute: intraday points of one symbol over the latest 1 or 5 sessions, kept columnar."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, cast

from .._model import UpstreamModel
from .._result import Fetched
from .._time import from_ms
from .._values import Number, number
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolParams, normalize_symbol

MinuteDays = Literal[1, 5]
"""Sessions covered; the only values measured, as period=1d and period=5d on 2026-11-04."""


class QuoteMinuteParams(SymbolParams):
    days: MinuteDays = 1
    """1: the latest session, a point a minute. 5: the latest 5, a point every 10 minutes."""


@dataclass(frozen=True, slots=True)
class MinuteChart(Fetched):
    """Intraday points of one symbol in columns, oldest first.

    ``columns`` holds every upstream value that was not null anywhere, under its upstream name, a
    nested one as ``parent.child``:

    - ``current``, ``avg_price``, ``high``, ``low``, ``chg``: quote currency. ``chg`` and
      ``percent`` (percent points) are measured from ``last_close``.
    - ``volume`` (shares), ``amount`` (quote currency): of that minute with ``days=1``. With
      ``days=5`` they do not match the differences of the totals, measured 2026-11-04.
    - ``volume_total`` (shares), ``amount_total`` (quote currency): since the open of the session.
    - ``avg_price`` is the upstream's own average: it equals ``amount_total / volume_total`` for
      A shares only, measured 2026-11-04.
    - ``capital.small``, ``.medium``, ``.large``, ``.xlarge``: A shares with ``days=1`` only. The
      four sum to zero; the unit is not known.
    - ``volume_compare.volume_sum``, ``.volume_sum_last``: with ``days=1``, ``volume_sum`` equals
      ``volume_total``; with ``days=5`` the two do not match any total.
    """

    symbol: str
    days: MinuteDays
    last_close: float | None
    """Close before the first session covered, in the quote currency."""
    timestamps: tuple[datetime, ...]
    """Point times, timezone-aware in the exchange timezone."""
    columns: Mapping[str, tuple[float | None, ...]]
    extra: Mapping[str, Any]
    """Other upstream values of the response, such as ``after``, empty whenever measured."""

    def index(self) -> tuple[str, tuple[datetime, ...]]:
        return "timestamp", self.timestamps

    def header(self) -> Mapping[str, Any]:
        return {} if self.last_close is None else {"last_close": self.last_close}

    def __len__(self) -> int:
        return len(self.timestamps)


class _Payload(UpstreamModel):
    last_close: Number = None
    items: tuple[dict[str, Any], ...]


def _flatten(point: Mapping[str, Any]) -> dict[str, Any]:
    """The values of a point that are not null, nested ones as ``parent.child``."""
    cells: dict[str, Any] = {}
    for key, value in point.items():
        if isinstance(value, dict):
            nested = cast(dict[str, Any], value)
            cells.update(
                {f"{key}.{name}": cell for name, cell in nested.items() if cell is not None}
            )
        elif value is not None:
            cells[key] = value
    return cells


def _build_query(params: QuoteMinuteParams, now: datetime) -> dict[str, str]:
    return {"symbol": params.symbol, "period": f"{params.days}d"}


def _parse(payload: Any, params: QuoteMinuteParams) -> MinuteChart:
    data = _Payload.model_validate(payload)
    tz = normalize_symbol(params.symbol).timezone
    points = [_flatten(item) for item in data.items]
    timestamps = tuple(from_ms(point.pop("timestamp", None), tz) for point in points)
    names = dict.fromkeys(name for point in points for name in point)
    return MinuteChart(
        symbol=params.symbol,
        days=params.days,
        last_close=data.last_close,
        timestamps=timestamps,
        columns={name: tuple(number(point.get(name)) for point in points) for name in names},
        extra=data.model_extra or {},
    )


ENDPOINT = Endpoint(
    name="quote_minute",
    doc=(
        "Intraday points of one symbol over the latest 1 or 5 sessions, oldest first; bars older "
        "than that are in ``kline``."
    ),
    host="stock.xueqiu.com",
    path="/v5/stock/chart/minute.json",
    params=QuoteMinuteParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="realtime",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    markets=("CN", "HK", "US"),
    order="oldest_first",
    agent=AgentInfo(
        category="quote",
        group="xq_intraday",
        section="minute",
        title_zh="分时",
        title_en="Intraday minute chart",
        summary="取一只股票最近 1 个或 5 个交易日的分时走势，含价格、均价、成交量和成交额。",
        keywords=(
            "分时",
            "分时图",
            "分时走势",
            "当日走势",
            "均价",
            "intraday",
            "minute chart",
            "timeline",
        ),
        default_fields=("timestamp", "current", "avg_price", "percent", "volume", "amount"),
        row_default=100,
        row_cap=500,
        upstream_cap=400,
        examples=({"symbol": "SH600000"},),
    ),
)
