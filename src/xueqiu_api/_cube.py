"""Params, row fields and page body shared by the portfolio endpoints, and the time type they
share with the watchlist endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Generic, TypeVar
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, BaseModel, BeforeValidator, ConfigDict, Field

from ._model import UpstreamModel
from ._time import from_ms
from ._values import Number, no_data
from .symbols import CubeCode

T = TypeVar("T")

_PLATFORM_ZONE = ZoneInfo("Asia/Shanghai")


def _platform_time(value: Any) -> Any:
    cleaned = no_data(value)
    if cleaned is None:
        return None
    return from_ms(int(cleaned) if isinstance(cleaned, str) else cleaned, _PLATFORM_ZONE)


PlatformTime = Annotated[datetime | None, BeforeValidator(_platform_time)]
"""An account or portfolio event, in Asia/Shanghai whichever securities it concerns. The upstream
sends milliseconds, as a number or a string; an empty string means none, as on an open portfolio
(measured 2026-11-05)."""


class CubeParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    cube_symbol: CubeCode
    """Portfolio code, such as ZH2567925."""


def cube_query(params: CubeParams, now: datetime) -> dict[str, str]:
    return {"cube_symbol": params.cube_symbol}


class CubeFigures(UpstreamModel):
    """Figures of one portfolio, the same in a quote and in a leaderboard row.

    The owner's profile and prose and the requester's state are removed before validation.
    """

    drop = frozenset(
        {
            "description",
            "following",
            "recommend_reason",
            "view_rebalancing",
            "last_rebalancing",
            "last_success_rebalancing",
        }
    )

    symbol: str
    """Portfolio code, such as ZH2567925."""
    name: str | None = None
    """Portfolio name, written by its owner."""
    market: str | None = None
    """Market of the portfolio, cn on all those measured."""
    net_value: Number = None
    """Net value per unit, 1.0 at inception."""
    daily_gain: Number = None
    """Gain of the latest day, in percent points (1.23 means 1.23%)."""
    monthly_gain: Number = None
    """Gain over a trailing month, in percent points: the one value checked, on 2026-11-05,
    matched the net value a month before and differed from the month to date."""
    total_gain: Number = None
    """Gain since inception, in percent points: ``(net_value - 1) * 100``."""
    annualized_gain: Number = Field(
        default=None, validation_alias=AliasChoices("annualized_gain", "annualized_gain_rate")
    )
    """Annualized gain, in percent points."""
    closed_at: PlatformTime = None
    """When the portfolio closed; None while it is open."""


class CubePageBody(BaseModel, Generic[T]):
    """The page object the portfolio lists send as their whole body."""

    items: tuple[T, ...] = Field(validation_alias="list")
    page: int
    page_size: int = Field(validation_alias="count")
    total: int = Field(validation_alias="totalCount")
    last_page: int = Field(validation_alias="maxPage")
