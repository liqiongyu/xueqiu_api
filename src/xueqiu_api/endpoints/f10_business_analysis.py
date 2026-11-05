"""f10_business_analysis: management's discussion in the periodic reports of one A-share."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BeforeValidator, Field

from .._f10 import report_date_from_label
from .._model import UpstreamModel
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolParams


class BusinessAnalysis(UpstreamModel):
    """Management's discussion of one report period, as the issuer disclosed it."""

    report_name: str | None = Field(default=None, validation_alias="report_date")
    """Report period label, such as 2026三季报 (upstream ``report_date``)."""
    report_date: Annotated[date | None, BeforeValidator(report_date_from_label)] = None
    """Period end, derived from the label; not a disclosure date, which the upstream does not
    send."""
    operating_analysis_explain: str | None = None
    """Discussion of operations: upstream free text."""


class _Payload(UpstreamModel):
    items: tuple[BusinessAnalysis, ...]


def _build_query(params: SymbolParams, now: datetime) -> dict[str, str]:
    return {"symbol": params.symbol}


def _parse(payload: Any, params: SymbolParams) -> list[BusinessAnalysis]:
    return list(_Payload.model_validate(payload).items)


ENDPOINT = Endpoint(
    name="f10_business_analysis",
    doc="Management's discussion in the periodic reports of one A-share, latest first.",
    host="stock.xueqiu.com",
    path="/v5/stock/f10/cn/business_analysis.json",
    params=SymbolParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    markets=("CN",),
    order="newest_first",
    agent=AgentInfo(
        category="f10",
        group="xq_company",
        section="business_analysis",
        title_zh="经营分析",
        title_en="Management discussion",
        summary="取 A 股公司各期定期报告里管理层对经营情况的讨论与分析原文，从最新一期开始。",
        keywords=(
            "经营分析",
            "经营情况",
            "管理层讨论",
            "定期报告",
            "MD&A",
            "management discussion",
            "business review",
        ),
        default_fields=("report_name", "report_date"),
        row_default=5,
        row_cap=10,
        upstream_cap=5,
        examples=({"symbol": "SH600000"},),
        free_text=("operating_analysis_explain",),
    ),
)
