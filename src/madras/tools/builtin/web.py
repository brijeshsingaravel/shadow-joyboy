"""Built-in governed web tools: web_search (ddgs) and web_fetch.

web_search uses `ddgs` (MIT — DuckDuckGo-backed meta-search) with recency +
safesearch + region filters and de-dupes by URL. Deliberately NOT SearXNG: that's
AGPL-3.0 and would trip the no-AGPL product-path doctrine (D45 / launch-readiness
§ H) — ddgs is a permissive drop-in needing no self-hosted service. web_fetch
delegates to the resilient Trafilatura → crawl4ai extraction chain (web_extract).
Both rank INTERN (read-only), self-register, and never raise — any error returns
ToolResult(ok=False).
"""

from __future__ import annotations

import asyncio
from typing import Any

from madras.models.agent_config import Rank
from madras.security.net_policy import NetPolicy
from madras.security.rails import scan_retrieval
from madras.tools.registry import ToolResult, tool
from madras.tools.web_extract import deep_crawl, fetch_clean

_MAX_CONTENT_CHARS = 8_000
_TIME_RANGES = {"day", "week", "month", "year"}
# ddgs `timelimit` codes for our human-readable recency ranges.
_TIMELIMIT = {"day": "d", "week": "w", "month": "m", "year": "y"}
# ddgs `safesearch` levels for our 0|1|2 ints.
_SAFESEARCH = {0: "off", 1: "moderate", 2: "on"}


@tool(
    name="web_search",
    toolset="web",
    rank_required=Rank.INTERN,
    description=(
        "Search the web (DuckDuckGo-backed) and return ranked results "
        "(title, url, snippet). Optional filters: 'time_range' (day|week|month|year) "
        "for recency, 'language'/'region' (e.g. 'us-en'), 'safesearch' (0|1|2), "
        "'page'. Results are de-duplicated by URL."
    ),
    parameters={
        "type": "object",
        "properties": {
            "q": {"type": "string", "description": "search query"},
            "k": {"type": "integer", "description": "number of results", "default": 5},
            "time_range": {"type": "string", "description": "day|week|month|year (recency filter)"},
            "language": {"type": "string", "description": "region/language, e.g. 'us-en'"},
            "safesearch": {"type": "integer", "description": "0 (off) | 1 (moderate) | 2 (strict)"},
            "page": {"type": "integer", "description": "result page number (1-based)"},
        },
        "required": ["q"],
    },
)
async def web_search(args: dict[str, Any]) -> ToolResult:
    query = str(args.get("q", "")).strip()
    if not query:
        return ToolResult(ok=False, error="q is required")
    k = max(1, int(args.get("k", 5)))

    kwargs: dict[str, Any] = {"max_results": k}
    tr = str(args.get("time_range", "")).strip().lower()
    if tr in _TIMELIMIT:
        kwargs["timelimit"] = _TIMELIMIT[tr]
    region = str(args.get("language", "")).strip()
    if region:
        kwargs["region"] = region
    if "safesearch" in args:
        try:
            kwargs["safesearch"] = _SAFESEARCH[max(0, min(2, int(args["safesearch"])))]
        except (TypeError, ValueError, KeyError):
            pass
    if "page" in args:
        try:
            kwargs["page"] = max(1, int(args["page"]))
        except (TypeError, ValueError):
            pass

    # ddgs is synchronous; run it off the event loop. Never raises upward.
    def _run() -> list[dict[str, Any]]:
        from ddgs import DDGS  # pyright: ignore[reportUnknownVariableType]  # no type stubs

        with DDGS() as ddgs:
            return list(ddgs.text(query, **kwargs))

    try:
        results = await asyncio.to_thread(_run)
    except Exception as exc:
        return ToolResult(ok=False, error=f"search unavailable: {exc}")

    # De-duplicate by URL, preserving rank order, until we have k. ddgs text results
    # carry `title` / `href` / `body`.
    seen: set[str] = set()
    picked: list[dict[str, Any]] = []
    for item in results:
        item_url = item.get("href", "")
        if item_url in seen:
            continue
        seen.add(item_url)
        picked.append(item)
        if len(picked) >= k:
            break
    if not picked:
        return ToolResult(ok=True, content="No results found.", extras={"count": 0})

    lines: list[str] = []
    for i, item in enumerate(picked, 1):
        title = item.get("title", "(no title)")
        item_url = item.get("href", "")
        snippet = item.get("body", "")
        lines.append(f"{i}. {title}\n   {item_url}\n   {snippet}")
    scanned = await scan_retrieval("\n\n".join(lines))
    return ToolResult(ok=True, content=scanned, extras={"count": len(picked)})


@tool(
    name="web_fetch",
    toolset="web",
    rank_required=Rank.INTERN,
    description=(
        "Fetch the readable main text (markdown) of a URL. Uses Trafilatura "
        "(fast, deterministic) with a crawl4ai/Playwright fallback for JS-heavy "
        "pages. Returns clean article text with boilerplate stripped."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "max_chars": {"type": "integer", "description": "cap on returned characters"},
        },
        "required": ["url"],
    },
)
async def web_fetch(args: dict[str, Any]) -> ToolResult:
    target_url = str(args.get("url", "")).strip()
    if not target_url:
        return ToolResult(ok=False, error="url is required")
    try:
        cap = int(args.get("max_chars") or _MAX_CONTENT_CHARS)
    except (TypeError, ValueError):
        cap = _MAX_CONTENT_CHARS
    cap = max(200, min(cap, 50_000))
    text, via = await fetch_clean(target_url, max_chars=cap)
    if text is None:
        return ToolResult(ok=False, error=f"fetch unavailable for {target_url}")
    scanned = await scan_retrieval(text)
    return ToolResult(ok=True, content=scanned, extras={"via": via, "url": target_url})


@tool(
    name="web_crawl",
    toolset="web",
    rank_required=Rank.INTERN,
    description=(
        "Multi-page crawl starting from a URL, following links up to a bounded depth "
        "(via the self-hosted crawl4ai service's own deep-crawl mode). Returns clean "
        "markdown per page visited. Use for gathering a whole doc/site section, not a "
        "single page (use web_fetch for that)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "seed URL to start crawling from"},
            "max_pages": {
                "type": "integer",
                "description": "max pages to visit (capped at 10)",
                "default": 5,
            },
            "max_depth": {
                "type": "integer",
                "description": "max link-follow depth (capped at 3)",
                "default": 2,
            },
        },
        "required": ["url"],
    },
)
async def web_crawl(args: dict[str, Any]) -> ToolResult:
    seed_url = str(args.get("url", "")).strip()
    if not seed_url:
        return ToolResult(ok=False, error="url is required")
    verdict = NetPolicy().check(seed_url)
    if not verdict.allow:
        return ToolResult(ok=False, error=f"blocked: {verdict.reason}")

    pages = await deep_crawl(
        seed_url,
        max_pages=int(args.get("max_pages", 5) or 5),
        max_depth=int(args.get("max_depth", 2) or 2),
    )
    if not pages:
        return ToolResult(ok=False, error=f"crawl unavailable or found nothing for {seed_url}")

    lines: list[str] = []
    for p in pages:
        scanned = await scan_retrieval(p["markdown"][:_MAX_CONTENT_CHARS])
        lines.append(f"## {p['url']}\n{scanned}")
    return ToolResult(
        ok=True,
        content="\n\n".join(lines),
        extras={"pages_visited": len(pages), "urls": [p["url"] for p in pages]},
    )
