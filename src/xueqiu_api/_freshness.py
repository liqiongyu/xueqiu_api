"""Cache keys, the stored form of a body, and how long a stored result stays fresh."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel

from . import _model
from .registry import Endpoint
from .symbols import normalize_symbol
from .trading_calendar import SESSION_HOURS, in_session

P = TypeVar("P", bound=BaseModel)

CACHE_FORMAT = 1
"""Increase it when the stored form changes, or what the params of an endpoint mean."""
MAX_ENTRY_BYTES = 8 * 2**20
"""Bodies larger than this in their stored form are not stored."""
INTRADAY_SESSION_S = 60
"""Freshness of an intraday result fetched or read while its market is in session."""
INTRADAY_CLOSED_S = 900
"""Freshness of an intraday result otherwise."""
DAILY_S = 21600
STATIC_S = 86400


def _format() -> str:
    """``CACHE_FORMAT`` and the first 8 hex digits of the sha256 of ``USER_KEY``'s pattern.

    The pattern decides the stored bytes, so changing it retires every entry. It is read at
    call time.
    """
    digest = hashlib.sha256(_model.USER_KEY.pattern.encode()).hexdigest()[:8]
    return f"{CACHE_FORMAT}:{digest}"


def cache_key(endpoint: str, params: Mapping[str, Any], who: str) -> str:
    """The key of one result: the sha256 of the format, ``endpoint``, the validated params as
    JSON and ``who``, the identity the request is sent as, in compact JSON with sorted keys."""
    text = json.dumps(
        [_format(), endpoint, params, who],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(text.encode()).hexdigest()


def encode_body(body: Any) -> bytes:
    """The stored form of an upstream body: compact JSON without the keys matching
    ``USER_KEY``, at every depth."""
    return json.dumps(_model.without_keys(body), ensure_ascii=False, separators=(",", ":")).encode()


def ttl_s(spec: Endpoint[P, Any], params: P, fetched_at: datetime, now: datetime) -> float | None:
    """Seconds a result fetched at ``fetched_at`` stays fresh, read at ``now``; None for
    realtime results, which are never stored.

    An intraday result gets ``INTRADAY_SESSION_S`` when either moment is in session for the
    market of the params' ``symbol``, or for any market when there is none, so a result
    fetched before the open expires soon after it. ``spec.freshness_of`` overrides the class.
    """
    freshness = (
        spec.freshness if spec.freshness_of is None else spec.freshness_of(params, fetched_at)
    )
    if freshness == "realtime":
        return None
    if freshness == "daily":
        return DAILY_S
    if freshness == "static":
        return STATIC_S
    symbol = getattr(params, "symbol", None)
    markets = (normalize_symbol(symbol).market,) if isinstance(symbol, str) else SESSION_HOURS
    if any(in_session(market, moment) for market in markets for moment in (fetched_at, now)):
        return INTRADAY_SESSION_S
    return INTRADAY_CLOSED_S
