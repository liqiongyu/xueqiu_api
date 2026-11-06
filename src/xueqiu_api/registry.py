"""Declarative endpoint specs.

Each module in ``xueqiu_api.endpoints`` defines one ``Endpoint`` named ``ENDPOINT``; the
package collects them into ``xueqiu_api.endpoints.REGISTRY``.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, Generic, Literal, TypeVar, get_args, get_origin

from pydantic import BaseModel, ValidationError

from .errors import InvalidParams
from .sites import Host
from .symbols import Market, normalize_symbol

Auth = Literal["anonymous", "optional", "required"]
Freshness = Literal["realtime", "intraday", "daily", "static"]
Envelope = Literal["xueqiu", "query", "danjuan", "bare"]
"""``xueqiu``: ``{"data": ..., "error_code": 0, "error_description": ...}``.

``query`` (most of ``/query/v1/`` on www.xueqiu.com):
``{"code": 200, "success": true, "data": ...}``.

``danjuan``: ``{"data": ..., "result_code": 0}``; ``result_code`` is 0 or ``"0"``.

``bare``: the body itself is the payload. Upstream error codes are still recognized.
"""
Category = Literal[
    "quote",
    "kline",
    "capital",
    "f10",
    "finance",
    "screener",
    "market",
    "search",
    "index",
    "fund",
    "cube",
    "watchlist",
]
"""Endpoint groups in display order; an endpoint name starts with its category."""
CATEGORY_TITLES: Mapping[Category, str] = MappingProxyType(
    {
        "quote": "行情",
        "kline": "K线",
        "capital": "资金流向",
        "f10": "F10",
        "finance": "财报",
        "screener": "选股器",
        "market": "市场概览",
        "search": "搜索",
        "index": "指数估值",
        "fund": "基金",
        "cube": "组合",
        "watchlist": "自选股",
    }
)
RowOrder = Literal["oldest_first", "newest_first", "rank"]
"""Order of the rows of a result: by time, or as the upstream ranks them."""

P = TypeVar("P", bound=BaseModel)
R = TypeVar("R")


@dataclass(frozen=True, kw_only=True)
class Pagination:
    """How the rows of an endpoint are reached beyond one request."""

    style: Literal["page", "latest", "bars"]
    """``page``: numbered pages. ``latest``: the most recent ``count`` rows, with no way to older
    ones. ``bars``: candlestick windows that page back in time."""
    end: Literal["total", "short", "empty"] = "empty"
    """What ends the walk: the upstream's total, a short page, or an empty page."""
    size_cap: int | None = None
    """Largest page size the upstream honours."""
    total_cap: int | None = None
    """Most rows the upstream serves across all pages."""
    key: Callable[[Any], Hashable] | None = None
    """Identity of a row, to drop rows repeated across pages."""
    guard_short: bool = True
    """Whether rows after a short page mean the upstream ignored the page size."""
    cap_hint: str = ""
    """How to get all rows when ``total_cap`` is reached."""


@dataclass(frozen=True, kw_only=True)
class NotFound:
    """Upstream errors of an endpoint that mean the requested code does not exist."""

    statuses: tuple[int, ...] = ()
    """HTTP statuses that mean it when the body carries no upstream error code."""
    codes: tuple[str, ...] = ()
    """Upstream error codes that mean it, at any HTTP status."""


@dataclass(frozen=True, kw_only=True)
class AgentInfo:
    """What the MCP server and the CLI need to present an endpoint."""

    category: Category
    group: str | None = None
    """Grouped MCP tool of this endpoint, if any; set together with ``section``."""
    section: str | None = None
    """Value of the grouped tool's ``section``, unique within the group."""
    title_zh: str
    title_en: str
    summary: str
    """One Chinese sentence."""
    keywords: tuple[str, ...]
    """Chinese and English search terms."""
    default_fields: tuple[str, ...]
    """Fields rendered when the caller does not choose any."""
    market_fields: Mapping[Market, tuple[str, ...]] | None = None
    """Fields rendered in place of ``default_fields`` for a symbol of that market, whose upstream
    keys differ."""
    row_default: int
    row_cap: int
    upstream_cap: int
    """Most items one upstream request accepts or returns."""
    examples: tuple[Mapping[str, Any], ...]
    """Parameter sets that validate against the params model."""
    mcp_tool: str | None = None
    """Dedicated MCP tool, if any; every endpoint is also reachable through ``xq_call``."""
    precision: int = 4
    """Decimal places of rendered floats; trailing zeros are dropped."""
    free_text: tuple[str, ...] = ()
    """Fields holding upstream prose: never rendered by default, cleaned and fenced on request."""
    labels: tuple[str, ...] = ()
    """Short text fields written by an owner, such as a portfolio name."""
    empty_hint: str | None = None
    """What an empty result means, when that is not obvious."""


