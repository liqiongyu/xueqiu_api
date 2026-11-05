"""The xueqiu CLI: usage and error JSON, doctor on the replay transport, the mcp hook.

Every run here is offline: an autouse fixture gives doctor's client the replay transport and a
virtual clock, and points the session cache at a temporary directory.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import platform
import re
import shutil
import sys
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest

import xueqiu_api
from xueqiu_api import ERROR_TABLE, REGISTRY, cli
from xueqiu_api._replay import ReplayTransport
from xueqiu_api._sync import SyncRuntime
from xueqiu_api.mcp_server import McpOptions
from xueqiu_api.sites import SITES, Host

from .harness import REPLAY_DIR, FakeClock, Register, synthetic_endpoint

LOGIN_ERROR = {"body": {"error_code": 60201, "error_description": "synthetic login error"}}
TOKEN_REJECTED = {"status": 400, "body": {"error_code": "400016", "error_description": ""}}


def replay_runtime(directory: Path) -> Callable[[], SyncRuntime]:
    """A runtime factory for doctor: the replay directory on a fresh virtual clock each run."""

    def runtime() -> SyncRuntime:
        clock = FakeClock()
        return SyncRuntime(transport=ReplayTransport(directory), clock=clock.now, sleep=clock.sleep)

    return runtime


@pytest.fixture(autouse=True)
def session_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Run every CLI call on the replay fixtures with a temporary session cache."""
    monkeypatch.setattr(cli, "_runtime", replay_runtime(REPLAY_DIR))
    path = tmp_path / "cache" / "session.json"
    monkeypatch.setattr(cli, "default_session_path", lambda: path)
    return path


@pytest.fixture
def logger_levels() -> Iterator[None]:
    """Restore the levels the real MCP server's main sets on the package and SDK loggers."""
    loggers = [logging.getLogger(name) for name in ("xueqiu_api", "mcp")]
    levels = [logger.level for logger in loggers]
    yield
    for logger, level in zip(loggers, levels, strict=True):
        logger.setLevel(level)


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def error_json(err: str) -> dict[str, Any]:
    """The one JSON object the CLI printed to stderr."""
    [line] = err.splitlines()
    payload: dict[str, Any] = json.loads(line)
    return payload


def table(out: str) -> list[list[str]]:
    """Endpoint rows of the doctor text output, split on whitespace."""
    return [line.split() for line in out.splitlines()[4:]]


def replay_with(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **cases: dict[str, Any]) -> None:
    """Serve the replay fixtures, with one case replacing the files of each named endpoint."""
    root = tmp_path / "replay"
    shutil.copytree(REPLAY_DIR, root)
    for endpoint, case in cases.items():
        shutil.rmtree(root / endpoint, ignore_errors=True)
        (root / endpoint).mkdir()
        (root / endpoint / "case.json").write_text(json.dumps(case), encoding="utf-8")
    monkeypatch.setattr(cli, "_runtime", replay_runtime(root))


def expected_rows() -> list[list[str]]:
    """Status and seconds of each check of a first anonymous run over the registry.

    A check waits for the slot of its host, and the first one that sends cookies also for the
    bootstrap on www.xueqiu.com. A login-only endpoint is refused before it takes a slot.
    """
    now = 0.0
    free: dict[Host, float] = {}
    bootstrapped = False

    def take(host: Host) -> None:
        nonlocal now
        now = max(now, free.get(host, 0.0))
        free[host] = now + SITES[host].min_interval_s

    rows: list[list[str]] = []
    for name, spec in REGISTRY.items():
        start = now
        if spec.auth != "required":
            if SITES[spec.host].cookies and spec.cookies and not bootstrapped:
                take("www.xueqiu.com")
                bootstrapped = True
            take(spec.host)
        status = "needs_login" if spec.auth == "required" else "ok"
        rows.append([name, status, f"{now - start:.2f}s"])
    return rows


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"xueqiu-api {xueqiu_api.__version__}\n"


def test_help_lists_every_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    out = capsys.readouterr().out

    assert exit_info.value.code == 0
    assert "  0   success" in out
    for code, info in ERROR_TABLE.items():
        assert f"  {info.exit_code:<3} {code} ({info.recoverable}" in out


