"""Cache keys, the stored form of a body, session hours and how long results stay fresh."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from xueqiu_api import REGISTRY
from xueqiu_api._freshness import (
    DAILY_S,
    INTRADAY_CLOSED_S,
    INTRADAY_SESSION_S,
    STATIC_S,
    cache_key,
    encode_body,
    ttl_s,
)
from xueqiu_api._model import USER_KEY
from xueqiu_api.endpoints.kline import ENDPOINT as KLINE
from xueqiu_api.symbols import Market
from xueqiu_api.trading_calendar import in_session, session_over

SHANGHAI = ZoneInfo("Asia/Shanghai")
HONG_KONG = ZoneInfo("Asia/Hong_Kong")
NEW_YORK = ZoneInfo("America/New_York")
FRIDAY = (2026, 11, 6)
SATURDAY = (2026, 11, 7)


def at(
    day: tuple[int, int, int], hour: int, minute: int, zone: tzinfo, second: int = 0
) -> datetime:
    return datetime(*day, hour, minute, second, tzinfo=zone)


def params(endpoint: str, **values: Any) -> dict[str, Any]:
    """Validated params as the client keys them."""
    return REGISTRY[endpoint].validate(values).model_dump(mode="json", exclude_none=True)


def ttl(endpoint: str, fetched_at: datetime, now: datetime, **values: Any) -> float | None:
    spec = REGISTRY[endpoint]
    return ttl_s(spec, spec.validate(values), fetched_at, now)


# ---- keys


def test_symbol_forms_give_one_key() -> None:
    keys = {
        cache_key("kline", params("kline", symbol=symbol, adjust="before"), "anonymous")
        for symbol in ("600000", "SH600000", "600000.SH")
    }
    assert len(keys) == 1
    assert re.fullmatch(r"[0-9a-f]{64}", keys.pop())


def test_the_identity_the_endpoint_and_the_params_are_in_the_key() -> None:
    base = params("kline", symbol="SH600000")
    key = cache_key("kline", base, "anonymous")

    assert cache_key("kline", base, "0123456789abcdef0123456789abcdef") != key
    assert cache_key("kline", params("kline", symbol="SH600000", count=5), "anonymous") != key
    assert cache_key("f10_company", params("f10_company", symbol="SH600000"), "anonymous") != key


def test_changing_the_user_key_pattern_changes_every_key(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = [
        ("kline", params("kline", symbol="SH600000"), "anonymous"),
        ("f10_company", params("f10_company", symbol="SH600000"), "anonymous"),
        ("quotec", params("quotec", symbols=["SH600000", "00700"]), "someone"),
    ]
    before = [cache_key(*call) for call in calls]
    monkeypatch.setattr("xueqiu_api._model.USER_KEY", re.compile(USER_KEY.pattern + "|signature"))
    after = [cache_key(*call) for call in calls]

    assert len(set(before)) == len(calls)
    assert not set(before) & set(after)


def test_the_stored_body_is_compact_json_without_user_keys() -> None:
    body = {
        "data": {
            "items": [{"symbol": "SH600000", "name": "浦发银行", "user_id": 1, "screen_name": "x"}],
            "owner": {"id": 2},
            "last_user_rb_gid": 3,
        }
    }
    stored = encode_body(body)

    assert json.loads(stored) == {
        "data": {"items": [{"symbol": "SH600000", "name": "浦发银行"}], "last_user_rb_gid": 3}
    }
    assert "浦发银行".encode() in stored
    assert b" " not in stored


# ---- session hours


@pytest.mark.parametrize(
    ("market", "moment", "expected"),
    [
        ("CN", at(FRIDAY, 9, 14, SHANGHAI, 59), False),
        ("CN", at(FRIDAY, 9, 15, SHANGHAI), True),
        ("CN", at(FRIDAY, 15, 30, SHANGHAI), True),
        ("CN", at(FRIDAY, 15, 31, SHANGHAI), False),
        ("CN", at(FRIDAY, 2, 0, UTC), True),
        ("HK", at(FRIDAY, 8, 59, HONG_KONG), False),
        ("HK", at(FRIDAY, 9, 0, HONG_KONG), True),
        ("HK", at(FRIDAY, 16, 30, HONG_KONG), True),
        ("HK", at(FRIDAY, 16, 31, HONG_KONG), False),
        ("US", at(FRIDAY, 9, 29, NEW_YORK), False),
        ("US", at(FRIDAY, 9, 30, NEW_YORK), True),
        ("US", at(FRIDAY, 16, 30, NEW_YORK), True),
        ("US", at(FRIDAY, 16, 31, NEW_YORK), False),
        ("US", at(FRIDAY, 14, 30, UTC), True),
        ("US", at(FRIDAY, 22, 30, SHANGHAI), True),
        ("CN", at(SATURDAY, 10, 0, SHANGHAI), False),
        ("HK", at(SATURDAY, 10, 0, HONG_KONG), False),
        ("US", at(SATURDAY, 10, 0, NEW_YORK), False),
    ],
)
def test_in_session(market: Market, moment: datetime, expected: bool) -> None:
    assert in_session(market, moment) is expected


@pytest.mark.parametrize(
    ("market", "moment", "expected"),
    [
        ("CN", at(FRIDAY, 15, 30, SHANGHAI), False),
        ("CN", at(FRIDAY, 15, 30, SHANGHAI, 1), True),
        ("CN", at(SATURDAY, 15, 30, SHANGHAI), False),
        ("CN", at(SATURDAY, 15, 31, SHANGHAI), True),
        ("CN", at(SATURDAY, 8, 0, SHANGHAI), False),
        ("HK", at(FRIDAY, 16, 31, HONG_KONG), True),
        ("US", at(FRIDAY, 16, 30, NEW_YORK), False),
        ("US", at(FRIDAY, 16, 31, NEW_YORK), True),
        ("US", at(SATURDAY, 5, 0, SHANGHAI), False),
        ("US", at(SATURDAY, 6, 0, SHANGHAI), True),
    ],
)
def test_session_over(market: Market, moment: datetime, expected: bool) -> None:
    assert session_over(market, moment) is expected


# ---- freshness


def test_intraday_results_last_a_minute_in_session_and_fifteen_minutes_outside() -> None:
    in_hours = at(FRIDAY, 10, 0, SHANGHAI)
    weekend = at(SATURDAY, 10, 0, SHANGHAI)
    assert ttl("capital_flow", in_hours, in_hours, symbol="SH600000") == INTRADAY_SESSION_S == 60
    assert ttl("capital_flow", weekend, weekend, symbol="SH600000") == INTRADAY_CLOSED_S == 900


def test_the_market_of_the_symbol_decides() -> None:
    hong_kong_only = at(FRIDAY, 16, 0, SHANGHAI)
    assert ttl("capital_flow", hong_kong_only, hong_kong_only, symbol="00700") == 60
    assert ttl("capital_flow", hong_kong_only, hong_kong_only, symbol="SH600000") == 900


def test_either_moment_in_session_gives_the_short_freshness() -> None:
    before_open = at(FRIDAY, 9, 10, SHANGHAI)
    after_open = at(FRIDAY, 9, 16, SHANGHAI)
    before_close = at(FRIDAY, 15, 29, SHANGHAI)
    after_close = at(FRIDAY, 15, 40, SHANGHAI)

    assert ttl("capital_flow", before_open, after_open, symbol="SH600000") == INTRADAY_SESSION_S
    assert ttl("capital_flow", before_close, after_close, symbol="SH600000") == INTRADAY_SESSION_S
    assert ttl("capital_flow", after_close, after_close, symbol="SH600000") == INTRADAY_CLOSED_S


def test_without_a_symbol_any_market_in_session_counts() -> None:
    new_york_morning = at(FRIDAY, 10, 0, NEW_YORK)
    sunday = at((2026, 11, 8), 12, 0, SHANGHAI)
    query = {"query": "茅台"}
    assert ttl("search_stock", new_york_morning, new_york_morning, **query) == INTRADAY_SESSION_S
    assert ttl("search_stock", sunday, sunday, **query) == INTRADAY_CLOSED_S


def test_daily_static_and_realtime_freshness() -> None:
    moment = at(FRIDAY, 10, 0, SHANGHAI)
    assert REGISTRY["finance_income"].freshness == "daily"
    assert REGISTRY["f10_company"].freshness == "static"
    assert REGISTRY["quotec"].freshness == "realtime"

    assert ttl("finance_income", moment, moment, symbol="SH600000") == DAILY_S == 21600
    assert ttl("f10_company", moment, moment, symbol="SH600000") == STATIC_S == 86400
    assert ttl("quotec", moment, moment, symbols=["SH600000"]) is None


@pytest.mark.parametrize(
    ("values", "fetched_at", "expected"),
    [
        pytest.param(
            {"symbol": "SH600000", "end": "2026-11-05"},
            at(FRIDAY, 10, 0, SHANGHAI),
            "daily",
            id="day-bars-ending-yesterday",
        ),
        pytest.param(
            {"symbol": "SH600000", "end": "2026-11-06"},
            at(FRIDAY, 18, 0, SHANGHAI),
            "intraday",
            id="day-bars-ending-today",
        ),
        pytest.param(
            {"symbol": "SH600000"},
            at(SATURDAY, 10, 0, SHANGHAI),
            "intraday",
            id="latest-bars",
        ),
        pytest.param(
            {"symbol": "SH600000", "begin": "2026-01-05"},
            at(SATURDAY, 10, 0, SHANGHAI),
            "intraday",
            id="bars-from-a-date-on",
        ),
        pytest.param(
            {"symbol": "SH600000", "period": "5m", "end": "2026-11-05"},
            at(FRIDAY, 10, 0, SHANGHAI),
            "daily",
            id="minute-bars-ending-yesterday",
        ),
        pytest.param(
            {"symbol": "SH600000", "period": "week", "end": "2026-10-30"},
            at(FRIDAY, 10, 0, SHANGHAI),
            "intraday",
            id="weekly-bars-ending-last-week",
        ),
        pytest.param(
            {"symbol": "SH600000", "period": "1m", "before": "2026-11-06T00:00:00+08:00"},
            at(FRIDAY, 10, 0, SHANGHAI),
            "daily",
            id="before-midnight",
        ),
        pytest.param(
            {"symbol": "SH600000", "period": "1m", "before": "2026-11-06T00:00:00.002+08:00"},
            at(FRIDAY, 10, 0, SHANGHAI),
            "intraday",
            id="before-just-after-midnight",
        ),
        pytest.param(
            {"symbol": "AAPL", "end": "2026-11-05"},
            at(FRIDAY, 8, 0, SHANGHAI),
            "intraday",
            id="still-the-end-date-in-new-york",
        ),
        pytest.param(
            {"symbol": "AAPL", "end": "2026-11-05"},
            at(FRIDAY, 13, 0, SHANGHAI),
            "daily",
            id="past-the-end-date-in-new-york",
        ),
    ],
)
def test_kline_freshness_follows_the_range(
    values: dict[str, Any], fetched_at: datetime, expected: str
) -> None:
    assert KLINE.freshness_of is not None
    assert KLINE.freshness_of(KLINE.validate(values), fetched_at) == expected


def test_kline_ttl_follows_the_range() -> None:
    moment = at(FRIDAY, 10, 0, SHANGHAI)
    assert ttl("kline", moment, moment, symbol="SH600000", end="2026-11-05") == DAILY_S
    assert ttl("kline", moment, moment, symbol="SH600000") == INTRADAY_SESSION_S
    weekly = {"symbol": "SH600000", "period": "week", "end": "2026-10-30"}
    assert ttl("kline", moment, moment, **weekly) == INTRADAY_SESSION_S


def test_only_kline_sets_a_freshness_hook() -> None:
    assert [name for name, spec in REGISTRY.items() if spec.freshness_of is not None] == ["kline"]
