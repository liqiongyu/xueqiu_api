"""fund_rank_options: the fund types and return windows fund_rank takes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from .._model import NoParams, UpstreamModel
from ..registry import AgentInfo, Endpoint

RESULT_CAP = 20
"""5 types and 11 windows were measured 2026-11-05."""


class FundRankOption(UpstreamModel):
    """One value a fund_rank parameter takes."""

    name: str | None = None
    """Such as 股票型 or 近一月."""
    code: str = Field(validation_alias="k")
    default: bool | None = Field(default=None, validation_alias="df")
    """Whether the upstream's own page starts with this value."""


class FundRankOptions(UpstreamModel):
    """The values of fund_rank's ``fund_type`` and ``order_by``, as the upstream lists them."""

    fund_types: tuple[FundRankOption, ...] = Field(validation_alias="type_model")
    order_by: tuple[FundRankOption, ...] = Field(validation_alias="order_model")

    def header(self) -> Mapping[str, Any]:
        return {}

    def sections(self) -> Mapping[str, Sequence[BaseModel | Mapping[str, Any]]]:
        return {"fund_types": self.fund_types, "order_by": self.order_by}

    def __len__(self) -> int:
        return len(self.fund_types) + len(self.order_by)


def _build_query(params: NoParams, now: datetime) -> dict[str, str]:
    return {"type": "yield"}


def _parse(payload: Any, params: NoParams) -> FundRankOptions:
    return FundRankOptions.model_validate(payload)


ENDPOINT = Endpoint(
    name="fund_rank_options",
    doc="The fund types and return windows that fund_rank takes, as the upstream lists them.",
    host="danjuanfunds.com",
    path="/djapi/v3/filter/conf",
    params=NoParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="static",
    envelope="danjuan",
    verified_on=date(2026, 11, 5),
    agent=AgentInfo(
        category="fund",
        group="xq_fund",
        section="rank_options",
        title_zh="基金排行选项",
        title_en="Fund ranking options",
        summary="列出基金排行可选的基金类型代码和排序区间代码。",
        keywords=(
            "基金排行选项",
            "基金类型列表",
            "排序区间",
            "fund rank options",
            "fund types",
        ),
        default_fields=("code", "name", "default"),
        row_default=RESULT_CAP,
        row_cap=RESULT_CAP,
        upstream_cap=RESULT_CAP,
        examples=({},),
    ),
)
