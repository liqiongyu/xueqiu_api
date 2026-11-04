"""Declarative endpoint specs.

Each module in ``xueqiu_api.endpoints`` defines one ``Endpoint`` named ``ENDPOINT``; the
package collects them into ``xueqiu_api.endpoints.REGISTRY``.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar, get_args, get_origin

from pydantic import BaseModel, ValidationError

from .errors import InvalidParams
from .sites import Host

Auth = Literal["anonymous", "optional", "required"]
Freshness = Literal["realtime", "intraday", "daily", "static"]
Envelope = Literal["xueqiu", "query"]
"""``xueqiu``: ``{"data": ..., "error_code": 0, "error_description": ...}``.

``query`` (``/query/v1/`` on www.xueqiu.com): ``{"code": 200, "success": true, "data": ...}``.
"""

P = TypeVar("P", bound=BaseModel)
R = TypeVar("R")


@dataclass(frozen=True, kw_only=True)
class AgentInfo:
    """What the MCP server and the CLI need to present an endpoint."""

    title_zh: str
    title_en: str
    summary: str
    """One Chinese sentence."""
    keywords: tuple[str, ...]
    """Chinese and English search terms."""
    default_fields: tuple[str, ...]
    """Fields rendered when the caller does not choose any."""
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


@dataclass(frozen=True, kw_only=True)
class Endpoint(Generic[P, R]):
    """One upstream endpoint.

    ``build_query`` maps validated params and the current time to upstream query values; values
    whose names appear as ``{name}`` in ``path`` fill the path template instead. ``parse`` turns
    the unwrapped envelope payload into the result and raises ``ValueError`` or ``TypeError`` on
    a shape mismatch.
    """

    name: str
    host: Host
    path: str
    params: type[P]
    build_query: Callable[[P, datetime], dict[str, str]]
    parse: Callable[[Any, P], R]
    auth: Auth
    freshness: Freshness
    envelope: Envelope
    verified_on: date
    agent: AgentInfo
    cookies: bool = True
    """False when the endpoint was measured to work without cookies; then none are sent."""

    def validate(self, params: Mapping[str, Any]) -> P:
        """Validate caller params; raise ``InvalidParams`` listing every problem."""
        try:
            return self.params.model_validate(dict(params))
        except ValidationError as exc:
            raise invalid_params(
                self.params,
                exc,
                name=self.name,
                endpoint=self.name,
                params={key: value for key, value in params.items() if value is not None},
            ) from None


def invalid_params(
    model: type[BaseModel],
    exc: ValidationError,
    *,
    name: str,
    endpoint: str | None,
    choices: Mapping[str, Sequence[str]] | None = None,
    more: str = "",
    params: Mapping[str, Any] | None = None,
) -> InvalidParams:
    """``InvalidParams`` for a failed validation of ``model``, the params of ``name``.

    It names the first rejected field, its allowed values (from ``choices`` or a Literal) and
    the closest of them; unknown parameter names get the list of known ones. The hint is the
    signature, followed by ``more``.
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
    valid = list((choices or {}).get(key) or _literal_values(model, key))
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
