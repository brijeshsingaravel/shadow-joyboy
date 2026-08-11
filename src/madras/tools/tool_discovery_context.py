"""Per-run context so the discovery bridge (tool_find/tool_describe/tool_call) reaches
the governed registry + executor. Mirrors mcp_context — set per tool-loop run."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from madras.models.agent_config import Rank


@dataclass
class ToolDiscoveryCtx:
    registry: Any  # ToolRegistry (duck-typed)
    executor: Any  # GovernedExecutor (duck-typed)
    agent_name: str = "shadow"
    session_id: str = ""
    agent_rank: Rank = Rank.INTERN
    toolsets: list[str] | None = None
    core_toolsets: frozenset[str] | None = None


_active: ContextVar[ToolDiscoveryCtx | None] = ContextVar("madras_tool_discovery_ctx", default=None)


def set_discovery_ctx(ctx: ToolDiscoveryCtx | None) -> None:
    _active.set(ctx)


def get_discovery_ctx() -> ToolDiscoveryCtx | None:
    return _active.get()
