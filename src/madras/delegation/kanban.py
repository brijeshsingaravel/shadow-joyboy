"""Durable multi-agent kanban — the cross-turn, async counterpart to delegate_team.

A board holds tasks toward a shared goal; multiple governed workers CLAIM tasks
atomically (`FOR UPDATE SKIP LOCKED`), complete or fail them, and the dispatcher
loops until the board drains. Lifts the Hermes kanban pattern (MIT) onto Madras's
Postgres + governance:
- **board = hard isolation, tenant = soft namespace** (every query is board+tenant scoped).
- **failure-limit auto-block** — a task that fails `failure_limit` times is blocked
  (anti-spin-loop), never retried forever.
- workers are spawned through the existing governed delegation path (`run_child`),
  so the rank gate + audit + 8-gate eval apply.

Migration: infra/migrations/0023_kanban.sql (also applied idempotently by `setup()`).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg

# The schema lives in infra/migrations/0023_kanban.sql, which owns it. This module used to
# carry an equivalent copy and execute it in `setup()`; that made the schema definable from
# two places and required DDL rights the app role must not have (D83).

_CLAIM = """
UPDATE madras_kanban_tasks SET status='claimed', claimed_by=$2, updated_at=$3
WHERE id = (
  SELECT id FROM madras_kanban_tasks
  WHERE board_id=$1 AND tenant=$4 AND status='ready'
  ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED
)
RETURNING id, title, role, attempts, failure_limit
"""

_FAIL = """
UPDATE madras_kanban_tasks
SET attempts = attempts + 1,
    status = CASE WHEN attempts + 1 >= failure_limit THEN 'blocked' ELSE 'ready' END,
    error = $2, claimed_by = NULL, updated_at = $3
