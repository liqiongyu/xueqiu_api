"""Test harness shared by every stage: fixtures, fake upstream, fake clock, client wrapper."""

from __future__ import annotations

import asyncio
import contextvars
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx2

from xueqiu_api import AsyncClient, Client
from xueqiu_api._async import AsyncRuntime
from xueqiu_api._replay import ReplayTransport
from xueqiu_api._sync import SyncRuntime

FIXTURES = Path(__file__).parent / "fixtures"
REPLAY_DIR = FIXTURES / "replay"

Kind = Literal["sync", "async"]
Reply = Callable[[httpx2.Request], httpx2.Response]


def fixture(name: str) -> Any:
    """Load ``tests/fixtures/<name>``: parsed JSON for ``.json`` files, text otherwise."""
    path = FIXTURES / name
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else text


def replay_body(name: str) -> Any:
    """Upstream body of a replay fixture, such as ``replay_body("kline/sh600000_day")``."""
    return fixture(f"replay/{name}.json")["body"]


class FakeClock:
    """Virtual monotonic clock for the limiter, breaker and deadlines.

    Every thread and asyncio task keeps its own sleep total in a context variable, so
    concurrent callers each see the time they would see for real, without races. ``advance``
    moves time for everyone. Read times inside calls (``Sent.at``), not from the test body:
    async calls run in the harness's own context.
    """

    def __init__(self, start: float = 1_000.0) -> None:
        self._base = start
        self._slept = contextvars.ContextVar[float](f"fake-clock-{id(self)}", default=0.0)
        self._lock = threading.Lock()
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self._base + self._slept.get()

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.sleeps.append(seconds)
        self._slept.set(self._slept.get() + seconds)

    async def async_sleep(self, seconds: float) -> None:
        self.sleep(seconds)
        await asyncio.sleep(0)

    def advance(self, seconds: float) -> None:
        self._base += seconds


def json_reply(body: Any, status: int = 200, headers: Mapping[str, str] | None = None) -> Reply:
    return lambda request: httpx2.Response(status, json=body, headers=headers)


def text_reply(text: str, status: int = 200, headers: Mapping[str, str] | None = None) -> Reply:
    return lambda request: httpx2.Response(status, text=text, headers=headers)


def fixture_reply(name: str) -> Reply:
    """Reply with the body of ``tests/fixtures/replay/<name>.json``."""
    return json_reply(replay_body(name))


def error_reply(code: int | str, description: str = "", status: int = 200) -> Reply:
    """Xueqiu error envelope: ``{"error_code": ..., "error_description": ...}``."""
    return json_reply({"error_code": code, "error_description": description}, status)


def danjuan_error_reply(code: int, message: str) -> Reply:
    return json_reply({"result_code": code, "message": message})


def raise_reply(exc_type: type[httpx2.RequestError]) -> Reply:
    def reply(request: httpx2.Request) -> httpx2.Response:
        raise exc_type("synthetic failure", request=request)

    return reply


def waf_reply(status: int = 200) -> Reply:
    return text_reply(fixture("waf_challenge.html"), status, {"Content-Type": "text/html"})


ANON_COOKIES: Mapping[str, str] = {
    "acw_tc": "fake-acw-tc-0001",
    "xq_a_token": "fake-anon-token-0001",
    "xqat": "fake-anon-xqat-0001",
    "xq_r_token": "fake-anon-r-0001",
    "xq_id_token": "fake-anon-id-0001",
    "cookiesu": "fake-anon-su-0001",
    "u": "fake-anon-u-0001",
}


def home_redirect() -> Reply:
    """Apex home page: 301 to www, setting the host-only acw_tc cookie like the real one."""
    headers = [
        ("Location", "https://www.xueqiu.com/"),
        ("Set-Cookie", f"acw_tc={ANON_COOKIES['acw_tc']}; Path=/; HttpOnly"),
    ]
    return lambda request: httpx2.Response(301, headers=headers)


def home_page(cookies: Mapping[str, str] | None = None) -> Reply:
    """www home page setting the anonymous cookies for ``.xueqiu.com``."""
    if cookies is None:
        cookies = {k: v for k, v in ANON_COOKIES.items() if k != "acw_tc"}
    headers = [("Content-Type", "text/html")]
    headers += [("Set-Cookie", f"{k}={v}; Domain=.xueqiu.com; Path=/") for k, v in cookies.items()]
    return lambda request: httpx2.Response(200, headers=headers, text="<!doctype html><html/>")


