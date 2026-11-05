"""Anonymous cookie bootstrap, session cache, credentials and redaction."""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path

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
from xueqiu_api._session import default_session_path, parse_credentials

from .harness import (
    ANON_COOKIES,
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
    monkeypatch.setattr(sys, "platform", "darwin")
    expected = Path.home() / "Library" / "Caches" / "xueqiu-api" / "session.json"
    assert default_session_path() == expected
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_session_path() == tmp_path / "xueqiu-api" / "Cache" / "session.json"


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
