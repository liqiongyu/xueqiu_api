"""Params, queries and validation shared by the capital flow endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .symbols import SymbolCode, SymbolParams, normalize_symbol

M = TypeVar("M", bound=BaseModel)

PAGE_SIZE_CAP = 100
"""Rows per page: size=100 gave full pages of block trades, measured 2026-11-05."""

HISTORY_DAYS = 20
"""Rows of the two daily series. A request for 3 returned 20, measured 2026-11-04, and other
counts were not tried, so callers do not choose it."""


class CapitalPageParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    symbol: SymbolCode
    """An A-share symbol."""
    page: int = Field(default=1, ge=1)
    """Page number, from 1; page 1 holds the latest rows."""
    page_size: int = Field(default=50, ge=1, le=PAGE_SIZE_CAP)
    """Rows per page."""


def page_query(params: CapitalPageParams, now: datetime) -> dict[str, str]:
    return {"symbol": params.symbol, "page": str(params.page), "size": str(params.page_size)}


def symbol_query(params: SymbolParams, now: datetime) -> dict[str, str]:
    return {"symbol": params.symbol}


def history_query(params: SymbolParams, now: datetime) -> dict[str, str]:
    return {"symbol": params.symbol, "count": str(HISTORY_DAYS)}


def validate_for(model: type[M], payload: Any, symbol: str) -> M:
    """``payload`` as ``model``, with the symbol asked for and its exchange timezone as context."""
    return model.model_validate(
        {**payload, "symbol": symbol}, context={"tz": normalize_symbol(symbol).timezone}
    )
