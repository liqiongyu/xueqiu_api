"""fund_asset: asset allocation and top holdings of one fund's latest report, on net assets."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from .._fund import (
    REPORT_NOISE,
    FundBondHolding,
    FundDate,
    FundHolding,
    FundParams,
    fund_code_query,
)
from .._model import UpstreamModel
from .._values import PercentPoints
from ..registry import AgentInfo, Endpoint

RESULT_CAP = 50
"""Most rows of one report's holdings tables, a bound: reports list the largest holdings only."""


class FundAsset(UpstreamModel):
    """The latest report of one fund, on net assets.

    The four classes need not sum to 100: they sum to 118.29 for a fund whose total assets are
    118.29% of its net assets, measured 2026-11-04. fund_detail has the classes on total assets.
    """

    drop: ClassVar[frozenset[str]] = REPORT_NOISE | {"source_mark"}
    """Quotes at fetch time, UI fields and duplicates."""

    report_date: FundDate = Field(default=None, validation_alias="source")
    """Period end of the report."""
    stock_pct_net_assets: PercentPoints = Field(default=None, validation_alias="stock_percent")
    """Stocks, in percent points of net assets (1.23 means 1.23%)."""
    bond_pct_net_assets: PercentPoints = Field(default=None, validation_alias="bond_percent")
    cash_pct_net_assets: PercentPoints = Field(default=None, validation_alias="cash_percent")
    other_pct_net_assets: PercentPoints = Field(default=None, validation_alias="other_percent")
    stocks: tuple[FundHolding, ...] = Field(default=(), validation_alias="stock_list")
    bonds: tuple[FundBondHolding, ...] = Field(default=(), validation_alias="bond_list")

    def header(self) -> Mapping[str, Any]:
        return {
            "report_date": self.report_date,
            "stock_pct_net_assets": self.stock_pct_net_assets,
            "bond_pct_net_assets": self.bond_pct_net_assets,
            "cash_pct_net_assets": self.cash_pct_net_assets,
            "other_pct_net_assets": self.other_pct_net_assets,
        }

    def sections(self) -> Mapping[str, Sequence[BaseModel | Mapping[str, Any]]]:
        return {"stocks": self.stocks, "bonds": self.bonds}

    def __len__(self) -> int:
        return len(self.stocks) + len(self.bonds)


def _parse(payload: Any, params: FundParams) -> FundAsset:
    return FundAsset.model_validate(payload)


ENDPOINT = Endpoint(
    name="fund_asset",
    doc="Asset allocation and top holdings of one fund's latest report, on net assets.",
    host="danjuanfunds.com",
    path="/djapi/fundx/base/fund/record/asset/percent",
    params=FundParams,
    build_query=fund_code_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="danjuan",
    verified_on=date(2026, 11, 4),
    agent=AgentInfo(
        category="fund",
        group="xq_fund",
        section="asset",
        title_zh="基金持仓和资产配置",
        title_en="Fund holdings and allocation",
        summary="取一只基金最新报告期的资产配置（占净资产比例）和前十大股票、债券持仓。",
        keywords=(
            "基金持仓",
            "重仓股",
            "资产配置",
            "股票仓位",
            "债券持仓",
            "fund holdings",
            "asset allocation",
            "top holdings",
            "fund owns",
        ),
        default_fields=(
            "name",
            "symbol",
            "pct_net_assets",
            "industry",
            "change_vs_prev_quarter_pct",
        ),
        row_default=RESULT_CAP,
        row_cap=RESULT_CAP,
        upstream_cap=RESULT_CAP,
        examples=({"fund_code": "008975"},),
    ),
)
