"""Snapshot of the securities listed in one market: the walk's state and the snapshot's checks.

CN walks the boards sha, kcb, sza, bj, shb and szb by symbol, because the whole market stops at
5000 rows; they do not overlap and summed to the popularity ranking's count of 5656 (measured
2026-11-04). HK walks its one list by symbol. US walks the popularity ranking, which is not capped:
count 9124, with the last 24 rows on page 92 (measured 2026-11-05).

A snapshot covers what is listed now. History built from snapshots lacks whatever was delisted
before the first one, so it has survivorship bias.

Nothing here sends: the clients loop over ``SnapshotWalk.next_request`` and ``SnapshotWalk.feed``,
with one call per page. A failed call leaves no snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, Literal

from ._pages import Page
from .endpoints.screener_popularity_rank import ENDPOINT as POPULARITY_RANK
from .endpoints.screener_popularity_rank import PAGE_SIZE_CAP as RANK_PAGE_SIZE_CAP
from .endpoints.screener_popularity_rank import PopularStock
from .endpoints.screener_quote_list import ENDPOINT as QUOTE_LIST
from .endpoints.screener_quote_list import PAGE_SIZE_CAP as LIST_PAGE_SIZE_CAP
from .endpoints.screener_quote_list import ScreenerQuote
from .errors import InvalidParams, SymbolNotFound
from .quality import CheckResult, CheckTally, QualityReport
from .symbols import Market, normalize_symbol

__all__ = [
    "SEGMENTS",
    "Security",
    "SecuritySnapshot",
    "Segment",
    "SegmentCoverage",
    "SnapshotRequest",
    "SnapshotWalk",
    "check_snapshot",
]

Segment = Literal["sha", "kcb", "sza", "bj", "shb", "szb", "hk", "us"]

SEGMENTS: Mapping[Market, tuple[Segment, ...]] = MappingProxyType(
    {"CN": ("sha", "kcb", "sza", "bj", "shb", "szb"), "HK": ("hk",), "US": ("us",)}
)
"""The segments of each market, in the order they are walked."""

_PAGE_SIZE = min(LIST_PAGE_SIZE_CAP, RANK_PAGE_SIZE_CAP)
_BOARD_PREFIXES: Mapping[Segment, str] = MappingProxyType(
    {"sha": "SH", "kcb": "SH", "shb": "SH", "sza": "SZ", "szb": "SZ", "bj": "BJ"}
)


@dataclass(frozen=True, slots=True)
class Security:
    """One listed security, with the fields that do not move during the day.

    US rows come from the popularity ranking, whose rows have no lot size, listing date, share
    counts or dual counter; those stay None.
    """

    symbol: str
    """As the upstream writes it, which ``normalize_symbol`` does not always accept: DX-A.CL."""
    name: str | None
    market: Market
    segment: Segment
    security_type: int | None
    """Upstream security type: 11 SH and SZ, 82 STAR, 90 BJ, 30 HK and 0 US were measured."""
    tick_size: float | None
    """Smallest price step, in the quote currency."""
    lot_size: int | None = None
    """Shares per lot, which differ by security within a market."""
    listed_on: date | None = None
    """Listing date; the upstream sends midnight in Asia/Shanghai for every market."""
    total_shares: float | None = None
    """Shares outstanding; None when the upstream sends 0."""
    float_shares: float | None = None
    """Shares in the free float."""
    dual_counter: str | None = None
    """The upstream's ``dual_counter_mapping_symbol``; no row measured so far had one."""


@dataclass(frozen=True, slots=True)
class SegmentCoverage:
    segment: Segment
    declared: int | None
    """The upstream's count on the segment's last page; None when it sent none."""
    fetched: int
    """Unique rows."""
    repeated: int
    """Rows left out because the segment had them already. Rows tied in the sort order can move
    between pages, and a listing moves the rows behind it, so a repeat usually hides another row."""
    capped: bool
    """The count reached the most rows the list serves, so the rest cannot be fetched."""

    @property
    def complete(self) -> bool:
        """Unique rows equal a count above 0, with no repeat and no cap. A count of 0 proves
        nothing: an unknown industry filter gets count 0 and no rows (measured 2026-11-05)."""
        return (
            bool(self.declared)
            and self.fetched == self.declared
            and not self.repeated
            and not self.capped
        )


