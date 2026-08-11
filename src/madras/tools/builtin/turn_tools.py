"""recall_turns — recall specific PAST TURNS by meaning (turn-level FTS over the session log).

Finer-grained than session_search (whole sessions): this surfaces the exact turns where
something happened — "what did I say about X", "the turn where we changed Y". Wrapped in
<retrieved> (ASI02). The turn log is the detailed, tagged raw material (W1·c).
"""

from __future__ import annotations

from typing import Any

from madras.models.agent_config import Rank
from madras.tools.registry import ToolResult, tool
from madras.tools.turn_log_context import get_turn_log_ctx


@tool(
    name="recall_turns",
    toolset="search",
    rank_required=Rank.INTERN,
    description=(
        "Recall specific PAST TURNS by meaning (turn-level, keyword-ranked) — the "
        "exact exchanges where something happened, finer than session_search. Use "
        "for 'what did I say about X', 'the turn where we changed Y'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "what to recall from past turns"},
            "k": {"type": "integer", "default": 6},
        },
        "required": ["query"],
    },
)
async def recall_turns(args: dict[str, Any]) -> ToolResult:
    ctx = get_turn_log_ctx()
    if ctx is None or getattr(ctx, "ledger", None) is None:
        return ToolResult(ok=False, error="turn recall not available in this context")
    query = str(args.get("query", "")).strip()
    if not query:
        return ToolResult(ok=False, error="query is required")
    k = int(args.get("k", 6) or 6)
    try:
        hits = await ctx.ledger.search(query=query, agent_name=ctx.agent_name, k=k)
    except Exception as exc:
        return ToolResult(ok=False, error=f"turn recall failed: {str(exc)[:160]}")
    if not hits:
        return ToolResult(ok=True, content="<retrieved>(no matching past turns)</retrieved>")
    lines: list[str] = []
    for r in hits:
        intent = getattr(r, "intent", "") or ""
        head = (
            f"[turn {getattr(r, 'turn_idx', '?')} · {getattr(r, 'session_id', '?')}"
            f"{(' · ' + intent) if intent else ''}]"
        )
        u = (getattr(r, "user_text", "") or "")[:160]
        a = (getattr(r, "assistant_text", "") or "")[:240]
        lines.append(f"{head}\nuser: {u}\nassistant: {a}")
    return ToolResult(
        ok=True,
        content="<retrieved>\n" + "\n\n".join(lines) + "\n</retrieved>",
        extras={"count": len(hits)},
    )
