"""Anonymous cookie bootstrap, session cache, credentials and redaction."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any, Self

import httpx2
import pytest

from xueqiu_api import (
    AuthExpired,
    Blocked,
    Client,
    InvalidParams,
    LoginRequired,
    UpstreamChanged,
    UpstreamError,
)
from xueqiu_api._async import AsyncClient, AsyncRuntime
from xueqiu_api._paths import cache_dir, data_dir
from xueqiu_api._session import default_session_path, parse_credentials
from xueqiu_api._shared import LEASE_POLL_S, STATE_FILE, SharedState, default_state_path
from xueqiu_api._sqlite import open_db
from xueqiu_api._sync import SyncRuntime

from .harness import (
    ANON_COOKIES,
    FakeClock,
    FakeUpstream,
    Harness,
    Register,
    error_reply,
    fixture_reply,
    home_page,
    json_reply,
    synthetic_endpoint,
    waf_reply,
)

STOCK = "stock.xueqiu.com"
WWW = "www.xueqiu.com"
KLINE = "/v5/stock/chart/kline.json"
QUOTEC = "/v5/stock/realtime/quotec.json"
DETAIL = "/v5/stock/quote.json"
SUGGEST = "/query/v1/suggest_stock.json"
LOGIN_TOKEN = "fake-login-token-9f3a"
LOGIN_U = "fake-login-u-77812"
TOKEN = f"xq_a_token={LOGIN_TOKEN}; u={LOGIN_U}"
SAVED_AT = 1_790_000_000.0
STALE = {"xq_a_token": "fake-stale-token-0001", "u": "fake-stale-u-0001"}
OTHER = {"xq_a_token": "fake-other-token-0001", "u": "fake-other-u-0001"}


def save_session(path: Path, cookies: Mapping[str, str], saved_at: float) -> None:
    """Write the session file as another process would."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "saved_at": saved_at, "cookies": dict(cookies)}
    path.write_text(json.dumps(payload), encoding="utf-8")


def header(cookies: Mapping[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


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
        json.dumps({"version": 1, "cookies": {"xq_a_token": "abc\r\nX-Injected: 1"}}),
        json.dumps({"version": 1, "cookies": {"xq_a_token": "abc", "u": "café"}}),
    ],
    ids=["broken", "no-token", "version", "crlf", "non-ascii"],
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
    saved = json.loads(session_path.read_text(encoding="utf-8"))
    assert saved["cookies"]["xq_a_token"] == ANON_COOKIES["xq_a_token"]


def test_bootstrap_drops_cookies_that_cannot_be_sent(h: Harness, session_path: Path) -> None:
    headers = [
        (b"Set-Cookie", f"xq_a_token={ANON_COOKIES['xq_a_token']}; Path=/".encode()),
        (b"Set-Cookie", "cookiesu=café; Path=/".encode()),
    ]
    h.upstream.route("www.xueqiu.com", "/", lambda request: httpx2.Response(200, headers=headers))
    h.upstream.route(STOCK, DETAIL, fixture_reply("quote_detail/sh600000"))

    assert h.run("quote_detail", "SH600000")

    kept = {name: ANON_COOKIES[name] for name in ("acw_tc", "xq_a_token")}
    cookie = h.upstream.requests(STOCK)[0].headers["cookie"]
    assert cookie == "; ".join(f"{name}={value}" for name, value in kept.items())
    assert json.loads(session_path.read_text(encoding="utf-8"))["cookies"] == kept


def test_bootstrap_with_an_unusable_token_cookie_is_upstream_changed(h: Harness) -> None:
    headers = [(b"Set-Cookie", "xq_a_token=café; Path=/".encode())]
    h.upstream.route("www.xueqiu.com", "/", lambda request: httpx2.Response(200, headers=headers))

    with pytest.raises(UpstreamChanged) as caught:
        h.kline("SH600000")

    assert "usable anonymous token cookie" in caught.value.message
    assert h.upstream.requests(STOCK) == []


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


def test_anonymous_endpoints_send_the_anonymous_cookies_with_a_token_configured(
    make: Callable[..., Harness], session_path: Path
) -> None:
    h = make(token=TOKEN)
    h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))
    h.upstream.route(STOCK, QUOTEC, fixture_reply("quotec/cn_hk_us"))

    assert len(h.kline("SH600000")) == 3
    h.quotec("SH600000")

    [kline] = h.upstream.requests(STOCK, KLINE)
    [quotec] = h.upstream.requests(STOCK, QUOTEC)
    assert kline.headers["cookie"] == "; ".join(f"{k}={v}" for k, v in ANON_COOKIES.items())
    assert "cookie" not in quotec.headers
    saved = session_path.read_text(encoding="utf-8")
    assert json.loads(saved)["cookies"] == ANON_COOKIES
    assert LOGIN_TOKEN not in saved and LOGIN_U not in saved


