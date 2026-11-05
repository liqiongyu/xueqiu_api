"""Params, dates, ranks and holdings shared by the fund endpoints."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Annotated, Any, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from ._model import UpstreamModel
from ._time import date_from_ms
from ._values import PercentPoints, WholeNumber, no_data, rank_total
from .symbols import FundCode

SHANGHAI = ZoneInfo("Asia/Shanghai")
REPORT_NOISE = frozenset(
    {
        "change_percentage",
        "chart_list",
        "current_price",
        "important_list_show",
        "percent_double",
        "xq_url",
    }
)
"""Keys of a fund report left out of the results: quotes at fetch time, UI fields, duplicates."""


class FundParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    fund_code: FundCode
    """Six digits, such as 008975."""


def fund_code_query(params: FundParams, now: datetime) -> dict[str, str]:
    """``build_query`` where the upstream calls the code ``fund_code``, in the path or the query."""
    return {"fund_code": params.fund_code}


def fd_code_query(params: FundParams, now: datetime) -> dict[str, str]:
    """``build_query`` where the upstream calls the code ``fd_code``."""
    return {"fd_code": params.fund_code}


def _fund_date(value: Any) -> Any:
    return date_from_ms(no_data(value), SHANGHAI)


FundDate = Annotated[date | None, BeforeValidator(_fund_date)]
"""A China calendar date, which the upstream sends as ``YYYY-MM-DD`` or as milliseconds."""


def split_ranks(value: Any, names: Mapping[str, str]) -> Any:
    """Before-validator body: each upstream ``rank/total`` text named in ``names`` becomes the
    fields ``<name>`` and ``<name>_total``."""
    if not isinstance(value, dict):
        return value
    fields = dict(cast(dict[str, Any], value))
    for upstream, name in names.items():
        rank = rank_total(fields.pop(upstream, None))
        if rank is not None:
            fields[name], fields[f"{name}_total"] = rank
    return fields


class FundBondHolding(UpstreamModel):
    """One bond in a fund report."""

    name: str | None = None
    code: str | None = None
    symbol: str | None = Field(default=None, validation_alias="xq_symbol")
    """Upstream ``xq_symbol`` as sent. A bond has the bare code here, such as 019792, which
    ``normalize_symbol`` would place on the wrong exchange."""
    pct_net_assets: PercentPoints = Field(default=None, validation_alias="percent")
    """Share of net assets, in percent points (1.23 means 1.23%)."""


class FundHolding(FundBondHolding):
    """One stock in a fund report. ``symbol`` is the Xueqiu symbol, such as SH600519."""

    industry: str | None = Field(default=None, validation_alias="industry_label")
    """Industry label; only fund_asset sends it."""
    change_vs_prev_quarter_pct: PercentPoints = Field(
        default=None, validation_alias="change_of_pre_quarter"
    )
    """Change of the share against the previous quarter, in percent points; inferred from values
    such as ``0.05%``."""
    change_type: WholeNumber = Field(default=None, validation_alias="change_of_pre_quarter_type")
    """Upstream code; only 1 was seen and its meaning is unconfirmed."""
