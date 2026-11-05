"""fund_manager: current managers of one fund, with tenure and returns."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import Field

from .._fund import FundDate, FundParams
from .._model import UpstreamModel
from .._values import Number, PercentPoints, WholeNumber
from ..registry import AgentInfo, Endpoint

RESULT_CAP = 20
"""The page size the upstream echoes; no paging params are known."""


class FundManager(UpstreamModel):
    """One current manager of a fund."""

    manager_id: str | None = Field(default=None, validation_alias="indi_id")
    """Holds only within this endpoint: fund_detail has another id for the same person."""
    name: str | None = None
    work_years: WholeNumber = Field(default=None, validation_alias="work_year")
    start_date: FundDate = Field(default=None, validation_alias="post_date")
    """First day managing this fund."""
    status: WholeNumber = Field(default=None, validation_alias="post_status")
    """1 for a current manager, the only value requested."""
    role: WholeNumber = Field(default=None, validation_alias="post_name")
    """Upstream code of unknown meaning; 1 was seen."""
    tenure_text: str | None = Field(default=None, validation_alias="cp_term")
    """Time in the post as the upstream writes it, such as 6年233天."""
    tenure_return_pct: PercentPoints = Field(default=None, validation_alias="cp_rate")
    """Return since ``start_date``, in percent points (1.23 means 1.23%)."""
    annualized_return_pct: PercentPoints = Field(default=None, validation_alias="performance_year")
    """Annualized return over the tenure, in percent points; inferred."""
    managed_assets: Number = Field(default=None, validation_alias="fund_total_nav")
    """Assets under this manager as sent, such as 361.85; the unit is unconfirmed, likely 亿元."""


class _Payload(UpstreamModel):
    items: tuple[FundManager, ...]


def _build_query(params: FundParams, now: datetime) -> dict[str, str]:
    return {"fund_code": params.fund_code, "post_status": "1"}


def _parse(payload: Any, params: FundParams) -> list[FundManager]:
    return list(_Payload.model_validate(payload).items)


ENDPOINT = Endpoint(
    name="fund_manager",
    doc="Current managers of one fund, with tenure, returns and assets managed.",
    host="danjuanfunds.com",
    path="/djapi/fundx/base/fund/record/manager/list",
    params=FundParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    # Tenure and return figures move with every net value.
    freshness="daily",
    envelope="danjuan",
    verified_on=date(2026, 11, 4),
    agent=AgentInfo(
        category="fund",
        group="xq_fund",
        section="manager",
        title_zh="基金经理",
        title_en="Fund managers",
        summary="取一只基金现任基金经理的任职日期、任职天数、任职回报、年化回报、从业年限和管理规模。",
        keywords=(
            "基金经理",
            "任职回报",
            "任职时间",
            "从业年限",
            "管理规模",
            "fund manager",
            "tenure return",
        ),
        default_fields=(
            "name",
            "start_date",
            "tenure_text",
            "tenure_return_pct",
            "annualized_return_pct",
            "work_years",
            "managed_assets",
        ),
        row_default=RESULT_CAP,
        row_cap=RESULT_CAP,
        upstream_cap=RESULT_CAP,
        examples=({"fund_code": "008975"},),
    ),
)