def test_a_token_client_reuses_the_cached_anonymous_cookies(
    make: Callable[..., Harness], session_path: Path
) -> None:
    session_path.parent.mkdir(parents=True)
    session_path.write_text(json.dumps({"version": 1, "cookies": ANON_COOKIES}), encoding="utf-8")
    h = make(token=TOKEN)
    h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))

    h.kline("SH600000")

    assert [sent.host for sent in h.upstream.sent] == [STOCK]
    assert "xq_a_token=fake-anon-token-0001" in h.upstream.sent[0].headers["cookie"]


def test_two_clients_keep_their_own_credentials(
    make: Callable[..., Harness], register_endpoint: Register
) -> None:
    spec = register_endpoint(synthetic_endpoint(auth="required"))
    tokens = [f"xq_a_token=fake-login-{name}; u=fake-u-{name}" for name in ("first", "second")]
    variables = set(os.environ)

    first, second = (make(token=token) for token in tokens)
    first.upstream.route(STOCK, spec.path, json_reply({"data": {"value": 7}, "error_code": 0}))
    for client in (first, second, first):
        client.call(spec.name, symbol="SH600000")

    sent = first.upstream.requests(STOCK, spec.path)
    assert [request.headers["cookie"] for request in sent] == [tokens[0], tokens[1], tokens[0]]
    assert set(os.environ) == variables


def test_raw_requests_send_the_token_and_write_nothing(
    make: Callable[..., Harness], session_path: Path
) -> None:
    h = make(token=TOKEN)
    h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))

    assert h.request(host=STOCK, path=KLINE)["error_code"] == 0

    [sent] = h.upstream.sent
    assert sent.headers["cookie"] == TOKEN
    assert not session_path.exists()


def test_bare_token_from_the_environment(
    make: Callable[..., Harness], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XUEQIU_TOKEN", LOGIN_TOKEN)
    h = make()
    h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))

    h.request(host=STOCK, path=KLINE)

    assert h.upstream.sent[0].headers["cookie"] == f"xq_a_token={LOGIN_TOKEN}"
    assert repr(h.client) == f"{type(h.client).__name__}(session='token')"


def test_credentials_never_leak_into_repr_errors_or_logs(
    make: Callable[..., Harness], caplog: pytest.LogCaptureFixture
) -> None:
    h = make(token=TOKEN)
    h.upstream.route(STOCK, KLINE, error_reply(60201, f"用户id无效 {LOGIN_TOKEN}"))

    with caplog.at_level(logging.DEBUG), pytest.raises(AuthExpired) as caught:
        h.request(host=STOCK, path=KLINE)

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
        # A Cookie header copied from a browser: empty, quoted and comma-separated values.
        (
            'xq_a_token=abc; xq_is_login=; s="quoted"; Hm_lvt_1db8=1730000000,1730100000',
            {
                "xq_a_token": "abc",
                "xq_is_login": "",
                "s": '"quoted"',
                "Hm_lvt_1db8": "1730000000,1730100000",
            },
        ),
    ],
)
def test_parse_credentials(token: str, expected: dict[str, str]) -> None:
    assert parse_credentials(token) == expected


@pytest.mark.parametrize(
    "token",
    ["", "   ", "xq_a_token=abc; garbage", "a=b\nc", "a=b c", "xq_a_token=abc; u=garbage é"],
)
def test_parse_credentials_rejects_bad_input_without_echoing_it(token: str) -> None:
    with pytest.raises(ValueError) as caught:
        parse_credentials(token)
    assert "garbage" not in str(caught.value)