@dataclass(frozen=True)
class Sent:
    host: str
    path: str
    params: dict[str, str]
    headers: dict[str, str]
    """Lowercase header names."""
    at: float
    """Fake clock time in the sending thread or task."""


class FakeUpstream:
    """Fake Xueqiu and Danjuan hosts on ``httpx2.MockTransport``.

    Routes are keyed by host and path and hold a queue of replies; the last reply repeats.
    The cookie bootstrap is routed by default: the apex 301-redirects to www, which sets the
    anonymous cookies. Unrouted requests get HTTP 404.
    """

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._routes: dict[tuple[str, str], list[Reply]] = {}
        self.sent: list[Sent] = []
        self.route("xueqiu.com", "/", home_redirect())
        self.route("www.xueqiu.com", "/", home_page())
        self.transport = httpx2.MockTransport(self._handle)

    def route(self, host: str, path: str, *replies: Reply) -> None:
        with self._lock:
            self._routes[(host, path)] = list(replies)

    def requests(self, host: str | None = None, path: str | None = None) -> list[Sent]:
        with self._lock:
            return [
                sent
                for sent in self.sent
                if (host is None or sent.host == host) and (path is None or sent.path == path)
            ]

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        host, path = request.url.host, request.url.path
        with self._lock:
            sent = Sent(
                host, path, dict(request.url.params), dict(request.headers), self._clock.now()
            )
            self.sent.append(sent)
            queue = self._routes.get((host, path))
            reply = None if not queue else queue.pop(0) if len(queue) > 1 else queue[0]
        if reply is None:
            return httpx2.Response(404, text="no fake route")
        return reply(request)


class Harness:
    """Drive ``Client`` or ``AsyncClient`` from one test body.

    Async calls run on a private event loop, so tests stay synchronous for both kinds.
    """

    def __init__(
        self,
        kind: Kind,
        *,
        upstream: FakeUpstream,
        clock: FakeClock,
        transport: ReplayTransport | None = None,
        **options: Any,
    ) -> None:
        """``transport`` replaces the fake upstream, for example with a replay transport."""
        self.kind: Kind = kind
        self.upstream = upstream
        self.clock = clock
        self.client: Client | AsyncClient
        self._runner: asyncio.Runner | None = None
        if kind == "sync":
            runtime = SyncRuntime(
                transport=transport or upstream.transport, clock=clock.now, sleep=clock.sleep
            )
            self.client = Client(_runtime=runtime, **options)
        else:
            self._runner = asyncio.Runner()
            async_runtime = AsyncRuntime(
                transport=transport or upstream.transport,
                clock=clock.now,
                sleep=clock.async_sleep,
            )
            self.client = AsyncClient(_runtime=async_runtime, **options)

    def run(self, method: str, *args: Any, **kwargs: Any) -> Any:
        result = getattr(self.client, method)(*args, **kwargs)
        return result if self._runner is None else self._runner.run(result)

    def quotec(self, symbols: str | Sequence[str]) -> Any:
        return self.run("quotec", symbols)

    def kline(self, symbol: str, **params: Any) -> Any:
        return self.run("kline", symbol, **params)

    def call(self, endpoint: str, **params: Any) -> Any:
        return self.run("call", endpoint, **params)

    def request(self, **params: Any) -> Any:
        return self.run("request", **params)

    def concurrently(
        self, calls: Sequence[tuple[str, tuple[Any, ...], Mapping[str, Any]]]
    ) -> list[Any]:
        """Start all calls at once: threads for ``Client``, tasks for ``AsyncClient``.

        Exceptions are returned in place of results.
        """
        if self._runner is None:
            results: list[Any] = [None] * len(calls)

            def work(index: int, method: str, args: tuple[Any, ...], kwargs: Any) -> None:
                try:
                    results[index] = getattr(self.client, method)(*args, **kwargs)
                except Exception as exc:
                    results[index] = exc

            threads = [
                threading.Thread(target=work, args=(index, *call))
                for index, call in enumerate(calls)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            return results

        async def gather() -> list[Any]:
            return await asyncio.gather(
                *(getattr(self.client, method)(*args, **kwargs) for method, args, kwargs in calls),
                return_exceptions=True,
            )

        return self._runner.run(gather())

    def close(self) -> None:
        if isinstance(self.client, Client):
            self.client.close()
        elif self._runner is not None:
            self._runner.run(self.client.close())
            self._runner.close()