@dataclass(frozen=True, slots=True)
class SecuritySnapshot:
    market: Market
    fetched_at: datetime
    securities: tuple[Security, ...]
    """Without repeats inside a segment. A symbol in two segments stays in both, for
    ``check_snapshot`` to report."""
    coverage: tuple[SegmentCoverage, ...]
    screen_total: int | None
    """The popularity ranking's count, from a walk of all of CN only. The two lists may differ on
    a listing day, so this does not decide ``complete``."""

    @property
    def complete(self) -> bool:
        """Whether every segment walked is complete; ``coverage`` says which were walked."""
        return all(coverage.complete for coverage in self.coverage)


@dataclass(frozen=True, slots=True)
class SnapshotRequest:
    """One page to fetch: ``client.call(request.endpoint, **request.params)``."""

    endpoint: str
    params: Mapping[str, Any]


class SnapshotWalk:
    """State of one walk over the segments of a market.

    Feed it pages from the single-page calls. The ``<name>_pages`` methods raise when a page comes
    twice and refuse a capped list; here both only make the snapshot incomplete.
    """

    def __init__(
        self, market: Market, *, segments: Sequence[Segment] | None = None, fetched_at: datetime
    ) -> None:
        """``segments`` narrows the walk to some of the market's; by default all of them."""
        known = SEGMENTS.get(market)
        if known is None:
            raise InvalidParams(
                f"unknown market {str(market)[:40]!r}", field="market", valid_values=tuple(SEGMENTS)
            )
        chosen = known if segments is None else tuple(dict.fromkeys(segments))
        if not chosen or not set(chosen) <= set(known):
            raise InvalidParams(
                f"segments must be some of {', '.join(known)} for market {market}",
                field="segments",
                valid_values=known,
            )
        self._market: Market = market
        self._list_name = POPULARITY_RANK.name if market == "US" else QUOTE_LIST.name
        self._fetched_at = fetched_at
        self._waiting = list(chosen)
        self._wants_total = market == "CN" and len(chosen) == len(known)
        self._securities: list[Security] = []
        self._coverage: list[SegmentCoverage] = []
        self._seen: set[str] = set()
        self._repeated = 0
        self._screen_total: int | None = None
        self._request: SnapshotRequest | None = self._first_page()

    def next_request(self) -> SnapshotRequest | None:
        """The page to fetch next, the same one until it is fed; None when the walk is over."""
        return self._request

    def feed(self, page: Page[ScreenerQuote] | Page[PopularStock]) -> None:
        """Take the page of the request ``next_request`` gave."""
        if not self._waiting:
            self._screen_total = page.total
            self._request = None
            return
        segment = self._waiting[0]
        for row in page:
            if row.symbol in self._seen:
                self._repeated += 1
            else:
                self._seen.add(row.symbol)
                self._securities.append(_security(row, self._market, segment))
        if page.next_params is not None:
            self._request = SnapshotRequest(self._list_name, page.next_params)
            return
        self._coverage.append(
            SegmentCoverage(segment, page.total, len(self._seen), self._repeated, page.capped)
        )
        self._seen, self._repeated = set(), 0
        del self._waiting[0]
        if self._waiting:
            self._request = self._first_page()
        elif self._wants_total:
            params = {"market": self._market, "page": 1, "page_size": 1}
            self._request = SnapshotRequest(POPULARITY_RANK.name, params)
        else:
            self._request = None

    def result(self) -> SecuritySnapshot:
        """The snapshot of a finished walk; ``ValueError`` before that, since a part of a walk
        could read as complete."""
        if self._request is not None:
            raise ValueError("the walk has not finished")
        return SecuritySnapshot(
            market=self._market,
            fetched_at=self._fetched_at,
            securities=tuple(self._securities),
            coverage=tuple(self._coverage),
            screen_total=self._screen_total,
        )

    def _first_page(self) -> SnapshotRequest:
        params: dict[str, Any] = {"market": self._market, "page": 1, "page_size": _PAGE_SIZE}
        if self._market != "US":
            params |= {"order_by": "symbol", "order": "asc"}
        if self._market == "CN":
            params["board"] = self._waiting[0]
        return SnapshotRequest(self._list_name, params)


