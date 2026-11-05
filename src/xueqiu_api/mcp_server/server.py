"""The stdio MCP server: ``Toolbox`` on the low-level ``mcp`` Server. Needs the mcp extra."""

from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
from collections.abc import Callable
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp import MCPError
from mcp.server import CacheHint, Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    INVALID_PARAMS,
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
    ToolAnnotations,
)

from .. import __version__
from .._async import AsyncClient, AsyncRuntime
from .._replay import ReplayTransport
from . import McpOptions
from .tools import Toolbox

SERVER_NAME = "xueqiu"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def build_server(
    options: McpOptions, *, client: AsyncClient, now: Callable[[], datetime] = _utc_now
) -> Server[Any]:
    """The server for one process; its tool list is built here once and never changes."""
    toolbox = Toolbox(options, client=client, now=now)
    tools = [
        Tool(
            name=spec.name,
            title=spec.title,
            description=spec.description,
            input_schema=spec.input_schema,
            annotations=ToolAnnotations(
                title=spec.title,
                read_only_hint=True,
                destructive_hint=False,
                open_world_hint=True,
            ),
            _meta={"anthropic/alwaysLoad": True},
        )
        for spec in toolbox.specs
    ]

    async def list_tools(
        ctx: ServerRequestContext[Any], params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        return ListToolsResult(tools=tools)

    async def call_tool(
        ctx: ServerRequestContext[Any], params: CallToolRequestParams
    ) -> CallToolResult:
        if params.name not in toolbox.tools:
            raise MCPError(INVALID_PARAMS, f"Unknown tool: {params.name[:80]}")
        text, is_error = await toolbox.call(params.name, params.arguments or {})
        return CallToolResult(content=[TextContent(type="text", text=text)], is_error=is_error)

    return Server(
        SERVER_NAME,
        version=__version__,
        instructions=toolbox.instructions,
        # The list is fixed for the life of the process.
        cache_hints={"tools/list": CacheHint(ttl_ms=3_600_000, scope="private")},
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def main(options: McpOptions) -> int:
    """Serve on stdin and stdout until the client disconnects; log to stderr only.

    The log level applies to this package and the mcp SDK. Other loggers stay at warning:
    the HTTP layer logs response headers at debug, Set-Cookie included.
    """
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for name in ("xueqiu_api", "mcp"):
        logging.getLogger(name).setLevel(options.log_level.upper())
    asyncio.run(_serve(options))
    return 0


async def _serve(options: McpOptions) -> None:
    with ExitStack() as stack:
        runtime, session_path = None, None
        if options.replay is not None:
            # Replay sessions are synthetic, so they never touch the user's session cache.
            runtime = AsyncRuntime(transport=ReplayTransport(options.replay))
            session_path = Path(stack.enter_context(tempfile.TemporaryDirectory())) / "session.json"
        async with AsyncClient(session_path=session_path, _runtime=runtime) as client:
            server = build_server(options, client=client)
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())
