"""f10_holders: holder counts of an A-share; major holders and share capital of HK and US stocks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from pydantic import AliasChoices, BaseModel, Field

from .._f10 import ExchangeDate, region_query
from .._model import UpstreamModel
from .._values import Number, WholeNumber
from ..registry import AgentInfo, Endpoint
from ..symbols import SymbolParams, normalize_symbol


class HolderCount(UpstreamModel):
    """Holder accounts of an A-share at one report period, latest first."""

    report_date: ExchangeDate = Field(default=None, validation_alias="timestamp")
    """Period end; not a disclosure date, which the upstream does not send."""
    ashare_holder: WholeNumber = None
    """Holder accounts."""
    ashare_holder_chg_pct: Number = Field(default=None, validation_alias="chg")
    """Relative change of ``ashare_holder`` from the period before, in percent points."""
    per_float: Number = None
    """Tradable shares per holder account."""
    per_float_chg_pct: Number = Field(default=None, validation_alias="per_float_chg")
    """Relative change of ``per_float`` from the period before, in percent points."""
    per_amount: Number = None
    """``per_float`` times ``price``, in CNY per holder account."""
    price: Number = None
    """Price at the period end, in CNY. Going by its decimals it looks forward-adjusted, so older
    periods would change after an ex-dividend date; not confirmed."""
    top_holder_ratio: Number = None
    """Share held by the ten largest holders, in percent points; of the total shares, going by the
    name."""
    top_float_holder_ratio: Number = None
    """Share of the tradable shares held by the ten largest tradable holders, in percent points."""


class MajorHolder(UpstreamModel):
    """One major holder of a Hong Kong or US stock, as the issuer or a filing disclosed it."""

    holder_name: str | None = Field(
        default=None, validation_alias=AliasChoices("shhname_ogdis", "reporter_name")
    )
    """A company or a natural person."""
    held_num: Number = Field(
        default=None, validation_alias=AliasChoices("numrts", "share_held_num")
    )
    """Shares held."""
    held_ratio: Number = Field(
        default=None, validation_alias=AliasChoices("pctishg", "total_held_ratio_dsclsr_value")
    )
    """Share held, in percent points: of the issued shares for Hong Kong, as filed for US."""


class ShareChangeRecord(UpstreamModel):
    """Share capital of a Hong Kong or US stock after one change, latest first."""

    change_date: ExchangeDate = Field(
        default=None, validation_alias=AliasChoices("chdate", "chg_date")
    )
    """Date of the change; it can be a non-trading day, such as a fiscal year end."""
    total_shares: Number = Field(
        default=None, validation_alias=AliasChoices("numpush", "total_shares")
    )
    """Shares outstanding; inferred for Hong Kong, from the size of the upstream's numpush."""
    common_shares: Number = Field(default=None, validation_alias="common_shares_num")
    """Common shares; US only."""
    reason: str | None = Field(default=None, validation_alias="chg_reason_explain")
    """Reason for the change: upstream free text; US only."""


class Split(UpstreamModel):
    """One stock split of a Hong Kong or US stock, latest first."""

    explain: str | None = None
    """The ratio as text, such as 每1.0股拆为4.0股."""
    announcement_date: ExchangeDate = Field(
        default=None, validation_alias=AliasChoices("frlsdateinfo", "announcement_date")
    )
    effective_date: ExchangeDate = Field(
        default=None, validation_alias=AliasChoices("sdatetpatr", "split_valid_date")
    )
    """Date the split took effect; inferred for Hong Kong."""
    ex_date: ExchangeDate = Field(default=None, validation_alias="exright_date")
    """Ex-split date; US only."""
    parallel_trading_start: ExchangeDate = Field(default=None, validation_alias="sdateprltr")
    """Start of parallel trading, going by the upstream name; Hong Kong only."""
    parallel_trading_end: ExchangeDate = Field(default=None, validation_alias="edateprltr")
    """End of parallel trading, going by the upstream name; Hong Kong only."""
    temporary_code: str | None = Field(default=None, validation_alias="tpastkcode")
    """Code of the temporary counter, going by the upstream name; Hong Kong only."""


