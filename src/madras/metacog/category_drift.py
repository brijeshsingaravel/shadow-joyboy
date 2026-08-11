"""Categorization Engine's adaptive-categorization layer (row categorization-engine).

catalog.py's ``category`` field is a free string, hand-typed per capability note --
27 distinct strings across 216 notes (not the 14 the note once assumed), with clear
near-duplicates never merged (e.g. "Reasoning & Orchestration" vs "Reasoning &
Planning"). This is the note's own "adaptive categorization... categories that learn/
refine" gap made concrete: detect near-duplicate category labels via embedding
similarity and propose merges, rather than requiring hand curation to catch drift.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from madras.memory.vector import cosine, embed


@dataclass
class MergeCandidate:
    a: str
    b: str
    similarity: float


async def detect_near_duplicate_categories(
    categories: list[str],
    *,
    threshold: float = 0.87,
    embed_fn: Callable[[str], Awaitable[list[float] | None]] | None = None,
) -> list[MergeCandidate]:
    """Pairwise-compare distinct category strings by embedding cosine similarity;
    return pairs at/above ``threshold`` as merge candidates, most-similar first.
    A category whose embed fails is silently dropped from comparison (degrades to
    fewer candidates, never raises)."""
    ef = embed_fn or embed
    uniq = sorted({c.strip() for c in categories if c and c.strip()})
    if len(uniq) < 2:
        return []
    vecs = await asyncio.gather(*(ef(c) for c in uniq))

    candidates: list[MergeCandidate] = []
    for i in range(len(uniq)):
        if vecs[i] is None:
            continue
        for j in range(i + 1, len(uniq)):
            if vecs[j] is None:
                continue
            sim = cosine(vecs[i], vecs[j])
            if sim >= threshold:
                candidates.append(MergeCandidate(uniq[i], uniq[j], round(sim, 4)))
    candidates.sort(key=lambda c: -c.similarity)
    return candidates


async def detect_catalog_category_drift(
    catalog: object,
    *,
    threshold: float = 0.87,
    embed_fn: Callable[[str], Awaitable[list[float] | None]] | None = None,
) -> list[MergeCandidate]:
    """Same as ``detect_near_duplicate_categories`` but sourced live from a loaded
    ``madras_capabilities.catalog.Catalog``."""
    categories = [
        str(getattr(cap, "category", "") or "") for cap in getattr(catalog, "capabilities", [])
    ]
    return await detect_near_duplicate_categories(
        categories, threshold=threshold, embed_fn=embed_fn
    )
