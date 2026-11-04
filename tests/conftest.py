from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from .harness import FakeClock, FakeUpstream, Harness, Kind


@pytest.fixture(autouse=True)
def _no_ambient_token(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's XUEQIU_TOKEN out of offline tests."""
    if request.node.get_closest_marker("live") is None:
        monkeypatch.delenv("XUEQIU_TOKEN", raising=False)


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
