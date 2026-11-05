"""quote_heat: popularity rank of one stock on Xueqiu."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, ClassVar, cast

from pydantic import BeforeValidator, Field

from .._model import UpstreamModel
from .._values import WholeNumber
from ..errors import SymbolNotFound
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolParams


def _compact_date(value: Any) -> date:
    """A date the upstream sends as ``YYYYMMDD``, a form pydantic reads as a Unix time."""
    if not isinstance(value, str):
        raise ValueError(f"expected YYYYMMDD, got {str(value)[:40]!r}")
    return datetime.strptime(value, "%Y%m%d").date()


class RankPoint(UpstreamModel):
    """The rank on one past day. The days are not consecutive: 10-06, 11-01 and 11-03 were seen."""

    day: Annotated[date, BeforeValidator(_compact_date)] = Field(validation_alias="time")
    rank: int


class RankTag(UpstreamModel):
    """A short label the upstream generates."""

    code: str | None = None
    """Kind of label, such as industryHot."""
    tag: str | None = None
    """Label text, such as 银行行业热议 top20."""


class StockHeat(UpstreamModel):
    """Popularity of one stock. The windows of the counts are not confirmed."""

    drop: ClassVar[frozenset[str]] = frozenset({"award"})

    symbol: str
    rank: WholeNumber = None
    """Popularity rank, 1 being the most popular."""
    rank_change: WholeNumber = None
    """Change of rank; which sign means a rise is not confirmed."""
    total: WholeNumber = None
    """Securities ranked, going by its size (32035 seen)."""
    visitors: WholeNumber = None
    """Visitor count."""
    discussions: WholeNumber = None
    """Discussion count; a number only, no post is included."""
    rank_history: tuple[RankPoint, ...] = ()
    """Ranks on recent days, oldest first."""
    rank_tag: tuple[RankTag, ...] = ()


def _build_query(params: SymbolParams, now: datetime) -> dict[str, str]:
    return {"symbol": params.symbol}


def _parse(payload: Any, params: SymbolParams) -> StockHeat:
    if payload == {}:
        raise ValueError("the upstream sent no popularity data")
    fields = cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
    if fields.get("rank") == 0 and not fields.get("rank_history"):
        # What an unknown code gets, with every count at 0, measured 2026-11-05.
        raise SymbolNotFound(f"the upstream has no popularity data for {params.symbol}")
    return StockHeat.model_validate({**payload, "symbol": params.symbol})


ENDPOINT = Endpoint(
    name="quote_heat",
    doc="Popularity rank of one stock on Xueqiu, with its visitor and discussion counts.",
    host="stock.xueqiu.com",
    path="/v5/stock/hot/realtime.json",
    params=SymbolParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="intraday",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    markets=("CN",),
    agent=AgentInfo(
        category="quote",
        group="xq_intraday",
        section="heat",
        title_zh="个股热度",
        title_en="Stock popularity",
        summary="取一只 A 股在雪球的热度排名、排名变化、访问和讨论计数以及近期排名记录。",
        keywords=(
            "个股热度",
            "热度排名",
            "人气排名",
            "关注度",
            "popularity",
            "hot rank",
            "attention",
        ),
        default_fields=(
            "symbol",
            "rank",
            "rank_change",
            "total",
            "visitors",
            "discussions",
            "rank_history",
            "rank_tag",
        ),
        row_default=1,
        row_cap=1,
        upstream_cap=1,
        examples=({"symbol": "SH600000"},),
    ),
)