WHERE id = $1 AND tenant = $4
RETURNING status, attempts
"""


class MissingKanbanTables(RuntimeError):
    """The kanban tables do not exist -- migrations have not been applied to this database."""


class KanbanStore:
    """Durable Postgres board store. board+tenant scoped on every op."""

    def __init__(self, *, postgres_url: str, tenant: str = "default") -> None:
        self._tables = ("madras_kanban_boards", "madras_kanban_tasks")
        self._verified = False
        self._url = postgres_url
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

    async def setup(self) -> None:
        """Verify the kanban tables exist. Does NOT create them (s61, D83 step 5).

        Owned by `0023_kanban.sql`. Creating them here again blocked the RLS cutover -- the app
        role has no DDL, and `CREATE TABLE IF NOT EXISTS` is refused on privilege grounds even
        when the table exists. Verified equivalent to the migration before removal: the two
        differed only in whitespace around punctuation, not in a single column or default.
        """
        if self._verified:
            return
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            missing = [
                t
                for t in self._tables
                if not await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", t)
            ]
        if missing:
            raise MissingKanbanTables(
                f"{', '.join(missing)} do(es) not exist -- apply infra/migrations "
                f"(0023_kanban.sql creates them)"
            )
        self._verified = True

    async def create_board(
        self, board_id: str, *, goal: str = "", now: float | None = None
    ) -> None:
        now = time.time() if now is None else now
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO madras_kanban_boards (id, tenant, goal, status, created_at) "
                "VALUES ($1,$2,$3,'open',$4) ON CONFLICT (id, tenant) DO NOTHING",
                board_id,
                self._tenant,
                goal,
                now,
            )

    async def add_task(
        self,
        task_id: str,
        board_id: str,
        title: str,
        *,
        role: str = "general",
        failure_limit: int = 2,
        now: float | None = None,
    ) -> None:
        now = time.time() if now is None else now
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO madras_kanban_tasks "
                "(id, board_id, tenant, title, role, status, failure_limit, created_at, "
                "updated_at) "
                "VALUES ($1,$2,$3,$4,$5,'ready',$6,$7,$7) ON CONFLICT (id, tenant) DO NOTHING",
                task_id,
                board_id,
                self._tenant,
                title,
                role,
                failure_limit,
                now,
            )

    async def claim_next(
        self, board_id: str, worker: str, now: float | None = None
    ) -> dict[str, Any] | None:
        """Atomically claim one ready task (concurrent-safe). None if none ready."""
        now = time.time() if now is None else now
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_CLAIM, board_id, worker, now, self._tenant)
        return dict(row) if row is not None else None

    async def complete(self, task_id: str, result: str = "", now: float | None = None) -> None:
        now = time.time() if now is None else now
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE madras_kanban_tasks SET status='done', result=$2, claimed_by=NULL, "
                "updated_at=$3 WHERE id=$1 AND tenant=$4",
                task_id,
                result[:4000],
                now,
                self._tenant,
            )

    async def fail(self, task_id: str, error: str = "", now: float | None = None) -> str:
        """Record a failure; auto-block past failure_limit, else re-queue. Returns new status."""
        now = time.time() if now is None else now
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_FAIL, task_id, error[:1000], now, self._tenant)
        return row["status"] if row is not None else "unknown"

    async def counts(self, board_id: str) -> dict[str, int]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT status, COUNT(*) AS n FROM madras_kanban_tasks "
                "WHERE board_id=$1 AND tenant=$2 GROUP BY status",
                board_id,
                self._tenant,
            )
        out = {"ready": 0, "claimed": 0, "done": 0, "blocked": 0}
        for r in rows:
            out[r["status"]] = int(r["n"])
        out["total"] = sum(out.values())
        return out

    async def maybe_close_board(self, board_id: str) -> bool:
        """Close the board when nothing is left to do (no ready/claimed)."""
        c = await self.counts(board_id)
        if c["ready"] == 0 and c["claimed"] == 0:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE madras_kanban_boards SET status='done' WHERE id=$1 AND tenant=$2",
                    board_id,
                    self._tenant,
                )
            return True
        return False

    async def list_tasks(self, board_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM madras_kanban_tasks WHERE board_id=$1 AND tenant=$2 "
                "ORDER BY created_at",
                board_id,
                self._tenant,
            )
        return [dict(r) for r in rows]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


# spawn signature: async (title, role) -> {"ok": bool, "summary": str, "error"?, "cost_usd"?}
SpawnFn = Callable[[str, str], Awaitable[dict[str, Any]]]


class KanbanDispatcher:
    """Drains a board by claiming ready tasks and spawning governed workers, until the
    board is done, no ready tasks remain, or max_rounds is hit (circuit breaker)."""

    def __init__(
        self, store: Any, *, spawn: SpawnFn, n_workers: int = 2, max_rounds: int = 20
    ) -> None:
        self._store = store
        self._spawn = spawn
        self._n = max(1, n_workers)
        self._max_rounds = max(1, max_rounds)

    async def run_board(self, board_id: str) -> dict[str, Any]:
        rounds = 0
        while rounds < self._max_rounds:
            claims: list[dict[str, Any]] = []
            for i in range(self._n):
                t = await self._store.claim_next(board_id, f"worker-{i}")
                if t is None:
                    break
                claims.append(t)
            if not claims:
                break  # nothing ready to do
            rounds += 1
            await asyncio.gather(*[self._work(t) for t in claims])
        counts = await self._store.counts(board_id)
        closed = await self._store.maybe_close_board(board_id)
        return {"rounds": rounds, "closed": closed, **counts}

    async def _work(self, t: dict[str, Any]) -> None:
        try:
            r = await self._spawn(t["title"], t.get("role", "general"))
        except Exception as exc:  # a worker crash is a task failure, not a dispatcher crash
            r = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if r.get("ok"):
            await self._store.complete(t["id"], str(r.get("summary", ""))[:4000])
        else:
            await self._store.fail(t["id"], str(r.get("error") or "worker did not complete"))
