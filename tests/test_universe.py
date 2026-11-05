"""The security snapshot: walking synthetic lists through both clients, completeness, checks."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from itertools import islice
from math import ceil
from string import ascii_uppercase
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import pytest

from xueqiu_api import InvalidParams, UpstreamChanged
from xueqiu_api._pages import Page
from xueqiu_api.endpoints.screener_popularity_rank import ENDPOINT as POPULARITY_RANK
from xueqiu_api.endpoints.screener_popularity_rank import PopularStock
from xueqiu_api.endpoints.screener_quote_list import ENDPOINT as QUOTE_LIST
from xueqiu_api.endpoints.screener_quote_list import ScreenerQuote
from xueqiu_api.quality import MAX_FINDINGS, CheckResult, QualityReport
from xueqiu_api.symbols import Market
from xueqiu_api.universe import (
    SEGMENTS,
    Security,
    SecuritySnapshot,
    Segment,
    SegmentCoverage,
    SnapshotWalk,
    check_snapshot,
)

from .harness import FakeUpstream, Harness, Reply, json_reply, replay_body

STOCK = "stock.xueqiu.com"
LIST_PATH = "/v5/stock/screener/quote/list.json"
RANK_PATH = "/v5/stock/screener/screen.json"
FETCHED_AT = datetime(2026, 11, 5, 7, 0, tzinfo=UTC)
MARKET_CAP = 5000
Lists = Mapping[str, Mapping[str, Sequence[str]]]


def cn_boards() -> dict[str, list[str]]:
    """Synthetic boards with the row counts measured 2026-11-04."""
    return {
        "sha": [f"SH{600000 + n}" for n in range(1700)],
        "kcb": [f"SH{688000 + n}" for n in range(618)],
        "sza": [f"SZ{n:06d}" for n in range(2907)],
        "bj": [f"BJ{920000 + n}" for n in range(354)],
        "shb": [f"SH{900900 + n}" for n in range(41)],
        "szb": [f"SZ{200000 + n}" for n in range(36)],
    }


def cn_symbols() -> list[str]:
    return [symbol for symbols in cn_boards().values() for symbol in symbols]


def hk_symbols(rows: int) -> list[str]:
    return [f"{n:05d}" for n in range(1, rows + 1)]


def us_symbols(rows: int) -> list[str]:
    """Tickers of letters only, and one symbol that ``normalize_symbol`` rejects."""
    letters = ascii_uppercase
    tickers = (f"T{a}{b}{c}" for a in letters for b in letters for c in letters)
    return ["DX-A.CL", *islice(tickers, rows - 1)]


def envelope(count: int, rows: Sequence[Mapping[str, Any]]) -> httpx2.Response:
    body = {"data": {"count": count, "list": rows}, "error_code": 0, "error_description": ""}
    return httpx2.Response(200, json=body)


def page_of(request: httpx2.Request, symbols: Sequence[str]) -> Sequence[str]:
    page, size = int(request.url.params["page"]), int(request.url.params["size"])
    return symbols[(page - 1) * size : page * size]


def quote_row(symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": f"Name {symbol}",
        "type": 11,
        "tick_size": 0.01,
        "lot_size": 100,
        "issue_date_ts": 1612886400000,
        "total_shares": 1000,
        "float_shares": 800,
        "dual_counter_mapping_symbol": None,
    }


def rank_row(symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": f"Name {symbol}",
        "type": 0,
        "current": 1.5,
        "pct": 0.1,
        "follow7d": None,
        "tick_size": 0.01,
        "has_follow": False,
    }


def quote_lists(lists: Lists, *, ignored: Collection[str] = ()) -> Reply:
    """Pages by market, type, order, page and size. A type the upstream ignores answers with all
    of CN, and the whole of CN stops at 5000 rows, as measured."""

    def reply(request: httpx2.Request) -> httpx2.Response:
        query = request.url.params
        assert query["order_by"] == "symbol"
        kind = query["type"]
        if kind in ignored:
            symbols = [symbol for board in lists["CN"].values() for symbol in board]
            symbols = sorted(symbols, reverse=query["order"] == "desc")[:MARKET_CAP]
        else:
            symbols = sorted(lists[query["market"]][kind], reverse=query["order"] == "desc")
        return envelope(len(symbols), [quote_row(symbol) for symbol in page_of(request, symbols)])

    return reply


def ranking(rankings: Mapping[str, Sequence[str]], *, count: int | None = None) -> Reply:
    """Pages by category, page and size, in the order given; ``count`` overrides the real count."""

    def reply(request: httpx2.Request) -> httpx2.Response:
        symbols = rankings[request.url.params["category"]]
        rows = [rank_row(symbol) for symbol in page_of(request, symbols)]
        return envelope(len(symbols) if count is None else count, rows)

    return reply


def after_page(page: int, reply: Reply, change: Callable[[], None]) -> Reply:
    """``reply``, with ``change`` applied once that page has been served."""

    def wrapped(request: httpx2.Request) -> httpx2.Response:
        response = reply(request)
        if int(request.url.params["page"]) == page:
            change()
        return response

    return wrapped


def walk(h: Harness, market: Market, **options: Any) -> SecuritySnapshot:
    """The loop the clients run."""
    state = SnapshotWalk(market, fetched_at=FETCHED_AT, **options)
    while (request := state.next_request()) is not None:
        state.feed(h.call(request.endpoint, **request.params))
    return state.result()


def serve_cn(upstream: FakeUpstream, *, rank_count: int | None = None) -> None:
    upstream.route(STOCK, LIST_PATH, quote_lists({"CN": cn_boards()}))
    upstream.route(STOCK, RANK_PATH, ranking({"CN": cn_symbols()}, count=rank_count))


def serve_us(upstream: FakeUpstream, symbols: Sequence[str]) -> None:
    upstream.route(STOCK, RANK_PATH, ranking({"US": symbols}))


def check(report: QualityReport, name: str) -> CheckResult:
    return next(result for result in report.results if result.check == name)


def security(symbol: str, segment: Segment = "sha", name: str | None = "Name") -> Security:
    return Security(symbol, name, "CN", segment, 11, 0.01)


def snapshot_of(*securities: Security, screen_total: int | None = None) -> SecuritySnapshot:
    rows: Counter[Segment] = Counter(item.segment for item in securities)
    coverage = tuple(SegmentCoverage(seg, n, n, 0, False) for seg, n in rows.items())
    return SecuritySnapshot("CN", FETCHED_AT, securities, coverage, screen_total)


def test_cn_walks_the_six_boards_and_asks_the_ranking_for_its_count(
    h: Harness, upstream: FakeUpstream
) -> None:
    serve_cn(upstream)

    snapshot = walk(h, "CN")

    boards = cn_boards()
    assert snapshot.complete
    assert snapshot.market == "CN"
    assert snapshot.fetched_at == FETCHED_AT
    assert snapshot.screen_total == 5656
    assert snapshot.coverage == tuple(
        SegmentCoverage(board, len(boards[board]), len(boards[board]), 0, False)
        for board in SEGMENTS["CN"]
    )
    assert [item.symbol for item in snapshot.securities] == cn_symbols()
    assert snapshot.securities[0].segment == "sha"
    assert snapshot.securities[-1].segment == "szb"

    sent = upstream.requests(STOCK, LIST_PATH)
    assert len(sent) == 60
    expected = [
        (board, str(page))
        for board in SEGMENTS["CN"]
        for page in range(1, ceil(len(boards[board]) / 100) + 1)
    ]
    assert [(item.params["type"], item.params["page"]) for item in sent] == expected
    assert {item.params["size"] for item in sent} == {"100"}
    assert {item.params["market"] for item in sent} == {"CN"}
    assert {item.params["order_by"] for item in sent} == {"symbol"}
    assert {item.params["order"] for item in sent} == {"asc"}

    [counted] = upstream.requests(STOCK, RANK_PATH)
    assert (counted.params["category"], counted.params["size"]) == ("CN", "1")

    report = check_snapshot(snapshot)
    assert report.ok
    assert {result.status for result in report.results} == {"pass"}


def test_hk_walks_one_list_by_symbol_and_keeps_the_upstream_symbols(
    h: Harness, upstream: FakeUpstream
) -> None:
    symbols = hk_symbols(2875)
    upstream.route(STOCK, LIST_PATH, quote_lists({"HK": {"hk": symbols}}))

    snapshot = walk(h, "HK")

    assert snapshot.complete
    assert snapshot.coverage == (SegmentCoverage("hk", 2875, 2875, 0, False),)
    assert snapshot.screen_total is None
    assert snapshot.securities[0].symbol == "00001"
    sent = upstream.requests(STOCK, LIST_PATH)
    assert [item.params["page"] for item in sent] == [str(page) for page in range(1, 30)]
    assert {(item.params["market"], item.params["type"]) for item in sent} == {("HK", "hk")}
    assert upstream.requests(STOCK, RANK_PATH) == []
    report = check_snapshot(snapshot)
    assert check(report, "snapshot.segment_prefix").status == "skipped"
    assert check(report, "snapshot.total_matches_screen").status == "skipped"
    assert report.ok


@pytest.mark.parametrize(("rows", "pages"), [(7000, 70), (9124, 92), (12000, 120)])
def test_us_walks_the_ranking_and_is_complete_when_unique_rows_equal_its_count(
    h: Harness, upstream: FakeUpstream, rows: int, pages: int
) -> None:
    serve_us(upstream, us_symbols(rows))

    snapshot = walk(h, "US")

    assert snapshot.complete
    assert snapshot.coverage == (SegmentCoverage("us", rows, rows, 0, False),)
    sent = upstream.requests(STOCK, RANK_PATH)
    assert [item.params["page"] for item in sent] == [str(page) for page in range(1, pages + 1)]
    assert {item.params["size"] for item in sent} == {"100"}
    assert {item.params["category"] for item in sent} == {"US"}
    assert upstream.requests(STOCK, LIST_PATH) == []


def test_us_is_not_complete_when_the_rows_stay_under_the_count(
    h: Harness, upstream: FakeUpstream
) -> None:
    upstream.route(STOCK, RANK_PATH, ranking({"US": us_symbols(12000)}, count=12001))

    snapshot = walk(h, "US")

    assert not snapshot.complete
    assert snapshot.coverage == (SegmentCoverage("us", 12001, 12000, 0, False),)
    assert len(upstream.requests(STOCK, RANK_PATH)) == 121


def test_a_list_ending_well_before_its_count_fails_the_page_call(
    h: Harness, upstream: FakeUpstream
) -> None:
    upstream.route(STOCK, RANK_PATH, ranking({"US": us_symbols(250)}, count=12000))

    with pytest.raises(UpstreamChanged, match="has 50 rows of 100"):
        walk(h, "US")


def test_tied_rows_moving_between_pages_make_a_walk_incomplete_without_raising(
    h: Harness, upstream: FakeUpstream
) -> None:
    symbols = us_symbols(250)
    moved, hidden = symbols[50], symbols[150]

    def swap() -> None:
        symbols[50], symbols[150] = symbols[150], symbols[50]

    upstream.route(STOCK, RANK_PATH, after_page(1, ranking({"US": symbols}), swap))

    snapshot = walk(h, "US")

    assert not snapshot.complete
    assert snapshot.coverage == (SegmentCoverage("us", 250, 249, 1, False),)
    found = [item.symbol for item in snapshot.securities]
    assert found.count(moved) == 1
    assert hidden not in found
    report = check_snapshot(snapshot)
    assert not report.ok
    assert check(report, "snapshot.segment_complete").status == "fail"
    assert check(report, "snapshot.unique").status == "pass"


def test_a_page_served_again_makes_a_walk_incomplete_without_raising(
    h: Harness, upstream: FakeUpstream
) -> None:
    symbols = us_symbols(250)

    def first_page_always(request: httpx2.Request) -> httpx2.Response:
        return envelope(250, [rank_row(symbol) for symbol in symbols[:100]])

    upstream.route(STOCK, RANK_PATH, first_page_always)

    snapshot = walk(h, "US")

    assert not snapshot.complete
    assert snapshot.coverage == (SegmentCoverage("us", 250, 100, 200, False),)
    assert len(upstream.requests(STOCK, RANK_PATH)) == 3


def test_a_repeated_row_clears_complete_even_when_unique_rows_reach_the_count(
    h: Harness, upstream: FakeUpstream
) -> None:
    symbols = hk_symbols(150)

    def overlapping(request: httpx2.Request) -> httpx2.Response:
        first = int(request.url.params["page"]) == 1
        rows = symbols[:100] if first else symbols[99:]
        return envelope(150, [quote_row(symbol) for symbol in rows])

    upstream.route(STOCK, LIST_PATH, overlapping)

    snapshot = walk(h, "HK")

    assert not snapshot.complete
    assert snapshot.coverage == (SegmentCoverage("hk", 150, 150, 1, False),)
    assert [item.symbol for item in snapshot.securities] == symbols


def test_a_listing_during_the_walk_makes_it_incomplete(h: Harness, upstream: FakeUpstream) -> None:
    symbols = hk_symbols(250)
    reply = quote_lists({"HK": {"hk": symbols}})
    upstream.route(STOCK, LIST_PATH, after_page(1, reply, lambda: symbols.append("00000")))

    snapshot = walk(h, "HK")

    assert not snapshot.complete
    assert snapshot.coverage == (SegmentCoverage("hk", 251, 250, 1, False),)
    assert "00000" not in {item.symbol for item in snapshot.securities}


def test_cn_stays_complete_with_a_warning_when_the_ranking_count_differs(
    h: Harness, upstream: FakeUpstream
) -> None:
    serve_cn(upstream, rank_count=5657)

    snapshot = walk(h, "CN")

    assert snapshot.complete
    assert snapshot.screen_total == 5657
    report = check_snapshot(snapshot)
    result = check(report, "snapshot.total_matches_screen")
    assert result.status == "fail"
    assert [finding.severity for finding in result.findings] == ["warning"]
    assert (result.findings[0].observed, result.findings[0].expected) == (5656, 5657)
    assert report.ok


def test_a_board_the_upstream_stops_honouring_shows_in_the_prefix_check(
    h: Harness, upstream: FakeUpstream
) -> None:
    boards = cn_boards()
    upstream.route(STOCK, LIST_PATH, quote_lists({"CN": boards}, ignored={"bj"}))
    upstream.route(STOCK, RANK_PATH, ranking({"CN": cn_symbols()}))

    snapshot = walk(h, "CN")

    coverage = {item.segment: item for item in snapshot.coverage}
    assert coverage["bj"] == SegmentCoverage("bj", MARKET_CAP, MARKET_CAP, 0, True)
    assert coverage["shb"].complete
    assert not snapshot.complete
    sent = upstream.requests(STOCK, LIST_PATH)
    assert sum(item.params["type"] == "bj" for item in sent) == 50
    assert {item.params["type"] for item in sent} == set(SEGMENTS["CN"])

    report = check_snapshot(snapshot)
    assert not report.ok
    prefix = check(report, "snapshot.segment_prefix")
    assert prefix.status == "fail"
    assert prefix.total_findings == MARKET_CAP - len(boards["bj"])
    assert len(prefix.findings) == MAX_FINDINGS
    assert {finding.where for finding in prefix.findings} == {"bj"}
    assert check(report, "snapshot.unique").status == "fail"
    assert check(report, "snapshot.segment_complete").findings[0].where == "bj"


def test_an_empty_segment_proves_nothing(h: Harness, upstream: FakeUpstream) -> None:
    upstream.route(STOCK, LIST_PATH, quote_lists({"HK": {"hk": []}}))

    snapshot = walk(h, "HK")

    assert not snapshot.complete
    assert snapshot.coverage == (SegmentCoverage("hk", 0, 0, 0, False),)
    assert len(upstream.requests(STOCK, LIST_PATH)) == 1
    report = check_snapshot(snapshot)
    assert not report.ok
    assert check(report, "snapshot.unique").status == "skipped"


def test_a_ranking_without_a_count_fails_the_page_call(h: Harness, upstream: FakeUpstream) -> None:
    body = {"data": {"list": [rank_row("AAPL")]}, "error_code": 0, "error_description": ""}
    upstream.route(STOCK, RANK_PATH, json_reply(body))

    with pytest.raises(UpstreamChanged, match="screener_popularity_rank"):
        walk(h, "US")


def test_a_page_without_a_count_ends_the_segment_incomplete() -> None:
    state = SnapshotWalk("US", fetched_at=FETCHED_AT)
    row = PopularStock.model_validate(rank_row("AAPL"))

    state.feed(Page((row,), page=1, page_size=100))

    assert state.next_request() is None
    snapshot = state.result()
    assert not snapshot.complete
    assert snapshot.coverage == (SegmentCoverage("us", None, 1, 0, False),)
    assert check(check_snapshot(snapshot), "snapshot.segment_complete").status == "fail"


def test_each_segment_counts_its_own_rows_and_repeats() -> None:
    first, second = (ScreenerQuote.model_validate(quote_row(s)) for s in ("SH600000", "SH688001"))
    state = SnapshotWalk("CN", segments=("sha", "kcb"), fetched_at=FETCHED_AT)

    state.feed(Page((first, first), page=1, page_size=100, total=2))
    state.feed(Page((second,), page=1, page_size=100, total=1))

    assert state.result().coverage == (
        SegmentCoverage("sha", 2, 1, 1, False),
        SegmentCoverage("kcb", 1, 1, 0, False),
    )


def test_cn_without_a_ranking_count_skips_the_comparison() -> None:
    state = SnapshotWalk("CN", fetched_at=FETCHED_AT)
    while (request := state.next_request()) is not None:
        last = request.endpoint == POPULARITY_RANK.name
        state.feed(Page((), page=1, page_size=1, total=None if last else 3))

    snapshot = state.result()

    assert snapshot.screen_total is None
    report = check_snapshot(snapshot)
    assert check(report, "snapshot.total_matches_screen").status == "skipped"


def test_listing_dates_read_in_shanghai_for_every_market() -> None:
    timestamp = 1679328000000
    assert datetime.fromtimestamp(timestamp / 1000, ZoneInfo("America/New_York")).date() == date(
        2023, 3, 20
    )
    data = replay_body("screener_quote_list/us_percent")["data"]
    params = QUOTE_LIST.validate({"market": "US", "order_by": "percent", "order": "desc"})
    state = SnapshotWalk("US", fetched_at=FETCHED_AT)

    state.feed(QUOTE_LIST.parse(data, params))

    row = next(item for item in state.result().securities if item.symbol == "OMH")
    assert row == Security(
        symbol="OMH",
        name=row.name,
        market="US",
        segment="us",
        security_type=0,
        tick_size=0.01,
        lot_size=1,
        listed_on=date(2023, 3, 21),
        total_shares=2128724.0,
    )


def test_ranking_rows_leave_the_listing_fields_empty() -> None:
    data = replay_body("screener_popularity_rank/us_p92")["data"]
    params = POPULARITY_RANK.validate({"market": "US", "page": 92, "page_size": 100})
    state = SnapshotWalk("US", fetched_at=FETCHED_AT)

    state.feed(POPULARITY_RANK.parse(data, params))

    snapshot = state.result()
    assert snapshot.coverage[0].declared == 9124
    assert snapshot.securities[0] == Security(
        symbol=snapshot.securities[0].symbol,
        name=snapshot.securities[0].name,
        market="US",
        segment="us",
        security_type=0,
        tick_size=0.01,
    )


def test_a_symbol_in_two_segments_stays_in_both_and_fails_the_unique_check(
    h: Harness, upstream: FakeUpstream
) -> None:
    boards = cn_boards()
    boards["kcb"][0] = boards["sha"][0]
    upstream.route(STOCK, LIST_PATH, quote_lists({"CN": boards}))
    upstream.route(STOCK, RANK_PATH, ranking({"CN": cn_symbols()}))

    snapshot = walk(h, "CN")

    assert snapshot.complete
    assert [item.symbol for item in snapshot.securities].count(boards["sha"][0]) == 2
    report = check_snapshot(snapshot)
    assert not report.ok
    [finding] = check(report, "snapshot.unique").findings
    assert (finding.where, finding.observed) == ("kcb", boards["sha"][0])


def test_some_of_the_boards_get_no_ranking_request(h: Harness, upstream: FakeUpstream) -> None:
    serve_cn(upstream)

    snapshot = walk(h, "CN", segments=("kcb", "kcb", "bj"))

    assert [item.segment for item in snapshot.coverage] == ["kcb", "bj"]
    assert snapshot.complete
    assert snapshot.screen_total is None
    assert upstream.requests(STOCK, RANK_PATH) == []
    sent = upstream.requests(STOCK, LIST_PATH)
    assert {item.params["type"] for item in sent} == {"kcb", "bj"}
    assert check(check_snapshot(snapshot), "snapshot.total_matches_screen").status == "skipped"


@pytest.mark.parametrize(
    ("market", "segments", "field"),
    [
        ("CN", ("hk",), "segments"),
        ("HK", ("sha",), "segments"),
        ("US", ("hk",), "segments"),
        ("CN", (), "segments"),
        ("JP", None, "market"),
    ],
)
def test_segments_of_another_market_are_refused(market: Any, segments: Any, field: str) -> None:
    with pytest.raises(InvalidParams) as caught:
        SnapshotWalk(market, segments=segments, fetched_at=FETCHED_AT)

    assert caught.value.field == field
    assert caught.value.valid_values


def test_the_request_stays_until_fed_and_the_result_waits_for_the_end() -> None:
    state = SnapshotWalk("HK", fetched_at=FETCHED_AT)
    request = state.next_request()

    assert request is not None
    assert state.next_request() == request
    assert request.endpoint == QUOTE_LIST.name
    with pytest.raises(ValueError, match="not finished"):
        state.result()


@pytest.mark.parametrize(
    ("coverage", "complete"),
    [
        (SegmentCoverage("hk", 3, 3, 0, False), True),
        (SegmentCoverage("hk", 3, 2, 0, False), False),
        (SegmentCoverage("hk", 3, 3, 1, False), False),
        (SegmentCoverage("hk", 5000, 5000, 0, True), False),
        (SegmentCoverage("hk", None, 3, 0, False), False),
        (SegmentCoverage("hk", 0, 0, 0, False), False),
    ],
)
def test_the_segment_check_agrees_with_complete(coverage: SegmentCoverage, complete: bool) -> None:
    snapshot = SecuritySnapshot("HK", FETCHED_AT, (), (coverage,), None)

    assert coverage.complete is complete
    assert snapshot.complete is complete
    result = check(check_snapshot(snapshot), "snapshot.segment_complete")
    assert result.checked == 1
    assert result.status == ("pass" if complete else "fail")
    assert [finding.severity for finding in result.findings] == ([] if complete else ["error"])


def test_one_incomplete_segment_makes_the_snapshot_incomplete() -> None:
    good = SegmentCoverage("sha", 3, 3, 0, False)
    bad = SegmentCoverage("kcb", 3, 2, 0, False)

    assert SecuritySnapshot("CN", FETCHED_AT, (), (good, good), None).complete
    assert not SecuritySnapshot("CN", FETCHED_AT, (), (good, bad), None).complete


def test_the_total_is_compared_with_the_rows_of_the_boards() -> None:
    snapshot = snapshot_of(security("SH600000"), security("SZ000001", "sza"), screen_total=2)
    matching = check(check_snapshot(snapshot), "snapshot.total_matches_screen")
    assert (matching.status, matching.checked) == ("pass", 1)

    differing = check(
        check_snapshot(replace(snapshot, screen_total=3)), "snapshot.total_matches_screen"
    )
    assert differing.status == "fail"
    assert (differing.findings[0].observed, differing.findings[0].expected) == (2, 3)

    short = replace(snapshot, coverage=(SegmentCoverage("sha", 2, 1, 0, False),), screen_total=2)
    assert check(check_snapshot(short), "snapshot.total_matches_screen").status == "fail"


def test_the_prefix_check_knows_each_board() -> None:
    good = snapshot_of(
        security("SH600000", "sha"),
        security("SH688001", "kcb"),
        security("SH900901", "shb"),
        security("SZ000001", "sza"),
        security("SZ200011", "szb"),
        security("BJ920000", "bj"),
    )
    assert check(check_snapshot(good), "snapshot.segment_prefix").status == "pass"
    assert check(check_snapshot(good), "snapshot.segment_prefix").checked == 6

    misplaced: list[tuple[str, Segment]] = [
        ("SZ000001", "sha"),
        ("SZ000001", "kcb"),
        ("SZ000001", "shb"),
        ("SH600000", "sza"),
        ("SH600000", "szb"),
        ("SH600000", "bj"),
    ]
    for symbol, segment in misplaced:
        bad = check_snapshot(snapshot_of(security(symbol, segment)))
        result = check(bad, "snapshot.segment_prefix")
        assert (result.status, result.total_findings) == ("fail", 1)
        assert not bad.ok


@pytest.mark.parametrize(
    "row", [security("SH600000", name=None), security("SH600000", name=""), security("")]
)
def test_a_row_needs_a_symbol_and_a_name(row: Security) -> None:
    report = check_snapshot(snapshot_of(security("SH600004"), row))

    result = check(report, "snapshot.required_fields")
    assert (result.status, result.checked, result.total_findings) == ("fail", 2, 1)
    assert not report.ok


def test_rejected_symbols_are_counted_without_failing(h: Harness, upstream: FakeUpstream) -> None:
    serve_us(upstream, us_symbols(150))

    report = check_snapshot(walk(h, "US"))

    result = check(report, "snapshot.symbols_normalizable")
    assert (result.status, result.checked, result.total_findings) == ("pass", 150, 1)
    [finding] = result.findings
    assert (finding.severity, finding.where, finding.observed) == ("info", "us", "DX-A.CL")
    assert report.ok


def test_findings_are_capped_and_counted() -> None:
    rows = [security(f"SH{600000 + n}", name=None) for n in range(MAX_FINDINGS + 5)]

    result = check(check_snapshot(snapshot_of(*rows)), "snapshot.required_fields")

    assert len(result.findings) == MAX_FINDINGS
    assert result.total_findings == MAX_FINDINGS + 5
