"""Anonymous cookie bootstrap, session cache, credentials and redaction."""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from xueqiu_api import AuthExpired, Blocked, Client, InvalidParams, UpstreamChanged
from xueqiu_api._session import default_session_path, parse_credentials

from .harness import (
    ANON_COOKIES,
    Harness,
    error_reply,
    fixture_reply,
    home_page,
    json_reply,
    waf_reply,
)

STOCK = "stock.xueqiu.com"
KLINE = "/v5/stock/chart/kline.json"
QUOTEC = "/v5/stock/realtime/quotec.json"
LOGIN_TOKEN = "fake-login-token-9f3a"
LOGIN_U = "fake-login-u-77812"
TOKEN = f"xq_a_token={LOGIN_TOKEN}; u={LOGIN_U}"


def test_bootstrap_follows_the_apex_redirect_and_caches_the_cookies(
    h: Harness, session_path: Path
) -> None:
    h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))

    h.kline("SH600000")

    apex, www = h.upstream.sent[:2]
    assert (apex.host, www.host) == ("xueqiu.com", "www.xueqiu.com")
    assert apex.headers["user-agent"].startswith("Mozilla/5.0")
    assert "cookie" not in apex.headers
    saved = json.loads(session_path.read_text(encoding="utf-8"))
    assert saved["cookies"] == ANON_COOKIES
    cookie = h.upstream.requests(STOCK)[0].headers["cookie"]
    assert cookie == "; ".join(f"{name}={value}" for name, value in ANON_COOKIES.items())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_session_cache_file_is_private(h: Harness, session_path: Path) -> None:
    h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))

    h.kline("SH600000")

    assert stat.S_IMODE(os.stat(session_path).st_mode) == 0o600
    assert [path.name for path in session_path.parent.iterdir()] == ["session.json"]


def test_cached_cookies_are_reused_by_a_new_client(make: Callable[..., Harness]) -> None:
    first = make()
    first.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))
    first.kline("SH600000")
    sent_before = len(first.upstream.sent)

    second = make()
    second.kline("SH600000")

    new = first.upstream.sent[sent_before:]
    assert [sent.host for sent in new] == [STOCK]
    assert "xq_a_token=fake-anon-token-0001" in new[0].headers["cookie"]


def test_concurrent_first_calls_bootstrap_once(h: Harness) -> None:
    h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))

    results = h.concurrently([("kline", ("SH600000",), {}) for _ in range(5)])

    assert all(len(result) == 3 for result in results)
    assert len(h.upstream.requests("xueqiu.com", "/")) == 1
    assert len(h.upstream.requests(STOCK, KLINE)) == 5


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        json.dumps({"version": 1, "cookies": {"u": "x"}}),
        json.dumps({"version": 99, "cookies": {"xq_a_token": "x"}}),
    ],
)
def test_unusable_cache_is_ignored(
    make: Callable[..., Harness], session_path: Path, content: str
) -> None:
    session_path.parent.mkdir(parents=True)
    session_path.write_text(content, encoding="utf-8")
    h = make()
    h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))

    h.kline("SH600000")

    assert len(h.upstream.requests("xueqiu.com", "/")) == 1


def test_cache_write_failure_does_not_fail_the_call(
    make: Callable[..., Harness], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    h = make(session_path=blocker / "session.json")
    h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))

    with caplog.at_level(logging.WARNING, logger="xueqiu_api"):
        assert len(h.kline("SH600000")) == 3

    assert "could not write the session cache" in caplog.text


def test_bootstrap_waf_page_is_blocked_and_opens_the_www_breaker(h: Harness) -> None:
    h.upstream.route("www.xueqiu.com", "/", waf_reply())

    with pytest.raises(Blocked):
        h.kline("SH600000")
    with pytest.raises(Blocked) as again:
        h.request(host="www.xueqiu.com", path="/query/v1/suggest_stock.json")

    assert "circuit breaker open for www.xueqiu.com" in again.value.message
    assert h.upstream.requests(STOCK) == []


def test_bootstrap_without_the_token_cookie_is_upstream_changed(h: Harness) -> None:
    h.upstream.route("www.xueqiu.com", "/", home_page({"u": "fake-anon-u-0001"}))

    with pytest.raises(UpstreamChanged) as caught:
        h.kline("SH600000")

    assert "anonymous token cookie" in caught.value.message
    assert h.upstream.requests(STOCK) == []


