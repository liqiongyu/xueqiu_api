from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NoReturn

import httpx2
import pytest

from .harness import FakeClock, FakeUpstream, Harness, Kind


@pytest.fixture(autouse=True)
def _no_ambient_token(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's XUEQIU_TOKEN out of offline tests."""
    if request.node.get_closest_marker("live") is None:
        monkeypatch.delenv("XUEQIU_TOKEN", raising=False)


@pytest.fixture(autouse=True)
def _no_real_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail an offline test that reaches a real HTTP transport, before anything is sent.

    ``pytest.fail`` is not an ``Exception``, so neither the clients' retries nor the CLI's
    error handling catch it.
    """
    if request.node.get_closest_marker("live") is not None:
        return

    def refuse(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("an offline test reached a real httpx2 transport")

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", refuse)


@pytest.fixture(autouse=True)
def _no_ambient_mcp_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's XUEQIU_MCP_* settings out of the CLI defaults under test."""
    for name in list(os.environ):
        if name.startswith("XUEQIU_MCP_"):
            monkeypatch.delenv(name)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def upstream(clock: FakeClock) -> FakeUpstream:
    return FakeUpstream(clock)


@pytest.fixture(params=["sync", "async"])
def kind(request: pytest.FixtureRequest) -> Kind:
    return request.param


@pytest.fixture
def session_path(tmp_path: Path) -> Path:
    return tmp_path / "cache" / "session.json"


@pytest.fixture
def make(
    kind: Kind, upstream: FakeUpstream, clock: FakeClock, session_path: Path
) -> Iterator[Callable[..., Harness]]:
    """Build harnessed clients of the parametrized kind; options go to the constructor."""
    made: list[Harness] = []

    def factory(**options: Any) -> Harness:
        options.setdefault("session_path", session_path)
        harness = Harness(kind, upstream=upstream, clock=clock, **options)
        made.append(harness)
        return harness

    yield factory
    for harness in made:
        harness.close()


@pytest.fixture
def h(make: Callable[..., Harness]) -> Harness:
    """Anonymous client of each kind against the fake upstream."""
    return make()
