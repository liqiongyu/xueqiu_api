"""fund_derived: latest net value, period returns, ranks and auto-invest returns of one fund."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BeforeValidator, Field, model_validator

from .._fund import SHANGHAI, FundDate, FundParams, fund_code_query, split_ranks
from .._model import UpstreamModel
from .._time import from_ms
from .._values import Number, PercentPoints, no_data
from ..registry import AgentInfo, Endpoint

_RANKS = {
    "srank_l1m": "rank_1m",
    "srank_l3m": "rank_3m",
    "srank_l6m": "rank_6m",
    "srank_lty": "rank_ytd",
    "srank_l1y": "rank_1y",
    "srank_l3y": "rank_3y",
    "srank_l5y": "rank_5y",
    "srank_base": "rank_since_inception",
}


def _shanghai_time(value: Any) -> Any:
    moment = no_data(value)
    return None if moment is None else from_ms(moment, SHANGHAI)


class FundDerived(UpstreamModel):
    """Figures of one fund on ``nav_date``. A window the fund is too young for is None.

    ``return_*_pct`` are cumulative over the window, not annualized, in percent points (1.23
    means 1.23%). ``rank_*`` is the place among peers and ``rank_*_total`` the number of peers;
    the upstream has none for one week and two years. ``aip_return_*_pct`` and
    ``smart_aip_return_*_pct`` are returns of regular and smart auto-invest plans in percent
    points, inferred from the names; they differ from fund_auto_invest_return.
    """

    fund_code: str = Field(validation_alias="fd_code")
    fund_type: str | None = Field(default=None, validation_alias="fd_type")
    """Upstream type code, as in fund_info."""
    nav_date: FundDate = Field(default=None, validation_alias="end_date")
    unit_nav: Number = None
    """Net value per unit on ``nav_date``, in CNY."""
    acc_nav: Number = Field(default=None, validation_alias="unit_acc_nav")
    """Accumulated net value per unit, in CNY: 1.2972 against a unit value of 1.2093 for a fund
    that paid dividends, measured 2026-11-05."""
    nav_change: Number = Field(default=None, validation_alias="nav_growth")
    """Change of ``unit_nav`` from the previous value, in CNY per unit."""
    daily_pct: PercentPoints = Field(default=None, validation_alias="nav_grtd")
    """Change on ``nav_date``, in percent points."""
    return_1w_pct: PercentPoints = Field(default=None, validation_alias="nav_grl1w")
    return_1m_pct: PercentPoints = Field(default=None, validation_alias="nav_grl1m")
    return_3m_pct: PercentPoints = Field(default=None, validation_alias="nav_grl3m")
    return_6m_pct: PercentPoints = Field(default=None, validation_alias="nav_grl6m")
    return_ytd_pct: PercentPoints = Field(default=None, validation_alias="nav_grlty")
    return_1y_pct: PercentPoints = Field(default=None, validation_alias="nav_grl1y")
    return_2y_pct: PercentPoints = Field(default=None, validation_alias="nav_grl2y")
    return_3y_pct: PercentPoints = Field(default=None, validation_alias="nav_grl3y")
    return_5y_pct: PercentPoints = Field(default=None, validation_alias="nav_grl5y")
    return_since_inception_pct: PercentPoints = Field(default=None, validation_alias="nav_grbase")
    rank_1m: int | None = None
    rank_1m_total: int | None = None
    rank_3m: int | None = None
    rank_3m_total: int | None = None
    rank_6m: int | None = None
    rank_6m_total: int | None = None
    rank_ytd: int | None = None
    rank_ytd_total: int | None = None
    rank_1y: int | None = None
    rank_1y_total: int | None = None
    rank_3y: int | None = None
    rank_3y_total: int | None = None
    rank_5y: int | None = None
    rank_5y_total: int | None = None
    rank_since_inception: int | None = None
    rank_since_inception_total: int | None = None
    aip_return_1y_pct: PercentPoints = Field(default=None, validation_alias="aip_grl1y")
    aip_return_2y_pct: PercentPoints = Field(default=None, validation_alias="aip_grl2y")
    aip_return_3y_pct: PercentPoints = Field(default=None, validation_alias="aip_grl3y")
    aip_return_5y_pct: PercentPoints = Field(default=None, validation_alias="aip_grl5y")
    smart_aip_return_1y_pct: PercentPoints = Field(default=None, validation_alias="itg_aip_grl1y")
    smart_aip_return_2y_pct: PercentPoints = Field(default=None, validation_alias="itg_aip_grl2y")
    smart_aip_return_3y_pct: PercentPoints = Field(default=None, validation_alias="itg_aip_grl3y")
    smart_aip_return_5y_pct: PercentPoints = Field(default=None, validation_alias="itg_aip_grl5y")
    updated_at: Annotated[datetime | None, BeforeValidator(_shanghai_time)] = None
    """When the upstream computed these figures, in Asia/Shanghai."""

    @model_validator(mode="before")
    @classmethod
    def _split_ranks(cls, value: Any) -> Any:
        return split_ranks(value, _RANKS)


def _parse(payload: Any, params: FundParams) -> FundDerived:
    return FundDerived.model_validate(payload)


ENDPOINT = Endpoint(
    name="fund_derived",
    doc="Latest net value, period returns, peer ranks and auto-invest returns of one fund.",
    host="danjuanfunds.com",
    path="/djapi/fund/derived/{fund_code}",
    params=FundParams,
    build_query=fund_code_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="danjuan",
    verified_on=date(2026, 11, 5),
    agent=AgentInfo(
        category="fund",
        group="xq_fund",
        section="derived",
        title_zh="基金收益和排名",
        title_en="Fund returns and ranks",
        summary="取一只基金的最新净值和近一周到成立以来各区间收益率、同类排名及定投收益率。",
        keywords=(
            "基金收益",
            "阶段收益",
            "同类排名",
            "定投收益",
            "近一年收益",
            "累计净值",
            "fund returns",
            "fund ranking",
            "period return",
        ),
        default_fields=(
            "nav_date",
            "unit_nav",
            "acc_nav",
            "daily_pct",
            "return_1m_pct",
            "return_3m_pct",
            "return_6m_pct",
            "return_ytd_pct",
            "return_1y_pct",
            "return_3y_pct",
            "return_since_inception_pct",
            "rank_1y",
            "rank_1y_total",
        ),
        row_default=1,
        row_cap=1,
        upstream_cap=1,
        examples=({"fund_code": "008975"},),
    ),
)
