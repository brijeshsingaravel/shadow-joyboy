"""deep_search — governed multi-source research: search → fetch → rerank → evidence.

The 2025-26 deep-research consensus is decompose → fan-out → RERANK → synthesize.
We do the retrieval half deterministically and governed: `ddgs` (MIT) search →
parallel Trafilatura/crawl4ai fetch → BM25 passage rerank → a ranked, cited
evidence pack. Synthesis stays with the agent loop (already governed + eval'd)
rather than an opaque external service. Deliberately NOT SearXNG (AGPL-3.0, § H /
D45). Evidence is wrapped in <retrieved>...</retrieved> (ASI02).
"""

from __future__ import annotations

import asyncio
from typing import Any

from madras.models.agent_config import Rank
from madras.tools.registry import ToolResult, tool
from madras.tools.rerank import bm25_rank, split_passages
from madras.tools.web_extract import fetch_clean

_MAX_CONTENT_CHARS = 8_000
_TIMELIMIT = {"day": "d", "week": "w", "month": "m", "year": "y"}


async def _search_urls(query: str, k: int, time_range: str = "") -> list[tuple[str, str]]:
    """Return up to k (title, url) via ddgs (DuckDuckGo), de-duped. Resilient: [] on error."""
    kwargs: dict[str, Any] = {"max_results": k}
    if time_range in _TIMELIMIT:
        kwargs["timelimit"] = _TIMELIMIT[time_range]

    def _run() -> list[dict[str, Any]]:
        from ddgs import DDGS  # pyright: ignore[reportUnknownVariableType]  # no type stubs

        with DDGS() as ddgs:
            return list(ddgs.text(query, **kwargs))

    try:
        results = await asyncio.to_thread(_run)
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in results:
        u = item.get("href", "")
        if not u or u in seen:
            continue
        seen.add(u)
        out.append((item.get("title", ""), u))
        if len(out) >= k:
            break
    return out


@tool(
    name="deep_search",
    toolset="web",
    rank_required=Rank.INTERN,
    description=(
        "Deep research: searches the web, fetches the top sources, and returns the "
        "passages most relevant to the query (BM25-reranked) with [n] source "
        "citations — an evidence pack to reason over. Optional 'time_range' "
        "(day|week|month|year). Wrapped in <retrieved>...</retrieved>."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "research query"},
            "k_sources": {"type": "integer", "description": "pages to fetch (default 5)"},
            "k_passages": {"type": "integer", "description": "evidence passages (default 8)"},
            "time_range": {"type": "string", "description": "day|week|month|year recency filter"},
        },
        "required": ["query"],
    },
)
async def deep_search(args: dict[str, Any]) -> ToolResult:
    query = str(args.get("query", "")).strip()
    if not query:
        return ToolResult(ok=False, error="query is required")
    try:
        k_sources = max(1, min(int(args.get("k_sources") or 5), 10))
    except (TypeError, ValueError):
        k_sources = 5
    try:
        k_passages = max(1, min(int(args.get("k_passages") or 8), 20))
    except (TypeError, ValueError):
        k_passages = 8
    time_range = str(args.get("time_range", "")).strip().lower()

    hits = await _search_urls(query, k_sources, time_range)
    if not hits:
        return ToolResult(ok=False, error="deep_search: no results (search unavailable?)")

    # Fetch sources in parallel via the resilient extraction chain.
    fetched = await asyncio.gather(
        *(fetch_clean(url) for _title, url in hits), return_exceptions=True
    )

    # Build the candidate passage pool, tagged with source index.
    sources: list[tuple[str, str]] = []  # (title, url) kept in citation order
    passages: list[str] = []
    owner: list[int] = []  # passages[i] belongs to sources[owner[i]]
    for (title, url), result in zip(hits, fetched, strict=True):
        if isinstance(result, BaseException):
            continue
        text, _via = result
        if not text:
            continue
        src_idx = len(sources)
        sources.append((title, url))
        for p in split_passages(text):
            passages.append(p)
            owner.append(src_idx)

    if not passages:
        return ToolResult(ok=False, error="deep_search: sources returned no readable content")

    ranked = bm25_rank(query, passages, top_k=k_passages)
    if ranked:
        chosen = [(owner[s.index], s.text) for s in ranked]
    else:
        # No lexical overlap — fall back to the leading passages in source order.
        chosen = [(owner[i], passages[i]) for i in range(min(k_passages, len(passages)))]

    # Render: ranked evidence with [n] citations, then the source list.
    used_src: dict[int, int] = {}
    body: list[str] = []
    for src_idx, text in chosen:
        if src_idx not in used_src:
            used_src[src_idx] = len(used_src) + 1
        cite = used_src[src_idx]
        body.append(f"[{cite}] {text}")
    src_lines = ["", "Sources:"]
    for src_idx, cite in sorted(used_src.items(), key=lambda kv: kv[1]):
        title, url = sources[src_idx]
        src_lines.append(f"[{cite}] {title} {url}".strip())

    content = "<retrieved>\n" + "\n\n".join(body) + "\n" + "\n".join(src_lines) + "\n</retrieved>"
    return ToolResult(
        ok=True,
        content=content[:_MAX_CONTENT_CHARS],
        extras={"sources": len(sources), "passages": len(chosen)},
    )
