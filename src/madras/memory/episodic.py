"""L2 Episodic memory — Postgres-backed for Phase 1.

The Blueprint §8 calls for Graphiti (temporal knowledge graph) for episodic.
Phase 1 ships a Postgres-backed interface that matches the contract; if
LongMemEval-style perf requires the temporal KG in Phase 2+, swap the
backend behind the EpisodicMemory class without changing the callers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from madras.memory.tenant_context import require_tenant

# The schema lives in infra/migrations/0000_bootstrap_runtime_tables.sql, which owns it.
# This module used to carry a byte-identical copy and execute it in `setup()`; that made the
# schema definable from two places and required DDL rights the app role must not have.

INSERT_SQL = """
INSERT INTO madras_episodes (session_id, agent_name, summary, decisions, tags, extras, tenant)
VALUES ($1, $2, $3, $4::jsonb, $5::text[], $6::jsonb, $7)
RETURNING id
"""

QUERY_BY_TAG_SQL = """
SELECT id, session_id, agent_name, summary, decisions, tags
FROM madras_episodes
WHERE agent_name = $1 AND $2 = ANY(tags)
ORDER BY ts DESC
LIMIT $3
"""


@dataclass
class Episode:
    session_id: str
    agent_name: str
    summary: str
    decisions: list[str] = field(default_factory=list[str])
    tags: list[str] = field(default_factory=list[str])
    extras: dict[str, Any] = field(default_factory=dict[str, Any])


class MissingEpisodesTable(RuntimeError):
    """The episodes table does not exist -- migrations have not been applied to this database."""


class EpisodicMemory:
    """L2 adapter — episodic record with tag-based recall."""

    def __init__(self, *, postgres_url: str) -> None:
        self._url = postgres_url
        self._pool: asyncpg.Pool | None = None
        self._table = "madras_episodes"
        self._verified = False

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=4)
        return self._pool

    async def get_pool(self) -> asyncpg.Pool:
        """Public pool accessor for same-package consumers (consolidator)."""
        return await self._get_pool()

    async def setup(self) -> None:
        """Verify the episodes table exists. Does NOT create it (s61, D83 step 5).

        Owned by `0000_bootstrap_runtime_tables.sql`. Creating it here again blocked the RLS
        cutover -- the app role has no DDL, and `CREATE TABLE IF NOT EXISTS` is refused on
        privilege grounds even when the table exists. Verification rather than a silent no-op so a
        missing table says "apply the migrations" instead of failing later at INSERT.
        """
        if self._verified:
            return
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            exists = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", self._table)
        if not exists:
            raise MissingEpisodesTable(
                f"{self._table} does not exist -- apply infra/migrations "
                f"(0000_bootstrap_runtime_tables.sql creates it)"
            )
        self._verified = True

    async def write(self, episode: Episode) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                INSERT_SQL,
                episode.session_id,
                episode.agent_name,
                episode.summary,
                json.dumps(episode.decisions),
                episode.tags,
                json.dumps(episode.extras),
                # s63: from the ambient badge, not a parameter. An episode with no owner cannot
                # be shown to its person, corrected by them, or deleted on request -- so a
                # missing tenant raises here rather than writing an unowned memory.
                require_tenant(),
            )
            return int(row["id"])  # type: ignore[index]

    async def query_by_tag(self, tag: str, *, agent_name: str, limit: int = 10) -> list[Episode]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(QUERY_BY_TAG_SQL, agent_name, tag, limit)
        return [
            Episode(
                session_id=r["session_id"],
                agent_name=r["agent_name"],
                summary=r["summary"],
                decisions=json.loads(r["decisions"])
                if isinstance(r["decisions"], str)
                else list(r["decisions"]),
                tags=list(r["tags"]),
            )
            for r in rows
        ]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
