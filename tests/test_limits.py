"""Per-host limiter, per-call deadline and circuit breaker, under threads and tasks."""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Sequence
from typing import Any

import pytest

from xueqiu_api import Blocked, RateLimited, UpstreamUnavailable
from xueqiu_api._core import HostLimiter

from .harness import Harness, fixture_reply, json_reply, text_reply

STOCK = "stock.xueqiu.com"
WWW = "www.xueqiu.com"
DANJUAN = "danjuanfunds.com"
QUOTEC = "/v5/stock/realtime/quotec.json"
WEB_PATH = "/query/v1/suggest_stock.json"
FUND_PATH = "/djapi/fund/008975"

Calls = list[tuple[str, tuple[Any, ...], dict[str, Any]]]


def gaps(times: Sequence[float]) -> list[float]:
    ordered = sorted(times)
    return [later - earlier for earlier, later in itertools.pairwise(ordered)]


def test_concurrent_calls_are_spaced_per_host(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTEC, fixture_reply("quotec/cn_hk_us"))
    h.upstream.route(WWW, WEB_PATH, json_reply({"data": []}))
    h.upstream.route(DANJUAN, FUND_PATH, json_reply({"data": {}, "result_code": 0}))
    calls: Calls = [("quotec", ("SH600000",), {}) for _ in range(6)]
    calls += [("request", (), {"host": WWW, "path": WEB_PATH}) for _ in range(3)]
    calls += [("request", (), {"host": DANJUAN, "path": FUND_PATH}) for _ in range(3)]

    results = h.concurrently(calls)

    assert not [result for result in results if isinstance(result, Exception)]
    stock = [sent.at for sent in h.upstream.requests(STOCK)]
    web = [sent.at for sent in h.upstream.requests(WWW, WEB_PATH)]
    home = [sent.at for sent in h.upstream.requests("xueqiu.com", "/")]
    danjuan = [sent.at for sent in h.upstream.requests(DANJUAN)]
    assert len(stock) == 6 and len(web) == 3 and len(home) == 1 and len(danjuan) == 3
    assert gaps(stock) == pytest.approx([1.0] * 5)
    assert gaps(home + web) == pytest.approx([3.0] * 3)
    assert gaps(danjuan) == pytest.approx([1.0] * 2)
    # Hosts do not wait for each other: every host starts at the same time.
    assert min(stock) == min(home) == min(danjuan)


def test_longer_intervals_can_be_configured(make: Callable[..., Harness]) -> None:
    h = make(min_intervals={STOCK: 2.5})
    h.upstream.route(STOCK, QUOTEC, fixture_reply("quotec/cn_hk_us"))

    h.concurrently([("quotec", ("SH600000",), {}) for _ in range(3)])

    assert gaps([sent.at for sent in h.upstream.sent]) == pytest.approx([2.5, 2.5])


@pytest.mark.parametrize(
    "overrides", [{STOCK: 0.5}, {WWW: 2.0}, {"example.com": 5.0}, {STOCK: math.nan}]
)
def test_intervals_cannot_be_shortened(
    make: Callable[..., Harness], overrides: dict[str, float]
) -> None:
    with pytest.raises(ValueError):
        make(min_intervals=overrides)


def test_calls_that_cannot_start_before_the_deadline_fail_fast(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTEC, fixture_reply("quotec/cn_hk_us"))

    results = h.concurrently([("quotec", ("SH600000",), {}) for _ in range(40)])

    limited = [result for result in results if isinstance(result, RateLimited)]
    assert len(h.upstream.sent) == 31
    assert len(limited) == 9
    assert all(error.retry_after_s == 31.0 for error in limited)
    assert all("local queue" in error.message for error in limited)
    assert max(sent.at for sent in h.upstream.sent) - min(sent.at for sent in h.upstream.sent) == 30


def test_a_shorter_deadline_limits_queueing(make: Callable[..., Harness]) -> None:
    h = make(deadline=2.5)
    h.upstream.route(STOCK, QUOTEC, fixture_reply("quotec/cn_hk_us"))

    results = h.concurrently([("quotec", ("SH600000",), {}) for _ in range(5)])

    assert sum(isinstance(result, RateLimited) for result in results) == 2
    assert len(h.upstream.sent) == 3


def test_retries_wait_for_the_host_interval(h: Harness) -> None:
    h.upstream.route(WWW, WEB_PATH, json_reply({}, 500), json_reply({}, 502), json_reply({}))

    h.request(host=WWW, path=WEB_PATH)

    home = h.upstream.requests("xueqiu.com", "/")
    attempts = h.upstream.requests(WWW, WEB_PATH)
    assert gaps([sent.at for sent in home + attempts]) == pytest.approx([3.0, 3.0, 3.0])


def test_retry_that_would_miss_the_deadline_reports_the_failure(
    make: Callable[..., Harness],
) -> None:
    h = make(deadline=1.5)
    h.upstream.route(STOCK, QUOTEC, json_reply({}, 500))

    with pytest.raises(UpstreamUnavailable):
        h.quotec("SH600000")

    # The second retry would start at 3 s, after the 1.5 s deadline.
    assert len(h.upstream.sent) == 2


def test_breaker_fails_fast_then_recovers_after_the_cooldown(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTEC, text_reply("teapot", 418), fixture_reply("quotec/cn_hk_us"))
    h.upstream.route(DANJUAN, FUND_PATH, json_reply({"data": {}, "result_code": 0}))

    with pytest.raises(Blocked):
        h.quotec("SH600000")
    h.clock.advance(299.0)
    with pytest.raises(Blocked) as open_breaker:
        h.quotec("SH600000")
    # Other hosts are not affected.
    h.request(host=DANJUAN, path=FUND_PATH)
    h.clock.advance(1.0)
    quotes = h.quotec("SH600000")

    assert open_breaker.value.retry_after_s == pytest.approx(1.0)
    assert len(quotes) == 4
    assert len(h.upstream.requests(STOCK)) == 2


def test_breaker_reopens_when_the_host_still_blocks(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTEC, text_reply("teapot", 418))

    with pytest.raises(Blocked):
        h.quotec("SH600000")
    h.clock.advance(300.0)
    with pytest.raises(Blocked):
        h.quotec("SH600000")
    with pytest.raises(Blocked) as again:
        h.quotec("SH600000")

    assert again.value.retry_after_s == 300.0
    assert len(h.upstream.sent) == 2


def test_limiter_books_slots_and_never_books_past_the_deadline() -> None:
    limiter = HostLimiter({STOCK: 1.0})

    assert limiter.reserve(STOCK, now=0.0, earliest=0.0, deadline=30.0) == (True, 0.0)
    assert limiter.reserve(STOCK, now=0.0, earliest=0.0, deadline=30.0) == (True, 1.0)
    assert limiter.reserve(STOCK, now=0.0, earliest=5.0, deadline=30.0) == (True, 5.0)
    assert limiter.reserve(STOCK, now=0.0, earliest=0.0, deadline=5.5) == (False, 6.0)
    assert limiter.reserve(STOCK, now=0.0, earliest=0.0, deadline=6.0) == (True, 6.0)
