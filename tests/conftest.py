from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn

import httpx2
import pytest

from xueqiu_api import REGISTRY
from xueqiu_api._base import ClientBase
from xueqiu_api.registry import Endpoint

from .harness import FakeClock, FakeUpstream, Harness, Kind, Register


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


@pytest.fixture(autouse=True)
def _samples_parse_their_endpoint(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """A test marked ``sample("name")`` must have parsed a response of that endpoint by its end."""
    marked = {mark.args[0] for mark in request.node.iter_markers("sample")}
    if not marked:
        yield
        return
    parsed: set[str] = set()
    finish = ClientBase._finish  # pyright: ignore[reportPrivateUsage]

    def recording(
        client: ClientBase, spec: Endpoint[Any, Any], params: Any, call: Any, body: Any
    ) -> Any:
        result = finish(client, spec, params, call, body)
        parsed.add(spec.name)
        return result

    monkeypatch.setattr(ClientBase, "_finish", recording)
    yield
    missing = marked - parsed
    assert not missing, f"marked as a sample of {sorted(missing)}, but it parsed {sorted(parsed)}"


@pytest.fixture
def register_endpoint(monkeypatch: pytest.MonkeyPatch) -> Register:
    """Add a synthetic endpoint to the module-level registries for one test."""

    def register(spec: Endpoint[Any, Any]) -> Endpoint[Any, Any]:
        merged = MappingProxyType(dict(sorted({**REGISTRY, spec.name: spec}.items())))
        for module in ("_base", "_replay", "cli", "mcp_server.tools"):
            monkeypatch.setattr(f"xueqiu_api.{module}.REGISTRY", merged)
        return spec

    return register


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
