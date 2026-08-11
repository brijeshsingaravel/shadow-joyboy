"""L6 Relationship tools: relate (add a typed edge) + related (query neighbours) +
concept_clusters (community detection over the relationship graph) + record_dissonance
(the first live writer of the "contradicted" edge type -- row mystery-engine).

Edges are directed, typed, provenance-stamped and temporal (stored via RelationshipStore).
Used for multi-agent / Boardroom reasoning about how entities connect.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from madras.memory.communities import detect_communities
from madras.memory.graph import EDGE_TYPES, Edge
from madras.models.agent_config import Rank
from madras.tools.delegation_context import get_delegation_ctx
from madras.tools.memory_fabric_context import get_memory_fabric_ctx
from madras.tools.registry import ToolResult, tool


@tool(
    name="relate",
    toolset="memory",
    rank_required=Rank.INTERN,
    description=(
        "Record a typed relationship edge between two entities (src --rel--> dst). "
        "rel = paired_with|deferred_to|contradicted|mentored|mentor_of|knows|"
        "works_with|depends_on|related_to."
    ),
    parameters={
        "type": "object",
        "properties": {
            "src": {"type": "string"},
            "rel": {"type": "string"},
            "dst": {"type": "string"},
            "weight": {"type": "number", "default": 1.0},
        },
        "required": ["src", "rel", "dst"],
    },
)
async def relate(args: dict[str, Any]) -> ToolResult:
    ctx = get_memory_fabric_ctx()
    if ctx is None or getattr(ctx, "graph", None) is None:
        return ToolResult(ok=False, error="relationship graph not available in this context")
    src, rel, dst = (str(args.get(k, "")).strip() for k in ("src", "rel", "dst"))
    if not (src and rel and dst):
        return ToolResult(ok=False, error="src, rel, dst are all required")
    if rel not in EDGE_TYPES:
        return ToolResult(
            ok=False, error=f"unknown rel '{rel}'; use one of {', '.join(EDGE_TYPES)}"
        )
    now = time.time()
    await ctx.graph.add_edge(
        Edge(
            id=uuid.uuid4().hex,
            src=src,
            rel=rel,
            dst=dst,
            weight=float(args.get("weight", 1.0) or 1.0),
            source=f"session:{ctx.session_id}",
            created_at=now,
        ),
        now=now,
    )
    return ToolResult(ok=True, content=f"Recorded: {src} --{rel}--> {dst}")


@tool(
    name="related",
    toolset="memory",
    rank_required=Rank.INTERN,
    description=(
        "List entities related to a node (its current relationship edges). "
        "Optionally filter by rel type. Returns neighbours wrapped in <retrieved>."
    ),
    parameters={
        "type": "object",
        "properties": {"node": {"type": "string"}, "rel": {"type": "string"}},
        "required": ["node"],
    },
)
async def related(args: dict[str, Any]) -> ToolResult:
    ctx = get_memory_fabric_ctx()
    if ctx is None or getattr(ctx, "graph", None) is None:
        return ToolResult(ok=False, error="relationship graph not available in this context")
    node = str(args.get("node", "")).strip()
    if not node:
        return ToolResult(ok=False, error="node is required")
    rel = str(args.get("rel", "")).strip() or None
    hits = await ctx.graph.neighbors(node, now=time.time(), rel=rel)
    if not hits:
        return ToolResult(ok=True, content=f"<retrieved>(no relationships for {node})</retrieved>")
    lines = [f"- {node} --{r}--> {other} (w={w:g})" for r, other, w in hits]
    return ToolResult(
        ok=True,
        content="<retrieved>\n" + "\n".join(lines) + "\n</retrieved>",
        extras={"count": len(hits)},
    )


@tool(
    name="concept_clusters",
    toolset="memory",
    rank_required=Rank.INTERN,
    description=(
        "Detect emergent concept clusters ('logic-layers') over the relationship "
        "graph via label-propagation community detection. Surfaces densely-"
        "connected groups of entities/concepts for navigation or cross-pollination "
        "-- the Categorization Engine's cross-category insight. Optionally "
        "LLM-summarizes each cluster into a retrieval-unit description (row "
        "nano-graphrag) instead of a raw member list."
    ),
    parameters={
        "type": "object",
        "properties": {
            "summarize": {
                "type": "boolean",
                "default": False,
                "description": "LLM-summarize each cluster (one extra call per "
                "cluster) instead of listing raw member names",
            }
        },
    },
)
async def concept_clusters(args: dict[str, Any]) -> ToolResult:
    ctx = get_memory_fabric_ctx()
    if ctx is None or getattr(ctx, "graph", None) is None:
        return ToolResult(ok=False, error="relationship graph not available in this context")
    edges = await ctx.graph.edges()
    communities = detect_communities(edges, now=time.time())
    if not communities:
        return ToolResult(ok=True, content="<retrieved>(no communities found)</retrieved>")

    summaries: list[str] | None = None
    if bool(args.get("summarize")):
        deleg_ctx = get_delegation_ctx()
        if deleg_ctx is not None:
            from madras.memory.community_summary import summarize_community

            summaries = [
                await summarize_community(c, deleg_ctx.gateway, deleg_ctx.base_model)  # type: ignore[attr-defined]
                for c in communities
            ]

    if summaries:
        lines = [
            f"- cluster {i + 1} ({len(c)}): {s}"
            for i, (c, s) in enumerate(zip(communities, summaries, strict=True))
        ]
    else:
        lines = [f"- cluster {i + 1} ({len(c)}): {', '.join(c)}" for i, c in enumerate(communities)]
    return ToolResult(
        ok=True,
        content="<retrieved>\n" + "\n".join(lines) + "\n</retrieved>",
        extras={"count": len(communities), "clusters": communities, "summaries": summaries},
    )


@tool(
    name="record_dissonance",
    toolset="memory",
    rank_required=Rank.INTERN,
    description=(
        "Record that a NEW claim contradicts an EXISTING belief/claim -- the "
        "Mystery Engine's dissonance trigger. Writes a 'contradicted' relationship "
        "edge (new_claim --contradicted--> existing_belief) so the contradiction "
        "is tracked, not silently smoothed over."
    ),
    parameters={
        "type": "object",
        "properties": {
            "new_claim": {"type": "string", "description": "the new, contradicting claim"},
            "existing_belief": {"type": "string", "description": "the belief/claim it contradicts"},
            "reason": {"type": "string", "description": "why they contradict"},
        },
        "required": ["new_claim", "existing_belief"],
    },
)
async def record_dissonance(args: dict[str, Any]) -> ToolResult:
    ctx = get_memory_fabric_ctx()
    if ctx is None or getattr(ctx, "graph", None) is None:
        return ToolResult(ok=False, error="relationship graph not available in this context")
    new_claim = str(args.get("new_claim", "")).strip()
    existing_belief = str(args.get("existing_belief", "")).strip()
    if not (new_claim and existing_belief):
        return ToolResult(ok=False, error="new_claim and existing_belief are required")
    reason = str(args.get("reason", "") or "")
    now = time.time()
    await ctx.graph.add_edge(
        Edge(
            id=uuid.uuid4().hex,
            src=new_claim,
            rel="contradicted",
            dst=existing_belief,
            source=f"session:{ctx.session_id}" + (f" ({reason})" if reason else ""),
            created_at=now,
        ),
        now=now,
    )
    return ToolResult(
        ok=True, content=f"Recorded dissonance: {new_claim!r} contradicts {existing_belief!r}"
    )
