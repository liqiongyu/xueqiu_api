"""screener_quote_list: securities of one market, board or industry with their quotes and ratios."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, ClassVar, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .._finance import REPORT_DATE_ZONE
from .._model import Order, QuoteRow, UpstreamModel
from .._pages import Page
from .._time import date_from_ms
from .._values import Number
from ..registry import AgentInfo, Endpoint, Pagination

PAGE_SIZE_CAP = 100
"""size=100 gave 100 rows; size=101 and size=500 silently gave 30, measured 2026-11-04."""
TOTAL_CAP = 5000
"""count and the rows stop at 5000: page 50 at size 100 was full and page 51 empty, measured
2026-11-04."""

QuoteListMarket = Literal["CN", "HK", "US"]
"""Markets of the list; the generated methods import this name from this module."""
Board = Literal["sha", "kcb", "sza", "cyb", "zxb", "bj", "shb", "szb", "sh_sz", "sh_sz_bj"]
"""A-share boards. sha, kcb, sza, bj, shb and szb do not overlap and summed to the count of the
whole market, measured 2026-11-04; cyb and zxb lie inside sza; sh_sz and sh_sz_bj stop at 5000. The
upstream ignores an unknown value and answers with all of CN, so only these pass."""
QuoteListOrderBy = Literal["symbol", "percent"]
"""Sort columns measured. Sorting by percent during trading may move rows between pages."""
IndCode = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[Ss][0-9]{4}$")
]
"""S plus 4 digits, as all 134 listed industries are. Not checked against that list: an unknown
code gets count 0 and no rows, measured 2026-11-05."""


class ScreenerQuoteListParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)

    market: QuoteListMarket = "CN"
    """CN, HK or US."""
    board: Board | None = None
    """A-share board, with market CN only; by default all of the market."""
    ind_code: IndCode | None = None
    """Industry code from screener_industries, such as S6308, with market CN only. A code that
    does not exist gives no rows."""
    order_by: QuoteListOrderBy = "symbol"
    """Sort column: symbol, or percent for the change from the previous close."""
    order: Order = "asc"
    """Sort direction: asc or desc."""
    page: int = Field(default=1, ge=1)
    """Page number, from 1."""
    page_size: int = Field(default=50, ge=1, le=PAGE_SIZE_CAP)
    """Rows per page."""

    @model_validator(mode="after")
    def _filters_are_for_a_shares(self) -> Self:
        if self.market != "CN" and (self.board is not None or self.ind_code is not None):
            raise ValueError("board and ind_code work with market CN only")
        return self


class ScreenerQuote(QuoteRow):
    """One security with its quote and ratios. Its symbol is data, as the upstream writes it:
    ``normalize_symbol`` does not accept all of them, such as DX-A.CL. Other upstream fields, such
    as eps, pb_ttm and lot_size, stay in ``model_extra``."""

    drop: ClassVar[frozenset[str]] = frozenset({"has_follow"})
    """Whether the session's own account follows the security."""

    stock_type: int | None = Field(default=None, validation_alias="type")
    """Upstream security type: 11 SH and SZ, 82 STAR, 90 BJ, 30 HK and 0 US were measured."""
    amount: Number = None
    """Turnover, in the quote currency."""
    volume: Number = None
    """Volume, in shares."""
    turnover_rate: Number = None
    """Turnover rate, in percent points. Not meaningful for US rows, which measured up to 1781."""
    amplitude: Number = None
    """Day's range over the previous close, in percent points."""
    current_year_percent: Number = None
    """Change since the start of the year, in percent points."""
    market_capital: Number = None
    """Market value, in the quote currency; None when the upstream sends 0 for it and for the
    shares, as on 3 of 200 US rows measured 2026-11-05, all with a .L suffix and a price."""
    float_market_capital: Number = None
    """Market value of the free float, in the quote currency; none on US rows."""
    total_shares: Number = None
    """Shares outstanding; None when the upstream sends 0."""
    float_shares: Number = None
    """Shares in the free float; none on US rows."""
    pe_ttm: Number = None
    """Price to trailing earnings, as a multiple; None when the upstream sends 0."""
    pb: Number = None
    """Price to book, as a multiple."""
    dividend_yield: Number = None
    """Dividend yield, in percent points."""
    issue_date: date | None = Field(default=None, validation_alias="issue_date_ts")
    """Listing date, sent as midnight in Asia/Shanghai for every market."""
    north_net_inflow_date: date | None = Field(
        default=None, validation_alias="north_net_inflow_time"
    )
    """Date of the northbound flow figure, sent as midnight in Asia/Shanghai."""

    @model_validator(mode="before")
    @classmethod
    def _no_placeholders(cls, value: Any) -> Any:
        """A ratio or a share count of 0 means no value, and so does the market value next to it."""
        if not isinstance(value, dict):
            return value
        fields = cast(dict[str, Any], value)
        blank: dict[str, None] = {"pe_ttm": None} if fields.get("pe_ttm") == 0 else {}
        if fields.get("total_shares") == 0:
            blank |= {"total_shares": None, "market_capital": None}
        return {**fields, **blank}

    @field_validator("issue_date", "north_net_inflow_date", mode="before")
    @classmethod
    def _shanghai_date(cls, value: Any) -> Any:
        return date_from_ms(value, REPORT_DATE_ZONE)