def test_parse_credentials_names_the_cookie_with_a_bad_value() -> None:
    with pytest.raises(ValueError) as caught:
        parse_credentials("xq_a_token=abc; u=12 3")
    assert "u cookie" in str(caught.value) and "12 3" not in str(caught.value)


def test_invalid_token_is_rejected_at_construction(tmp_path: Path) -> None:
    with pytest.raises(InvalidParams) as caught:
        Client(token="xq_a_token=a b", session_path=tmp_path / "session.json")
    assert caught.value.message.startswith("token is not a valid token")
    assert "a b" not in caught.value.message


def test_an_empty_token_means_anonymous_even_with_the_variable_set(
    make: Callable[..., Harness], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XUEQIU_TOKEN", LOGIN_TOKEN)
    h = make(token="")
    h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))

    h.kline("SH600000")

    assert repr(h.client) == f"{type(h.client).__name__}(session='anonymous')"
    assert LOGIN_TOKEN not in h.upstream.requests(STOCK)[0].headers["cookie"]


def test_default_session_path_follows_platform_conventions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert default_session_path() == tmp_path / "xueqiu-api" / "session.json"
    monkeypatch.setenv("XDG_CACHE_HOME", "relative/path")
    assert default_session_path() == Path.home() / ".cache" / "xueqiu-api" / "session.json"
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert default_session_path() == Path.home() / ".cache" / "xueqiu-api" / "session.json"
    monkeypatch.setattr(sys, "platform", "darwin")
    expected = Path.home() / "Library" / "Caches" / "xueqiu-api" / "session.json"
    assert default_session_path() == expected
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_session_path() == tmp_path / "xueqiu-api" / "Cache" / "session.json"
    monkeypatch.delenv("LOCALAPPDATA")
    local = Path.home() / "AppData" / "Local"
    assert default_session_path() == local / "xueqiu-api" / "Cache" / "session.json"
    assert default_session_path() == cache_dir() / "session.json"


def test_data_dir_follows_platform_conventions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert data_dir() == tmp_path / "xueqiu-api"
    monkeypatch.setenv("XDG_DATA_HOME", "relative/path")
    assert data_dir() == Path.home() / ".local" / "share" / "xueqiu-api"
    monkeypatch.delenv("XDG_DATA_HOME")
    assert data_dir() == Path.home() / ".local" / "share" / "xueqiu-api"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert data_dir() == Path.home() / "Library" / "Application Support" / "xueqiu-api"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert data_dir() == tmp_path / "xueqiu-api" / "Data"
    monkeypatch.delenv("LOCALAPPDATA")
    assert data_dir() == Path.home() / "AppData" / "Local" / "xueqiu-api" / "Data"


