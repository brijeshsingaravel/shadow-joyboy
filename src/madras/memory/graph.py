"""L6 Relationship layer — typed temporal edges + the RelationshipStore.

Graphiti-style directed edges (src --rel--> dst) with provenance + temporal validity.
Pure graph helpers (neighbors, n-hop reach) are deterministic + tested; the asyncpg
RelationshipStore persists over madras_memory_edges (migration 0013). Used for multi-
agent / Boardroom reasoning ("who pairs with whom", "what contradicted what").
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import asyncpg

EDGE_TYPES = (
    "paired_with",
    "deferred_to",
    "contradicted",
    "mentored",
    "mentor_of",
    "knows",
    "works_with",
    "depends_on",
    "related_to",
)
# edges that are symmetric (a paired_with b ⟺ b paired_with a) for neighbour queries
_SYMMETRIC = {"paired_with", "knows", "works_with", "related_to"}


@dataclass
class Edge:
    id: str
    src: str
    rel: str
    dst: str
    weight: float = 1.0
    source: str = ""
    created_at: float = 0.0
    valid_until: float | None = None


def edge_current(e: Edge, now: float) -> bool:
    return e.valid_until is None or now < e.valid_until


def neighbors(
    edges: list[Edge], node: str, now: float, *, rel: str | None = None
) -> list[tuple[str, str, float]]:
    """Currently-valid neighbours of `node` as (rel, other, weight). Honours symmetric
    edge types (matches on dst too). Sorted by weight desc."""
    n = node.strip().lower()
    out: list[tuple[str, str, float]] = []
    for e in edges:
        if not edge_current(e, now) or (rel and e.rel != rel):
            continue
        if e.src.strip().lower() == n:
            out.append((e.rel, e.dst, e.weight))
        elif e.rel in _SYMMETRIC and e.dst.strip().lower() == n:
            out.append((e.rel, e.src, e.weight))
    out.sort(key=lambda t: t[2], reverse=True)
    return out


def reachable(edges: list[Edge], node: str, now: float, *, depth: int = 2) -> set[str]:
    """All nodes reachable from `node` within `depth` hops (current edges only)."""
    seen: set[str] = set()
    frontier: deque[tuple[str, int]] = deque([(node.strip().lower(), 0)])
    while frontier:
        cur, d = frontier.popleft()
        if d >= depth:
            continue
        for _rel, other, _w in neighbors(edges, cur, now):
            key = other.strip().lower()
            if key not in seen:
                seen.add(other)
                frontier.append((key, d + 1))
    return seen


_INSERT = """
INSERT INTO madras_memory_edges
  (id, agent_name, tenant, src, rel, dst, weight, source, created_at, valid_until)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
ON CONFLICT (id) DO NOTHING
"""
_SELECT = """
SELECT * FROM madras_memory_edges
WHERE agent_name=$1 AND tenant=$2 AND valid_until IS NULL
"""


def _row(r: asyncpg.Record) -> Edge:
    return Edge(
        id=r["id"],
        src=r["src"],
        rel=r["rel"],
        dst=r["dst"],
        weight=float(r["weight"]),
        source=r["source"],
        created_at=float(r["created_at"]),
        valid_until=(float(r["valid_until"]) if r["valid_until"] is not None else None),
    )


class RelationshipStore:
    """Durable typed-edge store (L6). Graph logic delegated to the pure helpers above."""

    def __init__(
        self, *, postgres_url: str, agent_name: str = "shadow", tenant: str = "default"
    ) -> None:
        self._url = postgres_url
        self._agent = agent_name
        self._tenant = tenant
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            tenant = self._tenant

            async def _bind_tenant(conn: asyncpg.pool.PoolConnectionProxy[asyncpg.Record]) -> None:
                # `setup`, not `init`: init runs once per connection CREATION and asyncpg's
                # RESET ALL on release wipes it, so only the first acquire would carry a tenant
                # and every read afterwards would match nothing (D83 step 7, found the hard way).
                await conn.execute("SELECT set_config('madras.tenant', $1, false)", tenant)

            self._pool = await asyncpg.create_pool(
                self._url, min_size=1, max_size=4, setup=_bind_tenant
            )
        return self._pool

    async def add_edge(self, e: Edge, *, now: float) -> None:
        if not e.created_at:
            e.created_at = now
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                _INSERT,
                e.id,
                self._agent,
                self._tenant,
                e.src,
                e.rel,
                e.dst,
                e.weight,
                e.source,
                e.created_at,
                e.valid_until,
            )

    async def edges(self) -> list[Edge]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_SELECT, self._agent, self._tenant)
        return [_row(r) for r in rows]

    async def neighbors(self, node: str, *, now: float, rel: str | None = None):
        return neighbors(await self.edges(), node, now, rel=rel)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
