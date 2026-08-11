"""Discovery Engine's cross-domain analogy engine (row discovery-engine).

No OSS exists for "analogy transfer for LLM agents" (s46 research: the dominant
technique across papers is embedding-based analogical retrieval -- encode the
problem, retrieve structurally-similar-but-domain-different past cases via cosine
similarity, reason over the shared relational structure). This is a direct
generalization of `category_drift.py`'s near-duplicate detector, same primitives
(`memory/vector.py::embed`/`cosine`), applied to skill descriptions instead of
category labels, with the similarity band INVERTED: near-duplicate (>=0.87) means
"the same problem," not an analogy -- an analogy needs REAL similarity (shared
pattern) without being the same thing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from madras.memory.vector import cosine, embed


@dataclass
class Analogy:
    name: str
    description: str
    similarity: float


async def find_analogies(
    problem: str,
    skills: list[Any],
    *,
    top_k: int = 3,
    min_similarity: float = 0.35,
    max_similarity: float = 0.85,
    embed_fn: Callable[[str], Awaitable[list[float] | None]] | None = None,
) -> list[Analogy]:
    """Rank a skill corpus by cross-domain similarity to ``problem``, keeping only the
    band between ``min_similarity`` (too dissimilar to transfer) and ``max_similarity``
    (past that, it's the same problem, not an analogy). ``skills`` items need
    ``.name``/``.description`` (duck-typed to ``skills.format.Skill``). A skill whose
    embed fails, or the problem's own embed failing, degrades to an empty result --
    never raises."""
    ef = embed_fn or embed
    problem = (problem or "").strip()
    if not problem or not skills:
        return []
    descriptions = [str(getattr(s, "description", "") or "") for s in skills]
    vecs = await asyncio.gather(ef(problem), *(ef(d) for d in descriptions))
    pv = vecs[0]
    if pv is None:
        return []

    candidates: list[Analogy] = []
    for skill, dv in zip(skills, vecs[1:], strict=False):
        if dv is None:
            continue
        sim = cosine(pv, dv)
        if min_similarity <= sim < max_similarity:
            candidates.append(
                Analogy(
                    name=str(getattr(skill, "name", "")),
                    description=str(getattr(skill, "description", "")),
                    similarity=round(sim, 4),
                )
            )
    candidates.sort(key=lambda a: -a.similarity)
    return candidates[:top_k]
