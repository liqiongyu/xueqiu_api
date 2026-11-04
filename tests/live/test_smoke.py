"""Live smoke tests: one real call per registered endpoint.

They send real requests to Xueqiu, so they never run by default: plain ``uv run pytest``
deselects them, and CI does not select them. Opt in explicitly, on your own machine:

    uv run pytest -m live tests/live

Every endpoint is called once with its first registry example, serially through one client,
so the per-host limiter spaces the requests. The session cache goes to a temporary directory.
Set XUEQIU_TOKEN to run them with a login session.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from xueqiu_api import REGISTRY, Client

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Client]:
    with Client(session_path=tmp_path_factory.mktemp("live") / "session.json") as live_client:
        yield live_client


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_endpoint_answers(client: Client, name: str) -> None:
    result: Any = client.call(name, **REGISTRY[name].agent.examples[0])
    assert len(result) > 0
