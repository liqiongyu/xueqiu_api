"""The xueqiu CLI: usage and error JSON, doctor on the replay transport, the mcp hook.

Every run here is offline: an autouse fixture points the CLI's test-only replay hook at the
replay fixtures and the session cache at a temporary directory.
"""

from __future__ import annotations

import importlib
import json
import platform
import shutil
import sys
import types
from pathlib import Path
from typing import Any

import pytest

import xueqiu_api
from xueqiu_api import ERROR_TABLE, REGISTRY, cli
from xueqiu_api.mcp import McpOptions

from .harness import REPLAY_DIR

REPLAY_ENV = "XUEQIU_TEST_REPLAY_DIR"
LOGIN_ERROR = {"body": {"error_code": 60201, "error_description": "synthetic login error"}}


@pytest.fixture(autouse=True)
def session_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Run every CLI call on the replay fixtures with a temporary session cache."""
    monkeypatch.setenv(REPLAY_ENV, str(REPLAY_DIR))
    path = tmp_path / "cache" / "session.json"
    monkeypatch.setattr(cli, "default_session_path", lambda: path)
    return path


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
        shutil.rmtree(root / endpoint)
        (root / endpoint).mkdir()
        (root / endpoint / "case.json").write_text(json.dumps(case), encoding="utf-8")
    monkeypatch.setenv(REPLAY_ENV, str(root))


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


@pytest.mark.parametrize("argv", [["--help"], ["doctor", "--help"], ["mcp", "--help"]])
def test_help_leaves_out_the_test_hook(capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(argv)

    assert REPLAY_ENV not in capsys.readouterr().out


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
    # The cookie bootstrap and suggest_stock go to www.xueqiu.com; after the first request
    # each stock.xueqiu.com check waits 1 s for its slot.
    elapsed = ["0.00s", *["1.00s"] * 8, "0.00s"]
    assert table(out) == [
        [name, "ok", seconds] for name, seconds in zip(REGISTRY, elapsed, strict=True)
    ]


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
    later = [name for name in REGISTRY if name.startswith(("finance_", "kline", "quote"))]
    assert {name: row["status"] for name, row in rows.items()} == {
        "batch_quote": "ok",
        "f10_company": "blocked",
        **dict.fromkeys(later, "blocked"),
        "suggest_stock": "ok",
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
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XUEQIU_TOKEN", "fake-token")
    replay_with(tmp_path, monkeypatch, finance_income=LOGIN_ERROR)

    code, out, err = run(capsys, "doctor", "--endpoint", "finance_income", "--endpoint", "quotec")

    rows = table(out)
    assert out.splitlines()[1] == "credentials: env"
    assert rows[0][:4] == ["finance_income", "needs_login", "0.00s", "auth_expired:"]
    assert rows[1] == ["quotec", "ok", "1.00s"]
    assert code == ERROR_TABLE["auth_expired"].exit_code
    assert error_json(err)["error_code"] == "auth_expired"
    assert "fake-token" not in out + err


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
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XUEQIU_TOKEN", "fake-token")
    replay_with(
        tmp_path,
        monkeypatch,
        quotec={"status": 429, "body": {}},
        kline=LOGIN_ERROR,
        quote_detail={"body": {"data": "not a quote", "error_code": 0}},
    )

    code, out, err = run(
        capsys,
        "doctor",
        *("--endpoint", "quotec", "--endpoint", "kline", "--endpoint", "quote_detail"),
    )

    assert [row[:2] for row in table(out)] == [
        ["quotec", "rate_limited"],
        ["kline", "needs_login"],
        ["quote_detail", "error"],
    ]
    assert code == ERROR_TABLE["upstream_changed"].exit_code
    assert error_json(err)["endpoint"] == "quote_detail"


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


def test_mcp_without_the_extra_says_how_to_install_it(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real_import = importlib.import_module

    def import_module(name: str, package: str | None = None) -> types.ModuleType:
        if name == "xueqiu_api.mcp.server":
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
    monkeypatch.setitem(sys.modules, "xueqiu_api.mcp.server", None)

    code, _, err = run(capsys, "mcp")

    payload = error_json(err)
    assert (code, payload["error_code"]) == (1, "internal_error")
    assert "[mcp]" not in payload["hint"]


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[McpOptions]:
    """Options the CLI hands to a fake ``xueqiu_api.mcp.server`` module."""
    received: list[McpOptions] = []

    def main(options: McpOptions) -> int:
        received.append(options)
        return 0

    server = types.ModuleType("xueqiu_api.mcp.server")
    monkeypatch.setattr(server, "main", main, raising=False)
    monkeypatch.setitem(sys.modules, "xueqiu_api.mcp.server", server)
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
