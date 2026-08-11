"""tool_find / tool_describe / tool_call — progressive tool disclosure across ALL toolsets.

Generalises the MCP-only `mcp_find` pattern to the whole governed tool registry: instead of
dumping every tool's schema into context, the long tail is deferred behind this 3-tool bridge.
The model DISCOVERS (`tool_find`), INSPECTS (`tool_describe`), then INVOKES (`tool_call`) —
and `tool_call` routes the inner tool through the SAME GovernedExecutor, so the rank gate,
audit log, and 8-gate eval fire on the real tool. Lifts the Hermes `tool_search` / Anthropic
Tool Search pattern (MIT / public). Cache-safe: the visible bridge is small + stable, so the
model-visible tool array does not churn mid-conversation.
"""

from __future__ import annotations

import json
from typing import Any

from madras.mcp.retrieval import retrieve_tools
from madras.models.agent_config import Rank
from madras.tools.registry import ToolResult, tool
from madras.tools.tool_discovery_context import get_discovery_ctx

_BRIDGE = {"tool_find", "tool_describe", "tool_call"}


@tool(
    name="tool_find",
    toolset="discovery",
    rank_required=Rank.INTERN,
    description=(
        "Search your DEFERRED (not-yet-loaded) tools for ones relevant to a task — "
        "loading only the relevant few on demand instead of every tool's schema. "
        "Returns tool names + descriptions; then use tool_describe + tool_call."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "the capability you need"},
            "k": {"type": "integer", "default": 8},
        },
        "required": ["query"],
    },
)
async def tool_find(args: dict[str, Any]) -> ToolResult:
    ctx = get_discovery_ctx()
    if ctx is None:
        return ToolResult(ok=False, error="tool-discovery not available in this context")
    query = str(args.get("query", "")).strip()
    if not query:
        return ToolResult(ok=False, error="query is required")
    specs = ctx.registry.deferrable(
        agent_rank=ctx.agent_rank, toolsets=ctx.toolsets, core_toolsets=ctx.core_toolsets
    )
    catalog = [{"name": s.name, "description": s.description} for s in specs]
    hits = retrieve_tools(catalog, query, k=int(args.get("k", 8) or 8))
    if not hits:
        return ToolResult(ok=True, content="<retrieved>(no matching deferred tools)</retrieved>")
    lines = [f"- {t['name']} — {t.get('description', '')[:140]}" for t in hits]
    return ToolResult(
        ok=True,
        content="<retrieved>\n" + "\n".join(lines) + "\n</retrieved>",
        extras={"count": len(hits), "tools": [t["name"] for t in hits]},
    )


@tool(
    name="tool_describe",
    toolset="discovery",
    rank_required=Rank.INTERN,
    description="Get the full input schema for a tool found via tool_find, so you can call it.",
    parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
)
async def tool_describe(args: dict[str, Any]) -> ToolResult:
    ctx = get_discovery_ctx()
    if ctx is None:
        return ToolResult(ok=False, error="tool-discovery not available in this context")
    name = str(args.get("name", "")).strip()
    spec = ctx.registry.get(name)
    if spec is None:
        return ToolResult(ok=False, error=f"unknown tool: {name}")
    schema = {"name": spec.name, "description": spec.description, "parameters": spec.parameters}
    return ToolResult(ok=True, content=json.dumps(schema), extras={"tool": name})


@tool(
    name="tool_call",
    toolset="discovery",
    rank_required=Rank.INTERN,
    description=(
        "Invoke a deferred tool found via tool_find (after tool_describe). Runs it "
        "through full governance (rank gate + audit). `args` = the tool's arg object."
    ),
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string"}, "args": {"type": "object"}},
        "required": ["name"],
    },
)
async def tool_call(args: dict[str, Any]) -> ToolResult:
    ctx = get_discovery_ctx()
    if ctx is None:
        return ToolResult(ok=False, error="tool-discovery not available in this context")
    name = str(args.get("name", "")).strip()
    if not name:
        return ToolResult(ok=False, error="name is required")
    if name in _BRIDGE:
        return ToolResult(ok=False, error="cannot invoke the discovery bridge via tool_call")
    inner: Any = args.get("args") or {}
    if not isinstance(inner, dict):
        return ToolResult(ok=False, error="args must be an object")
    try:
        # Routes the inner tool through the SAME governance (rank gate + audit + eval).
        return await ctx.executor.execute(
            tool_name=name,
            args=inner,
            agent_name=ctx.agent_name,
            session_id=ctx.session_id,
            agent_rank=ctx.agent_rank,
        )
    except Exception as exc:
        # ToolDenied (rank gate) etc. surface as a result so the model can adapt, not crash.
        return ToolResult(ok=False, error=f"{type(exc).__name__}: {str(exc)[:160]}")
