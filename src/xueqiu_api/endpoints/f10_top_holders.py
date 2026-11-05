"""f10_top_holders: the ten largest shareholders of one A-share in the latest report period."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from pydantic import Field

from .._f10 import ReportOption
from .._model import UpstreamModel
from .._values import Number, WholeNumber
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolParams, normalize_symbol


class TopHolder(UpstreamModel):
    """One shareholder."""

    holder_name: str
    """A company or a person."""
    held_num: WholeNumber = None
    """Shares held."""
    held_ratio: Number = None
    """Share of the company held, in percent points as sent (21.28 means 21.28%). The denominator
    is unverified; the ratios of SH601398 fit its total shares, measured 2026-11-05."""
    held_chg_pct: Number = Field(default=None, validation_alias="chg")
    """Change against the previous report period in percent points, read as a relative change
    like the total's. None for a holder new to the list, measured 2026-11-05."""
    holder_rank: WholeNumber = None
    """Rank the holder had before leaving the list; only rows in ``quit`` have it."""


class TopHoldersTotal(UpstreamModel):
    """The ten holders together; the units are those of ``TopHolder``."""

    held_num: WholeNumber = None
    held_ratio: Number = None
    held_chg_pct: Number = Field(default=None, validation_alias="chg")
    """Relative change: -0.25 on SH600000 equals (74.6823 / 74.8702 - 1) * 100 from the ratios of
    the two latest periods, measured 2026-11-04."""


class TopHolders(UpstreamModel):
    """The ten largest shareholders in the latest report period.

    Only that period can be fetched: the upstream's circula and timestamp parameters changed
    neither the rows nor the period on SH600000, SH688517 and SH601398, measured 2026-11-05.
    """

    symbol: str
    holders: tuple[TopHolder, ...] = Field(validation_alias="items")
    """Largest first."""
    quit: tuple[TopHolder, ...] = ()
    """Holders of the previous period that left the list."""
    total: TopHoldersTotal | None = None
    report_periods: tuple[ReportOption, ...] = Field(default=(), validation_alias="times")
    """Periods the upstream names, latest first; the rows belong to the first. For reference:
    no parameter selects another one."""

    @property
    def report_date(self) -> date | None:
        """Period end of the rows."""
        return self.report_periods[0].report_date if self.report_periods else None

    @property
    def report_name(self) -> str | None:
        """Upstream label of the period of the rows, such as 2026三季报."""
        return self.report_periods[0].report_name if self.report_periods else None

    def header(self) -> Mapping[str, Any]:
        total = self.total or TopHoldersTotal()
        return {
            "report_date": self.report_date,
            "report_name": self.report_name,
            "total_held_num": total.held_num,
            "total_held_ratio": total.held_ratio,
            "total_held_chg_pct": total.held_chg_pct,
            "report_periods": [option.report_name for option in self.report_periods],
        }

    def sections(self) -> Mapping[str, tuple[TopHolder, ...]]:
        if self.quit:
            return {"holders": self.holders, "quit": self.quit}
        return {"holders": self.holders}

    def __len__(self) -> int:
        return len(self.holders)


def _build_query(params: SymbolParams, now: datetime) -> dict[str, str]:
    # Every measured request sent circula; 1 gave the total's ratio in 4 decimals and 0 in 2, with
    # the same rows, measured 2026-11-05.
    return {"symbol": params.symbol, "circula": "1"}


def _parse(payload: Any, params: SymbolParams) -> TopHolders:
    tz = normalize_symbol(params.symbol).timezone
    return TopHolders.model_validate({**payload, "symbol": params.symbol}, context={"tz": tz})


ENDPOINT = Endpoint(
    name="f10_top_holders",
    doc=(
        "Ten largest shareholders of one A-share in the latest report period; the denominator of "
        "held_ratio is unverified."
    ),
    host="stock.xueqiu.com",
    path="/v5/stock/f10/cn/top_holders.json",
    params=SymbolParams,
    build_query=_build_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="xueqiu",
    verified_on=date(2026, 11, 5),
    # The hk and us paths answer 404, measured 2026-11-04.
    markets=("CN",),
    order="rank",
    agent=AgentInfo(
        category="f10",
        group="xq_company",
        section="top_holders",
        title_zh="十大股东",
        title_en="Top 10 shareholders",
        summary=(
            "取一只 A 股最新一期前十大股东的名称、持股数、持股比例和持股变化，"
            "只有最新一期，持股比例的分母未经核实。"
        ),
        keywords=(
            "十大股东",
            "十大流通股东",
            "前十大股东",
            "大股东",
            "股东名单",
            "持股比例",
            "top holders",
            "top 10 shareholders",
            "major shareholders",
        ),
        default_fields=("holder_name", "held_num", "held_ratio", "held_chg_pct"),
        row_default=10,
        row_cap=10,
        upstream_cap=10,
        examples=({"symbol": "SH600000"},),
    ),
)
