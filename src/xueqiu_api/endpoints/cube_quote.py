"""cube_quote: latest net value and gains of several portfolios in one request."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from .._cube import CubeFigures
from ..registry import AgentInfo, Endpoint, NotFound
from ..symbols import CubeCode, split_symbols

CUBE_CAP = 10
"""Codes per request, kept low because only 2 were measured, on 2026-11-05."""


class CubeQuoteParams(BaseModel):
    """A code the upstream does not know raises ``SymbolNotFound`` when requested alone, measured
    2026-11-05. A request mixing known and unknown codes was not measured."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    cube_symbols: Annotated[
        tuple[CubeCode, ...],
        BeforeValidator(split_symbols),
        Field(min_length=1, max_length=CUBE_CAP),
    ]
    """Portfolio codes, as a sequence or one comma-separated string."""


class CubeQuote(CubeFigures):
    """Latest figures of one portfolio. The net value and gains move during trading, measured
    2026-11-05. ``hasexist``, ``badges_exist`` and ``game_id``, whose meanings are unknown, stay
    in ``model_extra`` as sent."""


def _build_query(params: CubeQuoteParams, now: datetime) -> dict[str, str]:
    return {"code": ",".join(params.cube_symbols)}


def _parse(payload: Any, params: CubeQuoteParams) -> list[CubeQuote]:
    if not isinstance(payload, dict):
        raise ValueError("cube quote data needs an object keyed by code")
    quotes = cast(dict[str, Any], payload)
    return [
        CubeQuote.model_validate(quotes[code]) for code in params.cube_symbols if code in quotes
    ]


ENDPOINT = Endpoint(
    name="cube_quote",
    doc="Latest net value and gains of up to 10 portfolios, in the order requested.",
    host="www.xueqiu.com",
    path="/cubes/quote.json",
    params=CubeQuoteParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="intraday",
    envelope="bare",
    verified_on=date(2026, 11, 5),
    not_found=NotFound(codes=("20809",)),
    agent=AgentInfo(
        category="cube",
        group="xq_cube",
        section="quote",
        title_zh="组合行情",
        title_en="Portfolio quotes",
        summary="一次请求取多个雪球组合（ZH 开头）的最新净值、日收益、月收益、总收益和年化收益。",
        keywords=(
            "组合行情",
            "组合净值",
            "雪球组合",
            "ZH",
            "收益率",
            "cube quote",
            "portfolio",
            "nav",
        ),
        default_fields=(
            "symbol",
            "name",
            "net_value",
            "daily_gain",
            "monthly_gain",
            "total_gain",
            "annualized_gain",
            "closed_at",
        ),
        row_default=CUBE_CAP,
        row_cap=CUBE_CAP,
        upstream_cap=CUBE_CAP,
        examples=({"cube_symbols": ["ZH2567925"]},),
        labels=("name",),
    ),
)
