"""Postgres-backed SkillStore — persists and manages SKILL.md records.

Progressive disclosure contract:
  L0  caller uses skill.l0()  -> "- name: description"  (loaded at startup)
  L1  skill.body              -> full markdown body      (on demand)
  L2  skill.metadata refs     -> external links/refs     (future Phase 3)
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from madras.skills.format import Skill

# The schema lives in infra/migrations/0003_skills.sql (+ 0024_skill_curation.sql), which own
# it. This module used to carry a copy and execute it in `setup()` -- a copy that had already
# gone STALE: it lacked `pinned` and `last_used_at`, the two columns 0024 added. Benign only
# because 0024 ALTERs afterwards, which is exactly the drift a second definition invites (D83).

INSERT_SQL = """
INSERT INTO madras_skills (project, name, description, body, toolsets, category, provenance)
VALUES ($1, $2, $3, $4, $5::text[], $6, $7::jsonb)
ON CONFLICT (project, name) DO UPDATE SET
    description = EXCLUDED.description,
    body        = EXCLUDED.body,
    toolsets    = EXCLUDED.toolsets,
    category    = EXCLUDED.category,
    provenance  = EXCLUDED.provenance,
    status      = 'candidate'
RETURNING id
"""

APPROVE_SQL = """
UPDATE madras_skills
SET status = 'active', approved_at = NOW()
WHERE project = $1 AND name = $2 AND status = 'candidate'
"""

REJECT_SQL = """
UPDATE madras_skills
SET status = 'rejected'
WHERE project = $1 AND name = $2
"""

LIST_ACTIVE_SQL = """
SELECT name, description, body, toolsets, category
FROM madras_skills
WHERE project = $1 AND status = 'active'
ORDER BY name
"""

LIST_CANDIDATES_SQL = """
SELECT name, description, body, toolsets, category
FROM madras_skills
WHERE project = $1 AND status = 'candidate'
ORDER BY name
"""

GET_SQL = """
SELECT name, description, body, toolsets, category
FROM madras_skills
WHERE project = $1 AND name = $2
"""

# Relevance search over a project's ACTIVE skills (name matches weigh more than description).
# Used for the shared skill LIBRARY — search-on-demand instead of dumping every skill's
# metadata into the prompt. $2 = array of '%term%' ILIKE patterns.
SEARCH_ACTIVE_SQL = """
SELECT name, description, body, toolsets, category,
  ((CASE WHEN name ILIKE ANY($2) THEN 2 ELSE 0 END)
   + (CASE WHEN description ILIKE ANY($2) THEN 1 ELSE 0 END)) AS score
FROM madras_skills
WHERE project = $1 AND status = 'active'
  AND (name ILIKE ANY($2) OR description ILIKE ANY($2))
ORDER BY score DESC, name
LIMIT $3
"""

RECORD_SUCCESS_SQL = """
UPDATE madras_skills SET success_count = success_count + 1, last_used_at = NOW()
WHERE project = $1 AND name = $2
"""

RECORD_FAIL_SQL = """
UPDATE madras_skills SET fail_count = fail_count + 1, last_used_at = NOW()
WHERE project = $1 AND name = $2
"""

# B28 — lifecycle curation (pin/archive/restore; never delete).


USAGE_ROWS_SQL = """
SELECT name, status, pinned,
       (success_count + fail_count) AS uses,
       EXTRACT(EPOCH FROM last_used_at) AS last_used_secs,
       EXTRACT(EPOCH FROM ts) AS created_secs