def test_offline_tests_and_their_subprocesses_use_private_user_directories(
    tmp_path: Path,
) -> None:
    code = (
        "from xueqiu_api._paths import cache_dir, data_dir\nprint(cache_dir())\nprint(data_dir())\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    for path in (Path.home(), cache_dir(), data_dir(), *map(Path, done.stdout.splitlines())):
        assert path.is_relative_to(tmp_path)
    assert os.environ["PYTHON_KEYRING_BACKEND"] == "keyring.backends.null.Keyring"


@pytest.mark.parametrize("module", ["_sync", "_async"])
def test_the_default_wall_clock_reads_the_patched_module_datetime(
    monkeypatch: pytest.MonkeyPatch, module: str
) -> None:
    moment = datetime(2026, 11, 5, 8, 0, tzinfo=UTC)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls.fromtimestamp(moment.timestamp(), tz)

    monkeypatch.setattr(f"xueqiu_api.{module}.datetime", Frozen)
    runtime = SyncRuntime() if module == "_sync" else AsyncRuntime()
    assert runtime.wall() == moment.timestamp()


def test_the_harness_replaces_the_wall_clock_only_when_given(
    make: Callable[..., Harness],
) -> None:
    given = make(wall=lambda: 1234.5).client
    default = make().client
    assert given._wall() == 1234.5  # pyright: ignore[reportPrivateUsage]
    runtime = SyncRuntime() if isinstance(default, Client) else AsyncRuntime()
    assert default._wall is runtime.wall  # pyright: ignore[reportPrivateUsage]


def test_home_page_without_a_needed_cookie_fails_only_the_endpoints_that_need_it(
    h: Harness,
) -> None:
    only_token = {"xq_a_token": ANON_COOKIES["xq_a_token"]}
    h.upstream.route(WWW, "/", home_page(only_token))
    h.upstream.route(WWW, SUGGEST, fixture_reply("suggest_stock/pfyh"))

    with pytest.raises(UpstreamChanged) as caught:
        h.kline("SH600000")

    assert "u cookie" in caught.value.message
    assert caught.value.endpoint == "kline"
    assert h.upstream.requests(STOCK, KLINE) == []
    assert len(h.upstream.requests("xueqiu.com", "/")) == 1
    assert h.run("suggest_stock", query="pfyh")
    assert len(h.upstream.requests("xueqiu.com", "/")) == 1


@pytest.mark.parametrize(
    ("home_cookies", "fetched"),
    [(None, True), ({"xq_a_token": "fake-anon-token-0002"}, False)],
    ids=["refresh-helps", "still-missing"],
)
def test_a_cached_set_without_a_needed_cookie_is_refreshed_once(
    make: Callable[..., Harness],
    session_path: Path,
    home_cookies: dict[str, str] | None,
    fetched: bool,
) -> None:
    session_path.parent.mkdir(parents=True)
    cached = {"version": 1, "cookies": {"xq_a_token": "fake-anon-token-0001"}}
    session_path.write_text(json.dumps(cached), encoding="utf-8")
    h = make()
    h.upstream.route(WWW, "/", home_page(home_cookies))
    h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))

    if fetched:
        assert len(h.kline("SH600000")) == 3
        assert "u=fake-anon-u-0001" in h.upstream.requests(STOCK, KLINE)[0].headers["cookie"]
    else:
        with pytest.raises(UpstreamChanged):
            h.kline("SH600000")
        assert h.upstream.requests(STOCK, KLINE) == []
    assert len(h.upstream.requests("xueqiu.com", "/")) == 1


def test_credentials_without_a_needed_cookie_are_login_required_with_no_request(
    make: Callable[..., Harness],
    register_endpoint: Register,
) -> None:
    spec = register_endpoint(synthetic_endpoint(auth="required", needs_cookies=("u",)))
    h = make(token=f"xq_a_token={LOGIN_TOKEN}")

    with pytest.raises(LoginRequired) as caught:
        h.call(spec.name, symbol="SH600000")

    assert "u cookie" in caught.value.message
    assert LOGIN_TOKEN not in caught.value.message
    assert h.upstream.sent == []


def test_raw_request_host_must_be_known(h: Harness) -> None:
    with pytest.raises(InvalidParams):
        h.request(host="xueqiu.com", path="/")


def test_an_empty_needed_cookie_counts_as_missing(h: Harness) -> None:
    h.upstream.route(WWW, "/", home_page({"xq_a_token": ANON_COOKIES["xq_a_token"], "u": ""}))
    h.upstream.route(STOCK, KLINE, json_reply({"data": {"items": []}, "error_code": 0}))

    with pytest.raises(UpstreamChanged) as caught:
        h.kline("SH600000")

    assert "u cookie" in caught.value.message
    assert h.upstream.requests(STOCK, KLINE) == []


def test_bootstrap_with_an_empty_token_cookie_is_upstream_changed(
    h: Harness, session_path: Path
) -> None:
    h.upstream.route(WWW, "/", home_page({"xq_a_token": "", "u": ANON_COOKIES["u"]}))

    with pytest.raises(UpstreamChanged):
        h.kline("SH600000")

    assert not session_path.exists()
    assert h.upstream.requests(STOCK, KLINE) == []


def test_a_cached_empty_token_is_ignored(make: Callable[..., Harness], session_path: Path) -> None:
    session_path.parent.mkdir(parents=True)
    cached = {"version": 1, "cookies": {"xq_a_token": "", "u": "fake-anon-u-0001"}}
    session_path.write_text(json.dumps(cached), encoding="utf-8")
    h = make()
    h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))

    h.kline("SH600000")

    assert len(h.upstream.requests("xueqiu.com", "/")) == 1
    cookie = h.upstream.requests(STOCK, KLINE)[0].headers["cookie"]
    assert f"xq_a_token={ANON_COOKIES['xq_a_token']}" in cookie


