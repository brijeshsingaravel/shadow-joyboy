"""Lightweight MCP client that speaks JSON-RPC over stdio.

For Phase 0 this is just a thin wrapper around the mcp Python SDK that
makes connecting to a stub server convenient for tests.
"""

from __future__ import annotations

import importlib.resources
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent, Tool


def _stub_server_path() -> Path:
    """Resolve echo_server.py via importlib so it works installed or editable."""
    # `madras.tools.stub_servers` is a package — its echo_server.py is a resource.
    files = importlib.resources.files("madras.tools.stub_servers")
    return Path(str(files / "echo_server.py"))


class MCPClient:
    def __init__(self, session: ClientSession) -> None:
        self._session = session

    @classmethod
    @asynccontextmanager
    async def connect_stub(cls) -> AsyncGenerator[MCPClient]:
        """Spawn the local echo server and yield a connected client."""
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(_stub_server_path())],
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield cls(session)

    async def list_tools(self) -> list[Tool]:
        result = await self._session.list_tools()
        return list(result.tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = await self._session.call_tool(name, arguments)
        # Concatenate any text content returned
        texts = [c.text for c in result.content if isinstance(c, TextContent)]
        return "".join(texts)