class _Payload(UpstreamModel):
    count: int
    rows: tuple[ScreenerQuote, ...] = Field(validation_alias="list")


def _build_query(params: ScreenerQuoteListParams, now: datetime) -> dict[str, str]:
    query = {
        "market": params.market,
        "order_by": params.order_by,
        "order": params.order,
        "page": str(params.page),
        "size": str(params.page_size),
    }
    # With HK and US the measured requests sent the market again as type.
    kind = params.board if params.market == "CN" else params.market.lower()
    if kind is not None:
        query["type"] = kind
    if params.ind_code is not None:
        query["ind_code"] = params.ind_code
    return query


def _parse(payload: Any, params: ScreenerQuoteListParams) -> Page[ScreenerQuote]:
    data = _Payload.model_validate(payload)
    return Page(data.rows, page=params.page, page_size=params.page_size, total=data.count)


ENDPOINT = Endpoint(
    name="screener_quote_list",
    doc="Securities of one market, board or industry with their quotes and ratios.",
    host="stock.xueqiu.com",
    path="/v5/stock/screener/quote/list.json",
    params=ScreenerQuoteListParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="intraday",
    envelope="xueqiu",
    verified_on=date(2026, 11, 5),
    pagination=Pagination(
        style="page",
        end="total",
        size_cap=PAGE_SIZE_CAP,
        total_cap=TOTAL_CAP,
        key=lambda row: row.symbol,
        cap_hint=(
            "Split CN by board: sha, kcb, sza, bj, shb, szb. For US walk "
            "screener_popularity_rank with market=US, which is not capped."
        ),
    ),
    order="rank",
    agent=AgentInfo(
        category="screener",
        group="xq_market",
        section="screener_quote_list",
        title_zh="证券列表行情",
        title_en="Securities list",
        summary="按市场、板块或行业翻页取证券列表，每行带最新价、涨跌幅、成交额、市值和估值。",
        keywords=(
            "证券列表",
            "全部A股",
            "板块筛选",
            "行业筛选",
            "涨幅榜",
            "科创板",
            "选股",
            "screener",
            "stock list",
            "top gainers",
        ),
        default_fields=(
            "symbol",
            "name",
            "current",
            "percent",
            "amount",
            "turnover_rate",
            "market_capital",
            "pe_ttm",
            "pb",
            "dividend_yield",
        ),
        row_default=50,
        row_cap=PAGE_SIZE_CAP,
        upstream_cap=PAGE_SIZE_CAP,
        examples=(
            {
                "market": "CN",
                "board": "kcb",
                "order_by": "percent",
                "order": "desc",
                "page_size": 3,
            },
        ),
        empty_hint="no rows: this ind_code may not exist; screener_industries lists the codes.",
    ),
)
