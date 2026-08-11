"""Mind Palace ledger — the felt-memory session log.

Tables are created by infra/migrations/0001_mindpalace.sql (T18).
Writes are UPSERTs on session_id so per-turn updates are durable.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any

import asyncpg

UPSERT_SQL = """
INSERT INTO madras_mindpalace_sessions
    (session_id, project, agent_name, started_at, ended_at, duration_secs,
     tokens_in, tokens_out, cost_usd, tools_used, decisions, files_touched,
     open_items, summary, tags, tenant)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb,$12::jsonb,$13::jsonb,$14,$15::text[],$16)
-- (session_id, tenant), not session_id alone: the bare target collapsed two tenants onto one
-- row and silently dropped a write -- the same shape 0033 fixed for madras_memory.
ON CONFLICT (session_id, tenant) DO UPDATE SET
    ended_at = EXCLUDED.ended_at,
    duration_secs = EXCLUDED.duration_secs,
    tokens_in = EXCLUDED.tokens_in,
    tokens_out = EXCLUDED.tokens_out,
    cost_usd = EXCLUDED.cost_usd,
    tools_used = EXCLUDED.tools_used,
    decisions = EXCLUDED.decisions,
    files_touched = EXCLUDED.files_touched,
    open_items = EXCLUDED.open_items,
    summary = EXCLUDED.summary,
    tags = EXCLUDED.tags
