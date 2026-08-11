"""note — Shadow writes a durable note to episodic memory (working-memory offload)."""

from __future__ import annotations

from typing import Any

from madras.models.agent_config import Rank
from madras.tools.memory_context import get_active_memory
from madras.tools.registry import ToolResult, tool


@tool(
    name="note",
    toolset="memory",
    rank_required=Rank.INTERN,
    description="Save a durable note to memory for later recall.",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The note content to save."},
        },
        "required": ["text"],
    },
)
async def note(args: dict[str, Any]) -> ToolResult:
    ctx = get_active_memory()
    if ctx is None or ctx.episodic is None:
        return ToolResult(ok=False, error="no memory context active")
    text = str(args.get("text", "")).strip()
    if not text:
        return ToolResult(ok=False, error="note text required")
    try:
        from madras.memory.episodic import Episode

        eid = await ctx.episodic.write(
            Episode(
                session_id=ctx.session_id,
                agent_name=ctx.agent_name,
                summary=text,
                tags=["note"],
                extras={"kind": "note"},
            )
        )
        return ToolResult(ok=True, content=f"noted (#{eid})")
    except Exception as exc:
        return ToolResult(ok=False, error=f"note failed: {type(exc).__name__}: {exc}")
