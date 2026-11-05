"""f10_executives: directors, supervisors and executives of one A-share (upstream skholder)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .._f10 import ExchangeDate
from .._model import UpstreamModel
from .._values import Number, WholeNumber
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolParams, normalize_symbol


class Executive(UpstreamModel):
    """One director, supervisor or executive, as the company discloses them."""

    personal_name: str
    position_name: str | None = None
    """Positions joined by 、, such as 董事长、执行董事."""
    employ_date: ExchangeDate = None
    """Start date of the term; the meaning is inferred."""
    employ_ed: ExchangeDate = None
    """End date of the term; the meaning is inferred, every value seen was null."""
    held_num: WholeNumber = None
    """Shares held."""
    annual_salary: Number = None
    """Annual pay in CNY; the unit is inferred from the size of the values, the year is not
    stated."""
    resume_cn: str | None = None
    """Resume: upstream free text."""


class _Payload(UpstreamModel):
    items: tuple[Executive, ...]


def _build_query(params: SymbolParams, now: datetime) -> dict[str, str]:
    return {"symbol": params.symbol}


def _parse(payload: Any, params: SymbolParams) -> list[Executive]:
    tz = normalize_symbol(params.symbol).timezone
    return list(_Payload.model_validate(payload, context={"tz": tz}).items)


ENDPOINT = Endpoint(
    name="f10_executives",
    doc="Directors, supervisors and executives of one A-share, in the upstream's order.",
    host="stock.xueqiu.com",
    path="/v5/stock/f10/cn/skholder.json",
    params=SymbolParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    markets=("CN",),
    order="rank",
    agent=AgentInfo(
        category="f10",
        group="xq_company",
        section="executives",
        title_zh="公司高管",
        title_en="Directors and executives",
        summary=(
            "取一只 A 股现任董事、监事和高管的姓名、职务、任职日期、持股数和年薪，简历按需返回。"
        ),
        keywords=(
            "公司高管",
            "董监高",
            "高管薪酬",
            "董事",
            "监事",
            "管理层",
            "高管简历",
            "董事会",
            "executives",
            "directors",
            "management",
            "board",
        ),
        default_fields=(
            "personal_name",
            "position_name",
            "employ_date",
            "held_num",
            "annual_salary",
        ),
        row_default=50,
        row_cap=100,
        upstream_cap=100,
        examples=({"symbol": "SH600000"},),
        free_text=("resume_cn",),
    ),
)
