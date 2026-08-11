"""Resilient web content extraction: Trafilatura → crawl4ai fallback.

The OSS extraction consensus (2025-26): Trafilatura is the best-in-class main-text
extractor (ACL-published, tops benchmarks, pure-Python, zero infra) — use it as the
fast deterministic primary. Fall back to our self-hosted crawl4ai (Playwright) for
JS-heavy SPAs or when Trafilatura yields too little. All local — no third-party
egress (privacy doctrine). This removes web_fetch's single-point crawl4ai dependency.

``fetch_clean`` takes injectable ``getter``/``crawler`` callables so tests run with
no network.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx

from madras.config import settings

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MadrasAI/1.0; +https://madras.ai)"}
_MIN_OK_CHARS = 200  # Trafilatura output below this → try crawl4ai
_DEFAULT_MAX = 8_000

# (status_code, content_type, body_text)
GetResult = tuple[int, str, str]


async def _real_get(url: str) -> GetResult:
    async with httpx.AsyncClient(timeout=15.0, headers=_HEADERS, follow_redirects=True) as client:
        r = await client.get(url)
        return r.status_code, r.headers.get("content-type", ""), r.text


async def _real_crawl(url: str) -> str | None:
    endpoint = f"{settings.crawl4ai_url}/crawl"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(endpoint, json={"urls": [url]})
        r.raise_for_status()
        data: dict[str, Any] = r.json()
    results: list[Any] = data.get("results") or []
    if not results or not results[0].get("success"):
        return None
    return results[0].get("markdown") or None


async def _real_deep_crawl(url: str, *, max_pages: int, max_depth: int) -> list[dict[str, Any]]:
    """The self-hosted crawl4ai service's own multi-page BFS mode, exposed on the
    SAME `/crawl` endpoint via a `deep_crawl_strategy` block (row crawl4ai) -- the
    service already supports this, Madras only ever called its single-URL path."""
    endpoint = f"{settings.crawl4ai_url}/crawl"
    payload = {
        "urls": [url],
        "crawler_config": {
            "type": "CrawlerRunConfig",
            "params": {
                "stream": False,
                "cache_mode": "bypass",
                "deep_crawl_strategy": {
                    "type": "BFSDeepCrawlStrategy",
                    "params": {
                        "max_depth": max_depth,
                        "max_pages": max_pages,
                        "include_external": False,
                    },
                },
            },
        },
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(endpoint, json=payload)
        r.raise_for_status()
        data: Any = r.json()
    # Response shape can drift by crawl4ai version -- accept either a top-level
    # list or a "results" list, never raise on an unexpected shape.
    results: list[Any] = (
        cast("list[Any]", data) if isinstance(data, list) else (data.get("results") or [])
    )
    out: list[dict[str, Any]] = []
    for row in results:
        if isinstance(row, dict):
            row = cast("dict[str, Any]", row)
            if row.get("success") and row.get("markdown"):
                out.append({"url": row.get("url", ""), "markdown": row.get("markdown", "")})
    return out


def _extract(html: str, url: str) -> str | None:
    """Trafilatura main-text extraction (pure CPU). None if nothing usable."""
    try:
        import trafilatura

        out = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_links=False,
            include_comments=False,
            favor_precision=True,
        )
        return out.strip() if out else None
    except Exception:
        return None


async def fetch_clean(
    url: str,
    *,
    max_chars: int = _DEFAULT_MAX,
    min_ok: int = _MIN_OK_CHARS,
    getter: Callable[[str], Awaitable[GetResult]] = _real_get,
    crawler: Callable[[str], Awaitable[str | None]] = _real_crawl,
) -> tuple[str | None, str]:
    """Return (clean_text|None, via) where via ∈ {trafilatura, crawl4ai, failed}."""
    text: str | None = None
    via = "failed"
    # 1. Fast path: httpx GET + Trafilatura.
    try:
        status, ctype, body = await getter(url)
        if status == 200 and body and ("html" in ctype or "text" in ctype or not ctype):
            ex = _extract(body, url)
            if ex and len(ex) >= min_ok:
                text, via = ex, "trafilatura"
    except Exception:
        pass
    # 2. Fallback: crawl4ai (Playwright) for JS-heavy / failed pages.
    if text is None:
        try:
            md = await crawler(url)
            if md and md.strip():
                text, via = md.strip(), "crawl4ai"
        except Exception:
            pass
    if text is None:
        return None, "failed"
    return text[:max_chars], via


_MAX_PAGES_CEILING = 10  # hard cap -- a single tool call must never trigger an unbounded crawl
_MAX_DEPTH_CEILING = 3


async def deep_crawl(
    url: str,
    *,
    max_pages: int = 5,
    max_depth: int = 2,
    crawler: Callable[..., Awaitable[list[dict[str, Any]]]] = _real_deep_crawl,
) -> list[dict[str, Any]]:
    """Multi-page BFS crawl from `url` via the self-hosted crawl4ai service's own
    deep-crawl mode. Returns a list of {"url", "markdown"} dicts, one per page
    visited; [] on any failure (never raises -- matches fetch_clean's contract)."""
    pages = max(1, min(int(max_pages), _MAX_PAGES_CEILING))
    depth = max(0, min(int(max_depth), _MAX_DEPTH_CEILING))
    try:
        return await crawler(url, max_pages=pages, max_depth=depth)
    except Exception:
        return []
