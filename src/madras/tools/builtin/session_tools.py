"""session_search — recall past sessions by meaning, hybrid-ranked (RRF + MMR).

Lets Shadow answer "what did we do about X / when did we last touch Y" across the
user's session history (Mind Palace), instead of only the current context. Hybrid
FTS + vector, fused + diversified by the pure ranking core. Results carry information
SCENT (date, tags, summary) for recognition, wrapped in <retrieved> (ASI02).
"""

from __future__ import annotations

from typing import Any

from madras.mindpalace.session_search import SessionSearch
from madras.models.agent_config import Rank
from madras.tools.registry import ToolResult, tool
from madras.tools.session_search_context import get_session_search_ctx


@tool(
    name="session_search",
    toolset="search",
    rank_required=Rank.INTERN,
    description=(
        "Search PAST sessions by meaning (hybrid keyword+semantic, relevance-"
        "ranked) to recall prior work — 'what did we decide about X', 'the "
        "session where we fixed Y'. Returns the most relevant sessions with "
        "their date, tags and summary."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "what to find across past sessions"},
            "k": {"type": "integer", "default": 6},
        },
        "required": ["query"],
    },
)
async def session_search(args: dict[str, Any]) -> ToolResult:
    ctx = get_session_search_ctx()
    if ctx is None:
        return ToolResult(ok=False, error="session search not available in this context")
    query = str(args.get("query", "")).strip()
    if not query:
        return ToolResult(ok=False, error="query is required")
    k = int(args.get("k", 6) or 6)
    try:
        ss = SessionSearch(ctx.ledger, vector_index=ctx.vector_index)
        hits = await ss.search(query, project=ctx.project, k=k)
    except Exception as exc:
        return ToolResult(ok=False, error=f"session search failed: {str(exc)[:160]}")
    if not hits:
        return ToolResult(ok=True, content="<retrieved>(no matching past sessions)</retrieved>")
    lines: list[str] = []
    for r in hits:
        when = ""
        ts = getattr(r, "ts", None)
        if ts is not None:
            when = str(ts)[:10]
        tags = ", ".join(getattr(r, "tags", []) or [])
        meta = " · ".join(p for p in (when, tags) if p)
        head = f"[{getattr(r, 'session_id', '?')}{(' · ' + meta) if meta else ''}]"
        lines.append(f"{head}\n{(getattr(r, 'summary', '') or '')[:400]}")
    return ToolResult(
        ok=True,
        content="<retrieved>\n" + "\n\n".join(lines) + "\n</retrieved>",
        extras={"count": len(hits), "session_ids": [getattr(r, "session_id", "") for r in hits]},
    )
