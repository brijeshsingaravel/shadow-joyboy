"""MCP tool retrieval (RAG-MCP) — load the RIGHT tools on demand, not the ocean.

The decisive MCP fix the field converged on (Anthropic's Tool Search: ~98.7% token
savings): with 1000s of MCP tools, don't dump every schema into context — retrieve the
relevant handful per task. Pure keyword relevance over name+description (+ exact-name and
server boosts); a vector index can layer on later via the same shape as the memory fabric.
"""

from __future__ import annotations

import re
from typing import Any

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset("the a an of to in on for and or is with use using get set list".split())


def _tokens(s: str) -> set[str]:
    return {w for w in _WORD.findall((s or "").lower()) if w not in _STOP and len(w) > 1}


def tool_score(tool: dict[str, Any], query: str) -> float:
    """Relevance of an MCP tool to a query. Name matches weigh more than description."""
    q = _tokens(query)
    if not q:
        return 0.0
    name = str(tool.get("name", ""))
    name_t = _tokens(name)
    desc_t = _tokens(str(tool.get("description", "")))
    if not (name_t or desc_t):
        return 0.0
    name_hits = len(q & name_t)
    desc_hits = len(q & desc_t)
    score = (2.0 * name_hits + desc_hits) / len(q)
    # exact substring of the query phrase in the tool name → strong boost
    if query.strip().lower() in name.lower():
        score += 1.5
    return score


def retrieve_tools(
    tools: list[dict[str, Any]], query: str, *, k: int = 8, min_score: float = 0.01
) -> list[dict[str, Any]]:
    """Top-k MCP tools for a query — the on-demand subset to expose to the model."""
    scored = [(tool_score(t, query), t) for t in tools]
    ranked = [t for s, t in sorted(scored, key=lambda x: x[0], reverse=True) if s > min_score]
    return ranked[: max(0, k)]
