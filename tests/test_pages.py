"""Walking numbered pages and bar history, for both clients, on synthetic upstream lists."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from itertools import pairwise
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel, ConfigDict, Field

from xueqiu_api import (
    Blocked,
    Client,
    InvalidParams,
    RateLimited,
    UpstreamChanged,
)
from xueqiu_api._pages import Page, PageWalk, finish
from xueqiu_api.endpoints.kline import BarWalk
from xueqiu_api.models import Kline
from xueqiu_api.registry import Endpoint, Pagination
from xueqiu_api.sites import Host

from .harness import Harness, Register, json_reply, synthetic_endpoint

STOCK = "stock.xueqiu.com"
WWW = "www.xueqiu.com"
DANJUAN = "danjuanfunds.com"


class Row(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    id: str


class ListParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)


def query(params: ListParams, now: datetime) -> dict[str, str]:
    return {"page": str(params.page), "size": str(params.page_size)}


def parse_count(payload: Any, params: ListParams) -> Page[Row]:
    return Page(
        tuple(Row.model_validate(row) for row in payload["list"]),
        page=params.page,
        page_size=params.page_size,
        total=payload["count"],
    )


def parse_last_page(payload: Any, params: ListParams) -> Page[Row]:
    return Page(
        tuple(Row.model_validate(row) for row in payload["list"]),
        page=params.page,
        page_size=params.page_size,
        last_page=payload["pages"],
    )


def parse_bare(payload: Any, params: ListParams) -> Page[Row]:
    return Page(
        tuple(Row.model_validate(row) for row in payload["items"]),
        page=params.page,
        page_size=params.page_size,
    )


def spec(name: str, host: Host, parse: Any, rule: Pagination) -> Endpoint[Any, Any]:
    return synthetic_endpoint(
        name,
        host=host,
        path=f"/{name}.json",
        params=ListParams,
        build_query=query,
        parse=parse,
        pagination=rule,
    )


def key(row: Row) -> str:
    return row.id


SCREENER = spec(
    "screener",
    STOCK,
    parse_count,
    Pagination(
        style="page",
        end="total",
        size_cap=100,
        total_cap=5000,
        key=key,
        cap_hint="Split by board: sha, kcb, sza, bj.",
    ),
)
MARGIN = spec("margin", STOCK, parse_bare, Pagination(style="page", size_cap=100, key=key))
BLOCK = spec("block", STOCK, parse_bare, Pagination(style="page", size_cap=100))
SHORT = spec("shortend", STOCK, parse_bare, Pagination(style="page", end="short", size_cap=100))
RANK = spec("rank", WWW, parse_count, Pagination(style="page", end="total", size_cap=100, key=key))
FUND = spec("fund", DANJUAN, parse_count, Pagination(style="page", end="total", size_cap=100))
PAGES = spec(
    "pages", STOCK, parse_last_page, Pagination(style="page", end="total", size_cap=100, key=key)
)


def rows(n: int, start: int = 0) -> list[dict[str, str]]:
    return [{"id": f"r{i:05d}"} for i in range(start, start + n)]


def row_ids(all_rows: Sequence[dict[str, str]]) -> list[str]:
    return [row["id"] for row in all_rows]


def page_of(
    all_rows: Sequence[dict[str, str]], request: httpx2.Request, max_size: int, fallback: int
) -> list[dict[str, str]]:
    """The rows the query asks for; a size above ``max_size`` falls back without an error."""
    page = int(request.url.params["page"])
    size = int(request.url.params["size"])
    if size > max_size:
        size = fallback
    return list(all_rows[(page - 1) * size : page * size])


def counted(
    all_rows: Sequence[dict[str, str]],
    *,
    count: int | None = None,
    cap: int | None = None,
    max_size: int = 100,
    fallback: int = 30,
) -> Any:
    """A list that stops at ``cap`` and reports ``count``."""
    served = all_rows if cap is None else all_rows[:cap]

    def reply(request: httpx2.Request) -> httpx2.Response:
        chunk = page_of(served, request, max_size, fallback)
        total = len(served) if count is None else count
        return httpx2.Response(
            200,
            json={"data": {"count": total, "list": chunk}, "error_code": 0},
        )

    return reply


def paged(all_rows: Sequence[dict[str, str]], *, pages: int, max_size: int = 100) -> Any:
    """A list that reports its number of pages."""

    def reply(request: httpx2.Request) -> httpx2.Response:
        chunk = page_of(all_rows, request, max_size, 30)
        return httpx2.Response(200, json={"data": {"pages": pages, "list": chunk}, "error_code": 0})

    return reply


def bare(all_rows: Sequence[dict[str, str]], *, max_size: int = 100, fallback: int = 30) -> Any:
    """A list that reports no total."""

    def reply(request: httpx2.Request) -> httpx2.Response:
        chunk = page_of(all_rows, request, max_size, fallback)
        return httpx2.Response(200, json={"data": {"items": chunk}, "error_code": 0})

    return reply


def ids(pages: Sequence[Page[Row]]) -> list[str]:
    return [row.id for page in pages for row in page]


def sent_pages(h: Harness, path: str) -> list[int]:
    return [int(sent.params["page"]) for sent in h.upstream.requests(path=path)]


# ---- what ends a walk


def test_total_stops_without_extra_request(h: Harness) -> None:
    h.upstream.route(STOCK, "/screener.json", counted(rows(250)))
    pages, exc = h.walk(SCREENER)
    assert exc is None and len(ids(pages)) == 250
    assert sent_pages(h, "/screener.json") == [1, 2, 3]
    assert pages[-1].next_params is None and pages[0].next_params["page"] == 2


def test_exact_multiple_stops_without_extra_request(h: Harness) -> None:
    h.upstream.route(STOCK, "/screener.json", counted(rows(300)))
    _, exc = h.walk(SCREENER)
    assert exc is None and sent_pages(h, "/screener.json") == [1, 2, 3]


def test_last_page_stops_without_extra_request(h: Harness) -> None:
    h.upstream.route(STOCK, "/pages.json", paged(rows(250), pages=3))
    pages, exc = h.walk(PAGES)
    assert exc is None and len(ids(pages)) == 250
    assert sent_pages(h, "/pages.json") == [1, 2, 3]
    assert [page.next_params is None for page in pages] == [False, False, True]


def test_a_short_page_before_the_last_page_raises(h: Harness) -> None:
    h.upstream.route(STOCK, "/pages.json", paged(rows(250), pages=3, max_size=50))
    pages, exc = h.walk(PAGES)
    assert isinstance(exc, UpstreamChanged) and pages == []
    assert sent_pages(h, "/pages.json") == [1]


def test_default_page_size_is_the_cap(h: Harness) -> None:
    h.upstream.route(STOCK, "/screener.json", counted(rows(10)))
    h.walk(SCREENER)
    assert h.upstream.requests(path="/screener.json")[0].params["size"] == "100"


def test_a_page_size_of_none_means_the_cap(h: Harness) -> None:
    h.upstream.route(STOCK, "/screener.json", counted(rows(10)))
    h.walk(SCREENER, page_size=None)
    assert h.upstream.requests(path="/screener.json")[0].params["size"] == "100"


def test_no_total_needs_one_empty_page(h: Harness) -> None:
    h.upstream.route(STOCK, "/margin.json", bare(rows(250)))
    pages, exc = h.walk(MARGIN)
    assert exc is None and len(ids(pages)) == 250
    assert sent_pages(h, "/margin.json") == [1, 2, 3, 4]
    assert [page.short for page in pages] == [False, False, True, True]


def test_measured_short_page_ends(h: Harness) -> None:
    h.upstream.route(STOCK, "/shortend.json", bare(rows(250)))
    pages, exc = h.walk(SHORT)
    assert exc is None and sent_pages(h, "/shortend.json") == [1, 2, 3]
    assert not any(page.short for page in pages)


def test_a_page_without_the_total_it_declares_is_a_shape_error(h: Harness) -> None:
    missing = spec("margin", STOCK, parse_bare, Pagination(style="page", end="total", size_cap=100))
    h.upstream.route(STOCK, "/margin.json", bare(rows(10)))
    _, exc = h.walk(missing)
    assert isinstance(exc, UpstreamChanged)


def test_silent_fallback_with_total_raises_on_page_one(h: Harness) -> None:
    h.upstream.route(STOCK, "/screener.json", counted(rows(250), max_size=50))
    pages, exc = h.walk(SCREENER)
    assert isinstance(exc, UpstreamChanged) and pages == []
    assert sent_pages(h, "/screener.json") == [1]


def test_silent_fallback_without_total_raises_on_page_two(h: Harness) -> None:
    h.upstream.route(STOCK, "/margin.json", bare(rows(250), max_size=50))
    pages, exc = h.walk(MARGIN)
    assert isinstance(exc, UpstreamChanged) and len(ids(pages)) == 30
    assert exc.params["page"] == 2
    assert sent_pages(h, "/margin.json") == [1, 2]


def test_rows_after_a_short_page_pass_when_the_guard_is_off(h: Harness) -> None:
    unguarded = spec(
        "margin", STOCK, parse_bare, Pagination(style="page", size_cap=100, guard_short=False)
    )
    h.upstream.route(
        STOCK,
        "/margin.json",
        json_reply({"data": {"items": rows(3)}, "error_code": 0}),
        json_reply({"data": {"items": rows(3, start=3)}, "error_code": 0}),
        json_reply({"data": {"items": []}, "error_code": 0}),
    )
    pages, exc = h.walk(unguarded)
    assert exc is None and len(ids(pages)) == 6


# ---- the most rows the upstream serves


def test_cap_fails_after_one_request(h: Harness) -> None:
    h.upstream.route(STOCK, "/screener.json", counted(rows(5200), cap=5000))
    pages, exc = h.walk(SCREENER)
    assert isinstance(exc, InvalidParams) and pages == []
    assert "sha" in (exc.hint or "") and sent_pages(h, "/screener.json") == [1]


def test_cap_accepted_by_max_items(h: Harness) -> None:
    h.upstream.route(STOCK, "/screener.json", counted(rows(5200), cap=5000))
    pages, exc = h.walk(SCREENER, max_items=150)
    assert exc is None and len(ids(pages)) == 150 and pages[0].capped
    assert sent_pages(h, "/screener.json") == [1, 2]
    assert pages[-1].next_params is not None and pages[-1].next_params["page"] == 2


def test_cap_walk_stops_at_page_fifty(h: Harness) -> None:
    h.upstream.route(STOCK, "/screener.json", counted(rows(5200), cap=5000))
    pages, exc = h.walk(SCREENER, max_pages=60)
    assert exc is None and len(ids(pages)) == 5000
    assert sent_pages(h, "/screener.json") == list(range(1, 51))
    assert pages[-1].next_params is None


def test_count_above_what_the_list_serves_raises(h: Harness) -> None:
    h.upstream.route(STOCK, "/screener.json", counted(rows(5000), count=5656))
    rule = Pagination(style="page", end="total", size_cap=100, key=key)
    pages, exc = h.walk(spec("screener", STOCK, parse_count, rule))
    assert isinstance(exc, UpstreamChanged) and len(ids(pages)) == 5000
    assert sent_pages(h, "/screener.json")[-1] == 51


def test_count_above_the_cap_stops_at_the_cap(h: Harness) -> None:
    h.upstream.route(STOCK, "/screener.json", counted(rows(5000), count=5656))
    pages, exc = h.walk(SCREENER, max_pages=60)
    assert exc is None and len(ids(pages)) == 5000 and pages[-1].capped
    assert sent_pages(h, "/screener.json")[-1] == 50


def test_a_page_past_the_cap_fails_before_any_request(
    h: Harness, register_endpoint: Register
) -> None:
    register_endpoint(SCREENER)
    h.upstream.route(STOCK, "/screener.json", counted(rows(10)))

    pages, exc = h.walk(SCREENER, page=51, page_size=100)
    with pytest.raises(InvalidParams) as single:
        h.call("screener", page=51, page_size=100)

    assert pages == [] and isinstance(exc, InvalidParams)
    assert "sha" in (exc.hint or "")
    assert single.value.field == "page"
    assert h.upstream.requests(path="/screener.json") == []


def test_the_last_page_inside_the_cap_is_allowed() -> None:
    SCREENER.validate({"page": 50, "page_size": 100})
    SCREENER.validate({"page": 51, "page_size": 98})
    with pytest.raises(InvalidParams):
        SCREENER.validate({"page": 51, "page_size": 100})


# ---- repeats and drift


def test_shift_between_pages_dedupes_and_loses_nothing(h: Harness) -> None:
    data = rows(250, start=1)
    state = {"calls": 0}

    def reply(request: httpx2.Request) -> httpx2.Response:
        state["calls"] += 1
        page = int(request.url.params["page"])
        current = ([{"id": "r00000"}] if state["calls"] > 1 else []) + data
        chunk = current[(page - 1) * 100 : page * 100]
        return httpx2.Response(
            200, json={"data": {"count": len(current), "list": chunk}, "error_code": 0}
        )

    h.upstream.route(STOCK, "/screener.json", reply)
    pages, exc = h.walk(SCREENER)
    got = ids(pages)
    assert exc is None and len(got) == len(set(got)) == 250
    assert set(got) == set(row_ids(data))


def test_page_parameter_ignored_raises(h: Harness) -> None:
    h.upstream.route(
        STOCK, "/margin.json", json_reply({"data": {"items": rows(100)}, "error_code": 0})
    )
    pages, exc = h.walk(MARGIN)
    assert isinstance(exc, UpstreamChanged) and len(pages) == 1
    assert sent_pages(h, "/margin.json") == [1, 2]


def test_page_parameter_ignored_without_key_raises(h: Harness) -> None:
    h.upstream.route(
        STOCK, "/block.json", json_reply({"data": {"items": rows(100)}, "error_code": 0})
    )
    _, exc = h.walk(BLOCK)
    assert isinstance(exc, UpstreamChanged) and sent_pages(h, "/block.json") == [1, 2]


def test_a_walk_that_never_ends_fails_at_the_runaway_limit(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("xueqiu_api._pages.RUNAWAY_PAGES", 5)

    def endless(request: httpx2.Request) -> httpx2.Response:
        page = int(request.url.params["page"])
        return httpx2.Response(
            200, json={"data": {"items": rows(100, start=page * 100)}, "error_code": 0}
        )

    h.upstream.route(STOCK, "/margin.json", endless)

    pages, exc = h.walk(MARGIN)

    assert isinstance(exc, UpstreamChanged) and len(pages) == 4
    assert exc.params["page"] == 5 and sent_pages(h, "/margin.json") == [1, 2, 3, 4, 5]


def test_an_explicit_max_pages_goes_past_the_runaway_limit(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("xueqiu_api._pages.RUNAWAY_PAGES", 5)
    h.upstream.route(STOCK, "/margin.json", bare(rows(800)))

    pages, exc = h.walk(MARGIN, max_pages=7)

    assert exc is None and len(pages) == 7 and pages[-1].next_params is not None


# ---- errors in the middle, and resuming


def test_block_mid_iteration_then_resume_from_error_params(h: Harness) -> None:
    good = bare(rows(250))
    calls = {"n": 0}

    def reply(request: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        if calls["n"] == 3:
            return httpx2.Response(418, text="teapot")
        return good(request)

    h.upstream.route(STOCK, "/margin.json", reply)
    pages, exc = h.walk(MARGIN)
    assert isinstance(exc, Blocked) and len(ids(pages)) == 200
    assert exc.params["page"] == 3 and exc.params["page_size"] == 100
    assert exc.retry_after_s
    h.clock.advance(301)
    rest, again = h.walk(MARGIN, **json.loads(json.dumps(exc.params)))
    assert again is None and ids(pages) + ids(rest) == row_ids(rows(250))


def test_5xx_is_retried_inside_the_page(h: Harness) -> None:
    good = bare(rows(150))
    calls = {"n": 0}

    def reply(request: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        if calls["n"] == 2:
            return httpx2.Response(503, text="busy")
        return good(request)

    h.upstream.route(STOCK, "/margin.json", reply)
    pages, exc = h.walk(MARGIN)
    assert exc is None and len(ids(pages)) == 150
    assert sent_pages(h, "/margin.json") == [1, 2, 2, 3]


def test_rate_limit_mid_iteration_pauses_host_and_resumes(h: Harness) -> None:
    good = counted(rows(250))
    calls = {"n": 0}

    def reply(request: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        if calls["n"] == 2:
            return httpx2.Response(
                400, json={"error_code": 31005, "error_description": "synthetic rate limit"}
            )
        return good(request)

    h.upstream.route(WWW, "/rank.json", reply)
    pages, exc = h.walk(RANK)
    assert isinstance(exc, RateLimited) and exc.params["page"] == 2 and exc.retry_after_s
    early, blocked = h.walk(RANK, **exc.params)
    assert early == [] and isinstance(blocked, RateLimited)
    h.clock.advance(exc.retry_after_s + 1)
    rest, again = h.walk(RANK, **exc.params)
    assert again is None and len(ids(pages) + ids(rest)) == 250


def test_next_params_can_be_stored_as_json_and_resume(h: Harness) -> None:
    h.upstream.route(STOCK, "/screener.json", counted(rows(300)))
    pages, exc = h.walk(SCREENER, max_pages=2)
    assert exc is None and len(pages) == 2 and pages[-1].next_params is not None
    stored = json.loads(json.dumps(pages[-1].next_params))
    rest, again = h.walk(SCREENER, **stored)
    assert again is None and len(ids(pages) + ids(rest)) == 300


def test_max_items_next_params_never_skips_a_row(h: Harness) -> None:
    h.upstream.route(STOCK, "/screener.json", counted(rows(300)))
    pages, exc = h.walk(SCREENER, max_items=150)
    assert exc is None and len(ids(pages)) == 150
    assert pages[-1].next_params is not None and pages[-1].next_params["page"] == 2
    rest, _ = h.walk(SCREENER, **pages[-1].next_params)
    assert set(ids(pages)) | set(ids(rest)) == set(row_ids(rows(300)))


# ---- pacing and deadlines


def test_pages_are_spaced_by_the_host_limiter(h: Harness) -> None:
    h.upstream.route(STOCK, "/screener.json", counted(rows(300)))
    h.upstream.route(WWW, "/rank.json", counted(rows(300)))
    h.upstream.route(DANJUAN, "/fund.json", counted(rows(300)))
    h.walk(SCREENER)
    h.walk(RANK)
    h.walk(FUND)
    for path, gap in (("/screener.json", 1.0), ("/rank.json", 3.0), ("/fund.json", 1.0)):
        times = [sent.at for sent in h.upstream.requests(path=path)]
        assert len(times) == 3
        assert all(b - a >= gap for a, b in pairwise(times))


def test_longer_interval_applies_between_pages(make: Callable[..., Harness]) -> None:
    h = make(min_intervals={STOCK: 2.5})
    h.upstream.route(STOCK, "/screener.json", counted(rows(300)))
    h.walk(SCREENER)
    times = [sent.at for sent in h.upstream.requests(path="/screener.json")]
    assert all(b - a >= 2.5 for a, b in pairwise(times))


def test_each_page_has_its_own_deadline(make: Callable[..., Harness]) -> None:
    h = make(deadline=30.0)
    good = counted(rows(300))

    def slow(request: httpx2.Request) -> httpx2.Response:
        h.clock.sleep(20.0)
        return good(request)

    h.upstream.route(STOCK, "/screener.json", slow)
    pages, exc = h.walk(SCREENER)
    assert exc is None and len(pages) == 3
    assert sum(h.clock.sleeps) >= 60.0


def test_queue_past_the_deadline_is_rate_limited_mid_iteration(
    make: Callable[..., Harness],
) -> None:
    h = make(deadline=2.0, min_intervals={STOCK: 5.0})
    h.upstream.route(STOCK, "/screener.json", counted(rows(300)))
    pages, exc = h.walk(SCREENER)
    assert isinstance(exc, RateLimited) and len(pages) == 1 and exc.params["page"] == 2


def test_stop_early_sends_nothing_more(h: Harness) -> None:
    h.upstream.route(STOCK, "/screener.json", counted(rows(300)))
    pages, exc = h.walk(SCREENER, stop_after=1)
    assert exc is None and pages[0].page == 1
    assert sent_pages(h, "/screener.json") == [1]


def test_invalid_params_raise_before_iterating_and_before_any_request(h: Harness) -> None:
    h.upstream.route(STOCK, "/screener.json", counted(rows(10)))
    client = h.client
    with pytest.raises(InvalidParams):
        client._pages(SCREENER, {"page_size": 101})  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(InvalidParams):
        client._pages(SCREENER, {"nope": 1})  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(InvalidParams):
        client._pages(SCREENER, {}, max_pages=0)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(InvalidParams):
        client._pages(SCREENER, {}, max_items=0)  # pyright: ignore[reportPrivateUsage]
    assert h.upstream.requests(path="/screener.json") == []


def test_two_walks_share_the_limiter(h: Harness) -> None:
    h.upstream.route(STOCK, "/screener.json", counted(rows(300)))
    h.upstream.route(STOCK, "/margin.json", bare(rows(300)))
    client = h.client
    if isinstance(client, Client):

        def drain(walked: Endpoint[Any, Any]) -> None:
            for _ in client._pages(walked, {}):  # pyright: ignore[reportPrivateUsage]
                pass

        threads = [threading.Thread(target=drain, args=(one,)) for one in (SCREENER, MARGIN)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    else:

        async def one(walked: Endpoint[Any, Any]) -> list[Any]:
            return [page async for page in client._pages(walked, {})]  # pyright: ignore[reportPrivateUsage]

        async def both() -> None:
            await asyncio.gather(one(SCREENER), one(MARGIN))

        assert h._runner is not None  # pyright: ignore[reportPrivateUsage]
        h._runner.run(both())  # pyright: ignore[reportPrivateUsage]
    times = sorted(sent.at for sent in h.upstream.requests(host=STOCK))
    assert len(times) == 3 + 4
    assert all(b - a >= 1.0 - 1e-9 for a, b in pairwise(times))


# ---- one page outside a walk


def test_a_single_call_sets_the_paging_fields(h: Harness, register_endpoint: Register) -> None:
    register_endpoint(MARGIN)
    h.upstream.route(STOCK, "/margin.json", bare(rows(20)))

    full = h.call("margin", page=2, page_size=7)
    short = h.call("margin", page=3, page_size=8)

    assert full.next_params == {"page": 3, "page_size": 7} and not full.short
    assert short.next_params == {"page": 4, "page_size": 8} and short.short


def test_a_single_call_reports_a_page_that_contradicts_its_total(
    h: Harness, register_endpoint: Register
) -> None:
    register_endpoint(SCREENER)
    h.upstream.route(STOCK, "/screener.json", counted(rows(250), max_size=50))

    with pytest.raises(UpstreamChanged) as caught:
        h.call("screener", page=1, page_size=100)

    assert caught.value.params["page"] == 1


def test_a_single_call_only_marks_a_capped_list(h: Harness, register_endpoint: Register) -> None:
    register_endpoint(SCREENER)
    h.upstream.route(STOCK, "/screener.json", counted(rows(5200), cap=5000))

    page = h.call("screener", page=1, page_size=100)

    assert page.capped and page.next_params is not None


def test_a_page_iterates_over_its_rows_and_has_a_length() -> None:
    page = Page(("a", "b"), page=1, page_size=2)

    assert list(page) == ["a", "b"] and len(page) == 2
    assert not Page((), page=1, page_size=2)


@pytest.mark.parametrize(
    ("end", "rows_on_page", "short", "more"),
    [
        ("empty", 2, False, True),
        ("empty", 1, True, True),
        ("empty", 0, True, False),
        ("short", 2, False, True),
        ("short", 1, False, False),
    ],
)
def test_short_is_set_only_for_lists_that_end_on_an_empty_page(
    end: Any, rows_on_page: int, short: bool, more: bool
) -> None:
    page = Page(("x",) * rows_on_page, page=1, page_size=2)

    done = finish(Pagination(style="page", end=end), page, {"page": 1})

    assert done.short is short
    assert (done.next_params is not None) is more


def test_a_walk_keeps_a_subclass_and_its_fields() -> None:
    @dataclass(frozen=True, slots=True)
    class Bonus(Page[str]):
        extra: tuple[str, ...] = ()

    walk = PageWalk("bonus", Pagination(style="page"), {"page": 1}, max_pages=None, max_items=None)
    rule = Pagination(style="page")

    page = walk.accept(finish(rule, Bonus(("a",), page=1, page_size=9, extra=("b",)), {"page": 1}))

    assert isinstance(page, Bonus) and page.extra == ("b",)


# ---- kline_history


KLINE_PATH = "/v5/stock/chart/kline.json"
SHANGHAI = timezone(timedelta(hours=8))
DAY_MS = 86_400_000
BASE = int(datetime(1990, 12, 19, tzinfo=SHANGHAI).timestamp() * 1000)


def bars_reply(stamps: Sequence[int]) -> Any:
    """Walks back from ``begin``, inclusive, oldest first; the shape of no bars as measured."""

    def reply(request: httpx2.Request) -> httpx2.Response:
        begin = int(request.url.params["begin"])
        count = int(request.url.params["count"])
        assert count < 0
        chosen = [stamp for stamp in stamps if stamp <= begin][count:]
        names = ["timestamp", "close"] if chosen else []
        return httpx2.Response(
            200,
            json={
                "data": {
                    "symbol": "SH000001",
                    "column": names,
                    "item": [[stamp, 1.0] for stamp in chosen],
                },
                "error_code": 0,
                "error_description": "",
            },
        )

    return reply


def history(h: Harness, symbol: str, **params: Any) -> Kline:
    return h.run("kline_history", symbol, **params)


def days(n: int) -> list[int]:
    return [BASE + DAY_MS * index for index in range(n)]


def test_full_history_under_the_cap_is_one_request(h: Harness) -> None:
    h.upstream.route(STOCK, KLINE_PATH, bars_reply(days(8756)))
    kline = history(h, "SH000001")
    assert len(kline) == 8756 and len(h.upstream.requests(path=KLINE_PATH)) == 1
    assert list(kline.timestamps) == sorted(kline.timestamps)


def test_series_past_the_cap_takes_a_second_request_without_duplicates(h: Harness) -> None:
    h.upstream.route(STOCK, KLINE_PATH, bars_reply(days(12_000)))
    kline = history(h, "SH000001")
    sent = h.upstream.requests(path=KLINE_PATH)
    assert len(sent) == 2 and len(kline) == 12_000 and len(set(kline.timestamps)) == 12_000
    assert list(kline.timestamps) == sorted(kline.timestamps)
    assert all(int(one.params["count"]) == -10_000 for one in sent)
    assert kline.columns["close"] == (1.0,) * 12_000


def test_minute_series_past_the_cap_pages_by_moment(h: Harness) -> None:
    start = int(datetime(2026, 1, 5, 9, 30, tzinfo=SHANGHAI).timestamp() * 1000)
    stamps = [start + 60_000 * index for index in range(12_000)]
    h.upstream.route(STOCK, KLINE_PATH, bars_reply(stamps))

    kline = history(h, "SH600000", period="1m", end=date(2026, 12, 31))

    sent = h.upstream.requests(path=KLINE_PATH)
    assert len(sent) == 2 and len(kline) == 12_000
    assert int(sent[1].params["begin"]) == stamps[2_000] - 1


def test_empty_second_page_ends(h: Harness) -> None:
    h.upstream.route(STOCK, KLINE_PATH, bars_reply(days(10_000)))
    kline = history(h, "SH000001")
    assert len(kline) == 10_000 and len(h.upstream.requests(path=KLINE_PATH)) == 2


def test_no_bars_at_all_is_an_empty_result(h: Harness) -> None:
    h.upstream.route(STOCK, KLINE_PATH, bars_reply([]))

    assert len(history(h, "SH000001")) == 0
    assert len(h.kline("SH000001")) == 0


def test_no_bars_in_a_one_day_range_is_one_request(h: Harness) -> None:
    h.upstream.route(STOCK, KLINE_PATH, bars_reply([]))

    kline = history(h, "SH000001", begin=date(2000, 1, 1), end=date(2000, 1, 1))

    assert len(kline) == 0 and len(h.upstream.requests(path=KLINE_PATH)) == 1


def test_begin_bounds_the_walk_and_trims(h: Harness) -> None:
    h.upstream.route(STOCK, KLINE_PATH, bars_reply(days(8756)))
    begin = date(2000, 1, 1)
    end = date(2000, 3, 1)
    kline = history(h, "SH000001", begin=begin, end=end)
    [sent] = h.upstream.requests(path=KLINE_PATH)
    assert kline.timestamps[0].date() == begin and kline.timestamps[-1].date() == end
    assert int(sent.params["count"]) == -61


def test_minute_bars_stop_at_retention_in_one_request(h: Harness) -> None:
    start = int(datetime(2026, 11, 2, 9, 38, tzinfo=SHANGHAI).timestamp() * 1000)
    stamps = [start + 60_000 * index for index in range(707)]
    h.upstream.route(STOCK, KLINE_PATH, bars_reply(stamps))
    kline = history(h, "SH600000", period="1m")
    assert len(kline) == 707 and len(h.upstream.requests(path=KLINE_PATH)) == 1


def test_block_on_the_second_kline_request_raises(h: Harness) -> None:
    good = bars_reply(days(12_000))
    calls = {"n": 0}

    def reply(request: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        return httpx2.Response(418, text="x") if calls["n"] == 2 else good(request)

    h.upstream.route(STOCK, KLINE_PATH, reply)
    with pytest.raises(Blocked):
        history(h, "SH000001")


def test_bad_arguments_raise_before_any_request(h: Harness) -> None:
    with pytest.raises(InvalidParams):
        history(h, "SH000001", period="daily")
    with pytest.raises(InvalidParams):
        history(h, "SH000001", begin=date(2020, 1, 2), end=date(2020, 1, 1))
    assert h.upstream.requests(path=KLINE_PATH) == []


def walk_of(*, begin: date | None = None, end: date | None = None) -> BarWalk:
    return BarWalk(
        "SH000001",
        period="day",
        adjust="before",
        begin=begin,
        end=end,
        now=datetime(2026, 11, 5, tzinfo=UTC),
    )


def bars(count: int, stamp: datetime) -> Kline:
    return Kline(
        symbol="SH000001",
        period="day",
        adjust="before",
        timestamps=(stamp,) * count,
        columns={},
    )


def test_a_page_one_short_may_still_be_a_full_one() -> None:
    walk = walk_of()

    walk.accept(bars(9_999, datetime(2000, 1, 5, tzinfo=SHANGHAI)))

    assert walk.params is not None
    assert walk.params["end"] == date(2000, 1, 4) and walk.params["count"] == 10_000


def test_a_page_two_short_ends_the_walk() -> None:
    walk = walk_of()

    walk.accept(bars(9_998, datetime(2000, 1, 5, tzinfo=SHANGHAI)))

    assert walk.params is None


def test_a_history_that_never_ends_fails() -> None:
    walk = walk_of(begin=date(2000, 1, 1), end=date(2000, 1, 2))
    stamp = datetime(2000, 1, 2, tzinfo=SHANGHAI)

    for _ in range(19):
        walk.accept(bars(2, stamp))
    with pytest.raises(UpstreamChanged):
        walk.accept(bars(2, stamp))