def test_credentials_with_an_empty_needed_cookie_are_login_required_with_no_request(
    make: Callable[..., Harness], register_endpoint: Register
) -> None:
    spec = register_endpoint(synthetic_endpoint(auth="required", needs_cookies=("u",)))
    h = make(token=f"xq_a_token={LOGIN_TOKEN}; u=")

    with pytest.raises(LoginRequired):
        h.call(spec.name, symbol="SH600000")

    assert h.upstream.sent == []


def test_a_refresh_that_still_lacks_a_needed_cookie_fetches_the_home_page_once(
    make: Callable[..., Harness], session_path: Path
) -> None:
    session_path.parent.mkdir(parents=True)
    cookies = {"xq_a_token": "fake-anon-token-0001", "u": "fake-anon-u-0001"}
    session_path.write_text(json.dumps({"version": 1, "cookies": cookies}), encoding="utf-8")
    h = make()
    h.upstream.route(WWW, "/", home_page({"xq_a_token": "fake-anon-token-0002"}))
    h.upstream.route(STOCK, KLINE, error_reply(60201, "synthetic login error"))

    with pytest.raises(UpstreamChanged) as caught:
        h.kline("SH600000")

    assert "u cookie" in caught.value.message
    assert len(h.upstream.requests(WWW, "/")) == 1
    assert len(h.upstream.requests(STOCK, KLINE)) == 1


@pytest.mark.parametrize("deadline", [float("inf"), 1e10], ids=["inf", "huge"])
def test_a_first_call_works_with_any_deadline_the_constructor_takes(
    make: Callable[..., Harness], deadline: float
) -> None:
    h = make(deadline=deadline)
    h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))

    assert len(h.kline("SH600000")) == 3


def test_a_cookie_value_starting_with_another_leaves_no_tail_in_errors(
    make: Callable[..., Harness],
) -> None:
    h = make(token="xq_a_token=abcdefgh; xq_r_token=abcdefghSECRETTAIL")
    h.upstream.route(STOCK, KLINE, error_reply(99999, "bad abcdefghSECRETTAIL"))

    with pytest.raises(UpstreamError) as caught:
        h.request(host=STOCK, path=KLINE)

    assert "SECRETTAIL" not in caught.value.message
    assert "abcdefgh" not in caught.value.message


@pytest.mark.parametrize(
    ("offset", "adopted"),
    [(1.0, True), (0.0, False), (-1.0, False)],
    ids=["newer", "equal", "older"],
)
def test_a_refresh_adopts_only_a_set_saved_after_the_one_it_holds(
    make: Callable[..., Harness], session_path: Path, offset: float, adopted: bool
) -> None:
    save_session(session_path, STALE, SAVED_AT)
    h = make()
    save_session(session_path, OTHER, SAVED_AT + offset)
    h.upstream.route(
        STOCK,
        KLINE,
        error_reply(60201, "synthetic login error"),
        fixture_reply("kline/sh600000_day"),
    )

    assert len(h.kline("SH600000")) == 3

    first, retry = h.upstream.requests(STOCK, KLINE)
    assert first.headers["cookie"] == header(STALE)
    assert retry.headers["cookie"] == header(OTHER if adopted else ANON_COOKIES)
    assert len(h.upstream.requests("xueqiu.com", "/")) == (0 if adopted else 1)


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="needs POSIX permissions that apply to the user",
)
def test_a_session_that_cannot_save_never_adopts_the_stale_set_again(
    make: Callable[..., Harness], session_path: Path
) -> None:
    save_session(session_path, STALE, SAVED_AT)
    session_path.parent.chmod(0o500)
    fresh = {"xq_a_token": "fake-anon-token-0002", "u": "fake-anon-u-0002"}
    try:
        h = make()
        h.upstream.route(WWW, "/", home_page(), home_page(fresh))
        rejected = error_reply(60201, "synthetic login error")
        bars = fixture_reply("kline/sh600000_day")
        h.upstream.route(STOCK, KLINE, rejected, bars, rejected, bars)

        # Each call spends its one refresh on a home page fetch, though the file keeps STALE.
        assert len(h.kline("SH600000")) == 3
        assert len(h.kline("SH600000")) == 3

        assert json.loads(session_path.read_text(encoding="utf-8"))["cookies"] == STALE
    finally:
        session_path.parent.chmod(0o700)
    sent = [request.headers["cookie"] for request in h.upstream.requests(STOCK, KLINE)]
    refetched = {"acw_tc": ANON_COOKIES["acw_tc"], **fresh}
    assert sent == [header(STALE), header(ANON_COOKIES), header(ANON_COOKIES), header(refetched)]
    assert len(h.upstream.requests("xueqiu.com", "/")) == 2


