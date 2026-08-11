"""Memory Fabric store (Step 1b) — asyncpg persistence over the pure retrieval core.

Wraps ``madras_memory`` (migration 0012). The store keeps writes durable + provenance-
stamped and delegates ALL ranking/temporal/contradiction logic to ``memory.retrieval``
(pure, tested). ``remember`` does contradiction-on-write: a new superseding fact marks
the stale ones ``valid_until=now`` and links ``supersedes`` — temporal reflection, never
a silent overwrite. ``recall`` pulls the agent's currently-valid items and ranks them.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import cast

import asyncpg

from madras.memory.retrieval import (
    MemoryItem,
    apply_order,
    find_contradictions,
    reinforce,
)
from madras.memory.retrieval import (
    recall as _recall,
)

_INSERT = """
INSERT INTO madras_memory
  (id, agent_name, tenant, kind, subject, content, tags, confidence, source,
   session_id, created_at, valid_from, valid_until, supersedes)
VALUES ($1,$2,$3,$4,$5,$6,$7::text[],$8,$9,$10,$11,$12,$13,$14)
ON CONFLICT (id, tenant) DO NOTHING
"""
_EXPIRE = (
    "UPDATE madras_memory SET valid_until=$3 WHERE id=$1 AND tenant=$2 AND valid_until IS NULL"
)
_REINFORCE = (
    "UPDATE madras_memory SET strength=$2, last_accessed=$3, recall_count=$4 "
    "WHERE id=$1 AND tenant=$5"
)
_SELECT_CURRENT = """
SELECT * FROM madras_memory
WHERE agent_name=$1 AND tenant=$2 AND valid_until IS NULL
ORDER BY created_at DESC LIMIT $3
"""
_SELECT_ALL = "SELECT * FROM madras_memory WHERE agent_name=$1 AND tenant=$2"
_SELECT_BY_IDS = """
SELECT * FROM madras_memory
WHERE agent_name=$1 AND tenant=$2 AND id = ANY($3::text[]) AND valid_until IS NULL
"""
_SELECT_AS_OF = """
SELECT * FROM madras_memory
WHERE agent_name=$1 AND tenant=$2
  AND valid_from <= $3 AND (valid_until IS NULL OR valid_until > $3)
