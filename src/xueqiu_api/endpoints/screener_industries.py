"""screener_industries: the industry codes screener_quote_list filters by."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import Field

from .._model import NoParams, UpstreamModel
from ..registry import AgentInfo, Endpoint


class ScreenerIndustry(UpstreamModel):
    """One industry of the A-share screener."""

    ind_code: str = Field(validation_alias="encode")
    """Code to pass as ``ind_code`` to screener_quote_list, such as S1101."""
    name: str
    """Industry name, such as 种植业."""
    pinyin: str | None = None
    """Pinyin initials of the name, such as zzy."""


class _Payload(UpstreamModel):
    industries: tuple[ScreenerIndustry, ...]


def _build_query(params: NoParams, now: datetime) -> dict[str, str]:
    return {"category": "cn"}  # the only value measured


def _parse(payload: Any, params: NoParams) -> list[ScreenerIndustry]:
    return list(_Payload.model_validate(payload).industries)


ENDPOINT = Endpoint(
    name="screener_industries",
    doc="The A-share industries and the codes screener_quote_list filters by.",
    host="stock.xueqiu.com",
    path="/v5/stock/screener/industries.json",
    params=NoParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="static",
    envelope="xueqiu",
    verified_on=date(2026, 11, 5),
    agent=AgentInfo(
        category="screener",
        group="xq_market",
        section="screener_industries",
        title_zh="行业列表",
        title_en="Screener industries",
        summary="取选股器的 A 股行业和行业代码，代码可以传给证券列表按行业筛选。",
        keywords=(
            "行业列表",
            "行业代码",
            "行业分类",
            "板块",
            "申万行业",
            "industry",
            "industries",
            "sector",
        ),
        default_fields=("ind_code", "name", "pinyin"),
        # 134 rows measured 2026-11-05, with no way to page them.
        row_default=200,
        row_cap=200,
        upstream_cap=200,
        examples=({},),
    ),
)
