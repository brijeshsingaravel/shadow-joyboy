"""Structured memory tools: remember + recall over the MemoryFabric (Step 2).

Distinct from the qdrant_* vector tools: these store ATOMIC, temporal, contradiction-
aware memories (the fabric) so knowledge updates supersede stale facts and recall is
to-the-point. ASI02: recalled content is wrapped in <retrieved>. ASI06: every write
carries provenance (session + source). `now` comes from the wall clock at call time.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from madras.memory.extract import extract_salient
from madras.memory.retrieval import MemoryItem
from madras.models.agent_config import Rank
from madras.tools.memory_fabric_context import get_memory_fabric_ctx
from madras.tools.registry import ToolResult, tool

_VALID_KINDS = {"fact", "preference", "principle", "relationship", "semantic", "episodic"}


@tool(
    name="remember",
    toolset="memory",
    rank_required=Rank.INTERN,
    description=(
        "Store a durable atomic memory (a fact/preference/principle) so it can be "
        "recalled in future turns. A new fact about the same subject SUPERSEDES the "
        "old one (knowledge update). Pass `auto` with a user message to extract "
        "salient memories automatically."
    ),
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "the atomic statement to remember"},
            "subject": {"type": "string", "description": "entity/topic (drives contradiction)"},
            "kind": {
                "type": "string",
                "description": "fact|preference|principle",
                "default": "fact",
            },
            "tags": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "default": 1.0},
            "auto": {"type": "string", "description": "a message to auto-extract memories from"},
        },
        "required": [],
    },
)
async def remember(args: dict[str, Any]) -> ToolResult:
    ctx = get_memory_fabric_ctx()
    if ctx is None:
        return ToolResult(ok=False, error="memory not available in this context")
    now = time.time()
    src = f"session:{ctx.session_id}" if ctx.session_id else "agent"

    items: list[MemoryItem] = []
    auto = str(args.get("auto", "")).strip()
    if auto:
        for c in extract_salient(auto):
            items.append(
                MemoryItem(
                    id=str(uuid.uuid4()),
                    kind=c.kind,
                    subject=c.subject,
                    content=c.content,
                    source=src,
                    session_id=ctx.session_id,
                    agent_name=ctx.agent_name,
                    created_at=now,
                    valid_from=now,
                )
            )
    content = str(args.get("content", "")).strip()
    if content:
        kind = str(args.get("kind", "fact"))
        if kind not in _VALID_KINDS:
            kind = "fact"
        items.append(
            MemoryItem(
                id=str(uuid.uuid4()),
                kind=kind,
                subject=str(args.get("subject", "")).strip() or content[:40],
                content=content,
                tags=list(args.get("tags") or []),
                confidence=float(args.get("confidence", 1.0) or 1.0),
                source=src,
                session_id=ctx.session_id,
                agent_name=ctx.agent_name,
                created_at=now,
                valid_from=now,
            )
        )

    if not items:
        return ToolResult(ok=False, error="nothing to remember (give `content` or `auto`)")

    stored, superseded = 0, 0
    for it in items:
        expired = await ctx.fabric.remember(it, now=now)
        stored += 1
        superseded += len(expired)
    note = f"Remembered {stored} item(s)"
    if superseded:
        note += f"; superseded {superseded} stale memory(ies)"
    return ToolResult(ok=True, content=note, extras={"stored": stored, "superseded": superseded})


@tool(
    name="memory_import",
    toolset="memory",
    rank_required=Rank.INTERN,
    description=(
        "Import a memory dump (e.g. an exported ChatGPT/Claude/Gemini history or "
        "a notes blob) into durable atomic memories — extracts the salient "
        "facts/preferences and stores them (contradiction-aware)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "dump": {"type": "string", "description": "the text to import memories from"}
        },
        "required": ["dump"],
    },
)
async def memory_import(args: dict[str, Any]) -> ToolResult:
    from madras.memory.experiences import import_candidates

    ctx = get_memory_fabric_ctx()
    if ctx is None:
        return ToolResult(ok=False, error="memory not available in this context")
    dump = str(args.get("dump", "")).strip()
    if not dump:
        return ToolResult(ok=False, error="dump is required")
    now = time.time()
    stored = 0
    for it in import_candidates(dump):
        it.id = str(uuid.uuid4())
        it.session_id = ctx.session_id
        it.agent_name = ctx.agent_name
        it.created_at = now
        it.valid_from = now
        it.source = f"import:session:{ctx.session_id}"
        await ctx.fabric.remember(it, now=now)
        stored += 1
    if not stored:
        return ToolResult(ok=True, content="No durable memories found to import.")
    return ToolResult(ok=True, content=f"Imported {stored} memories.", extras={"stored": stored})


@tool(
    name="recall",
    toolset="memory",
    rank_required=Rank.INTERN,
    description=(
        "Recall the most relevant durable memories for a query (to-the-point, "
        "currently-valid only — superseded facts are excluded). Returns them "
        "wrapped in <retrieved>."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "what to recall about"},
            "k": {"type": "integer", "default": 6},
        },
        "required": ["query"],
    },
)
async def recall(args: dict[str, Any]) -> ToolResult:
    ctx = get_memory_fabric_ctx()
    if ctx is None:
        return ToolResult(ok=False, error="memory not available in this context")
    query = str(args.get("query", "")).strip()
    if not query:
        return ToolResult(ok=False, error="query is required")
    k = int(args.get("k", 6) or 6)
    from madras.tools.rerank import bm25_order

    items = await ctx.fabric.recall(query, now=time.time(), k=k, reranker=bm25_order)
    if not items:
        return ToolResult(ok=True, content="<retrieved>(no relevant memories)</retrieved>")
    lines = [f"- ({it.kind}) {it.content}" for it in items]
    return ToolResult(
        ok=True,
        content="<retrieved>\n" + "\n".join(lines) + "\n</retrieved>",
        extras={"count": len(items)},
    )


@tool(
    name="memory_export",
    toolset="memory",
    rank_required=Rank.INTERN,
    description=(
        "Export the agent's current memories as a portable, content-addressed, "
        "verifiable bundle (for backup / transfer / marketplace portability)."
    ),
    parameters={"type": "object", "properties": {}},
)
async def memory_export(args: dict[str, Any]) -> ToolResult:
    ctx = get_memory_fabric_ctx()
    if ctx is None or getattr(ctx, "fabric", None) is None:
        return ToolResult(ok=False, error="memory not available in this context")
    from madras.memory.portability import export_memory

    items = await ctx.fabric.current_items(now=time.time())
    bundle = export_memory(items, agent=ctx.agent_name)
    return ToolResult(
        ok=True,
        content=f"<retrieved>exported {bundle['count']} memories "
        f"(root {bundle['root'][:12]}…)</retrieved>",
        extras={"bundle": bundle},
    )


@tool(
    name="resolve",
    toolset="memory",
    rank_required=Rank.INTERN,
    description=(
        "Resolve conflicting memories about a subject to the current "
        "best-supported answer (D1.10 / ConflictQA / KnowEdit). Unlike recall(), "
        "which just returns a flat top-k list, resolve() explicitly names the "
        "winning claim, flags any dissenting still-valid claims, and separates "
        "superseded (knowledge-updated) history. Use this instead of recall() "
        "when memories about the same subject might disagree, or when the user "
        "asks what changed / what's current about a subject."
    ),
    parameters={
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "the subject to resolve conflicts for"}
        },
        "required": ["subject"],
    },
)
async def resolve(args: dict[str, Any]) -> ToolResult:
    ctx = get_memory_fabric_ctx()
    if ctx is None:
        return ToolResult(ok=False, error="memory not available in this context")
    subject = str(args.get("subject", "")).strip()
    if not subject:
        return ToolResult(ok=False, error="subject is required")

    from madras.memory.retrieval import arbitrate, same_subject

    now = time.time()
    # all_items(include_expired=True) — the raw, unscored pool (current + superseded).
    # recall()'s pure-core ranking zeros out non-current items regardless of `as_of`
    # (score() treats "non-current" as 0 relevance, not "include it anyway"), so
    # arbitrate() — which does its OWN current/superseded split — needs the unfiltered
    # pool, not a ranked top-k.
    items = await ctx.fabric.all_items(include_expired=True)
    matching = [it for it in items if same_subject(it.subject, subject)]
    result = arbitrate(matching, now)

    if result.winner is None:
        return ToolResult(
            ok=True,
            content="<retrieved>(no memory found for this subject)</retrieved>",
            extras={"winner": None, "conflict_count": 0, "superseded_count": 0},
        )

    lines = [f"WINNER: {result.winner.content} (confidence {result.winner.confidence:.2f})"]
    if result.conflicts:
        lines.append("DISSENTING (still valid, but outvoted):")
        lines.extend(f"  - {it.content}" for it in result.conflicts)
    if result.superseded:
        lines.append("SUPERSEDED (knowledge-update history):")
        lines.extend(f"  - {it.content}" for it in result.superseded)
    lines.append(f"REASON: {result.reason}")

    return ToolResult(
        ok=True,
        content="<retrieved>\n" + "\n".join(lines) + "\n</retrieved>",
        extras={
            "winner": result.winner.content,
            "conflict_count": len(result.conflicts),
            "superseded_count": len(result.superseded),
        },
    )
