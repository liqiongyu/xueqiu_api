"""Child process for tests/test_shared_processes.py; not collected by pytest.

``DIR INDEX book``: book 200 one-second slots on the state file in DIR without sleeping.

``DIR INDEX sync SCENARIO`` and ``DIR INDEX async SCENARIO``: a ``Client`` or ``AsyncClient``
with the state file and session file in DIR calls a mock upstream. SCENARIO is ``pace``
(5 quotec calls, concurrent in the async client), ``418`` or ``429`` (3 quotec calls in turn,
each answered with that status), or ``cold`` or ``stale`` (one quote_detail call, which needs
the anonymous cookies; ``stale`` answers 60201 to ``STALE_TOKEN`` and holds the home page
until every child has had its 60201, so that they all look for new cookies at once).
``pace``, ``cold`` and ``stale`` space stock.xueqiu.com requests by ``FAST_S``; the others
keep the real intervals. The clients keep their default clocks, so the file holds epoch
times; every send is stamped with ``time.monotonic()``, which the clients wait on.

Each child opens the state file, creates ``ready-INDEX``, waits for ``go``, does its work,
closes the file and prints one JSON object.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx2

from xueqiu_api import AsyncClient, Client, XueqiuError, _base
from xueqiu_api._async import AsyncRuntime
from xueqiu_api._shared import STATE_FILE, SharedState
from xueqiu_api._sqlite import StoreStatus
from xueqiu_api._sync import SyncRuntime
from xueqiu_api.sites import SITES

HOST = "stock.xueqiu.com"
SLOTS = 200
FAST_S = 0.1
STALE_TOKEN = "fake-stale-token-0001"
CALLS = {"pace": 5, "418": 3, "429": 3, "cold": 1, "stale": 1}
QUOTES = {"data": [{"symbol": "SH600000", "current": 8.0}], "error_code": 0}
DETAIL = {
    "data": {"market": {"status_id": 7}, "quote": {"symbol": "SH600000", "current": 8.0}},
    "error_code": 0,
}
LOGIN_ERROR = {"error_code": 60201, "error_description": "synthetic login error"}


def barrier(directory: Path, index: str) -> None:
    (directory / f"ready-{index}").touch()
    while not (directory / "go").exists():
        time.sleep(0.001)


def book(directory: Path, index: str) -> dict[str, Any]:
    """Each slot is the clock reading ``book`` takes inside the lock plus the wait it returns,
    on the monotonic clock the waits run on."""
    reading = [0.0]

    def clock() -> float:
        reading[0] = time.monotonic()
        return reading[0]

    state = SharedState(directory / STATE_FILE, clock=clock, wall=time.time, busy_timeout_s=30)
    state.holds(HOST)
    barrier(directory, index)
    slots: list[tuple[bool, float] | None] = []
    for _ in range(SLOTS):
        result = state.book(HOST, 1.0, time.monotonic(), 1e9)
        slots.append(None if result is None else (result[0], reading[0] + result[1]))
    state.close()
    return {"slots": slots}


class Upstream:
    """The mock hosts of one child, recording what it sends."""

    def __init__(self, scenario: str, directory: Path, index: str) -> None:
        self.scenario = scenario
        self.directory = directory
        self.index = index
        self.cookies = scenario in ("cold", "stale")
        """Whether the calls need the anonymous cookies."""
        self.sent: list[float] = []
        self.home = 0
        self.errors: list[dict[str, Any]] = []

    def respond(self, request: httpx2.Request) -> httpx2.Response:
        at = time.monotonic()
        if request.url.path == "/":
            self.home += 1
            if self.scenario == "stale":
                self.wait_for_rejections()
            cookie = "xq_a_token=fake-anon-token-0001; Domain=.xueqiu.com; Path=/"
            return httpx2.Response(200, headers=[("Set-Cookie", cookie)], text="<html></html>")
        self.sent.append(at)
        if self.scenario == "418":
            return httpx2.Response(418, text="blocked")
        if self.scenario == "429":
            return httpx2.Response(429, headers={"Retry-After": "7"}, text="slow down")
        if f"xq_a_token={STALE_TOKEN}" in request.headers.get("cookie", ""):
            (self.directory / f"rejected-{self.index}").touch()
            return httpx2.Response(200, json=LOGIN_ERROR)
        return httpx2.Response(200, json=QUOTES if "quotec" in request.url.path else DETAIL)

    def wait_for_rejections(self) -> None:
        """Wait until every child has had its 60201, or for 30 s."""
        children = len(list(self.directory.glob("ready-*")))
        give_up = time.monotonic() + 30
        while len(list(self.directory.glob("rejected-*"))) < children:
            if time.monotonic() > give_up:
                return
            time.sleep(0.005)

    def note(self, exc: XueqiuError) -> None:
        self.errors.append(
            {
                "type": type(exc).__name__,
                "status": exc.status,
                "retry_after_s": exc.retry_after_s,
                "at": time.monotonic(),
            }
        )

    def report(self, shared: StoreStatus | None) -> dict[str, Any]:
        assert shared is not None
        return {
            "sent": self.sent,
            "home": self.home,
            "errors": self.errors,
            "store_available": shared.available,
            "store_reason": shared.reason,
            "tol": 2 * time.get_clock_info("monotonic").resolution + 0.005,
        }


def run_sync(directory: Path, index: str, upstream: Upstream) -> dict[str, Any]:
    runtime = SyncRuntime(
        transport=httpx2.MockTransport(upstream.respond), state_path=directory / STATE_FILE
    )
    with Client(token="", session_path=directory / "session.json", _runtime=runtime) as client:
        client.status()
        barrier(directory, index)
        for _ in range(CALLS[upstream.scenario]):
            try:
                if upstream.cookies:
                    client.quote_detail("SH600000")
                else:
                    client.quotec("SH600000")
            except XueqiuError as exc:
                upstream.note(exc)
        return upstream.report(client.status().shared)


async def run_async(directory: Path, index: str, upstream: Upstream) -> dict[str, Any]:
    runtime = AsyncRuntime(
        transport=httpx2.MockTransport(upstream.respond), state_path=directory / STATE_FILE
    )
    client = AsyncClient(token="", session_path=directory / "session.json", _runtime=runtime)
    async with client:
        await client.status()
        barrier(directory, index)

        async def attempt() -> None:
            try:
                if upstream.cookies:
                    await client.quote_detail("SH600000")
                else:
                    await client.quotec("SH600000")
            except XueqiuError as exc:
                upstream.note(exc)

        count = CALLS[upstream.scenario]
        if upstream.scenario == "pace":
            await asyncio.gather(*(attempt() for _ in range(count)))
        else:
            for _ in range(count):
                await attempt()
        return upstream.report((await client.status()).shared)


def main(argv: list[str]) -> None:
    directory, index, mode = Path(argv[0]), argv[1], argv[2]
    if mode == "book":
        print(json.dumps(book(directory, index)))
        return
    upstream = Upstream(argv[3], directory, index)
    if upstream.scenario in ("pace", "cold", "stale"):
        intervals = {host: site.min_interval_s for host, site in SITES.items()} | {HOST: FAST_S}
        _base._intervals = lambda overrides: dict(intervals)  # pyright: ignore[reportPrivateUsage]
    if mode == "sync":
        result = run_sync(directory, index, upstream)
    else:
        result = asyncio.run(run_async(directory, index, upstream))
    print(json.dumps(result))


if __name__ == "__main__":
    main(sys.argv[1:])
