"""Live smoke tests: one real call per registered endpoint.

They send real requests to Xueqiu, so they never run by default: plain ``uv run pytest``
deselects them, and CI does not select them. Opt in explicitly, on your own machine:

    uv run pytest -m live tests/live

Every endpoint that needs no login is called once with its first registry example, serially
through one client, so the per-host limiter spaces the requests; finance and F10 are also called
once each for a Hong Kong and a US symbol. The session cache goes to a temporary directory. Set
XUEQIU_TOKEN to run them with a login session.
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


@pytest.mark.parametrize("name", [n for n in sorted(REGISTRY) if REGISTRY[n].auth != "required"])
def test_endpoint_answers(client: Client, name: str) -> None:
    result: Any = client.call(name, **REGISTRY[name].agent.examples[0])
    # Lists, Kline and Financials are empty when nothing came back; records are models.
    assert result


@pytest.mark.parametrize("symbol", ["00700", "AAPL"])
@pytest.mark.parametrize("name", ["finance_income", "f10_company"])
def test_other_regions_answer(client: Client, name: str, symbol: str) -> None:
    result: Any = client.call(name, symbol=symbol)
    assert result
