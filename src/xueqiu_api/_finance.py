"""Params, result model and spec factory shared by the four finance statement endpoints."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, Literal, NamedTuple, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ._time import from_ms
from .registry import AgentInfo, Endpoint
from .symbols import SymbolCode, normalize_symbol

ReportType = Literal["all", "annual"]
"""Report periods: ``all`` (quarterly, half-year and annual reports) or ``annual`` only."""
_UPSTREAM_TYPES: Mapping[ReportType, str] = MappingProxyType({"all": "all", "annual": "Q4"})

DEFAULT_COUNT = 5
MAX_COUNT = 20
"""Most report periods per request. Not measured: the survey sent 2."""

REPORT_DATE_ZONE = ZoneInfo("Asia/Shanghai")
"""The upstream encodes report dates as midnight in Asia/Shanghai, for US companies too."""


class FinanceParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    symbol: SymbolCode
    """A-share, Hong Kong or US stock; the request goes to the region of its market."""
    type: ReportType = "all"
    is_detail: bool = True
    """Every line item, or only the upstream's short list (8 items for A-share indicators)."""
    count: int = Field(default=DEFAULT_COUNT, ge=1, le=MAX_COUNT)
    """Report periods to fetch, latest first."""


class LineItem(NamedTuple):
    """One line item of a report period, from the upstream ``[value, yoy]`` pair."""

    value: float | None
    """Reported value. Amounts are in the statement currency; ratios keep the upstream unit,
    which differs between items."""
    yoy: float | None
    """Change against the same period a year earlier, in percent points (1.23 means +1.23%):
    the upstream fraction times 100."""


@dataclass(frozen=True, slots=True)
class ReportPeriod:
    report_date: date
    """Period end date. Read in Asia/Shanghai for every market: AAPL's fiscal year ending
    2026-09-26 arrives as 2026-09-26T00:00+08:00, which is still 09-25 in New York."""
    report_name: str
    """Upstream period name, such as 2026三季报, 2026中报 or 2026年FY."""
    items: Mapping[str, LineItem]
    """Line items under their upstream names, which differ between A shares, Hong Kong and US."""
    extra: Mapping[str, Any]
    """Other upstream fields as sent, such as ``ctime``, or ``sd`` and ``ed`` (period start and
    end dates) for Hong Kong and US."""


@dataclass(frozen=True, slots=True)
class Financials:
    """Report periods of one statement of one company, latest first."""

    symbol: str
    name: str | None
    """Security name (upstream ``quote_name``)."""
    currency: str | None
    """Currency of the amounts, such as CNY or USD; it can differ from the quote currency."""
    periods: tuple[ReportPeriod, ...]
    extra: Mapping[str, Any]
    """Other upstream fields as sent, such as ``last_report_name`` and ``org_type``, and for
    Hong Kong and US ``sas`` (accounting standard) and ``annual_settle_date``."""

    def __len__(self) -> int:
        return len(self.periods)


_TEXT = TypeAdapter(str | None)
_PAIRS = TypeAdapter(dict[str, tuple[float | None, float | None]])
_PERIOD_KEYS = frozenset({"report_date", "report_name"})
_HEADER_KEYS = frozenset({"quote_name", "currency", "list"})


def _build_query(params: FinanceParams, now: datetime) -> dict[str, str]:
    return {
        "region": normalize_symbol(params.symbol).region,
        "symbol": params.symbol,
        "type": _UPSTREAM_TYPES[params.type],
        "is_detail": "true" if params.is_detail else "false",
        "count": str(params.count),
    }


def _parse(payload: Any, params: FinanceParams) -> Financials:
    data = cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
    rows: Any = data.get("list")
    if not isinstance(rows, list):
        raise ValueError("finance data needs a list of report periods")
    return Financials(
        symbol=params.symbol,
        name=_TEXT.validate_python(data.get("quote_name")),
        currency=_TEXT.validate_python(data.get("currency")),
        periods=tuple(_period(row) for row in cast(list[Any], rows)),
        extra={key: value for key, value in data.items() if key not in _HEADER_KEYS},
    )


def _period(row: Any) -> ReportPeriod:
    if not isinstance(row, dict):
        raise ValueError("a report period is not an object")
    fields = cast(dict[str, Any], row)
    name = fields.get("report_name")
    if not isinstance(name, str):
        raise ValueError("a report period needs a report_name")
    pairs = _PAIRS.validate_python({k: v for k, v in fields.items() if isinstance(v, list)})
    return ReportPeriod(
        report_date=from_ms(fields.get("report_date"), REPORT_DATE_ZONE).date(),
        report_name=name,
        items={
            key: LineItem(value, None if yoy is None else yoy * 100)
            for key, (value, yoy) in pairs.items()
        },
        extra={
            key: value
            for key, value in fields.items()
            if key not in _PERIOD_KEYS and not isinstance(value, list)
        },
    )


def finance_endpoint(statement: str, *, agent: AgentInfo) -> Endpoint[FinanceParams, Financials]:
    """Spec of ``/v5/stock/finance/{region}/<statement>.json``, named ``finance_<statement>``."""
    return Endpoint(
        name=f"finance_{statement}",
        host="stock.xueqiu.com",
        path=f"/v5/stock/finance/{{region}}/{statement}.json",
        params=FinanceParams,
        build_query=_build_query,
        parse=_parse,
        auth="anonymous",
        freshness="daily",
        envelope="xueqiu",
        verified_on=date(2026, 11, 4),
        agent=agent,
    )
