"""Command line interface ``xueqiu``: ``doctor`` and ``mcp``.

Errors are printed to stderr as one JSON object (``XueqiuError.to_dict``) and the process exits
with the error's code from ``ERROR_TABLE``; usage errors are ``invalid_params``, exit code 2.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, NoReturn, get_args

from . import __version__
from ._replay import ReplayTransport
from ._session import TOKEN_ENV, default_session_path
from ._sync import Client, SyncRuntime
from .endpoints import REGISTRY
from .errors import (
    ERROR_TABLE,
    DisabledByConfig,
    ErrorCode,
    InternalError,
    InvalidParams,
    UnknownEndpoint,
    XueqiuError,
)
from .mcp import MAX_MAX_CHARS, MIN_MAX_CHARS, LogLevel, McpOptions

Status = Literal["ok", "empty", "needs_login", "blocked", "rate_limited", "error"]
"""Outcome of one ``doctor`` check."""

SEVERITY: tuple[ErrorCode, ...] = (
    "internal_error",
    "invalid_params",
    "unknown_endpoint",
    "symbol_not_found",
    "upstream_changed",
    "upstream_error",
    "auth_expired",
    "login_required",
    "disabled_by_config",
    "blocked",
    "upstream_unavailable",
    "rate_limited",
)
"""Error codes from most to least severe; ``doctor`` exits with the code of the worst failure.

Bugs in this package come first, then upstream changes, then problems the user has to fix,
then problems that pass with time.
"""

_TEST_REPLAY_ENV = "XUEQIU_TEST_REPLAY_DIR"
"""Test-only, not a public interface and kept out of ``--help``: when set, every request is
served from this replay directory on a virtual clock, so tests run ``doctor`` offline and
without waiting."""


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``xueqiu`` with ``argv`` (default: ``sys.argv[1:]``) and return the exit code."""
    try:
        args = _parser().parse_args(argv)
        return _doctor(args) if args.command == "doctor" else _mcp(args)
    except XueqiuError as exc:
        error = exc
    except Exception as exc:
        error = InternalError(f"unexpected {type(exc).__name__}: {str(exc)[:200]}")
    print(json.dumps(error.to_dict()), file=sys.stderr)
    return error.exit_code


class _Parser(argparse.ArgumentParser):
    """Raises usage errors as ``InvalidParams``, so they print as JSON like every other error."""

    def error(self, message: str) -> NoReturn:
        raise InvalidParams(message, hint=f"Run '{self.prog} --help' for the usage.")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="xueqiu",
        description="Xueqiu (雪球) and Danjuan market data.",
        epilog=_exit_codes(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--format", choices=("text", "json"), default="text", help="output format (default: text)"
    )
    parser.add_argument("--version", action="version", version=f"xueqiu-api {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    doctor = commands.add_parser(
        "doctor",
        help="check the setup and every endpoint",
        description="Report the versions, the credential source and the session cache, then "
        "call every endpoint once with its first registry example, serially through one "
        "client, so the per-host limiter spaces the requests.",
        epilog="Statuses: ok, empty, needs_login, blocked, rate_limited, error. The exit code "
        "is 0 when every checked endpoint is ok or empty. Otherwise the most severe failure "
        "is also printed to stderr as the error JSON and its code is the exit code, from most "
        f"to least severe: {', '.join(SEVERITY)}.",
    )
    doctor.add_argument(
        "--endpoint",
        action="append",
        metavar="NAME",
        help=f"check only this endpoint; repeatable. One of: {', '.join(REGISTRY)}",
    )
    mcp = commands.add_parser(
        "mcp",
        help="run the MCP server on stdio",
        description="Run the read-only MCP server on stdio. It needs the mcp extra: "
        "pip install 'xueqiu-api[mcp]'.",
        epilog="Each option can also be set with the environment variable named in its help; "
        "a flag wins over the variable, except that --exclude adds to XUEQIU_MCP_EXCLUDE.",
    )
    mcp.add_argument(
        "--exclude",
        action="append",
        default=_env_list("XUEQIU_MCP_EXCLUDE"),
        metavar="NAME",
        help="endpoint or tool to disable, glob patterns allowed; repeatable; wins over the "
        "other options (env: XUEQIU_MCP_EXCLUDE, comma-separated)",
    )
    mcp.add_argument(
        "--max-chars",
        type=_max_chars,
        default=os.environ.get("XUEQIU_MCP_MAX_CHARS") or McpOptions.max_chars,
        metavar="N",
        help=f"character budget of one tool result, {MIN_MAX_CHARS} to {MAX_MAX_CHARS} "
        "(default: %(default)s; env: XUEQIU_MCP_MAX_CHARS)",
    )
    mcp.add_argument(
        "--replay",
        type=_directory,
        default=os.environ.get("XUEQIU_MCP_REPLAY") or None,
        metavar="DIR",
        help="serve recorded responses from DIR instead of the upstream (env: XUEQIU_MCP_REPLAY)",
    )
    mcp.add_argument(
        "--log-level",
        type=_log_level,
        choices=get_args(LogLevel),
        default=os.environ.get("XUEQIU_MCP_LOG_LEVEL") or McpOptions.log_level,
        help="lowest level logged to stderr (default: %(default)s; env: XUEQIU_MCP_LOG_LEVEL)",
    )
    return parser


def _env_list(name: str) -> list[str]:
    return [part.strip() for part in os.environ.get(name, "").split(",") if part.strip()]


