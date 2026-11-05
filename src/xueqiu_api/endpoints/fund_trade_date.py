"""fund_trade_date: settlement dates of an order for one fund placed now."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import Field

from .._fund import FundParams, fd_code_query
from .._model import UpstreamModel
from ..registry import AgentInfo, Endpoint


class FundTradeDate(UpstreamModel):
    """Settlement dates of an order placed now.

    The dates stay as the upstream writes them, as text: a month and day with no year, sometimes
    with a cutoff, such as ``11-06`` or ``11-11 12:00前``, in Asia/Shanghai. The year is not
    filled in, because across New Year it depends on when the response was fetched. The answer
    moves with the 15:00 order cutoff. fund_detail has the same offsets as counts of trading days.
    """

    buy_confirm: str | None = Field(default=None, validation_alias="buy_confirm_date")
    buy_query: str | None = Field(default=None, validation_alias="buy_query_date")
    sale_confirm: str | None = Field(default=None, validation_alias="sale_confirm_date")
    sale_query: str | None = Field(default=None, validation_alias="sale_query_date")
    sale_to_cash_query: str | None = Field(default=None, validation_alias="sale_to_cash_query_date")
    can_pay_with_wallet: bool | None = Field(default=None, validation_alias="if_can_pay_xjb")
    """Whether the platform's money fund wallet can pay; inferred from the upstream name."""
    can_sell_to_wallet: bool | None = Field(default=None, validation_alias="if_can_sale_xjb")
    can_convert: bool | None = Field(default=None, validation_alias="if_can_transition")
    """Whether the fund can be converted into another; inferred from the upstream name."""
    can_sell_to_cash: bool | None = Field(default=None, validation_alias="if_can_sale_to_cash")


def _parse(payload: Any, params: FundParams) -> FundTradeDate:
    return FundTradeDate.model_validate(payload)


ENDPOINT = Endpoint(
    name="fund_trade_date",
    doc="Settlement dates of an order for one fund placed now, as month-day text with no year.",
    host="danjuanfunds.com",
    path="/djapi/fund/order/v2/trade_date",
    params=FundParams,
    build_query=fd_code_query,
    parse=_parse,
    auth="anonymous",
    # A request at 23:38 counted from the next day: the answer moves at the 15:00 order cutoff.
    freshness="intraday",
    envelope="danjuan",
    verified_on=date(2026, 11, 4),
    agent=AgentInfo(
        category="fund",
        group="xq_fund",
        section="trade_date",
        title_zh="基金交易日期",
        title_en="Fund trade dates",
        summary="取现在下单买卖一只基金时的申购确认、份额可查、赎回确认和到账日期（只有月日）。",
        keywords=(
            "基金交易日",
            "申购确认日",
            "赎回到账",
            "确认日期",
            "fund trade date",
            "settlement date",
            "confirmation date",
        ),
        default_fields=(
            "buy_confirm",
            "buy_query",
            "sale_confirm",
            "sale_query",
            "sale_to_cash_query",
            "can_convert",
        ),
        row_default=1,
        row_cap=1,
        upstream_cap=1,
        examples=({"fund_code": "008975"},),
    ),
)
