"""Parsing and query building of quotec and kline, without any transport."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from xueqiu_api import InvalidParams, Kline, KlineParams, Quote, QuotecParams
from xueqiu_api.endpoints.kline import ENDPOINT as KLINE
from xueqiu_api.endpoints.quotec import ENDPOINT as QUOTEC

from .harness import replay_body

SHANGHAI = ZoneInfo("Asia/Shanghai")
HONG_KONG = ZoneInfo("Asia/Hong_Kong")
NEW_YORK = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 4, 15, 30, tzinfo=UTC)


def ms(moment: datetime) -> str:
    return str(int(moment.timestamp() * 1000))


def quotes() -> list[Quote]:
    params = QUOTEC.validate({"symbols": ["SH600000", "SH000001", "00700", "AAPL"]})
    return QUOTEC.parse(replay_body("quotec/cn_hk_us")["data"], params)


def kline(name: str, **params: Any) -> Kline:
    body = replay_body(f"kline/{name}")
    validated = KLINE.validate({"symbol": body["data"]["symbol"], **params})
    return KLINE.parse(body["data"], validated)


def test_quotes_keep_upstream_units_and_use_exchange_time() -> None:
    sh600000, sh000001, hk00700, aapl = quotes()

    assert (sh600000.current, sh600000.percent, sh600000.turnover_rate) == (10.09, 0.0, 0.25)
    assert sh000001.percent == 2.16
    assert aapl.percent == pytest.approx(6.2669)
    assert sh600000.timestamp == datetime(2026, 11, 4, 15, 0, tzinfo=SHANGHAI)
    assert hk00700.timestamp is not None and hk00700.timestamp.tzinfo == HONG_KONG
    assert aapl.timestamp == datetime(2026, 11, 4, 10, 27, 4, 278000, tzinfo=NEW_YORK)
    assert aapl.timestamp is not None and aapl.timestamp.tzinfo == NEW_YORK
    assert aapl.is_trade is True


def test_quotes_keep_unknown_upstream_fields() -> None:
    aapl = quotes()[3]

    assert aapl.model_extra is not None
    assert aapl.model_extra["trade_session"] == 2
    assert aapl.model_extra["level"] == 3

    params = QUOTEC.validate({"symbols": "AAPL"})
    future = QUOTEC.parse([{"symbol": "AAPL", "current": "1.5", "new_field": [1]}], params)
    assert future[0].current == 1.5
    assert future[0].model_extra == {"new_field": [1]}


def test_a_share_daily_bar_falls_on_the_shanghai_date() -> None:
    bars = kline("sh600000_day")

    first = bars.timestamps[0]
    assert first == datetime(2026, 11, 2, tzinfo=SHANGHAI)
    assert first.date() == date(2026, 11, 2)
    # Read as UTC the same bar would land on the previous day.
    assert first.astimezone(UTC).date() == date(2026, 11, 1)
    assert [stamp.date() for stamp in bars.timestamps] == [
        date(2026, 11, 2),
        date(2026, 11, 3),
        date(2026, 11, 4),
    ]


def test_us_and_hk_daily_bars_use_their_exchange_midnight() -> None:
    aapl = kline("aapl_day")
    hk = kline("00700_day")

    assert aapl.timestamps[0] == datetime(2026, 11, 2, tzinfo=NEW_YORK)
    assert aapl.timestamps[0].tzinfo == NEW_YORK
    assert hk.timestamps[0] == datetime(2026, 11, 2, tzinfo=HONG_KONG)


def test_kline_is_columnar_and_keeps_unknown_columns() -> None:
    bars = kline("sh600000_day")

    assert bars.symbol == "SH600000"
    assert (bars.period, bars.adjust) == ("day", "before")
    assert set(bars.columns) == {
        "volume",
        "open",
        "high",
        "low",
        "close",
        "chg",
        "percent",
        "turnoverrate",
        "amount",
    }
    assert bars.columns["percent"] == (0.4, 0.3, 0.0)
    assert all(len(column) == len(bars) for column in bars.columns.values())

    params = KLINE.validate({"symbol": "SH600000"})
    data = {"symbol": "SH600000", "column": ["timestamp", "pe", "new"], "item": [[0, 6.4, None]]}
    extra = KLINE.parse(data, params)
    assert extra.columns == {"pe": (6.4,), "new": (None,)}


def test_kline_drops_bars_outside_the_requested_dates() -> None:
    bars = kline("sh600000_day", begin=date(2026, 11, 3), end=date(2026, 11, 3))

    assert [stamp.date() for stamp in bars.timestamps] == [date(2026, 11, 3)]
    assert bars.columns["close"] == (10.09,)


def build(**params: Any) -> dict[str, str]:
    return KLINE.build_query(KLINE.validate({"symbol": "SH600000", **params}), NOW)


def test_kline_query_defaults_to_the_latest_100_bars() -> None:
    assert build() == {
        "symbol": "SH600000",
        "begin": ms(NOW),
        "period": "day",
        "type": "before",
        "count": "-100",
        "indicator": "kline",
    }


def test_kline_query_ends_at_the_end_date_in_the_exchange_timezone() -> None:
    query = build(end=date(2026, 6, 3), count=5, adjust="after")

    last_moment = datetime(2026, 6, 3, 23, 59, 59, 999000, tzinfo=SHANGHAI)
    assert (query["begin"], query["count"], query["type"]) == (ms(last_moment), "-5", "after")


def test_kline_query_with_begin_goes_forward_and_covers_the_range() -> None:
    query = build(begin=date(2026, 6, 1), end=date(2026, 6, 30))
    assert query["begin"] == ms(datetime(2026, 6, 1, tzinfo=SHANGHAI))
    assert query["count"] == "30"

    assert build(begin=date(2026, 6, 1), count=7)["count"] == "7"
    assert build(begin=date(2026, 11, 3), period="5m")["count"] == str(2 * 288)
    assert build(begin=date(2000, 1, 1))["count"] == "9805"
    assert build(begin=date(1990, 1, 1))["count"] == "10000"


def test_us_kline_query_uses_new_york_dates() -> None:
    query = KLINE.build_query(KLINE.validate({"symbol": "AAPL", "begin": "2026-11-02"}), NOW)

    assert query["begin"] == ms(datetime(2026, 11, 2, tzinfo=NEW_YORK))


@pytest.mark.parametrize(
    "params",
    [
        {"begin": "2026-11-04", "end": "2026-11-01"},
        {"count": 0},
        {"count": 10_001},
        {"period": "2d"},
        {"adjust": "qfq"},
        {"since": "2026-01-01"},
        {"symbol": 600000},
    ],
)
def test_kline_params_are_validated(params: dict[str, Any]) -> None:
    with pytest.raises(InvalidParams):
        KLINE.validate({"symbol": "SH600000", **params})


def test_params_models_normalize_symbols() -> None:
    assert KlineParams.model_validate({"symbol": "600519.sh"}).symbol == "SH600519"
    params = QuotecParams.model_validate({"symbols": "aapl, 0700.hk"})
    assert params.symbols == ("AAPL", "00700")


@pytest.mark.parametrize("symbols", [[], "", [f"SH60{n:04d}" for n in range(51)]])
def test_quotec_symbol_count_is_bounded(symbols: Any) -> None:
    with pytest.raises(InvalidParams):
        QUOTEC.validate({"symbols": symbols})
