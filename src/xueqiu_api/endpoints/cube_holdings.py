"""cube_holdings: what one portfolio holds after its last successful rebalancing; needs a login."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import AliasPath, Field

from .._cube import CubeParams, PlatformTime, cube_query
from .._model import UnverifiedModel
from .._values import Number
from ..registry import AgentInfo, Endpoint

_DONE = "last_success_rb"
_LATEST = "last_rb"


class CubeHolding(UnverifiedModel):
    """One security held. The structure is unverified, see ``CubeHoldings``."""

    symbol: str = Field(validation_alias="stock_symbol")
    """Xueqiu symbol of the security."""
    name: str | None = Field(default=None, validation_alias="stock_name")
    """Security name."""
    weight: Number = None
    """Share of the portfolio, in percent points (1.23 means 1.23%)."""
    segment_name: str | None = None
    """Group label written by the portfolio's owner."""


class CubeHoldings(UnverifiedModel):
    """Holdings of one portfolio.

    The structure is unverified: no real response has been seen, only the keys needed to parse
    are declared, others are not kept, and this model is outside the semantic versioning promise.
    ``holdings`` holds the rows; the other fields describe the rebalancing.
    """

    symbol: str
    """Portfolio code, such as ZH2567925."""
    rebalancing_id: int = Field(validation_alias=AliasPath(_DONE, "id"))
    """Id of the last successful rebalancing."""
    status: str | None = Field(default=None, validation_alias=AliasPath(_DONE, "status"))
    """Status of that rebalancing."""
    updated_at: PlatformTime = Field(default=None, validation_alias=AliasPath(_DONE, "updated_at"))
    """When that rebalancing last changed."""
    cash_weight: Number = Field(default=None, validation_alias=AliasPath(_DONE, "cash"))
    """Share of cash, in percent points."""
    holdings: tuple[CubeHolding, ...] = Field(validation_alias=AliasPath(_DONE, "holdings"))
    """The securities held."""
    last_rebalancing_id: int | None = Field(default=None, validation_alias=AliasPath(_LATEST, "id"))
    """Id of the latest rebalancing, which may be pending or failed."""
    last_rebalancing_status: str | None = Field(
        default=None, validation_alias=AliasPath(_LATEST, "status")
    )
    """Status of the latest rebalancing."""

    def header(self) -> Mapping[str, Any]:
        return self.model_dump(exclude={"symbol", "holdings"})

    def sections(self) -> Mapping[str, Sequence[CubeHolding]]:
        return {"holdings": self.holdings}

    def __len__(self) -> int:
        return len(self.holdings)


def _parse(payload: Any, params: CubeParams) -> CubeHoldings:
    return CubeHoldings.model_validate({**payload, "symbol": params.cube_symbol})


ENDPOINT = Endpoint(
    name="cube_holdings",
    doc="What one portfolio holds, with weights and cash; needs a login, structure unverified.",
    host="www.xueqiu.com",
    path="/cubes/rebalancing/current.json",
    params=CubeParams,
    build_query=cube_query,
    parse=_parse,
    auth="required",
    freshness="intraday",
    envelope="bare",
    verified_on=None,
    agent=AgentInfo(
        category="cube",
        group="xq_cube",
        section="holdings",
        title_zh="组合持仓",
        title_en="Portfolio holdings",
        summary="取一个雪球组合当前的持仓股票、权重和现金比例，需要登录。",
        keywords=("组合持仓", "持仓", "仓位", "权重", "cube holdings", "positions", "weights"),
        default_fields=("symbol", "name", "weight", "segment_name"),
        row_default=50,
        row_cap=100,
        upstream_cap=100,
        examples=({"cube_symbol": "ZH2567925"},),
        labels=("segment_name",),
    ),
)