ORDER BY created_at DESC LIMIT $4
"""


def _row_to_item(r: asyncpg.Record) -> MemoryItem:
    return MemoryItem(
        id=r["id"],
        kind=r["kind"],
        subject=r["subject"],
        content=r["content"],
        tags=list(r["tags"]),
        confidence=float(r["confidence"]),
        source=r["source"],
        session_id=r["session_id"],
        agent_name=r["agent_name"],
        created_at=float(r["created_at"]),
        valid_from=float(r["valid_from"]),
        valid_until=(float(r["valid_until"]) if r["valid_until"] is not None else None),
        supersedes=r["supersedes"],
        # E-X4 reinforcement columns (migration 0020); defensive for a pre-migration DB.
        strength=float(r["strength"]) if "strength" in r else 1.0,
        last_accessed=float(r["last_accessed"]) if "last_accessed" in r else 0.0,
        recall_count=int(r["recall_count"]) if "recall_count" in r else 0,
    )


class MemoryFabric:
    """Durable unified memory store; ranking/temporal logic lives in retrieval.py."""

    def __init__(
        self,
        *,
        postgres_url: str,
        agent_name: str = "shadow",
        tenant: str = "default",
        vector_index: object | None = None,
    ) -> None:
        self._url = postgres_url
        self._agent = agent_name
        self._tenant = tenant
        # Optional semantic (L3) layer: any object with async index(id, text) +
        # search(query, k) -> list[id]. None → keyword-only recall (graceful default).
        self._vec = vector_index

        # Both halves now carry a tenant (s61), so both halves can disagree: a fabric on "acme"
        # holding a "default"-scoped index would write rows to one namespace and vectors to
        # another, and recall would return nothing while every component behaved correctly on its
        # own. That is a wiring bug no unit test of either half can catch, so it is caught here at
        # construction rather than inferred from an empty search result weeks later.
        # `getattr` and not an isinstance check: the index is a duck-typed seam, and a double or a
        # future implementation may legitimately have no tenant concept at all.
        vec_tenant = getattr(vector_index, "tenant", None)
        if vec_tenant is not None and vec_tenant != tenant:
            raise ValueError(
                f"tenant mismatch: fabric is on {tenant!r} but its vector index is on "
                f"{vec_tenant!r} -- rows and vectors would land in different namespaces"
            )
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        """The pool, with this fabric's tenant bound to every connection in it (D83 step 7).

        RLS reads the tenant from `current_setting('madras.tenant')`; a connection that never sets
        it matches no rows, so wiring this is what makes policies workable rather than a silent
        blackout.

        Bound in `setup` (runs on every ACQUIRE), not `init` (runs once per connection CREATION).
        That distinction is the whole correctness of this method, and getting it wrong produced a
        genuinely deceptive bug: with `init`, the FIRST acquire carried the tenant -- so writes
        succeeded and passed the policy's WITH CHECK -- and every acquire afterwards did not, so
        reads returned nothing. Data went in and could not be read back out.

        The cause is asyncpg's own `RESET ALL` on release, which this codebase already documents
        as the reason a session-scoped tenant cannot LEAK between borrowers
        (`tests/test_security/test_tenant_scope.py`). The same behaviour that makes leaking
        impossible makes `init` insufficient: it wipes the setting the moment the connection goes
        back to the pool.

        Per-acquire session scope is correct HERE because a `MemoryFabric` is bound to exactly one
        tenant for its whole life, so every connection it hands out serves that tenant alone.
        Behind an EXTERNAL pooler (PgBouncer/Supavisor in transaction mode) that stops holding --
        backends are multiplexed below the driver, `RESET ALL` never runs between two tenants, and
        this must become `security.tenant_scope`'s per-transaction `SET LOCAL`, which exists and
        whose guard test pins both shapes.
        """
        if self._pool is None:
            tenant = self._tenant

            async def _bind_tenant(conn: asyncpg.pool.PoolConnectionProxy[asyncpg.Record]) -> None:
                await conn.execute("SELECT set_config('madras.tenant', $1, false)", tenant)

            self._pool = await asyncpg.create_pool(
                self._url, min_size=1, max_size=4, setup=_bind_tenant
            )
        return self._pool

    async def _write(self, conn: asyncpg.pool.PoolConnectionProxy, it: MemoryItem) -> None:
        await conn.execute(
            _INSERT,
            it.id,
            it.agent_name or self._agent,
            self._tenant,
            it.kind,
            it.subject,
            it.content,
            it.tags,
            it.confidence,
            it.source,
            it.session_id,
            it.created_at,
            it.valid_from,
            it.valid_until,
            it.supersedes,
        )

    async def remember(self, item: MemoryItem, *, now: float) -> list[str]:
        """Persist a memory; supersede any contradicting current items. Returns the ids
        that were expired by this write (empty if none)."""
        item.agent_name = item.agent_name or self._agent
        if not item.created_at:
            item.created_at = now
        if not item.valid_from:
            item.valid_from = item.created_at
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            current = await self._current_items(conn, limit=1000)
            stale = find_contradictions(current, item, now)
            for s in stale:
                await conn.execute(_EXPIRE, s.id, self._tenant, now)
            if stale and item.supersedes is None:
                item.supersedes = stale[0].id
            await self._write(conn, item)
        # Best-effort semantic indexing (never blocks the durable write).
        if self._vec is not None:
            try:
                await self._vec.index(item.id, f"{item.subject}. {item.content}")  # type: ignore[attr-defined]
            except Exception:
                pass
        return [s.id for s in stale]

    async def _items_by_ids(
        self, conn: asyncpg.pool.PoolConnectionProxy, ids: list[str]
    ) -> list[MemoryItem]:
        if not ids:
            return []
        rows = await conn.fetch(_SELECT_BY_IDS, self._agent, self._tenant, ids)
        return [_row_to_item(r) for r in rows]

    async def _current_items(
        self, conn: asyncpg.pool.PoolConnectionProxy, *, limit: int
    ) -> list[MemoryItem]:
        rows = await conn.fetch(_SELECT_CURRENT, self._agent, self._tenant, limit)
        return [_row_to_item(r) for r in rows]

    async def _items_as_of(
        self, conn: asyncpg.pool.PoolConnectionProxy, t: float, *, limit: int
    ) -> list[MemoryItem]:
        """Items that WERE valid at time ``t`` (born by t, not yet invalidated) — incl.
        facts since superseded. The bi-temporal point-in-time set."""
        rows = await conn.fetch(_SELECT_AS_OF, self._agent, self._tenant, t, limit)
        return [_row_to_item(r) for r in rows]

    async def current_items(self, *, now: float, limit: int = 1000) -> list[MemoryItem]:
        """All currently-valid items (whole-store assembly, e.g. the E-B7 user-model).

        ``now`` is accepted for call-site symmetry; the SQL already filters to current rows."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await self._current_items(conn, limit=limit)

    async def recall(
        self,
        query: str,
        *,
        now: float,
        k: int = 6,
        half_life_days: float = 30.0,
        max_chars: int = 1200,
        pool_limit: int = 1000,
        reranker: Callable[[str, list[str]], list[int] | Awaitable[list[int]]] | None = None,
        as_of: float | None = None,
    ) -> list[MemoryItem]:
        """Top-k memories for the query, ranked by the pure core.

        If a semantic (vector) index is attached, its hits are MERGED into the candidate
        pool first (L3 folded into the unified recall). Ranking stays the pure core.

        ``as_of`` (bi-temporal point-in-time): when set, recall the facts that were valid
        AT that time — including ones since superseded — and rank as-of then (a read-only
        historical query, so no reinforcement). Default (None) = currently-valid recall."""
        pool = await self._get_pool()
        eff = as_of if as_of is not None else now  # validate + rank as-of this time
        sem_ids: set[str] = set()
        async with pool.acquire() as conn:
            if as_of is not None:
                items = await self._items_as_of(conn, as_of, limit=pool_limit)
            else:
                items = await self._current_items(conn, limit=pool_limit)
                if self._vec is not None:
                    try:
                        raw_hits = await self._vec.search(query, max(k * 3, 12))  # type: ignore[attr-defined]
                        hit_ids = cast("list[str]", raw_hits)
                        sem_ids = set(hit_ids)
                        known = {it.id for it in items}
                        extra_ids = [i for i in hit_ids if i not in known]
                        items += await self._items_by_ids(conn, extra_ids)
                    except Exception:
                        pass
        if reranker is not None:
            # Two-stage (hybrid -> rerank, the +17% precision lever): widen the candidate
            # pool, re-order it with the injected reranker (sync BM25 or async semantic —
            # await if it returns a coroutine), then take top-k (budgeted).
            cand = _recall(
                items,
                query,
                now=eff,
                k=max(k * 4, 20),
                half_life_days=half_life_days,
                max_chars=10**9,
                semantic_ids=sem_ids,
            )
            texts = [f"{it.subject}. {it.content}" for it in cand]
            res = reranker(query, texts)
            order = await res if inspect.isawaitable(res) else res
            out = apply_order(cand, list(order or []), k=k, max_chars=max_chars)
        else:
            out = _recall(
                items,
                query,
                now=eff,
                k=k,
                half_life_days=half_life_days,
                max_chars=max_chars,
                semantic_ids=sem_ids,
            )
        # E-X4: biological reinforcement — recalling an item strengthens it (best-effort).
        # Skip on historical (as_of) reads — they must not mutate strength/recency.
        if out and as_of is None:
            try:
                async with pool.acquire() as conn:
                    for it in out:
                        reinforce(it, now)
                        await conn.execute(
                            _REINFORCE,
                            it.id,
                            it.strength,
                            it.last_accessed,
                            it.recall_count,
                            self._tenant,
                        )
            except Exception:
                pass
        return out

    async def archive(self, item_id: str, *, now: float) -> None:
        """Decay with dignity: expire an item (recoverable), never delete."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(_EXPIRE, item_id, self._tenant, now)

    async def principles(self, *, now: float, max_chars: int = 5000) -> list[MemoryItem]:
        """The Principle Layer (L5) — current principles loaded once per session, budgeted."""
        from madras.memory.reflection import load_principles

        items = await self.all_items(include_expired=False)
        return load_principles(items, now, max_chars=max_chars)

    async def all_items(self, *, include_expired: bool = True) -> list[MemoryItem]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            if include_expired:
                rows = await conn.fetch(_SELECT_ALL, self._agent, self._tenant)
                return [_row_to_item(r) for r in rows]
            return await self._current_items(conn, limit=10000)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
