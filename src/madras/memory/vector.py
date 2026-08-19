"""Semantic (L3) vector index for the Memory Fabric — Ollama embed + Qdrant.

A thin, fully-degrading index: ``index(id, text)`` embeds + upserts a point keyed by the
fabric item id; ``search(query, k)`` returns the fabric ids of the nearest points. Any
network/embed error returns silently (keyword-only recall keeps working). This is the L3
Semantic layer folded into the unified fabric, not a separate store the agent must query.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from madras.config import settings

# A wedged Ollama (TCP accepts but never answers) is a real, observed failure mode --
# distinct from a down one (connect refused, fast). This module is designed to be
# "fully-degrading" (see the docstring), but a flat 30s timeout undermines that: a healthy
# nomic-embed answers in <1s, so 30s only ever fires when Ollama is broken, and the fabric
# pipeline's several serial embeds compound it into a multi-minute stall. Split the budget:
# fail a dead/wedged connect fast (2s), keep a bounded-but-generous read (10s) for a slow
# real embed -- degrade to keyword-only recall quickly instead of hanging the caller.
_EMBED_TIMEOUT = httpx.Timeout(connect=2.0, read=10.0, write=10.0, pool=5.0)


def qdrant_headers() -> dict[str, str]:
    """Headers for a Qdrant request: the api-key, or nothing at all when none is configured.

    Returning an EMPTY DICT rather than `{"api-key": ""}` is the whole point -- some Qdrant
    builds reject an empty api-key outright, which would break every unauthenticated local
    instance. Import this rather than reaching for `settings.qdrant_api_key` directly, so the
    empty case stays decided in one place. NOT for Ollama calls: `embed()` below talks to a
    different server and must never be given this key.
    """
    key = settings.qdrant_api_key
    return {"api-key": key} if key else {}


async def embed(text: str) -> list[float] | None:
    """Embed ``text`` via Ollama (nomic-embed). None on any error — callers degrade."""
    try:
        async with httpx.AsyncClient(timeout=_EMBED_TIMEOUT) as client:
            r = await client.post(
                f"{settings.ollama_url}/api/embeddings",
                json={"model": settings.embed_model, "prompt": text},
            )
            r.raise_for_status()
            return r.json()["embedding"]
    except Exception:
        return None


async def embed_many(texts: list[str], *, batch_size: int = 32) -> list[list[float] | None]:
    """Embed many texts in batched calls. Returns one entry per input, in order.

    WHY THIS EXISTS. `embed()` is one HTTP round trip per text, and callers loop over it. Indexing
    one MemoryAgentBench row -- 1,272 chunks -- took 1,104 seconds that way, 0.87s per chunk, and
    every one of those trips was independent of the others (s66).

    Measured on the same model and machine before this was written: 48 chunks one at a time took
    6.6s; in batches of 32, 1.1s. **5.8x, from doing nothing cleverer than asking for more than
    one at a time.**

    Uses Ollama's `/api/embed`, which takes a list, rather than `/api/embeddings`, which takes a
    single prompt. Order is preserved because callers zip the result against their own ids -- a
    reordered list would attach the wrong vector to the wrong memory, which is worse than being
    slow. Returns `None` in place of any batch that failed, so one bad batch never discards the
    others; callers already treat `None` as "skip this one".
    """
    if not texts:
        return []
    out: list[list[float] | None] = []
    async with httpx.AsyncClient(timeout=_EMBED_TIMEOUT) as client:
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            try:
                r = await client.post(
                    f"{settings.ollama_url}/api/embed",
                    json={"model": settings.embed_model, "input": batch},
                )
                r.raise_for_status()
                # Annotated because `r.json()` is Any: without this every element that
                # reaches `out` is Unknown under pyright strict, and the pre-push gate
                # refuses. The runtime shape is Ollama's `{"embeddings": [[float, ...]]}`.
                vecs: list[list[float]] = r.json().get("embeddings") or []
                if len(vecs) != len(batch):
                    # A short response would silently misalign every id after it.
                    out.extend([None] * len(batch))
                else:
                    out.extend(vecs)
            except Exception:  # one failed batch must not lose the rest
                out.extend([None] * len(batch))
    return out


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    """Cosine similarity in [-1,1]; -1.0 for missing/mismatched vectors."""
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else -1.0


async def semantic_order(
    query: str,
    passages: list[str],
    *,
    embed_fn: Callable[[str], Awaitable[list[float] | None]] | None = None,
) -> list[int]:
    """Bi-encoder reranker: order ``passages`` by cosine similarity to ``query`` (nomic-embed
    via Ollama — live, free, zero-leak). Plugs the same ``(query, passages) -> indices`` seam
    as ``bm25_order``; a true cross-encoder can replace it later. Catches semantic matches
    BM25 misses (e.g. "which weekday" ↔ "ships on Fridays"). Embeds CONCURRENTLY; a passage
    whose embed fails is dropped; if the query embed fails → [] (caller keeps candidate order)."""
    if not passages:
        return []
    ef = embed_fn or embed
    vecs = await asyncio.gather(ef(query), *[ef(p) for p in passages])
    qv = vecs[0]
    if qv is None:
        return []
    scored = [(cosine(qv, pv), i) for i, pv in enumerate(vecs[1:]) if pv is not None]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [i for _, i in scored]


class QdrantVectorIndex:
    """Embeds via Ollama and indexes/searches a Qdrant collection by fabric item id."""

    def __init__(self, *, collection: str = "madras_fabric", tenant: str = "default") -> None:
        self._col = collection
        self._tenant = tenant
        self._ensured = False

    @property
    def tenant(self) -> str:
        """The namespace every write is stamped with and every search is filtered to.

        Defaults to `"default"`, matching `MemoryFabric`'s own default -- if the two disagreed,
        callers would write into one namespace and read from another and see an empty store."""
        return self._tenant

    async def _embed(self, text: str) -> list[float] | None:
        return await embed(text)

    async def _ensure(self, client: httpx.AsyncClient, size: int) -> None:
        if self._ensured:
            return
        url = f"{settings.qdrant_url}/collections/{self._col}"
        try:
            r = await client.get(url)
            if r.status_code != 200:
                await client.put(url, json={"vectors": {"size": size, "distance": "Cosine"}})
        except httpx.HTTPError:
            await client.put(url, json={"vectors": {"size": size, "distance": "Cosine"}})
        self._ensured = True

    def _point_id(self, item_id: str) -> str:
        """Qdrant point ids must be uint or UUID; derive a stable UUID5 from (tenant, item id).

        **The tenant is in the hash, not merely in the payload.** `MemoryFabric`'s
        `ON CONFLICT (id, tenant)` means the same `item_id` legitimately exists in two tenants;
        hashing `item_id` alone collapsed both onto one point, so the second tenant's write
        silently DESTROYED the first tenant's vector. A payload filter alone would not have fixed
        that -- the collision happens before any filter runs.
        """
        import uuid

        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self._tenant}/{item_id}"))

    async def index(self, item_id: str, text: str) -> None:
        vec = await self._embed(text)
        if vec is None:
            return
        async with httpx.AsyncClient(timeout=15.0, headers=qdrant_headers()) as client:
            await self._ensure(client, len(vec))
            await client.put(
                f"{settings.qdrant_url}/collections/{self._col}/points",
                json={
                    "points": [
                        {
                            "id": self._point_id(item_id),
                            "vector": vec,
                            "payload": {"item_id": item_id, "tenant": self._tenant},
                        }
                    ]
                },
            )

    async def index_many(self, items: list[tuple[str, str]], *, batch_size: int = 32) -> int:
        """Index many `(item_id, text)` pairs. Returns how many were actually stored.

        Two round trips per batch instead of two per item: one batched embed, one batched upsert.
        `index()` is unchanged and still correct for a single write -- this is for the ingest
        path, where a document set arrives all at once.

        Items whose embedding failed are skipped rather than stored without a vector, matching
        `index()`, which returns early on `None`. The count comes back so a caller can tell
        "stored 1,270 of 1,272" from "stored everything" -- a silent partial write into someone's
        memory is the kind of thing that surfaces weeks later as "it forgot".
        """
        if not items:
            return 0
        vecs = await embed_many([text for _, text in items], batch_size=batch_size)
        points = [
            {
                "id": self._point_id(item_id),
                "vector": vec,
                "payload": {"item_id": item_id, "tenant": self._tenant},
            }
            for (item_id, _), vec in zip(items, vecs, strict=True)
            if vec is not None
        ]
        if not points:
            return 0
        async with httpx.AsyncClient(timeout=60.0, headers=qdrant_headers()) as client:
            await self._ensure(client, len(points[0]["vector"]))  # type: ignore[arg-type]
            for start in range(0, len(points), batch_size):
                await client.put(
                    f"{settings.qdrant_url}/collections/{self._col}/points",
                    json={"points": points[start : start + batch_size]},
                )
        return len(points)

    def _tenant_filter(self) -> dict[str, Any]:
        """Restrict a search to this tenant's own points.

        Without it, `search` returned top-k across EVERY tenant. Content never leaked -- fabric
        re-fetches ids `WHERE agent_name=$1 AND tenant=$2`, so foreign ids yielded no rows -- but
        two things went wrong anyway: isolation rested on that single SQL predicate (any consumer
        using `search()` directly would leak), and foreign hits silently consumed the k budget, so
        asking for 12 could return 3 with nothing reporting an error.

        **Legacy points are admitted for the default tenant, deliberately.** Points written before
        this change carry no `tenant` payload at all. A strict filter would make every one of them
        invisible and drop existing recall to zero *silently* -- the exact failure shape this
        change exists to remove. They can only belong to the single namespace that was in use
        before tenancy reached this layer, so matching them as `default` is correct rather than
        merely convenient. Non-default tenants get the strict filter, since no legacy point can
        belong to a tenant that did not yet exist.
        """
        match_tenant: dict[str, Any] = {"key": "tenant", "match": {"value": self._tenant}}
        if self._tenant != "default":
            return {"must": [match_tenant]}
        return {"should": [match_tenant, {"is_empty": {"key": "tenant"}}]}

    async def search(self, query: str, k: int = 12) -> list[str]:
        vec = await self._embed(query)
        if vec is None:
            return []
        try:
            async with httpx.AsyncClient(timeout=15.0, headers=qdrant_headers()) as client:
                r = await client.post(
                    f"{settings.qdrant_url}/collections/{self._col}/points/search",
                    json={
                        "vector": vec,
                        "limit": k,
                        "with_payload": True,
                        "filter": self._tenant_filter(),
                    },
                )
                if r.status_code == 404:
                    return []
                r.raise_for_status()
                return [
                    h["payload"]["item_id"]
                    for h in r.json().get("result", [])
                    if h.get("payload", {}).get("item_id")
                ]
        except Exception:
            return []
