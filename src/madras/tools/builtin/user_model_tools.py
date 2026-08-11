"""user_model tool (E-B7) — the agent's evolving model of the user.

Reads the MemoryFabric (same per-run context as remember/recall/relate), assembles the
current user-model (facts + preferences + relationships), and returns a compact profile.
Dialectical evolution is inherited from supersession + the E6 drift-flag (memory/user_model).
"""

from __future__ import annotations

import time
from typing import Any

from madras.memory.user_model import build_user_model, render_user_model
from madras.models.agent_config import Rank
from madras.tools.memory_fabric_context import get_memory_fabric_ctx
from madras.tools.registry import ToolResult, tool


@tool(
    name="user_model",
    toolset="memory",
    rank_required=Rank.INTERN,
    description=(
        "Get your evolving model of the user: known facts (name/location/job), stated "
        "preferences, and relationships. Use it to act on what you already know about them."
    ),
    parameters={"type": "object", "properties": {}},
)
async def user_model(args: dict[str, Any]) -> ToolResult:
    ctx = get_memory_fabric_ctx()
    if ctx is None or getattr(ctx, "fabric", None) is None:
        return ToolResult(ok=False, error="user-model not available in this context")
    now = time.time()
    items = await ctx.fabric.current_items(now=now)
    model = build_user_model(items, now=now)
    if model.is_empty():
        return ToolResult(ok=True, content="<retrieved>(no user-model yet)</retrieved>")
    return ToolResult(
        ok=True,
        content="<retrieved>\n" + render_user_model(model) + "\n</retrieved>",
        extras={
            "facts": model.facts,
            "preferences": model.preferences,
            "relationships": model.relationships,
        },
    )