def test_severity_ranks_every_error_code() -> None:
    assert sorted(cli.SEVERITY) == sorted(ERROR_TABLE)


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["status"],
        ["--format", "xml", "doctor"],
        ["doctor", "--format", "json"],
        ["mcp", "--max-chars", "3999"],
        ["mcp", "--max-chars", "40001"],
        ["mcp", "--max-chars", "4e3"],
        ["mcp", "--replay", "no-such-replay-dir"],
        ["mcp", "--log-level", "trace"],
    ],
)
def test_usage_errors_are_invalid_params_json_with_exit_code_2(
    capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    code, out, err = run(capsys, *argv)

    payload = error_json(err)
    assert (code, out) == (2, "")
    assert (payload["error_code"], payload["recoverable"]) == ("invalid_params", "fix_params")
    assert payload["message"]
    assert "--help" in payload["hint"]


def test_doctor_checks_every_endpoint_once_spaced_by_the_limiter(
    capsys: pytest.CaptureFixture[str], session_cache: Path
) -> None:
    code, out, err = run(capsys, "doctor")

    lines = out.splitlines()
    assert (code, err) == (0, "")
    assert lines[:3] == [
        f"xueqiu-api {xueqiu_api.__version__}, Python {platform.python_version()}",
        "credentials: none",
        f"session cache: {session_cache} (missing)",
    ]
    assert lines[3].split() == ["endpoint", "status", "elapsed", "detail"]
    assert [row[:3] for row in table(out)] == expected_rows()


def test_offline_tests_cannot_reach_a_real_transport() -> None:
    # Loopback, so that a broken guard sends nothing off this machine.
    request = httpx2.Request("GET", "http://127.0.0.1:9/")

    with pytest.raises(pytest.fail.Exception, match="real httpx2 transport"):
        httpx2.HTTPTransport().handle_request(request)
    with pytest.raises(pytest.fail.Exception, match="real httpx2 transport"):
        asyncio.run(httpx2.AsyncHTTPTransport().handle_async_request(request))


def test_doctor_reports_whether_the_session_cache_exists(
    capsys: pytest.CaptureFixture[str], session_cache: Path
) -> None:
    run(capsys, "doctor", "--endpoint", "kline")
    assert session_cache.is_file()

    code, out, _ = run(capsys, "doctor", "--endpoint", "kline")

    assert code == 0
    assert out.splitlines()[2] == f"session cache: {session_cache} (exists)"


def test_doctor_json_is_a_list_of_objects_in_the_order_given(
    capsys: pytest.CaptureFixture[str], session_cache: Path
) -> None:
    code, out, err = run(
        capsys,
        "--format",
        "json",
        "doctor",
        *("--endpoint", "kline", "--endpoint", "quotec", "--endpoint", "kline"),
    )

    facts = {
        "package_version": xueqiu_api.__version__,
        "python_version": platform.python_version(),
        "credentials": "none",
        "session_cache": str(session_cache),
        "session_cache_exists": False,
    }
    assert (code, err) == (0, "")
    assert json.loads(out) == [
        {"endpoint": "kline", "status": "ok", "elapsed_s": 0.0, "error": None, **facts},
        {"endpoint": "quotec", "status": "ok", "elapsed_s": 1.0, "error": None, **facts},
    ]


def test_doctor_counts_empty_results_as_success(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay_with(tmp_path, monkeypatch, kline={"body": {"data": {"items": []}, "error_code": 0}})

    code, out, err = run(capsys, "doctor", "--endpoint", "kline")

    assert (code, err) == (0, "")
    assert out.splitlines()[3:] == [
        "endpoint  status        elapsed  detail",
        "kline     empty           0.00s",
    ]


def test_doctor_reports_a_blocked_host_and_sends_nothing_more_to_it(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay_with(tmp_path, monkeypatch, f10_company={"status": 418, "body": {}})

    code, out, err = run(capsys, "--format", "json", "doctor")

    rows = {row["endpoint"]: row for row in json.loads(out)}
    order = list(REGISTRY)
    later = [
        name
        for name in order[order.index("f10_company") + 1 :]
        if REGISTRY[name].host == "stock.xueqiu.com" and REGISTRY[name].auth != "required"
    ]
    assert {name: row["status"] for name, row in rows.items()} == {
        **{row[0]: row[1] for row in expected_rows()},
        "f10_company": "blocked",
        **dict.fromkeys(later, "blocked"),
    }
    # The 418 opened the breaker of stock.xueqiu.com, so the later checks fail fast.
    for name in later:
        assert rows[name]["elapsed_s"] == 0.0
        assert rows[name]["error"]["message"] == "circuit breaker open for stock.xueqiu.com"
        assert rows[name]["error"]["retry_after_s"] == 300.0
    payload = error_json(err)
    assert code == ERROR_TABLE["blocked"].exit_code
    assert (payload["error_code"], payload["endpoint"]) == ("blocked", "f10_company")


def test_doctor_reports_rejected_credentials_as_needs_login(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    register_endpoint: Register,
) -> None:
    monkeypatch.setenv("XUEQIU_TOKEN", "fake-token")
    spec = register_endpoint(synthetic_endpoint("quote_login_only", auth="required"))
    replay_with(tmp_path, monkeypatch, quote_login_only=LOGIN_ERROR)

    code, out, err = run(capsys, "doctor", "--endpoint", spec.name, "--endpoint", "quotec")

    rows = table(out)
    assert out.splitlines()[1] == "credentials: env"
    assert rows[0][:4] == ["quote_login_only", "needs_login", "0.00s", "auth_expired:"]
    assert rows[1] == ["quotec", "ok", "1.00s"]
    assert code == ERROR_TABLE["auth_expired"].exit_code
    assert error_json(err)["error_code"] == "auth_expired"
    assert "fake-token" not in out + err


def test_doctor_does_not_count_a_login_only_endpoint_without_credentials(
    capsys: pytest.CaptureFixture[str], register_endpoint: Register
) -> None:
    spec = register_endpoint(synthetic_endpoint("quote_login_only", auth="required"))

    code, out, err = run(capsys, "doctor", "--endpoint", spec.name)

    assert (code, err) == (0, "")
    assert table(out)[0][:4] == ["quote_login_only", "needs_login", "0.00s", "login_required:"]


@pytest.mark.parametrize(
    ("auth", "case", "status", "detail"),
    [
        ("anonymous", None, "error", "upstream_changed:"),
        ("required", TOKEN_REJECTED, "needs_login", "auth_expired:"),
    ],
    ids=["no-fixture", "token-rejected"],
)
def test_doctor_shows_unverified_failures_without_counting_them(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    register_endpoint: Register,
    auth: str,
    case: dict[str, Any] | None,
    status: str,
    detail: str,
) -> None:
    monkeypatch.setenv("XUEQIU_TOKEN", "fake-token")
    spec = synthetic_endpoint("quote_unverified", auth=auth, verified_on=None)
    register_endpoint(spec)
    replay_with(tmp_path, monkeypatch, **({"quote_unverified": case} if case else {}))

    code, out, err = run(capsys, "doctor", "--endpoint", "quote_unverified")

    assert (code, err) == (0, "")
    assert table(out)[0][:5] == ["quote_unverified", status, "0.00s", "unverified", detail]

    code, out, err = run(capsys, "--format", "json", "doctor", "--endpoint", "quote_unverified")

    [row] = json.loads(out)
    assert (code, err) == (0, "")
    assert (row["status"], row["unverified"]) == (status, True)
    assert row["error"]["error_code"] == detail.removesuffix(":")


def test_doctor_marks_an_unverified_endpoint_that_answers(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    register_endpoint: Register,
) -> None:
    spec = synthetic_endpoint("quote_unverified", verified_on=None)
    register_endpoint(spec)
    case = {"body": {"data": {"value": 1}, "error_code": 0}}
    replay_with(tmp_path, monkeypatch, quote_unverified=case)

    code, out, _ = run(capsys, "doctor", "--endpoint", spec.name)

    assert code == 0
    assert table(out) == [["quote_unverified", "ok", "0.00s", "unverified"]]


def test_doctor_reports_login_errors_of_anonymous_endpoints_as_upstream_changes(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay_with(tmp_path, monkeypatch, finance_income=LOGIN_ERROR)

    code, out, _ = run(capsys, "--format", "json", "doctor", "--endpoint", "finance_income")

    [row] = json.loads(out)
    # One cookie refresh, 3 s after the bootstrap on www.xueqiu.com, did not help, so the
    # registered anonymous access no longer holds.
    assert (row["status"], row["elapsed_s"]) == ("error", 3.0)
    assert row["error"]["error_code"] == "upstream_changed"
    assert code == ERROR_TABLE["upstream_changed"].exit_code


def test_doctor_exits_with_the_most_severe_failure(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    register_endpoint: Register,
) -> None:
    monkeypatch.setenv("XUEQIU_TOKEN", "fake-token")
    spec = register_endpoint(synthetic_endpoint("quote_login_only", auth="required"))
    replay_with(
        tmp_path,
        monkeypatch,
        quotec={"status": 429, "body": {}},
        quote_login_only=LOGIN_ERROR,
        quote_detail={"body": {"data": "not a quote", "error_code": 0}},
    )

    # quotec goes last: after a 429 the client holds the host for a while.
    code, out, err = run(
        capsys,
        "doctor",
        *("--endpoint", spec.name, "--endpoint", "quote_detail", "--endpoint", "quotec"),
    )

    assert [row[:2] for row in table(out)] == [
        ["quote_login_only", "needs_login"],
        ["quote_detail", "error"],
        ["quotec", "rate_limited"],
    ]
    assert code == ERROR_TABLE["upstream_changed"].exit_code
    assert error_json(err)["endpoint"] == "quote_detail"


def test_doctor_reports_out_of_range_upstream_values_and_checks_the_rest(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = json.loads((REPLAY_DIR / "f10_company" / "sh600000.json").read_text(encoding="utf-8"))
    case["body"]["data"]["company"]["listed_date"] = 9_999_999_999_999_999
    replay_with(tmp_path, monkeypatch, f10_company=case)

    code, out, _ = run(capsys, "--format", "json", "doctor")

    rows = {row["endpoint"]: row for row in json.loads(out)}
    assert list(rows) == list(REGISTRY)
    assert rows["f10_company"]["error"]["error_code"] == "upstream_changed"
    assert "out of range" in rows["f10_company"]["error"]["message"]
    assert code == ERROR_TABLE["upstream_changed"].exit_code


def test_doctor_reports_a_bug_in_one_check_and_runs_the_others(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    call = cli.Client.call

    def broken(self: cli.Client, endpoint: str, /, **params: Any) -> Any:
        if endpoint == "f10_company":
            raise RuntimeError("secret internals")
        return call(self, endpoint, **params)

    monkeypatch.setattr(cli.Client, "call", broken)

    code, out, err = run(capsys, "--format", "json", "doctor")

    rows = {row["endpoint"]: row for row in json.loads(out)}
    assert {name: row["status"] for name, row in rows.items()} == {
        **{row[0]: row[1] for row in expected_rows()},
        "f10_company": "error",
    }
    payload = error_json(err)
    [event] = re.findall(r"event id ([0-9a-f]{12})", payload["message"])
    assert (code, payload["error_code"], payload["endpoint"]) == (
        1,
        "internal_error",
        "f10_company",
    )
    assert "secret internals" not in out + err
    assert event in caplog.text and "secret internals" in caplog.text


def test_unexpected_errors_print_an_event_id_and_log_the_details(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(args: object) -> int:
        raise RuntimeError("secret internals")

    monkeypatch.setattr(cli, "_doctor", broken)

    code, out, err = run(capsys, "doctor")

    payload = error_json(err)
    [event] = re.findall(r"event id ([0-9a-f]{12})", payload["message"])
    assert (code, out, payload["error_code"]) == (1, "", "internal_error")
    assert event in payload["hint"] and "secret internals" not in err
    [record] = [r for r in caplog.records if event in r.getMessage()]
    assert record.exc_info is not None and "secret internals" in str(record.exc_info[1])


def test_doctor_rejects_unknown_endpoints_before_any_request(
    capsys: pytest.CaptureFixture[str], session_cache: Path
) -> None:
    code, out, err = run(capsys, "doctor", "--endpoint", "kline", "--endpoint", "klines")

    payload = error_json(err)
    assert (code, out) == (ERROR_TABLE["unknown_endpoint"].exit_code, "")
    assert payload["error_code"] == "unknown_endpoint"
    assert "kline" in payload["hint"]
    assert not session_cache.exists()


def test_doctor_rejects_an_unusable_token_without_echoing_it(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XUEQIU_TOKEN", "fake token")

    code, out, err = run(capsys, "doctor")

    assert (code, out) == (2, "")
    assert error_json(err)["error_code"] == "invalid_params"
    assert "fake token" not in err


@pytest.mark.usefixtures("logger_levels")
def test_mcp_rejects_an_unusable_token_before_serving(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mcp")
    monkeypatch.setenv("XUEQIU_TOKEN", "fake token")

    # The real server: the client is built before stdin is read.
    code, out, err = run(capsys, "mcp", "--replay", str(REPLAY_DIR))

    payload = error_json(err)
    assert (code, out) == (2, "")
    assert (payload["error_code"], payload["recoverable"]) == ("invalid_params", "fix_params")
    assert "XUEQIU_TOKEN" in payload["message"] and "fake token" not in err


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("no_such_endpoint/case.json", '{"body": {}}'),
        ("kline/broken.json", "{not json"),
        ("kline/status.json", '{"status": null, "body": {}}'),
    ],
)
def test_mcp_rejects_an_unusable_replay_directory(
    served: list[McpOptions],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    name: str,
    content: str,
) -> None:
    file = tmp_path / "replay" / name
    file.parent.mkdir(parents=True)
    file.write_text(content, encoding="utf-8")

    code, out, err = run(capsys, "mcp", "--replay", str(tmp_path / "replay"))

    payload = error_json(err)
    assert (code, out, served) == (2, "", [])
    assert payload["error_code"] == "invalid_params"
    assert file.name in payload["message"]


def test_mcp_without_the_extra_says_how_to_install_it(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real_import = importlib.import_module

    def import_module(name: str, package: str | None = None) -> types.ModuleType:
        if name == "xueqiu_api.mcp_server.server":
            raise ModuleNotFoundError("No module named 'mcp'", name="mcp")
        return real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", import_module)

    code, out, err = run(capsys, "mcp")

    payload = error_json(err)
    assert (code, out) == (ERROR_TABLE["disabled_by_config"].exit_code, "")
    assert payload["error_code"] == "disabled_by_config"
    assert "xueqiu-api[mcp]" in payload["hint"]


def test_mcp_reports_other_import_errors_as_bugs(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "xueqiu_api.mcp_server.server", None)

    code, _, err = run(capsys, "mcp")

    payload = error_json(err)
    assert (code, payload["error_code"]) == (1, "internal_error")
    assert "[mcp]" not in payload["hint"]


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[McpOptions]:
    """Options the CLI hands to a fake ``xueqiu_api.mcp_server.server`` module."""
    received: list[McpOptions] = []

    def main(options: McpOptions) -> int:
        received.append(options)
        return 0

    server = types.ModuleType("xueqiu_api.mcp_server.server")
    monkeypatch.setattr(server, "main", main, raising=False)
    monkeypatch.setitem(sys.modules, "xueqiu_api.mcp_server.server", server)
    return received


def test_mcp_hands_the_options_to_the_server(served: list[McpOptions], tmp_path: Path) -> None:
    assert cli.main(["mcp"]) == 0
    assert (
        cli.main(
            [
                "mcp",
                *("--exclude", "kline", "--exclude", "xq_call", "--max-chars", "4000"),
                *("--replay", str(tmp_path), "--log-level", "debug"),
            ]
        )
        == 0
    )
    assert served == [
        McpOptions(),
        McpOptions(
            exclude=("kline", "xq_call"), max_chars=4000, replay=tmp_path, log_level="debug"
        ),
    ]


def test_mcp_options_also_come_from_the_environment(
    served: list[McpOptions], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XUEQIU_MCP_EXCLUDE", "kline, xq_call")
    monkeypatch.setenv("XUEQIU_MCP_MAX_CHARS", "5000")
    monkeypatch.setenv("XUEQIU_MCP_REPLAY", str(tmp_path))
    monkeypatch.setenv("XUEQIU_MCP_LOG_LEVEL", "info")

    assert cli.main(["mcp"]) == 0
    flags = ["--exclude", "quotec", "--max-chars", "6000", "--log-level", "error"]
    assert cli.main(["mcp", *flags]) == 0
    # Flags win over the variables, except that --exclude adds to XUEQIU_MCP_EXCLUDE.
    assert served == [
        McpOptions(exclude=("kline", "xq_call"), max_chars=5000, replay=tmp_path, log_level="info"),
        McpOptions(
            exclude=("kline", "xq_call", "quotec"),
            max_chars=6000,
            replay=tmp_path,
            log_level="error",
        ),
    ]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("XUEQIU_MCP_MAX_CHARS", "abc"),
        ("XUEQIU_MCP_MAX_CHARS", "100"),
        ("XUEQIU_MCP_LOG_LEVEL", "trace"),
        ("XUEQIU_MCP_REPLAY", "no-such-replay-dir"),
    ],
)
def test_invalid_mcp_environment_values_are_usage_errors(
    served: list[McpOptions],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)

    code, out, err = run(capsys, "mcp")

    assert (code, out, served) == (2, "", [])
    assert error_json(err)["error_code"] == "invalid_params"
