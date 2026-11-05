"""Live checks of the data features, with the real sizes behind them.

They send real requests to Xueqiu, so they never run by default. Opt in explicitly, on your own
machine:

    uv run pytest -m live tests/live/test_data_features.py

The CN snapshot takes 61 requests and the HK one 29, one a second; HK pages after page 1 had not
been measured when this was written. The 92 pages of the US walk are not here. Every feature uses
endpoints that need no login, so the client has no token, and the session cache goes to a temporary
directory.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from xueqiu_api import Client
from xueqiu_api.symbols import Market, normalize_symbol
from xueqiu_api.trading_calendar import CALENDAR_SOURCES
from xueqiu_api.universe import SEGMENTS, check_snapshot

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Client]:
    path = tmp_path_factory.mktemp("live") / "session.json"
    with Client(token="", session_path=path) as live_client:
        yield live_client


@pytest.mark.parametrize("market", ["CN", "HK"])
def test_a_snapshot_is_complete(client: Client, market: Market) -> None:
    snapshot = client.security_snapshot(market)

    assert snapshot.complete, snapshot.coverage
    assert [item.segment for item in snapshot.coverage] == list(SEGMENTS[market])
    report = check_snapshot(snapshot)
    assert report.ok, report.results
    if market == "CN":
        # A listing day can set the two apart for a moment; run it again before reading more in.
        assert snapshot.screen_total == sum(item.fetched for item in snapshot.coverage)


@pytest.mark.parametrize("market", ["CN", "HK", "US"])
def test_a_calendar_reaches_the_recent_past(client: Client, market: Market) -> None:
    calendar = client.trading_calendar(market)

    assert calendar.source == CALENDAR_SOURCES[market].symbol
    assert calendar.first >= CALENDAR_SOURCES[market].usable_from
    assert calendar.last >= datetime.now(UTC).date() - timedelta(days=14)


@pytest.mark.parametrize("market", ["CN", "HK", "US"])
def test_a_market_state_has_a_status_and_a_time(client: Client, market: Market) -> None:
    state = client.market_state(market)

    assert state.market == market
    assert state.status_id is not None
    assert state.quote_time is not None


@pytest.mark.parametrize(
    ("symbol", "steps"),
    # At least the steps counted on 2026-11-05; a later ex-date adds one.
    [("SH600000", 27), ("SH601166", 22), ("AAPL", 61)],
)
def test_factors_end_before_today_and_keep_the_known_steps(
    client: Client, symbol: str, steps: int
) -> None:
    factors = client.adjust_factors(symbol)

    today = datetime.now(normalize_symbol(symbol).timezone).date()
    assert factors.dates[-1] < today
    assert len(factors.steps) >= steps
