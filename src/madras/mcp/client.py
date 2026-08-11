"""Live MCP client connections (MCP live-client surface).

A thin **governed wrapper over the official MCP SDK** (`mcp`, MIT — adopted, not
reimplemented): open-per-call connect (stdio via `StdioServerParameters`/`stdio_client`, or
streamable-http via `streamablehttp_client`) → `ClientSession.initialize` → yield the session
→ close. The registry GATES the call (active · tool present · unflagged); this layer only
provides the live client. Stateless per call (no session pool) — governed every time.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

# s37: same class of fix as vision_analyze/rca_analyze — a stuck real MCP server
# (dead process, hung connect) must not hang the whole chat turn forever.
_MCP_INVOKE_TIMEOUT_SECONDS = 25


def _args(server: dict[str, Any]) -> list[str]:
    raw = server.get("args")
    if isinstance(raw, str):
        try:
            return list(json.loads(raw))
        except (json.JSONDecodeError, ValueError):
            return []
    return list(raw or [])


@asynccontextmanager
async def open_session(server: dict[str, Any]):
    """Open a live MCP ClientSession for a server record (stdio or http). Per-call."""
    from mcp import ClientSession

    transport = (server.get("transport") or "stdio").lower()
    if transport in ("http", "streamable_http", "streamable-http"):
        from mcp.client.streamable_http import streamable_http_client

        async with streamable_http_client(server.get("url") or "") as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    else:  # stdio (default)
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(command=server.get("command") or "", args=_args(server))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def result_text(result: Any) -> str:
    """Extract text from an MCP CallToolResult (joins TextContent blocks)."""
    parts: list[str] = []
    blocks: list[Any] = getattr(result, "content", None) or []
    for block in blocks:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts) if parts else str(result)


async def mcp_invoke(tool: str, args: dict[str, Any] | None = None, *, ctx: Any = None) -> str:
    """Governed live MCP tool call: resolve tool→active server, gate via the registry, call.

    Reads the per-run MCP context (or an injected `ctx`); raises if unavailable / no server
    hosts the tool / the registry gate denies it.
    """
    if ctx is None:
        from madras.tools.mcp_context import get_mcp_ctx

        ctx = get_mcp_ctx()
    if ctx is None or getattr(ctx, "registry", None) is None:
        raise RuntimeError("MCP registry not available in this context")
    server = await ctx.registry.find_server_for_tool(tool)
    if server is None:
        raise RuntimeError(f"no active MCP server hosts tool {tool!r}")

    async def _connect_and_call() -> Any:
        async with open_session(server) as client:
            return await ctx.registry.call_tool(server["id"], tool, args or {}, client)

    result = await asyncio.wait_for(_connect_and_call(), timeout=_MCP_INVOKE_TIMEOUT_SECONDS)
    return result_text(result)
