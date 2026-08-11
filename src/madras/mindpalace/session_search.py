"""Session search — hybrid (FTS + vector) fused ranking with RRF + MMR.

The relevance core is PURE + deterministic (no DB/LLM): Reciprocal Rank Fusion merges
keyword and semantic result lists without score calibration (the robust 2025 baseline,
k=60), and MMR diversifies so results aren't near-duplicates. The SessionSearch class
wires FTS + an optional vector index through this core; everything degrades to FTS-only.
"""

from __future__ import annotations

import re
from typing import Any

_WORD = re.compile(r"[a-z0-9]+")


def rrf_fuse(ranked_lists: list[list[str]], *, k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: merge ranked id-lists by Σ 1/(k+rank). Rewards agreement
    across retrievers, no score calibration needed. Returns (id, score) desc."""
    scores: dict[str, float] = {}
    for lst in ranked_lists:
        for rank, id_ in enumerate(lst, start=1):
            scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda t: t[1], reverse=True)


def _tokens(s: str) -> set[str]:
    return set(_WORD.findall((s or "").lower()))


def jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def mmr(
    ordered_ids: list[str], texts: dict[str, str], *, k: int = 6, lambda_: float = 0.7
) -> list[str]:
    """Maximal Marginal Relevance re-rank: greedily pick items that are relevant (their
    fused rank) yet diverse vs. already-picked (Jaccard on text). lambda_=1 → pure
    relevance, 0 → pure diversity. `ordered_ids` is the relevance order (best first)."""
    rel = {id_: 1.0 / (i + 1) for i, id_ in enumerate(ordered_ids)}  # rank → relevance
    selected: list[str] = []
    pool = list(ordered_ids)
    while pool and len(selected) < k:
        best_id, best_score = None, -1e9
        for id_ in pool:
            div = max(
                (jaccard(texts.get(id_, ""), texts.get(s, "")) for s in selected), default=0.0
            )
            score = lambda_ * rel[id_] - (1 - lambda_) * div
            if score > best_score:
                best_id, best_score = id_, score
        selected.append(best_id)  # type: ignore[arg-type]
        pool.remove(best_id)  # type: ignore[arg-type]
    return selected


def passes_facets(
    rec: Any,
    *,
    since: str | None = None,
    until: str | None = None,
    tags: list[str] | None = None,
    agent: str | None = None,
) -> bool:
    """Faceted filter for partial-cue ('tip-of-tongue') queries: time window (ISO date
    prefixes; ISO sorts lexically), tag overlap (any), and agent. None = no constraint."""
    ts = str(getattr(rec, "ts", "") or "")[:10]
    if since and ts and ts < since[:10]:
        return False
    if until and ts and ts > until[:10]:
        return False
    if tags:
        rtags = {t.lower() for t in (getattr(rec, "tags", []) or [])}
        if not (rtags & {t.lower() for t in tags}):
            return False
    if agent and (getattr(rec, "agent_name", "") or "").lower() != agent.lower():
        return False
    return True


class SessionSearch:
    """Hybrid session retrieval: FTS + optional vector, fused via RRF, MMR-diversified.

    ``vector_index`` is any object with ``search(query, k) -> list[session_id]`` (e.g. a
    QdrantVectorIndex over a sessions collection). None → FTS-only (graceful)."""

    def __init__(self, ledger: Any, *, vector_index: Any = None, reranker: Any = None) -> None:
        self._ledger = ledger
        self._vec = vector_index

        # Same guard as `MemoryFabric` (s61). Both halves carry a tenant, so both can disagree: a
        # ledger on "acme" paired with a "default"-scoped index would search one namespace's
        # vectors and then hydrate from another's rows, returning nothing while each half behaved
        # correctly alone. This is the search path that made the tenant column necessary in the
        # first place -- it hydrates vector hits by session_id -- so a silent mismatch here would
        # reopen exactly the hole the column closed.
        # `getattr`, not isinstance: the index is a duck-typed seam and a double may legitimately
        # have no tenant concept.
        led_tenant = getattr(ledger, "tenant", None)
        vec_tenant = getattr(vector_index, "tenant", None)
        if led_tenant is not None and vec_tenant is not None and led_tenant != vec_tenant:
            raise ValueError(
                f"tenant mismatch: ledger is on {led_tenant!r} but its vector index is on "
                f"{vec_tenant!r} -- hits and rows would come from different namespaces"
            )
        # optional async reranker(query, ordered_ids, texts) -> reordered ids
        # (cross-encoder/LLM). None → fusion order. Degrades on error.
        self._reranker = reranker

    async def index_session(self, record: Any) -> bool:
        """Embed a session into the vector half (summary + tags), keyed by session_id, so
        semantic recall finds it. Best-effort: no-op (False) without a vector index."""
        if self._vec is None:
            return False
        text = f"{getattr(record, 'summary', '')} {' '.join(getattr(record, 'tags', []) or [])}"
        if not text.strip():
            return False
        try:
            await self._vec.index(getattr(record, "session_id", ""), text)
            return True
        except Exception:
            return False

    async def search(
        self,
        query: str,
        *,
        project: str = "default",
        k: int = 6,
        pool: int = 50,
        mmr_lambda: float = 0.7,
        since: str | None = None,
        until: str | None = None,
        tags: list[str] | None = None,
        agent: str | None = None,
    ) -> list[Any]:
        """Return up to k SessionRecords for the query, hybrid-ranked + diversified.
        Optional facets (time window / tags / agent) narrow the candidate pool first."""
        from madras.mindpalace.search import search_fts

        fts = await search_fts(self._ledger, query=query, project=project, limit=pool)
        fts_ids = [r.session_id for r in fts]
        by_id = {r.session_id: r for r in fts}

        vec_ids: list[str] = []
        if self._vec is not None:
            try:
                vec_ids = await self._vec.search(query, pool)
            except Exception:
                vec_ids = []
        # hydrate any vector-only hits the FTS pool missed
        for sid in vec_ids:
            if sid not in by_id:
                rec = await self._ledger.get(session_id=sid)
                if rec is not None:
                    by_id[sid] = rec

        # Faceted narrowing (tip-of-the-tongue): drop candidates outside the facets.
        if since or until or tags or agent:
            by_id = {
                sid: r
                for sid, r in by_id.items()
                if passes_facets(r, since=since, until=until, tags=tags, agent=agent)
            }
            fts_ids = [s for s in fts_ids if s in by_id]
            vec_ids = [s for s in vec_ids if s in by_id]

        fused = rrf_fuse([fts_ids, [s for s in vec_ids if s in by_id]])
        ordered = [sid for sid, _ in fused if sid in by_id]
        texts = {sid: f"{by_id[sid].summary} {' '.join(by_id[sid].tags)}" for sid in ordered}
        # Two-stage: rerank the fused candidates (top precision), THEN MMR for diversity.
        if self._reranker is not None and ordered:
            try:
                reranked = await self._reranker(query, ordered[: max(k * 4, 20)], texts)
                if reranked:
                    ordered = [s for s in reranked if s in by_id] + [
                        s for s in ordered if s not in set(reranked)
                    ]
            except Exception:
                pass
        picked = mmr(ordered, texts, k=k, lambda_=mmr_lambda)
        return [by_id[sid] for sid in picked]