@dataclass(frozen=True, kw_only=True)
class Endpoint(Generic[P, R]):
    """One upstream endpoint.

    ``build_query`` maps validated params and the current time to upstream query values; values
    whose names appear as ``{name}`` in ``path`` fill the path template instead. ``parse`` turns
    the unwrapped envelope payload into the result and raises ``ValueError`` or ``TypeError`` on
    a shape mismatch, or ``SymbolNotFound`` on the shape an unknown code gets.
    """

    name: str
    doc: str
    """One English sentence for the method's docstring."""
    host: Host
    path: str
    params: type[P]
    build_query: Callable[[P, datetime], dict[str, str]]
    parse: Callable[[Any, P], R]
    auth: Auth
    freshness: Freshness
    envelope: Envelope
    verified_on: date | None
    """Date of the last live check; None when no real response was ever seen."""
    agent: AgentInfo
    cookies: bool = True
    """False when the endpoint was measured to work without cookies; then none are sent."""
    pagination: Pagination | None = None
    markets: tuple[Market, ...] | None = None
    """Markets measured to work; a ``symbol`` param of any other market is rejected before a
    request. None when the endpoint does not depend on a market."""
    order: RowOrder | None = None
    needs_cookies: tuple[str, ...] = ()
    """Cookies without which the upstream answers wrongly instead of with an error."""
    not_found: NotFound | None = None
    freshness_of: Callable[[P, datetime], Freshness] | None = None
    """The freshness of a result from its params and the moment it was fetched, for an endpoint
    where they matter; None means ``freshness`` always holds."""

    def validate(self, params: Mapping[str, Any]) -> P:
        """Validate caller params; raise ``InvalidParams`` listing every problem."""
        try:
            validated = self.params.model_validate(dict(params))
        except ValidationError as exc:
            raise invalid_params(
                self.params,
                exc,
                name=self.name,
                endpoint=self.name,
                params={key: value for key, value in params.items() if value is not None},
            ) from None
        self._check_market(validated)
        self._check_total(validated)
        return validated

    def _check_market(self, validated: P) -> None:
        symbol = getattr(validated, "symbol", None)
        if self.markets is None or not isinstance(symbol, str):
            return
        market = normalize_symbol(symbol).market
        if market not in self.markets:
            allowed = ", ".join(self.markets)
            raise InvalidParams(
                f"{self.name} has no data for {symbol}, a {market} symbol",
                endpoint=self.name,
                params={"symbol": symbol},
                field="symbol",
                valid_values=self.markets,
                hint=f"Use a symbol of these markets: {allowed}.",
            )

    def _check_total(self, validated: P) -> None:
        """Reject a page that starts past the most rows the upstream serves across all pages."""
        rule = self.pagination
        if rule is None or rule.total_cap is None:
            return
        page: int = getattr(validated, "page", 1)
        page_size: int = getattr(validated, "page_size", rule.size_cap or 0)
        if (page - 1) * page_size >= rule.total_cap:
            raise InvalidParams(
                f"{self.name} serves at most {rule.total_cap} rows; page {page} of {page_size} "
                "starts after them",
                endpoint=self.name,
                params=validated.model_dump(mode="json", exclude_none=True),
                field="page",
                hint=rule.cap_hint,
            )


def invalid_params(
    model: type[BaseModel],
    exc: ValidationError,
    *,
    name: str,
    endpoint: str | None,
    more: str = "",
    params: Mapping[str, Any] | None = None,
) -> InvalidParams:
    """``InvalidParams`` for a failed validation of ``model``, the params of ``name``.

    It names the first rejected field, the allowed values of a Literal and the closest of them;
    unknown parameter names get the list of known ones. The hint is the signature, followed by
    ``more``.
    """
    errors = exc.errors()
    hint = f"Parameters: {signature(name, model)}.{more}"
    unknown = [str(e["loc"][0])[:60] for e in errors if e["type"] == "extra_forbidden" and e["loc"]]
    keys = list(model.model_fields)
    if unknown:
        return InvalidParams(
            f"unknown parameter {', '.join(unknown)} for {name}",
            endpoint=endpoint,
            params=params,
            field=unknown[0],
            valid_values=keys,
            candidates=difflib.get_close_matches(unknown[0], keys, n=3),
            hint=hint,
        )
    first = errors[0]
    key = str(first["loc"][0]) if first["loc"] else ""
    valid = _literal_values(model, key)
    value = first.get("input")
    problems = "; ".join(
        f"{'.'.join(str(part) for part in e['loc']) or 'arguments'}: {e['msg']}" for e in errors
    )
    return InvalidParams(
        f"invalid parameters for {name}: {problems}",
        endpoint=endpoint,
        params=params,
        field=".".join(str(part) for part in first["loc"]) or None,
        valid_values=valid,
        candidates=difflib.get_close_matches(value, valid, n=3) if isinstance(value, str) else (),
        hint=hint,
    )


def signature(name: str, model: type[BaseModel]) -> str:
    """Compact call signature, such as ``kline(symbol, period='day', count=null)``."""
    parts = [
        key
        if field.is_required()
        else f"{key}={_default(field.get_default(call_default_factory=True))}"
        for key, field in model.model_fields.items()
    ]
    return f"{name}({', '.join(parts)})"


def _literal_values(model: type[BaseModel], key: str) -> list[str]:
    field = model.model_fields.get(key)
    if field is None:
        return []
    for option in (field.annotation, *get_args(field.annotation)):
        if get_origin(option) is Literal:
            return [str(value) for value in get_args(option)]
    return []


def _default(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return repr(value)
    return str(value)