class Repurchase(UpstreamModel):
    """Shares a Hong Kong company bought back on one day, latest first."""

    repurchase_date: ExchangeDate = Field(default=None, validation_alias="rpdate")
    repurchase_shares: Number = Field(default=None, validation_alias="numrpdsh")
    """Shares bought back."""
    avg_price: Number = Field(default=None, validation_alias="avg")
    """Average price, in ``Holders.currency``."""


class Holders(UpstreamModel):
    """Holders of one stock. The upstream sends different tables per market, so each market fills
    only some: A-shares ``holder_counts``, Hong Kong ``major_holders``, ``share_changes``,
    ``splits`` and ``repurchases``, US the same without ``repurchases``.

    The holder names of an A-share are in f10_top_holders.
    """

    symbol: str
    """The requested symbol."""
    currency: str | None = None
    """Currency of the repurchase prices; Hong Kong only."""
    holder_counts: tuple[HolderCount, ...] = Field(default=(), validation_alias="items")
    major_holders: tuple[MajorHolder, ...] = Field(default=(), validation_alias="holders")
    share_changes: tuple[ShareChangeRecord, ...] = Field(
        default=(), validation_alias=AliasChoices("shares", "sharechg")
    )
    splits: tuple[Split, ...] = ()
    repurchases: tuple[Repurchase, ...] = Field(default=(), validation_alias="repurchase")

    def header(self) -> Mapping[str, Any]:
        return {"currency": self.currency} if self.currency else {}

    def sections(self) -> Mapping[str, Sequence[BaseModel]]:
        # A table another market has is not reported as empty, only one this market has.
        market = normalize_symbol(self.symbol).market
        if market == "CN":
            return {"holder_counts": self.holder_counts}
        tables: dict[str, Sequence[BaseModel]] = {
            "major_holders": self.major_holders,
            "share_changes": self.share_changes,
            "splits": self.splits,
        }
        return {**tables, "repurchases": self.repurchases} if market == "HK" else tables

    def __len__(self) -> int:
        return sum(
            len(table)
            for table in (
                self.holder_counts,
                self.major_holders,
                self.share_changes,
                self.splits,
                self.repurchases,
            )
        )


_TABLE_KEYS = frozenset({"items", "holders", "shares", "sharechg", "splits", "repurchase"})


def _parse(payload: Any, params: SymbolParams) -> Holders:
    data = {**payload, "symbol": params.symbol}
    if payload and not _TABLE_KEYS & payload.keys():
        raise ValueError("holders data has none of the known tables")
    return Holders.model_validate(data, context={"tz": normalize_symbol(params.symbol).timezone})


ENDPOINT = Endpoint(
    name="f10_holders",
    doc="Holder counts of an A-share; major holders, capital and splits of HK and US stocks.",
    host="stock.xueqiu.com",
    path="/v5/stock/f10/{region}/holders.json",
    params=SymbolParams,
    build_query=region_query,
    parse=_parse,
    auth="anonymous",
    freshness="daily",
    envelope="xueqiu",
    verified_on=date(2026, 11, 4),
    markets=("CN", "HK", "US"),
    order="newest_first",
    agent=AgentInfo(
        category="f10",
        group="xq_company",
        section="holders",
        title_zh="股东与股本",
        title_en="Holders and share capital",
        summary=(
            "A 股取各报告期的股东户数、户均持股和前十大股东持股比例，"
            "港股和美股取主要股东、股本变动、拆股和港股回购记录。"
        ),
        keywords=(
            "股东户数",
            "股东人数",
            "主要股东",
            "户均持股",
            "筹码集中度",
            "股本变动",
            "拆股",
            "回购",
            "shareholders",
            "holder count",
            "stock split",
            "buyback",
        ),
        default_fields=(
            "report_date",
            "ashare_holder",
            "ashare_holder_chg_pct",
            "per_float",
            "per_amount",
            "price",
            "top_holder_ratio",
            "top_float_holder_ratio",
            "holder_name",
            "held_num",
            "held_ratio",
            "change_date",
            "total_shares",
            "explain",
            "effective_date",
            "ex_date",
            "repurchase_date",
            "repurchase_shares",
            "avg_price",
        ),
        row_default=20,
        row_cap=50,
        upstream_cap=10,
        examples=({"symbol": "SH600000"},),
        free_text=("reason",),
    ),
)
