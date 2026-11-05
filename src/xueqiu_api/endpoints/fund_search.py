"""fund_search: look up funds and advisory portfolios by code, name or keyword."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .._model import UpstreamModel
from .._values import WholeNumber
from ..registry import AgentInfo, Endpoint

RESULT_CAP = 20
"""A broad keyword gave 20 rows, measured 2026-11-04; no paging params are known."""
QUERY_CHARS = 50


class FundSearchParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    query: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=QUERY_CHARS)
    ]
    """Fund code, fund name or keyword, such as 008975, 富国中证消费50 or 消费."""


class FundSearchResult(UpstreamModel):
    """One search result: a fund, or an advisory portfolio."""

    drop: ClassVar[frozenset[str]] = frozenset({"store_status"})
    """Its meaning is unknown and it may be state of the session."""

    code: str = Field(validation_alias="scode")
    """A fund code such as 008975, or a portfolio code such as TIAA026061, which the fund
    endpoints reject."""
    name: str | None = Field(default=None, validation_alias="sname")
    kind: str | None = Field(default=None, validation_alias="stype")
    """``fund``, or ``ic`` for an advisory portfolio (inferred from the codes and names)."""
    result_id: WholeNumber = Field(default=None, validation_alias="id")
    """Upstream's internal id."""


class _Payload(UpstreamModel):
    items: tuple[FundSearchResult, ...]


def _build_query(params: FundSearchParams, now: datetime) -> dict[str, str]:
    # Without xq_access_token, even empty, the upstream answers 200007 api参数缺少, measured
    # 2026-11-04.
    return {"key": params.query, "xq_access_token": "", "source": "index"}


def _parse(payload: Any, params: FundSearchParams) -> list[FundSearchResult]:
    return list(_Payload.model_validate(payload).items)


ENDPOINT = Endpoint(
    name="fund_search",
    doc="Funds and advisory portfolios matching a fund code, a fund name or a keyword.",
    host="danjuanfunds.com",
    path="/djapi/v2/search",
    params=FundSearchParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="static",
    envelope="danjuan",
    # 008975 and a fund name gave funds, measured 2026-11-05; 消费 gave portfolios, 2026-11-04.
    verified_on=date(2026, 11, 5),
    order="rank",
    agent=AgentInfo(
        category="fund",
        group="xq_fund",
        section="search",
        title_zh="基金搜索",
        title_en="Fund search",
        summary="按基金名称、基金代码或主题词搜索基金和投顾组合，返回代码、名称和类型。",
        keywords=(
            "基金搜索",
            "基金代码",
            "基金名称",
            "投顾组合",
            "fund search",
            "fund code lookup",
        ),
        default_fields=("code", "name", "kind"),
        row_default=RESULT_CAP,
        row_cap=RESULT_CAP,
        upstream_cap=RESULT_CAP,
        examples=({"query": "富国中证消费50"},),
    ),
)
