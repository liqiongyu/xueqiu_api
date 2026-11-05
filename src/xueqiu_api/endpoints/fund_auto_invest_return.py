"""fund_auto_invest_return: returns of a default auto-invest plan in one fund, by window."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from .._fund import FundParams, fd_code_query
from .._model import UpstreamModel
from .._values import Number, PercentPoints
from ..registry import AgentInfo, Endpoint

RESULT_CAP = 10
"""Four windows were seen in the upstream's list."""


class FundAutoInvestWindow(UpstreamModel):
    """Returns of the plan over one window, in percent points (inferred)."""

    window: str | None = Field(default=None, validation_alias="duration")
    """Upstream label, such as 近1年."""
    return_pct: PercentPoints = Field(default=None, validation_alias="normal")
    """Return of the regular plan."""
    smart_return_max_pct: PercentPoints = Field(default=None, validation_alias="max_itg")
    """Best return among the smart plans (inferred)."""


class FundAutoInvestReturn(UpstreamModel):
    """The plan the upstream assumes, and its returns. These figures differ from the
    ``aip_return_*_pct`` of fund_derived, so the two are not interchangeable."""

    drop: ClassVar[frozenset[str]] = frozenset({"fd_code"})
    """The request's own code."""

    duration: str | None = None
    """Upstream setting, such as 3Y; its meaning is unconfirmed."""
    strategy: str | None = Field(default=None, validation_alias="buy_type1")
    """Upstream setting, such as normal."""
    smart_strategy: str | None = Field(default=None, validation_alias="buy_type2")
    """Upstream setting, such as mv."""
    cycle: str | None = None
    """Upstream cadence code; its meaning is unconfirmed."""
    invest_amount: Number = Field(default=None, validation_alias="base_invest_amount")
    """CNY per installment (inferred)."""
    windows: tuple[FundAutoInvestWindow, ...] = Field(default=(), validation_alias="recent_yields")

    def header(self) -> Mapping[str, Any]:
        return {
            "duration": self.duration,
            "strategy": self.strategy,
            "smart_strategy": self.smart_strategy,
            "cycle": self.cycle,
            "invest_amount": self.invest_amount,
        }

    def sections(self) -> Mapping[str, Sequence[BaseModel | Mapping[str, Any]]]:
        return {"windows": self.windows}

    def __len__(self) -> int:
        return len(self.windows)


def _parse(payload: Any, params: FundParams) -> FundAutoInvestReturn:
    return FundAutoInvestReturn.model_validate(payload)


ENDPOINT = Endpoint(
    name="fund_auto_invest_return",
    doc="Returns of the upstream's default auto-invest plan in one fund, by window.",
    host="danjuanfunds.com",
    path="/djapi/fundx/autoinvest/quote/yield/list",
    params=FundParams,
    build_query=fd_code_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="danjuan",
    verified_on=date(2026, 11, 4),
    agent=AgentInfo(
        category="fund",
        group="xq_fund",
        section="auto_invest_return",
        title_zh="基金定投收益",
        title_en="Fund auto-invest returns",
        summary="取一只基金近一年到近几年按默认设置定投的收益率，以及智能定投的最高收益率。",
        keywords=(
            "定投收益",
            "基金定投",
            "智能定投",
            "定投回测",
            "auto invest",
            "systematic investment",
            "aip return",
        ),
        default_fields=("window", "return_pct", "smart_return_max_pct"),
        row_default=RESULT_CAP,
        row_cap=RESULT_CAP,
        upstream_cap=RESULT_CAP,
        examples=({"fund_code": "008975"},),
    ),
)