RETURNING id
"""

GET_SQL = "SELECT * FROM madras_mindpalace_sessions WHERE session_id = $1 AND tenant = $2"

RECENT_SQL = """
SELECT * FROM madras_mindpalace_sessions
WHERE project = $1 AND agent_name = $2 AND tenant = $4
ORDER BY ts DESC, id DESC
LIMIT $3
"""


@dataclass
class SessionRecord:
    session_id: str
    agent_name: str
    started_at: datetime
    summary: str = ""
    project: str = "default"
    ended_at: datetime | None = None
    duration_secs: int | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    tools_used: list[str] = field(default_factory=list[str])
    decisions: list[str] = field(default_factory=list[str])
    files_touched: list[str] = field(default_factory=list[str])
    open_items: list[str] = field(default_factory=list[str])
    tags: list[str] = field(default_factory=list[str])


def _loads(v: Any) -> list[Any]:
    return json.loads(v) if isinstance(v, str) else list(v)


def session_record_from_row(r: asyncpg.Record) -> SessionRecord:
    return SessionRecord(
        session_id=r["session_id"],
        project=r["project"],
        agent_name=r["agent_name"],
        started_at=r["started_at"],
        ended_at=r["ended_at"],
        duration_secs=r["duration_secs"],
        tokens_in=r["tokens_in"],
        tokens_out=r["tokens_out"],
        cost_usd=float(r["cost_usd"]),
        tools_used=_loads(r["tools_used"]),
        decisions=_loads(r["decisions"]),
        files_touched=_loads(r["files_touched"]),
        open_items=_loads(r["open_items"]),
        summary=r["summary"],
        tags=list(r["tags"]),
    )


def _streaks(days: list[date]) -> tuple[int, int]:
    """Current + longest run of consecutive calendar days from a sorted list.

    Current streak counts back from today (or yesterday, so an in-progress day
    without activity yet doesn't break the streak).
    """
    if not days:
        return 0, 0
    longest = run = 1
    for prev, cur in pairwise(days):
        run = run + 1 if cur - prev == timedelta(days=1) else 1
        longest = max(longest, run)

    today = date.today()
    if days[-1] not in (today, today - timedelta(days=1)):
        return 0, longest
    current = 1
    for prev, cur in pairwise(reversed(days)):
        # reversed pairs: prev is the later day, cur the earlier one.
        if prev - cur == timedelta(days=1):
            current += 1
        else:
            break
    return current, longest


class MindPalaceLedger:
    """Session ledger — one row per session, upserted as the session evolves."""

    def __init__(self, *, postgres_url: str, tenant: str = "default") -> None:
        self._url = postgres_url
        self._tenant = tenant
        self._pool: asyncpg.Pool | None = None

    @property
    def tenant(self) -> str:
        """The namespace every read and write is scoped to.

        Defaults to `"default"`, the same default `MemoryFabric` and `QdrantVectorIndex` use -- a
        third default here would put the Mind Palace in a namespace of its own for every existing
        caller, and the symptom would be an empty history rather than an error."""
        return self._tenant

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

    async def get_pool(self) -> asyncpg.Pool:
        """Public pool accessor for same-package consumers (briefing, search)."""
        return await self._get_pool()

    async def write(self, rec: SessionRecord) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                UPSERT_SQL,
                rec.session_id,
                rec.project,
                rec.agent_name,
                rec.started_at,
                rec.ended_at,
                rec.duration_secs,
                rec.tokens_in,
                rec.tokens_out,
                rec.cost_usd,
                json.dumps(rec.tools_used),
                json.dumps(rec.decisions),
                json.dumps(rec.files_touched),
                json.dumps(rec.open_items),
                rec.summary,
                rec.tags,
                self._tenant,
            )
            return int(row["id"])  # type: ignore[index]

    async def get(self, *, session_id: str) -> SessionRecord | None:
        """Read a session by id -- with a short bounded retry against a real, rare
        (~2%, empirically measured over ~95 trials) miss: a just-committed `write()` is
        occasionally invisible to an immediate `get()`, no exception raised, just a
        clean empty result. Reproduces specifically on a fresh process's first
        connection (not a warm, already-used pool) -- consistent with this
        environment's independently-diagnosed Docker Desktop port-forward flakiness
        (proven flaky/reset elsewhere this session), not a query or transaction bug
        (`write()`'s INSERT...RETURNING is a single autocommitted statement, and
        Postgres guarantees cross-connection READ COMMITTED visibility for it). Kept
        deliberately cheap (3 attempts, 10ms/20ms between them -- 30ms worst case) so a
        genuinely absent session_id -- the common case -- still returns `None` quickly,
        not slowly."""
        pool = await self._get_pool()
        for attempt in range(3):
            async with pool.acquire() as conn:
                r = await conn.fetchrow(GET_SQL, session_id, self._tenant)
            if r is not None:
                return session_record_from_row(r)
            if attempt < 2:
                await asyncio.sleep(0.01 * (attempt + 1))
        return None

    async def recent(self, *, project: str, agent_name: str, limit: int = 3) -> list[SessionRecord]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(RECENT_SQL, project, agent_name, limit, self._tenant)
        return [session_record_from_row(r) for r in rows]

    async def _usage_stats_once(
        self, conn: asyncpg.pool.PoolConnectionProxy, agent_name: str, cutoff: date
    ) -> dict[str, Any]:
        agg = await conn.fetchrow(
            """
            SELECT
                COALESCE(SUM(tokens_in + tokens_out), 0) AS lifetime_tokens,
                COALESCE(SUM(cost_usd), 0)               AS lifetime_cost_usd,
                COUNT(*)                                 AS session_count,
                COALESCE(MAX(tokens_in + tokens_out), 0) AS peak_tokens,
                COALESCE(MAX(duration_secs), 0)          AS longest_task_secs
            FROM madras_mindpalace_sessions
            WHERE agent_name = $1
            """,
            agent_name,
        )
        daily_rows = await conn.fetch(
            """
            SELECT started_at::date AS day,
                   SUM(tokens_in + tokens_out)::bigint AS tokens
            FROM madras_mindpalace_sessions
            WHERE agent_name = $1 AND started_at::date >= $2
            GROUP BY day
            ORDER BY day
            """,
            agent_name,
            cutoff,
        )
        all_days = await conn.fetch(
            """
            SELECT DISTINCT started_at::date AS day
            FROM madras_mindpalace_sessions
            WHERE agent_name = $1
            ORDER BY day
            """,
            agent_name,
        )

        days = [r["day"] for r in all_days]
        current_streak, longest_streak = _streaks(days)
        assert agg is not None, "aggregate query with COALESCE always returns exactly one row"
        return {
            "lifetime_tokens": int(agg["lifetime_tokens"]),
            "lifetime_cost_usd": float(agg["lifetime_cost_usd"]),
            "session_count": int(agg["session_count"]),
            "peak_tokens": int(agg["peak_tokens"]),
            "longest_task_secs": int(agg["longest_task_secs"]),
            "current_streak": current_streak,
            "longest_streak": longest_streak,
            "daily": [
                {"date": r["day"].isoformat(), "tokens": int(r["tokens"])} for r in daily_rows
            ],
        }

    async def usage_stats(self, *, agent_name: str) -> dict[str, Any]:
        """Cross-project token/cost/streak aggregates for one agent.

        `daily` lists only days with activity in the last 53 weeks (371 days),
        each `{date: "YYYY-MM-DD", tokens: int}` — for a heatmap. Streaks are
        consecutive calendar days (by started_at::date) with >=1 session.

        Retries a short bounded number of times (same fresh-pool read-after-write
        flake `get()` retries against, see its docstring) -- an aggregate read has no
        single row to poll for, so instead this keeps the read with the most sessions
        seen and stops once two consecutive reads agree (stable, nothing newer to find)."""
        pool = await self._get_pool()
        cutoff = date.today() - timedelta(days=371)
        best: dict[str, Any] | None = None
        best_richness: tuple[int, int, int] | None = None
        prev_richness: tuple[int, int, int] | None = None
        for attempt in range(3):
            async with pool.acquire() as conn:
                candidate = await self._usage_stats_once(conn, agent_name, cutoff)
            # session_count, current_streak, and daily are computed from separate
            # queries on the same connection -- a stale read can leave any one of them
            # behind even when the others already look complete, so all three feed the
            # richness/stability comparison (not session_count alone).
            richness = (
                candidate["session_count"],
                candidate["current_streak"],
                len(candidate["daily"]),
            )
            if best_richness is None or richness > best_richness:
                best, best_richness = candidate, richness
            if richness == prev_richness:
                break
            prev_richness = richness
            if attempt < 2:
                await asyncio.sleep(0.01 * (attempt + 1))
        assert best is not None, "loop runs at least once"
        return best

    async def projects(
        self, *, agent_name: str, per_project_limit: int = 8
    ) -> list[dict[str, Any]]:
        """Sessions grouped by project, projects sorted by most-recent activity."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT project, session_id, summary, cost_usd, started_at
                FROM madras_mindpalace_sessions
                WHERE agent_name = $1
                ORDER BY started_at DESC, id DESC
                """,
                agent_name,
            )

        order: list[str] = []
        grouped: dict[str, dict[str, Any]] = {}
        for r in rows:
            proj = r["project"]
            if proj not in grouped:
                order.append(proj)
                grouped[proj] = {
                    "project": proj,
                    "session_count": 0,
                    "total_cost_usd": 0.0,
                    "sessions": [],
                }
            g = grouped[proj]
            g["session_count"] += 1
            g["total_cost_usd"] += float(r["cost_usd"])
            if len(g["sessions"]) < per_project_limit:
                g["sessions"].append(
                    {
                        "session_id": r["session_id"],
                        "summary": (r["summary"] or "")[:120],
                        "cost_usd": float(r["cost_usd"]),
                        "started_at_iso": r["started_at"].isoformat(),
                    }
                )
        return [grouped[p] for p in order]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