def test_a_call_waiting_on_the_home_lease_adopts_the_set_its_holder_saves(
    make: Callable[..., Harness],
    clock: FakeClock,
    session_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep = clock.sleep

    def holder_saves(seconds: float) -> None:
        sleep(seconds)
        save_session(session_path, OTHER, SAVED_AT)

    monkeypatch.setattr(clock, "sleep", holder_saves)
    with closing(SharedState(default_state_path(), clock=clock.now, wall=clock.now)) as other:
        assert other.lease("home", clock.now() + 10) == 0.0
        h = make(shared=True)
        h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))

        assert len(h.kline("SH600000")) == 3

    assert h.upstream.requests("xueqiu.com", "/") == []
    assert h.upstream.requests(STOCK, KLINE)[0].headers["cookie"] == header(OTHER)
    assert clock.sleeps == [LEASE_POLL_S]


def test_a_call_takes_over_a_home_lease_that_expires_while_it_waits(
    make: Callable[..., Harness], clock: FakeClock
) -> None:
    with closing(SharedState(default_state_path(), clock=clock.now, wall=clock.now)) as other:
        assert other.lease("home", clock.now() + 2 * LEASE_POLL_S) == 0.0
        h = make(shared=True)
        h.upstream.route(STOCK, KLINE, fixture_reply("kline/sh600000_day"))

        assert len(h.kline("SH600000")) == 3

        assert clock.sleeps == [LEASE_POLL_S, LEASE_POLL_S]
        assert len(h.upstream.requests("xueqiu.com", "/")) == 1
        # The fetch gave the lease back.
        assert other.lease("home", clock.now() + 10) == 0.0


def test_a_failed_home_fetch_gives_the_home_lease_back(
    make: Callable[..., Harness], clock: FakeClock
) -> None:
    h = make(shared=True)
    h.upstream.route(WWW, "/", waf_reply())

    with pytest.raises(Blocked):
        h.kline("SH600000")

    with closing(SharedState(default_state_path(), clock=clock.now, wall=clock.now)) as other:
        assert other.lease("home", clock.now() + 10) == 0.0


def test_a_bootstrap_cancelled_while_taking_the_home_lease_gives_it_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state" / STATE_FILE
    upstream = FakeUpstream(FakeClock())
    runtime = AsyncRuntime(transport=upstream.transport, state_path=path)
    entered, go = threading.Event(), threading.Event()

    async def main() -> None:
        async with AsyncClient(session_path=tmp_path / "session.json", _runtime=runtime) as client:
            limiter = client._limiter  # pyright: ignore[reportPrivateUsage]
            lease_home = limiter.lease_home

            def held(deadline: float) -> float | None:
                entered.set()
                go.wait(10)
                return lease_home(deadline)

            monkeypatch.setattr(limiter, "lease_home", held)
            task = asyncio.create_task(client.kline("SH600000"))
            assert await asyncio.to_thread(entered.wait, 10)
            task.cancel()
            go.set()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(main())

    conn = open_db(path, busy_timeout_s=1, readonly=True)
    try:
        leases: list[Any] = conn.execute("SELECT * FROM leases").fetchall()
    finally:
        conn.close()
    assert leases == []
    with closing(SharedState(path, clock=time.monotonic, wall=time.time)) as other:
        assert other.lease("home", time.monotonic() + 10) == 0.0
    assert upstream.sent == []
