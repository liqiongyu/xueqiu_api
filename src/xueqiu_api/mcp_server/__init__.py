"""MCP server behind ``xueqiu mcp``.

This module must import without the optional ``mcp`` dependency: the CLI builds ``McpOptions``
first and only then imports ``xueqiu_api.mcp_server.server``, the module that needs it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

LogLevel = Literal["debug", "info", "warning", "error"]

MIN_MAX_CHARS = 4_000
MAX_MAX_CHARS = 40_000


@dataclass(frozen=True, kw_only=True)
class McpOptions:
    """Options of ``xueqiu mcp``.

    The CLI passes them to ``xueqiu_api.mcp_server.server.main(options)``, which runs the stdio
    server and returns the process exit code.
    """

    exclude: tuple[str, ...] = ()
    """Endpoint or tool names to disable, glob patterns allowed; they win over other options."""
    max_chars: int = 20_000
    """Character budget of one tool result, from ``MIN_MAX_CHARS`` to ``MAX_MAX_CHARS``."""
    replay: Path | None = None
    """Serve requests from this replay directory instead of the upstream."""
    log_level: LogLevel = "warning"
    """Lowest level of the log records written to stderr; stdout carries only JSON-RPC."""
    no_cache: bool = False
    """Keep no response cache. Otherwise results are kept in the user cache directory, or in
    memory with ``replay``."""
    stale_if_error: float | None = None
    """When the upstream fails with an error worth backing off from, serve a cached result up
    to this many seconds old, marked stale; None serves none."""
