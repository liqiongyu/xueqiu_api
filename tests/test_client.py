"""End-to-end behaviour of Client and AsyncClient against the fake upstream."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from xueqiu_api import (
    InvalidParams,
    Kline,
    Quote,
    SymbolNotFound,
    UnknownEndpoint,
    UpstreamError,
)

from .harness import Harness, error_reply, fixture_reply, json_reply

STOCK = "stock.xueqiu.com"
QUOTEC = "/v5/stock/realtime/quotec.json"
KLINE = "/v5/stock/chart/kline.json"


def test_quotec_sends_one_batch_request_without_cookies(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTEC, fixture_reply("quotec/cn_hk_us"))

    quotes = h.quotec(["sh600000", "000001.sz", "0700.HK", "aapl"])

    assert [sent.host for sent in h.upstream.sent] == [STOCK]
    sent = h.upstream.sent[0]
    assert sent.params == {"symbol": "SH600000,SZ000001,00700,AAPL"}
    assert sent.headers["user-agent"] == "Xueqiu iPhone 14.15.1"
    assert sent.headers["accept"] == "application/json"
    assert "cookie" not in sent.headers
    assert [quote.symbol for quote in quotes] == ["SH600000", "SH000001", "00700", "AAPL"]
    assert all(isinstance(quote, Quote) for quote in quotes)


def test_quotec_accepts_a_comma_separated_string(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTEC, fixture_reply("quotec/cn_hk_us"))

    h.quotec("SH600000, 00700")

    assert h.upstream.sent[0].params == {"symbol": "SH600000,00700"}


def test_kline_bootstraps_cookies_and_parses_bars(h: Harness) -> None:
    h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))

    bars = h.kline("600000", count=3)

    assert [(sent.host, sent.path) for sent in h.upstream.sent] == [
        ("xueqiu.com", "/"),
        ("www.xueqiu.com", "/"),
        (STOCK, KLINE),
    ]
    sent = h.upstream.sent[-1]
    assert sent.params["symbol"] == "SH600000"
    assert sent.params["count"] == "-3"
    assert sent.params["period"] == "day"
    assert sent.params["type"] == "before"
    assert sent.params["indicator"] == "kline"
    assert "xq_a_token=fake-anon-token-0001" in sent.headers["cookie"]
    assert "u=fake-anon-u-0001" in sent.headers["cookie"]
    assert isinstance(bars, Kline)
    assert len(bars) == 3
    assert bars.timestamps[0] == datetime(2026, 11, 2, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert bars.columns["close"] == (10.06, 10.09, 10.09)


def test_kline_with_a_date_range_goes_forward_from_begin(h: Harness) -> None:
    h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))

    bars = h.kline("SH600000", begin=date(2026, 11, 3), end=date(2026, 11, 3), adjust="normal")

    params = h.upstream.sent[-1].params
    begin = datetime(2026, 11, 3, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert params["begin"] == str(int(begin.timestamp() * 1000))
    assert params["count"] == "1"
    assert params["type"] == "normal"
    assert [stamp.date() for stamp in bars.timestamps] == [date(2026, 11, 3)]


def test_call_returns_the_same_result_as_the_explicit_method(h: Harness) -> None:
    h.upstream.route(STOCK, QUOTEC, fixture_reply("quotec/cn_hk_us"))

    assert h.call("quotec", symbols="SH600000,00700") == h.quotec("SH600000,00700")


def test_call_rejects_unknown_endpoints_and_params(h: Harness) -> None:
    with pytest.raises(UnknownEndpoint) as unknown:
        h.call("klien", symbol="SH600000")
    assert "kline" in unknown.value.hint

    with pytest.raises(InvalidParams) as invalid:
        h.call("kline", symbol="SH600000", start="2026-01-01")
    assert "start" in invalid.value.message
    assert invalid.value.endpoint == "kline"

    with pytest.raises(InvalidParams):
        h.kline("SH600000", period="2d")
    assert h.upstream.sent == []


def test_unrecognized_symbols_raise_symbol_not_found(h: Harness) -> None:
    with pytest.raises(SymbolNotFound):
        h.kline("7xxxxx")
    with pytest.raises(SymbolNotFound):
        h.quotec(["SH600000", "700000"])
    assert h.upstream.sent == []


def test_request_returns_the_raw_body(h: Harness) -> None:
    path = "/query/v1/suggest_stock.json"
    body: dict[str, Any] = {"data": {"items": []}, "error_code": 0}
    h.upstream.route("www.xueqiu.com", path, json_reply(body))

    result = h.request(host="www.xueqiu.com", path=path, params={"q": "x", "full": True})

    assert result == body
    sent = h.upstream.sent[-1]
    assert sent.params == {"q": "x", "full": "true"}
    assert sent.headers["user-agent"].startswith("Mozilla/5.0")
    assert "xq_a_token=" in sent.headers["cookie"]


def test_request_to_danjuan_sends_no_cookies(h: Harness) -> None:
    h.upstream.route("danjuanfunds.com", "/djapi/fund/008975", json_reply({"data": {}}))

    h.request(host="danjuanfunds.com", path="/djapi/fund/008975")

    assert [sent.host for sent in h.upstream.sent] == ["danjuanfunds.com"]
    assert "cookie" not in h.upstream.sent[0].headers


def test_request_rejects_unknown_hosts_and_relative_paths(h: Harness) -> None:
    with pytest.raises(InvalidParams):
        h.request(host="example.com", path="/")
    with pytest.raises(InvalidParams):
        h.request(host="stock.xueqiu.com", path="@example.com/")
    assert h.upstream.sent == []


@pytest.mark.parametrize("data", [None, []])
def test_empty_quotec_data_is_a_success(h: Harness, data: object) -> None:
    h.upstream.route(STOCK, QUOTEC, json_reply({"data": data, "error_code": 0}))

    assert h.quotec("SH600000") == []


def test_empty_kline_shape_is_a_success(h: Harness) -> None:
    empty: dict[str, Any] = {"data": {"items": [], "items_size": 0}, "error_code": 0}
    h.upstream.route(STOCK, KLINE, json_reply(empty))

    bars = h.kline("SH600000", period="1m")

    assert len(bars) == 0
    assert bars.columns == {}


def test_errors_from_the_upstream_keep_their_code(h: Harness) -> None:
    h.upstream.route(STOCK, KLINE, error_reply(100002, "Required parameter missing", 400))

    with pytest.raises(UpstreamError) as caught:
        h.kline("SH600000")

    error = caught.value
    assert (error.code, error.upstream_code, error.status) == ("upstream_error", "100002", 400)
    assert "Required parameter missing" in error.message
