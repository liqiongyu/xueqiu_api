"""f10_bonus: dividends of one listed company, and the share issues and allotments of an A-share."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, Field

from .._f10 import ExchangeDate, region_query
from .._model import UpstreamModel
from .._pages import Page
from .._values import Number
from ..registry import AgentInfo, Endpoint, Pagination
from ..symbols import SymbolParams, normalize_symbol

PAGE_SIZE_CAP = 100
"""Sent with size=100, the upstream returned all of SH600519's 31 rows, measured 2026-11-05: the
real cap is at least 31 and 100 itself was never reached."""


class BonusParams(SymbolParams):
    page: int = Field(default=1, ge=1)
    """Page number, from 1; page 1 holds the latest dividends."""
    page_size: int = Field(default=50, ge=1, le=PAGE_SIZE_CAP)
    """Dividends per page."""


class Dividend(UpstreamModel):
    """One dividend plan, latest first. Dates are in the exchange timezone.

    The upstream's keys differ per market; each field names the market when only one sends it.
    """

    report_name: str | None = Field(default=None, validation_alias="dividend_year")
    """A-shares: the report period, such as 2025年报, or on a few rows a date such as
    2023-11-21."""
    plan: str | None = Field(
        default=None, validation_alias=AliasChoices("plan_explain", "divdstep", "explain")
    )
    """The plan as text, holding the amounts: 10派4.2元(实施方案), 末期派息每股股息5.3元（HKD ）
    or 每股分配0.27美元. One row of the 79 measured on 2026-11-05 was a sentence, a proposal with no
    dates yet. Allotments never appear here."""
    announcement_date: ExchangeDate = None
    """US only."""
    record_date: ExchangeDate = Field(default=None, validation_alias="equity_date")
    """A-shares only."""
    ex_date: ExchangeDate = Field(
        default=None,
        validation_alias=AliasChoices("ashare_ex_dividend_date", "dertsdiv", "exright_date"),
    )
    """Ex-dividend date; None for a plan not carried out yet. The meaning of the Hong Kong keys of
    this field and ``pay_date`` is inferred."""
    pay_date: ExchangeDate = Field(
        default=None, validation_alias=AliasChoices("datedivpy", "dividend_date")
    )
    """Hong Kong and US only."""
    cancelled_date: ExchangeDate = Field(default=None, validation_alias="cancle_dividend_date")
    """A-shares only, going by the upstream name; None on every row measured on 2026-11-05."""


class Addition(UpstreamModel):
    """One additional share issue of an A-share, latest first."""

    listing_ad: ExchangeDate = None
    """Upstream name kept, because its meaning is unconfirmed: one value is a Saturday, so it is
    not always a first trading day."""
    actual_issue_vol: Number = None
    """Shares issued; a float, because the upstream sends values such as 1915146700.0000002."""
    actual_issue_price: Number = None
    """Issue price, in CNY per share."""
    actual_rc_net_amt: Number = None
    """Net proceeds, in CNY."""


class Allotment(UpstreamModel):
    """One rights issue of an A-share, latest first. The upstream sends no allotment ratio."""

    allot_date: ExchangeDate = Field(default=None, validation_alias="allot_specification_ad")
    """Upstream ``allot_specification_ad``, and not the ex-rights date: 2010-05-20 for SH601166,
    whose adjusted prices step on 2010-06-02, measured 2026-11-05. Which day it marks is unknown."""
    allot_price: Number = Field(default=None, validation_alias="allot_price_ps")
    """Price, in CNY per share."""
    actual_allot_num: Number = None
    """Shares allotted."""
    actual_rc_total_amt: Number = Field(default=None, validation_alias="actua_rc_total_amt")
    """Gross proceeds, in CNY: the price times the shares, measured 2026-11-05."""


@dataclass(frozen=True, slots=True, kw_only=True)
class BonusPage(Page[Dividend]):
    """One page of dividends in ``items``; its length is theirs.

    ``page`` and ``page_size`` apply to the dividends only. An A-share's page 1 also has every
    share issue and allotment. The upstream repeats them on later pages, measured 2026-11-05, so
    there they are empty here. Hong Kong and US responses have neither.
    """

    symbol: str
    """The requested symbol."""
    additions: tuple[Addition, ...] = ()
    allotments: tuple[Allotment, ...] = ()

    def header(self) -> Mapping[str, Any]:
        return {}

    def sections(self) -> Mapping[str, Sequence[BaseModel]]:
        if self.page > 1 or normalize_symbol(self.symbol).market != "CN":
            return {"dividends": self.items}
        return {
            "dividends": self.items,
            "additions": self.additions,
            "allotments": self.allotments,
        }


class _Payload(UpstreamModel):
    items: tuple[Dividend, ...]
    additions: tuple[Addition, ...] = Field(default=(), validation_alias="addtions")
    allotments: tuple[Allotment, ...] = Field(default=(), validation_alias="allots")


def _build_query(params: BonusParams, now: datetime) -> dict[str, str]:
    paging = {"page": str(params.page), "size": str(params.page_size)}
    return {**region_query(params, now), **paging}


def _parse(payload: Any, params: BonusParams) -> BonusPage:
    tz = normalize_symbol(params.symbol).timezone
    data = _Payload.model_validate(payload, context={"tz": tz})
    first = params.page == 1
    return BonusPage(
        items=data.items,
        page=params.page,
        page_size=params.page_size,
        symbol=params.symbol,
        additions=data.additions if first else (),
        allotments=data.allotments if first else (),
    )


ENDPOINT = Endpoint(
    name="f10_bonus",
    doc="Dividends of one listed company, latest first; A-shares add issues and allotments.",
    host="stock.xueqiu.com",
    path="/v5/stock/f10/{region}/bonus.json",
    params=BonusParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="xueqiu",
    verified_on=date(2026, 11, 5),
    # Page 2 of SH600000 had no dividends, measured 2026-11-05.
    pagination=Pagination(style="page", end="empty", size_cap=PAGE_SIZE_CAP),
    markets=("CN", "HK", "US"),
    order="newest_first",
    agent=AgentInfo(
        category="f10",
        group="xq_company",
        section="bonus",
        title_zh="分红送配",
        title_en="Dividends and share issues",
        summary=(
            "取 A 股、港股或美股公司的分红记录，含方案、股权登记日、除权除息日和派息日，"
            "A 股另有增发和配股记录，按页从新到旧返回。"
        ),
        keywords=(
            "分红",
            "派息",
            "除权除息",
            "股息",
            "送转",
            "增发",
            "配股",
            "dividend",
            "ex-dividend",
            "rights issue",
            "share issue",
        ),
        default_fields=(
            "report_name",
            "plan",
            "record_date",
            "ex_date",
            "pay_date",
            "announcement_date",
            "listing_ad",
            "actual_issue_vol",
            "actual_issue_price",
            "actual_rc_net_amt",
            "allot_date",
            "allot_price",
            "actual_allot_num",
            "actual_rc_total_amt",
        ),
        row_default=50,
        row_cap=PAGE_SIZE_CAP,
        upstream_cap=PAGE_SIZE_CAP,
        examples=({"symbol": "SH600000", "page_size": 3},),
    ),
)