def _exit_codes() -> str:
    lines = [
        "Errors are printed to stderr as one JSON object with error_code, recoverable,",
        "message and hint, plus fields such as retry_after_s when they apply.",
        "",
        "exit codes:",
        "  0   success",
    ]
    for code, info in sorted(ERROR_TABLE.items(), key=lambda item: item[1].exit_code):
        note = ", also usage errors" if code == "invalid_params" else ""
        lines.append(f"  {info.exit_code:<3} {code} ({info.recoverable}{note})")
    return "\n".join(lines)


def _max_chars(value: str) -> int:
    if not value.isdecimal() or not MIN_MAX_CHARS <= int(value) <= MAX_MAX_CHARS:
        raise argparse.ArgumentTypeError(
            f"expected an integer from {MIN_MAX_CHARS} to {MAX_MAX_CHARS}, got {value!r}"
        )
    return int(value)


def _directory(value: str) -> Path:
    if not Path(value).is_dir():
        raise argparse.ArgumentTypeError(f"{value!r} is not a directory")
    return Path(value)


def _log_level(value: str) -> str:
    # argparse checks choices for flags but not for defaults read from the environment.
    if value not in get_args(LogLevel):
        raise argparse.ArgumentTypeError(
            f"expected one of {', '.join(get_args(LogLevel))}, got {value[:20]!r}"
        )
    return value


def _doctor(args: argparse.Namespace) -> int:
    names: list[str] = list(dict.fromkeys(args.endpoint or REGISTRY))
    for name in names:
        if name not in REGISTRY:
            raise UnknownEndpoint(
                f"unknown endpoint {name[:60]!r}", hint=f"Use one of: {', '.join(REGISTRY)}."
            )
    runtime = _runtime()
    cache = default_session_path()
    credentials = "env" if os.environ.get(TOKEN_ENV) else "none"
    cache_exists = cache.exists()
    try:
        client = Client(session_path=cache, _runtime=runtime)
    except ValueError as exc:
        raise InvalidParams(
            f"{TOKEN_ENV} is not a valid token: {exc}",
            hint=f"Set {TOKEN_ENV} to the xq_a_token value or to a cookie string such as "
            "'xq_a_token=...; u=...'.",
        ) from None
    # Every JSON object carries the run's facts, so the output stays a flat list of objects.
    facts = {
        "package_version": __version__,
        "python_version": platform.python_version(),
        "credentials": credentials,
        "session_cache": str(cache),
        "session_cache_exists": cache_exists,
    }
    text = args.format == "text"
    width = max(len(name) for name in ["endpoint", *names])
    if text:
        print(f"xueqiu-api {__version__}, Python {platform.python_version()}")
        print(f"credentials: {credentials}")
        print(f"session cache: {cache} ({'exists' if cache_exists else 'missing'})")
        print(_row(width, "endpoint", "status", "elapsed", "detail"), flush=True)
    rows: list[dict[str, Any]] = []
    failures: list[XueqiuError] = []
    with client:
        for name in names:
            status, elapsed, error = _check(client, runtime, name)
            if error is not None:
                failures.append(error)
            if text:
                detail = "" if error is None else f"{error.code}: {error.message}"
                print(_row(width, name, status, f"{elapsed:.2f}s", detail), flush=True)
            rows.append(
                {
                    "endpoint": name,
                    "status": status,
                    "elapsed_s": round(elapsed, 3),
                    "error": None if error is None else error.to_dict(),
                    **facts,
                }
            )
    if not text:
        print(json.dumps(rows, indent=2))
    if failures:
        raise min(failures, key=lambda failure: SEVERITY.index(failure.code))
    return 0


def _check(
    client: Client, runtime: SyncRuntime, name: str
) -> tuple[Status, float, XueqiuError | None]:
    """Call ``name`` with its first registry example; return the status, seconds and error."""
    start = runtime.clock()
    try:
        result: Any = client.call(name, **REGISTRY[name].agent.examples[0])
    except XueqiuError as exc:
        return _status(exc), runtime.clock() - start, exc
    # Lists, Kline and Financials are empty when nothing came back; records are models.
    return ("ok" if result else "empty"), runtime.clock() - start, None


def _status(error: XueqiuError) -> Status:
    if error.recoverable == "reauth":
        return "needs_login"
    if error.code == "blocked" or error.code == "rate_limited":
        return error.code
    return "error"


def _row(width: int, endpoint: str, status: str, elapsed: str, detail: str) -> str:
    return f"{endpoint:<{width}}  {status:<12}  {elapsed:>7}  {detail}".rstrip()


class _VirtualClock:
    """Test-only time that moves only when the client sleeps."""

    def __init__(self) -> None:
        self._now = 0.0

    def now(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += seconds


def _runtime() -> SyncRuntime:
    directory = os.environ.get(_TEST_REPLAY_ENV)
    if not directory:
        return SyncRuntime()
    clock = _VirtualClock()
    return SyncRuntime(transport=ReplayTransport(directory), clock=clock.now, sleep=clock.sleep)


def _mcp(args: argparse.Namespace) -> int:
    options = McpOptions(
        exclude=tuple(args.exclude or ()),
        max_chars=args.max_chars,
        replay=args.replay,
        log_level=args.log_level,
    )
    try:
        server = importlib.import_module("xueqiu_api.mcp.server")
    except ModuleNotFoundError as exc:
        if (exc.name or "").partition(".")[0] != "mcp":
            raise
        raise DisabledByConfig(
            "xueqiu mcp needs the optional mcp dependency, which is not installed",
            hint="Install it with pip install 'xueqiu-api[mcp]', or run "
            "uvx --from 'xueqiu-api[mcp]' xueqiu mcp.",
        ) from None
    return server.main(options)