def test_bootstrap_5xx_is_retried(h: Harness) -> None:
    h.upstream.route("www.xueqiu.com", "/", json_reply({}, 503), home_page())
    h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))

    assert len(h.kline("SH600000")) == 3
    assert len(h.upstream.requests("xueqiu.com", "/")) == 2


def test_token_is_sent_instead_of_anonymous_cookies(
    make: Callable[..., Harness], session_path: Path
) -> None:
    h = make(token=TOKEN)
    h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))
    h.upstream.route(STOCK, QUOTEC, fixture_reply("quotec/cn_hk_us"))

    h.kline("SH600000")
    h.quotec("SH600000")

    kline, quotec = h.upstream.sent
    assert kline.headers["cookie"] == TOKEN
    assert "cookie" not in quotec.headers
    assert not session_path.exists()


def test_bare_token_from_the_environment(
    make: Callable[..., Harness], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XUEQIU_TOKEN", LOGIN_TOKEN)
    h = make()
    h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))

    h.kline("SH600000")

    assert h.upstream.sent[0].headers["cookie"] == f"xq_a_token={LOGIN_TOKEN}"
    assert repr(h.client) == f"{type(h.client).__name__}(session='token')"


def test_credentials_never_leak_into_repr_errors_or_logs(
    make: Callable[..., Harness], caplog: pytest.LogCaptureFixture
) -> None:
    h = make(token=TOKEN)
    h.upstream.route(STOCK, KLINE, error_reply(60201, f"用户id无效 {LOGIN_TOKEN}"))

    with caplog.at_level(logging.DEBUG), pytest.raises(AuthExpired) as caught:
        h.kline("SH600000")

    error = caught.value
    texts = [repr(h.client), str(error), repr(error), json.dumps(error.to_dict()), caplog.text]
    texts += [repr(vars(h.client))]
    for text in texts:
        assert LOGIN_TOKEN not in text
        assert LOGIN_U not in text
    assert "<redacted>" in error.message


def test_anonymous_cookies_never_leak_into_errors_or_logs(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    secret = ANON_COOKIES["xq_a_token"]
    h.upstream.route(STOCK, KLINE, error_reply(500016, f"bad session {secret}", 400))

    with caplog.at_level(logging.DEBUG), pytest.raises(Exception) as caught:
        h.kline("SH600000")

    assert secret not in str(caught.value)
    assert secret not in caplog.text
    assert secret not in repr(h.client)


def test_credential_like_params_are_redacted_in_error_payloads(h: Harness) -> None:
    h.upstream.route("danjuanfunds.com", "/djapi/v2/search", json_reply({}, 404))

    with pytest.raises(UpstreamChanged) as caught:
        h.request(
            host="danjuanfunds.com",
            path="/djapi/v2/search",
            params={"key": "fund", "xq_access_token": "fake-secret-value"},
        )

    assert caught.value.params["xq_access_token"] == "<redacted>"
    assert "fake-secret-value" not in json.dumps(caught.value.to_dict())


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("abc123", {"xq_a_token": "abc123"}),
        (" xq_a_token=abc; u=123 ;", {"xq_a_token": "abc", "u": "123"}),
    ],
)
def test_parse_credentials(token: str, expected: dict[str, str]) -> None:
    assert parse_credentials(token) == expected


@pytest.mark.parametrize("token", ["", "   ", "xq_a_token=abc; garbage", "a=b\nc", "a=b c"])
def test_parse_credentials_rejects_bad_input_without_echoing_it(token: str) -> None:
    with pytest.raises(ValueError) as caught:
        parse_credentials(token)
    assert "garbage" not in str(caught.value)


def test_invalid_token_is_rejected_at_construction(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Client(token="xq_a_token=a b", session_path=tmp_path / "session.json")


def test_default_session_path_follows_platform_conventions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert default_session_path() == tmp_path / "xueqiu-api" / "session.json"
    monkeypatch.setenv("XDG_CACHE_HOME", "relative/path")
    assert default_session_path() == Path.home() / ".cache" / "xueqiu-api" / "session.json"
    monkeypatch.setattr(sys, "platform", "darwin")
    expected = Path.home() / "Library" / "Caches" / "xueqiu-api" / "session.json"
    assert default_session_path() == expected
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_session_path() == tmp_path / "xueqiu-api" / "Cache" / "session.json"


def test_raw_request_host_must_be_known(h: Harness) -> None:
    with pytest.raises(InvalidParams):
        h.request(host="xueqiu.com", path="/")
