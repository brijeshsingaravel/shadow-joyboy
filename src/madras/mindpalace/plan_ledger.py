"""Plan ledger — the durable, cross-session intent spine.

Tables are created by infra/migrations/0004_plans.sql.
Writes are UPSERTs on plan_id so plan evolution is durable across sessions.
A later Memory-Manager step reconciles these structured plans against the
raw session log in the Mind Palace ledger.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

import asyncpg

UPSERT_SQL = """
INSERT INTO madras_plans
    (plan_id, project, agent_name, title, source, seq, status,
     started_session, items)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)
ON CONFLICT (plan_id) DO UPDATE SET
    title = EXCLUDED.title,
    seq = EXCLUDED.seq,
    status = EXCLUDED.status,
    started_session = EXCLUDED.started_session,
    items = EXCLUDED.items,
    updated_at = NOW()
RETURNING id
"""

GET_SQL = "SELECT * FROM madras_plans WHERE plan_id = $1"

LIST_OPEN_SQL = """
SELECT * FROM madras_plans
WHERE project = $1 AND agent_name = $2 AND status = 'open'
ORDER BY seq ASC, id ASC
"""

RECENT_SQL = """
SELECT * FROM madras_plans
WHERE project = $1 AND agent_name = $2
ORDER BY updated_at DESC, id DESC
LIMIT $3
"""


@dataclass
class PlanItem:
    id: str
    text: str
    status: str = "pending"  # pending | in_progress | done | blocked | drift
    evidence: list[str] = field(default_factory=list[str])


@dataclass
class Plan:
    plan_id: str
    agent_name: str
    title: str
    project: str = "default"
    source: str = "user"  # user | agent | tdd
    seq: int = 0
    status: str = "open"  # open | complete | abandoned
    started_session: str | None = None
    items: list[PlanItem] = field(default_factory=list[PlanItem])


def _loads(v: Any) -> list[Any]:
    return json.loads(v) if isinstance(v, str) else list(v)


def _from_row(r: asyncpg.Record) -> Plan:
    items = [
        PlanItem(
            id=i["id"],
            text=i["text"],
            status=i.get("status", "pending"),
            evidence=list(i.get("evidence", [])),
        )
        for i in _loads(r["items"])
    ]
    return Plan(
        plan_id=r["plan_id"],
        project=r["project"],
        agent_name=r["agent_name"],
        title=r["title"],
        source=r["source"],
        seq=r["seq"],
        status=r["status"],
        started_session=r["started_session"],
        items=items,
    )


class PlanLedger:
    """Plan ledger — one row per plan, upserted as the plan evolves."""

    def __init__(self, *, postgres_url: str) -> None:
        self._url = postgres_url
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=4)
        return self._pool

    async def get_pool(self) -> asyncpg.Pool:
        """Public pool accessor for same-package consumers (reconciler)."""
        return await self._get_pool()

    async def upsert(self, plan: Plan) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                UPSERT_SQL,
                plan.plan_id,
                plan.project,
                plan.agent_name,
                plan.title,
                plan.source,
                plan.seq,
                plan.status,
                plan.started_session,
                json.dumps([asdict(i) for i in plan.items]),
            )
            return int(row["id"])  # type: ignore[index]

    async def get(self, *, plan_id: str) -> Plan | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            r = await conn.fetchrow(GET_SQL, plan_id)
        return _from_row(r) if r else None

    async def list_open(self, *, project: str, agent_name: str) -> list[Plan]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(LIST_OPEN_SQL, project, agent_name)
        return [_from_row(r) for r in rows]

    async def recent(self, *, project: str, agent_name: str, limit: int = 10) -> list[Plan]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(RECENT_SQL, project, agent_name, limit)
        return [_from_row(r) for r in rows]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
