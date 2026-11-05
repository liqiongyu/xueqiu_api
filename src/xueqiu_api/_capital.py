"""Params and query shared by the capital flow endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .symbols import SymbolCode

PAGE_SIZE_CAP = 100
"""Rows per page: size=100 gave full pages of block trades, measured 2026-11-05."""


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
