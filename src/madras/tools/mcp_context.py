"""Per-run MCP context so mcp_find/mcp_servers reach the registry."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class McpCtx:
    registry: Any  # McpRegistry (duck-typed)
    agent_name: str = "shadow"


_active: ContextVar[McpCtx | None] = ContextVar("madras_mcp_ctx", default=None)


def set_mcp_ctx(ctx: McpCtx | None) -> None:
    _active.set(ctx)


def get_mcp_ctx() -> McpCtx | None:
    return _active.get()
