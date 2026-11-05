"""f10_industry: the industry and concept boards of one listed company."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from .._f10 import region_query
from .._model import UpstreamModel
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolParams


class IndustryBoard(UpstreamModel):
    """One industry or concept board, in the upstream's order."""

    ind_name: str | None = None
    """Board name, such as 银行."""
    ind_code: str | None = None
    """Board code, such as BK0055; the same code as f10_industry_compare, and not one of the S
    codes of screener_industries."""


class IndustryInfo(UpstreamModel):
    """The boards of one company. The upstream's company block is dropped: f10_company has it."""

    drop: ClassVar[frozenset[str]] = frozenset({"company"})

    symbol: str
    """The requested symbol."""
    industry_class: str | None = None
    """Classification standard as sent: SW2014, HS or GICS, measured 2026-11-04."""
    concept_class: str | None = None
    """Concept list code as sent: 0003 or 0004; its meaning is unknown."""
    industries: tuple[IndustryBoard, ...] = Field(default=(), validation_alias="industry")
    concepts: tuple[IndustryBoard, ...] = Field(default=(), validation_alias="concept")
    """Concept boards; none were sent for Hong Kong, measured 2026-11-04."""

    def header(self) -> Mapping[str, Any]:
        classes = {"industry_class": self.industry_class, "concept_class": self.concept_class}
        return {key: value for key, value in classes.items() if value is not None}

    def sections(self) -> Mapping[str, Sequence[BaseModel]]:
        return {"industries": self.industries, "concepts": self.concepts}

    def __len__(self) -> int:
        return len(self.industries) + len(self.concepts)


def _parse(payload: Any, params: SymbolParams) -> IndustryInfo:
    data = {**payload, "symbol": params.symbol}
    if payload and not {"industry", "concept"} & payload.keys():
        raise ValueError("industry data has neither an industry nor a concept list")
    return IndustryInfo.model_validate(data)


ENDPOINT = Endpoint(
    name="f10_industry",
    doc="Industry and concept boards of one listed company, with the classification standard.",
    host="stock.xueqiu.com",
    path="/v5/stock/f10/{region}/industry.json",
    params=SymbolParams,
    build_query=region_query,
    parse=_parse,
    auth="anonymous",
    freshness="static",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    markets=("CN", "HK", "US"),
    agent=AgentInfo(
        category="f10",
        group="xq_company",
        section="industry",
        title_zh="所属行业和概念",
        title_en="Industry and concepts",
        summary=(
            "取 A 股、港股或美股公司所属的行业和概念板块及其板块代码，并给出所用的行业分类标准。"
        ),
        keywords=(
            "所属行业",
            "概念板块",
            "行业分类",
            "板块",
            "题材",
            "申万行业",
            "GICS",
            "industry classification",
            "sector",
            "concept board",
        ),
        default_fields=("ind_name", "ind_code"),
        row_default=50,
        row_cap=100,
        upstream_cap=100,
        examples=({"symbol": "SH600000"},),
    ),
)
