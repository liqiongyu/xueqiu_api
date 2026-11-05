"""quotec: realtime quotes of several symbols in one request."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationInfo,
    field_validator,
)

from .._model import UpstreamModel
from .._time import from_ms
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolCode, exchange_timezone, split_symbols

BATCH_CAP = 50
"""Symbols per request. Not measured: the survey sent 4."""


class QuotecParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    symbols: Annotated[
        tuple[SymbolCode, ...],
        BeforeValidator(split_symbols),
        Field(min_length=1, max_length=BATCH_CAP),
    ]
    """Symbols, as a sequence or one comma-separated string."""


def exchange_time(value: Any, info: ValidationInfo) -> Any:
    """Before-validator body: a millisecond timestamp becomes an aware datetime in the exchange
    timezone of the model's ``symbol``."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return from_ms(value, exchange_timezone(info.data.get("symbol")))
    return value


class Quote(UpstreamModel):
    """Realtime quote of one symbol. Unknown upstream fields are kept in ``model_extra``."""

    symbol: str
    """Xueqiu symbol, such as SH600519, 00700 or AAPL."""
    current: float | None = None
    """Last price, in the quote currency."""
    percent: float | None = None
    """Change from the previous close, in percent points (1.23 means 1.23%)."""
    chg: float | None = None
    """Change from the previous close, in the quote currency."""
    timestamp: datetime | None = None
    """Quote time, timezone-aware in the exchange timezone."""
    volume: float | None = None
    """Volume, in shares for stocks."""
    amount: float | None = None
    """Turnover, in the quote currency."""
    open: float | None = None
    high: float | None = None
    low: float | None = None
    last_close: float | None = None
    """Previous close."""
    avg_price: float | None = None
    """Volume-weighted average price of the session."""
    turnover_rate: float | None = None
    """Turnover rate, in percent points."""
    amplitude: float | None = None
    """High minus low relative to the previous close, in percent points."""
    current_year_percent: float | None = None
    """Change since the start of the year, in percent points."""
    market_capital: float | None = None
    """Market capitalization, in the quote currency."""
    float_market_capital: float | None = None
    """Free-float market capitalization, in the quote currency."""
    is_trade: bool | None = None
    """Whether the symbol was trading at quote time."""

    @field_validator("timestamp", mode="before")
    @classmethod
    def _exchange_time(cls, value: Any, info: ValidationInfo) -> Any:
        return exchange_time(value, info)


_QUOTES = TypeAdapter(list[Quote])


def _build_query(params: QuotecParams, now: datetime) -> dict[str, str]:
    return {"symbol": ",".join(params.symbols)}


def _parse(payload: Any, params: QuotecParams) -> list[Quote]:
    return [] if payload is None else _QUOTES.validate_python(payload)


ENDPOINT = Endpoint(
    name="quotec",
    doc="Realtime quotes of up to 50 symbols in one request; a string may be comma-separated.",
    host="stock.xueqiu.com",
    path="/v5/stock/realtime/quotec.json",
    params=QuotecParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="realtime",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    cookies=False,
    markets=("CN", "HK", "US"),
    agent=AgentInfo(
        category="quote",
        title_zh="实时行情",
        title_en="Realtime quotes",
        summary="一次请求取多只股票或指数的最新价、涨跌幅、成交量和成交额。",
        keywords=(
            "实时行情",
            "最新价",
            "涨跌幅",
            "批量行情",
            "股价",
            "quote",
            "realtime",
            "price",
            "batch",
        ),
        default_fields=("symbol", "current", "percent", "chg", "volume", "amount", "timestamp"),
        row_default=BATCH_CAP,
        row_cap=BATCH_CAP,
        upstream_cap=BATCH_CAP,
        examples=({"symbols": ["SH600000", "00700", "AAPL"]},),
    ),
)
