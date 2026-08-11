"""Deterministic passage reranking (BM25) for governed deep research.

The 2025 deep-research consensus is decompose → fan-out → RERANK → synthesize.
Reranking is the lever that most improves retrieval quality (SAGE). We do it
deterministically with BM25 — no model, no embedding service, fully auditable —
so the tool gathers + ranks evidence and the agent loop (already governed +
eval'd) does the synthesis. Pure stdlib.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass
class Scored:
    index: int  # position in the original passages list
    score: float
    text: str


def bm25_rank(
    query: str, passages: list[str], *, k1: float = 1.5, b: float = 0.75, top_k: int | None = None
) -> list[Scored]:
    """Rank ``passages`` by BM25 relevance to ``query`` (highest first).

    Ties break by original order (stable). Empty query or passages → []."""
    q_terms = set(_tokenize(query))
    if not q_terms or not passages:
        return []
    docs = [_tokenize(p) for p in passages]
    n = len(docs)
    avgdl = sum(len(d) for d in docs) / n if n else 0.0
    # document frequency per query term
    df: dict[str, int] = {}
    for term in q_terms:
        df[term] = sum(1 for d in docs if term in d)
    scored: list[Scored] = []
    for i, (passage, doc) in enumerate(zip(passages, docs, strict=True)):
        dl = len(doc)
        score = 0.0
        for term in q_terms:
            f = doc.count(term)
            if f == 0:
                continue
            # idf with the BM25 +1 smoothing (never negative)
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            denom = f + k1 * (1 - b + b * (dl / avgdl if avgdl else 0.0))
            score += idf * (f * (k1 + 1)) / denom if denom else 0.0
        if score > 0:
            scored.append(Scored(index=i, score=score, text=passage))
    scored.sort(key=lambda s: (-s.score, s.index))
    return scored[:top_k] if top_k is not None else scored


def bm25_order(query: str, passages: list[str]) -> list[int]:
    """Indices of ``passages`` ranked by BM25 relevance to ``query`` (best first).

    The injected reranker seam for memory recall (W1·a). Passages scoring 0 are omitted;
    the caller keeps them via stable fallback. A cross-encoder can replace this behind the
    same ``(query, passages) -> list[int]`` signature later. Empty query/passages → []."""
    return [s.index for s in bm25_rank(query, passages)]


def split_passages(text: str, *, min_chars: int = 80, max_chars: int = 1200) -> list[str]:
    """Split a document into passages on blank lines, merging tiny fragments and
    hard-splitting overlong ones, so BM25 scores comparable units."""
    raw = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    out: list[str] = []
    buf = ""
    for block in raw:
        if len(block) > max_chars:
            if buf:
                out.append(buf)
                buf = ""
            for j in range(0, len(block), max_chars):
                out.append(block[j : j + max_chars])
            continue
        if len(buf) + len(block) + 1 <= max_chars:
            buf = f"{buf}\n{block}" if buf else block
        else:
            out.append(buf)
            buf = block
        if len(buf) >= min_chars:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    # de-dupe consecutive duplicates from the flush-on-min logic
    deduped: list[str] = []
    for p in out:
        if not deduped or deduped[-1] != p:
            deduped.append(p)
    return deduped
