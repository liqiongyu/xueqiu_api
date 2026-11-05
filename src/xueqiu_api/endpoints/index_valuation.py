"""index_valuation: the current valuation of one index."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from .._index import IndexValuation
from ..errors import SymbolNotFound
from ..registry import AgentInfo, Endpoint, NotFound
from ..symbols import IndexCode


class IndexValuationParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    index_code: IndexCode
    """An index from index_valuation_list, such as SH000300 or CSIH30269. Case-insensitive."""


def _build_query(params: IndexValuationParams, now: datetime) -> dict[str, str]:
    return {"index_code": params.index_code}


def _parse(payload: Any, params: IndexValuationParams) -> IndexValuation:
    # SH999999 got HTTP 200 and {"result_code": 0} with no data key, measured 2026-11-05.
    if payload is None:
        raise SymbolNotFound(f"no valuation for index {params.index_code}")
    return IndexValuation.model_validate(payload)


ENDPOINT = Endpoint(
    name="index_valuation",
    doc="The current valuation of one index.",
    host="danjuanfunds.com",
    path="/djapi/index_eva/detail/{index_code}",
    params=IndexValuationParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="danjuan",
    verified_on=date(2026, 11, 5),
    not_found=NotFound(),
    agent=AgentInfo(
        category="index",
        group="xq_market",
        section="index_valuation",
        title_zh="单个指数估值",
        title_en="Index valuation",
        summary="取一个指数当前的市盈率、市净率、历史百分位、净资产收益率和股息率。",
        keywords=(
            "指数估值",
            "估值高吗",
            "市盈率",
            "市净率",
            "百分位",
            "股息率",
            "index valuation",
            "pe",
            "pb",
        ),
        default_fields=(
            "index_code",
            "name",
            "trade_date",
            "pe",
            "pe_percentile",
            "pb",
            "pb_percentile",
            "roe",
            "dividend_yield",
            "peg",
            "eva_type",
            "pb_flag",
            "updated_at",
        ),
        row_default=1,
        row_cap=1,
        upstream_cap=1,
        examples=(
            {"index_code": "SH000300"},
            {"index_code": "SZ399006"},
            {"index_code": "CSIH30269"},
            {"index_code": "HKHSI"},
            {"index_code": "NDX"},
        ),
    ),
)