def _security(row: ScreenerQuote | PopularStock, market: Market, segment: Segment) -> Security:
    if isinstance(row, PopularStock):
        return Security(row.symbol, row.name, market, segment, row.stock_type, row.tick_size)
    extra = row.model_extra or {}
    tick_size, lot_size, dual = (
        extra.get(key) for key in ("tick_size", "lot_size", "dual_counter_mapping_symbol")
    )
    return Security(
        symbol=row.symbol,
        name=row.name,
        market=market,
        segment=segment,
        security_type=row.stock_type,
        tick_size=float(tick_size) if isinstance(tick_size, int | float) else None,
        lot_size=lot_size if isinstance(lot_size, int) else None,
        listed_on=row.issue_date,
        total_shares=row.total_shares,
        float_shares=row.float_shares,
        dual_counter=dual if isinstance(dual, str) else None,
    )


def check_snapshot(snapshot: SecuritySnapshot) -> QualityReport:
    """Check one snapshot: like the other checks it returns a report and never raises on bad data.

    ``snapshot.segment_complete`` fails exactly when ``snapshot.complete`` is false.
    """
    return QualityReport(
        subject=f"{snapshot.market} snapshot",
        results=(
            _segment_complete(snapshot),
            _total_matches_screen(snapshot),
            _unique(snapshot),
            _segment_prefix(snapshot),
            _required_fields(snapshot),
            _symbols_normalizable(snapshot),
        ),
    )


def _segment_complete(snapshot: SecuritySnapshot) -> CheckResult:
    tally = CheckTally("snapshot.segment_complete")
    for coverage in snapshot.coverage:
        tally.checked += 1
        if not coverage.complete:
            capped = " and reached the cap" if coverage.capped else ""
            tally.found(
                "error",
                coverage.segment,
                f"the walk has {coverage.fetched} unique rows of the upstream's count, "
                f"{coverage.repeated} repeated{capped}",
                observed=coverage.fetched,
                expected=coverage.declared,
            )
    return tally.result()


def _total_matches_screen(snapshot: SecuritySnapshot) -> CheckResult:
    tally = CheckTally("snapshot.total_matches_screen")
    if snapshot.screen_total is None:
        return tally.result("no ranking count: only a walk of all of CN has one")
    tally.checked += 1
    total = sum(coverage.fetched for coverage in snapshot.coverage)
    if total != snapshot.screen_total:
        tally.found(
            "warning",
            snapshot.market,
            "the rows of the boards differ from the popularity ranking's count",
            observed=total,
            expected=snapshot.screen_total,
        )
    return tally.result()


def _unique(snapshot: SecuritySnapshot) -> CheckResult:
    tally = CheckTally("snapshot.unique")
    seen: set[str] = set()
    for security in snapshot.securities:
        tally.checked += 1
        if security.symbol in seen:
            tally.found(
                "error",
                security.segment,
                "the symbol is in an earlier segment too",
                observed=security.symbol,
            )
        seen.add(security.symbol)
    return tally.result("no securities")


def _segment_prefix(snapshot: SecuritySnapshot) -> CheckResult:
    """The upstream answers a board value it does not know with all of CN, which shows here."""
    tally = CheckTally("snapshot.segment_prefix")
    for security in snapshot.securities:
        prefix = _BOARD_PREFIXES.get(security.segment)
        if prefix is None:
            continue
        tally.checked += 1
        if not security.symbol.startswith(prefix):
            tally.found(
                "error",
                security.segment,
                "the symbol does not belong to this board",
                observed=security.symbol,
                expected=f"{prefix}...",
            )
    return tally.result("no securities on an A-share board")


def _required_fields(snapshot: SecuritySnapshot) -> CheckResult:
    tally = CheckTally("snapshot.required_fields")
    for security in snapshot.securities:
        tally.checked += 1
        if not (security.symbol and security.name):
            tally.found(
                "error",
                security.segment,
                "a row has no symbol or no name",
                observed=security.symbol,
            )
    return tally.result("no securities")


def _symbols_normalizable(snapshot: SecuritySnapshot) -> CheckResult:
    """Only a count of what ``normalize_symbol`` still rejects: the symbols stay usable as data."""
    tally = CheckTally("snapshot.symbols_normalizable")
    for security in snapshot.securities:
        tally.checked += 1
        try:
            normalize_symbol(security.symbol)
        except SymbolNotFound:
            tally.found(
                "info",
                security.segment,
                "normalize_symbol rejects this symbol",
                observed=security.symbol,
            )
    return tally.result("no securities")
