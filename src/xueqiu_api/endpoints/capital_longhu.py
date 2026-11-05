"""capital_longhu: disclosures of one A-share on the top-traders list (龙虎榜), latest first."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import Field

from .._capital import PAGE_SIZE_CAP, CapitalPageParams, page_query
from .._f10 import ExchangeDate
from .._model import UpstreamModel
from .._pages import Page
from .._values import Number, WholeNumber
from ..registry import AgentInfo, Endpoint, Pagination
from ..symbols import normalize_symbol


class LonghuBranch(UpstreamModel):
    """One broker on a disclosure."""

    branch_id: str | None = None
    """Upstream code of the broker."""
    branch_name: str | None = None
    """Broker name."""
    buy_amt: Number = None
    """Bought, in CNY."""
    sell_amt: Number = None
    """Sold, in CNY."""
    net_amt: Number = None
    """Bought minus sold, in CNY."""
    ratio: Number = None
    """Bought as a share of the stock's turnover that day, in percent points (1.23 means 1.23%).
    None of the rows measured had any sales, so whether sales count is not known."""


class LonghuEntry(UpstreamModel):
    """One disclosure.

    ``buy_total`` equalled the sum of the five brokers' buys, and ``ratio_total`` equalled
    ``buy_total / trans_amt * 100``, on every row measured 2026-11-05.
    """

    seq: int
    """The upstream's id of the disclosure."""
    trade_date: ExchangeDate = Field(validation_alias="td_date")
    """Trading day."""
    rank_type: WholeNumber = None
    """The upstream's kind of list; 1 wherever it was measured, its meaning is not known."""
    info_type_name: str | None = None
    """Why the stock was disclosed."""
    percent: Number = Field(default=None, validation_alias="chg")
    """Change of the day, in percent points (1.23 means 1.23%): it equalled the percent of the
    unadjusted daily bar on the 3 days measured 2026-11-05."""
    trans_amt: Number = None
    """Turnover of the stock that day, in CNY: it equalled the amount of the daily bar on the same
    3 days."""
    buy_total: Number = None
    """Bought by the listed brokers, in CNY."""
    sell_total: Number = None
    """Sold by the listed brokers, in CNY."""
    net_buy: Number = None
    """``buy_total`` minus ``sell_total``, in CNY."""
    ratio_total: Number = None
    """``buy_total`` as a share of ``trans_amt``, in percent points."""
    total_in: Number = None
    """In CNY; equal to ``buy_total`` wherever it was measured."""
    total_out: Number = None
    """In CNY; equal to ``sell_total`` wherever it was measured."""
    branches: tuple[LonghuBranch, ...] = ()
    """The listed brokers: five on each entry measured."""


class _Payload(UpstreamModel):
    items: tuple[tuple[LonghuEntry, ...], ...]


def _parse(payload: Any, params: CapitalPageParams) -> Page[LonghuEntry]:
    tz = normalize_symbol(params.symbol).timezone
    groups = _Payload.model_validate(payload, context={"tz": tz}).items
    return Page(
        tuple(entry for group in groups for entry in group),
        page=params.page,
        page_size=params.page_size,
    )


ENDPOINT = Endpoint(
    name="capital_longhu",
    doc="Disclosures of one A-share on the top-traders list (longhu), latest first.",
    host="stock.xueqiu.com",
    path="/v5/stock/capital/longhu.json",
    params=CapitalPageParams,
    build_query=page_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="xueqiu",
    verified_on=date(2026, 11, 5),
    # size counts the upstream's groups, each holding at least one row, so a full page is never
    # short once flattened; measured 2026-11-05 at size=2. Page 2 and size=100 were never sent, so
    # the cap is assumed and only an empty page ends the list.
    pagination=Pagination(
        style="page", end="empty", size_cap=PAGE_SIZE_CAP, key=lambda row: row.seq
    ),
    markets=("CN",),
    order="newest_first",
    agent=AgentInfo(
        category="capital",
        group="xq_capital",
        section="longhu",
        title_zh="龙虎榜",
        title_en="Top-trader disclosures (longhu)",
        summary=(
            "取一只 A 股的龙虎榜记录，包括上榜原因、当日涨跌幅、成交额和买卖合计，最新的在前；"
            "营业部明细在 branches 列，要在 fields 里点名。"
        ),
        keywords=(
            "龙虎榜",
            "上榜原因",
            "营业部",
            "游资",
            "机构席位",
            "席位",
            "dragon tiger list",
            "top traders",
            "longhu",
        ),
        default_fields=(
            "trade_date",
            "info_type_name",
            "percent",
            "trans_amt",
            "buy_total",
            "sell_total",
            "net_buy",
            "ratio_total",
        ),
        row_default=50,
        row_cap=PAGE_SIZE_CAP,
        upstream_cap=PAGE_SIZE_CAP,
        examples=({"symbol": "SH600000", "page_size": 20},),
        precision=2,
        empty_hint="no rows: a stock that was never on this list has none.",
    ),
)