FROM madras_skills
WHERE project = $1 AND status IN ('active', 'archived')
ORDER BY name
"""

# row skill-mastery-engine — the success/fail split usage_rows() collapses into one
# "uses" count; weakness diagnosis needs the split to compute a success rate.
WEAKNESS_ROWS_SQL = """
SELECT name, success_count, fail_count
FROM madras_skills
WHERE project = $1 AND status = 'active'
ORDER BY name
"""

SET_STATUS_SQL = "UPDATE madras_skills SET status = $3 WHERE project = $1 AND name = $2"
SET_PINNED_SQL = "UPDATE madras_skills SET pinned = $3 WHERE project = $1 AND name = $2"


def _row_to_skill(row: Any) -> Skill:
    return Skill(
        name=row["name"],
        description=row["description"],
        body=row["body"],
        toolsets=list(row["toolsets"]),
        category=row["category"],
    )


class MissingSkillsTable(RuntimeError):
    """The skills table does not exist -- migrations have not been applied to this database."""


class SkillStore:
    """Postgres-backed store for SKILL.md records with lifecycle management."""

    def __init__(self, *, postgres_url: str) -> None:
        self._url = postgres_url
        self._table = "madras_skills"
        self._verified = False
        self._pool: asyncpg.Pool | None = None  # type: ignore[type-arg]

    async def _get_pool(self) -> asyncpg.Pool:  # type: ignore[type-arg]
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=4)
        return self._pool

    async def setup(self) -> None:
        """Verify the skills table exists. Does NOT create it (s61, D83 step 5).

        Owned by `0003_skills.sql` and `0024_skill_curation.sql`. This method used to re-run both
        -- the CREATE and the curation ALTERs -- and its CREATE had already drifted: it did not
        list `pinned` or `last_used_at`. Harmless only because the migration ALTERs afterwards,
        which is the whole argument against a second definition.

        It also blocked the RLS cutover: the app role has no DDL, and `CREATE TABLE IF NOT EXISTS`
        is refused on privilege grounds even when the table exists.
        """
        if self._verified:
            return
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            exists = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", self._table)
        if not exists:
            raise MissingSkillsTable(
                f"{self._table} does not exist -- apply infra/migrations "
                f"(0003_skills.sql creates it, 0024_skill_curation.sql extends it)"
            )
        self._verified = True

    async def add_candidate(
        self,
        skill: Skill,
        *,
        project: str = "default",
        provenance: dict[str, Any] | None = None,
    ) -> int:
        """Insert skill as candidate (upsert on conflict — resets to candidate). Returns id."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                INSERT_SQL,
                project,
                skill.name,
                skill.description,
                skill.body,
                skill.toolsets,
                skill.category,
                json.dumps(provenance or {}),
            )
            return int(row["id"])  # type: ignore[index]

    async def approve(self, name: str, *, project: str = "default") -> bool:
        """Promote candidate -> active. Returns True if a row was updated."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(APPROVE_SQL, project, name)
        # result is e.g. "UPDATE 1"
        return result.endswith("1")

    async def reject(self, name: str, *, project: str = "default") -> bool:
        """Mark skill as rejected. Returns True if a row was updated."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(REJECT_SQL, project, name)
        return result.endswith("1")

    async def list_active(self, *, project: str = "default") -> list[Skill]:
        """Return all active skills (L1 — full body available)."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(LIST_ACTIVE_SQL, project)
        return [_row_to_skill(r) for r in rows]

    async def search_active(self, *, project: str, terms: list[str], limit: int = 5) -> list[Skill]:
        """Relevance-search a project's active skills by ILIKE term patterns (top `limit`).
        Powers the shared skill library — only the matched few load, not all."""
        if not terms:
            return []
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(SEARCH_ACTIVE_SQL, project, terms, limit)
        return [_row_to_skill(r) for r in rows]

    async def list_candidates(self, *, project: str = "default") -> list[Skill]:
        """Return all candidate skills."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(LIST_CANDIDATES_SQL, project)
        return [_row_to_skill(r) for r in rows]

    async def get(self, name: str, *, project: str = "default") -> Skill | None:
        """Return a single skill by name (L1 — full body), or None."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(GET_SQL, project, name)
        return _row_to_skill(row) if row else None

    async def record_use(self, name: str, *, project: str = "default", success: bool) -> None:
        """Increment success or fail counter for a skill."""
        pool = await self._get_pool()
        sql = RECORD_SUCCESS_SQL if success else RECORD_FAIL_SQL
        async with pool.acquire() as conn:
            await conn.execute(sql, project, name)

    async def usage_rows(self, *, project: str = "default") -> list[dict[str, Any]]:
        """Telemetry for the Curator: name, status, pinned, uses, last_used/created epochs."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(USAGE_ROWS_SQL, project)
        return [dict(r) for r in rows]

    async def weakness_rows(self, *, project: str = "default") -> list[dict[str, Any]]:
        """Telemetry for skills/diagnose.py: name + the success/fail split (row
        skill-mastery-engine)."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(WEAKNESS_ROWS_SQL, project)
        return [dict(r) for r in rows]

    async def set_status(self, name: str, status: str, *, project: str = "default") -> bool:
        """Set a skill's lifecycle status (e.g. 'archived'/'active'). Never deletes."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(SET_STATUS_SQL, project, name, status)
        return result.endswith("1")

    async def set_pinned(self, name: str, pinned: bool, *, project: str = "default") -> bool:
        """Pin/unpin a skill (pinned skills are never auto-archived)."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(SET_PINNED_SQL, project, name, pinned)
        return result.endswith("1")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
