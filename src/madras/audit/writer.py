"""Immutable audit log writer.

There is intentionally no update() or delete() method. Forensics-grade.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import asyncpg

# The schema lives in infra/migrations/0000_bootstrap_runtime_tables.sql, which owns it.
# This module used to carry a byte-identical copy and execute it in `setup()`; that made the
# schema reproducible from two places and required DDL rights the app role must not have.

INSERT_SQL = """
INSERT INTO madras_audit_log
  (agent_name, session_id, action, signals, tool_calls, extras, prev_hash, record_hash)
VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb, $7, $8)
RETURNING id
"""

LATEST_HASH_SQL = (
    "SELECT record_hash FROM madras_audit_log WHERE session_id = $1 ORDER BY id DESC LIMIT 1"
)


def _loads_obj(v: Any) -> Any:
    """Decode a JSONB column that may arrive as str (asyncpg) or already-parsed."""
    return json.loads(v) if isinstance(v, str) else v


@dataclass
class AuditRecord:
    agent_name: str
    session_id: str
    action: str
    signals: dict[str, Any]
    tool_calls: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    extras: dict[str, Any] = field(default_factory=dict[str, Any])


class MissingAuditTable(RuntimeError):
    """The audit table does not exist -- migrations have not been applied to this database."""


class AuditLogWriter:
    def __init__(self, *, postgres_url: str) -> None:
        self._url = postgres_url
        self._pool: asyncpg.Pool | None = None
        self._table = "madras_audit_log"
        self._verified = False

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=4)
        return self._pool

    async def setup(self) -> None:
        """Verify the audit table exists. Does NOT create it (s61, D83 step 5).

        `madras_audit_log` is owned by `0000_bootstrap_runtime_tables.sql`; this method used to
        create it again, byte-identically. That blocked the RLS cutover -- `madras_app` has no DDL,
        and `CREATE TABLE IF NOT EXISTS` is refused on privilege grounds even when the table
        already exists. Granting DDL instead would be worse: the creator OWNS the table, and owners
        bypass RLS unless FORCE is set.

        Verification rather than a silent no-op, because a missing table means migrations were not
        applied, and that should say so here rather than surface as a confusing INSERT failure
        several frames later. Cached: `setup()` is called inside operations (`abuse.py`,
        `byok.py`), so it must not cost a round trip per call -- it previously cost a DDL one.
        """
        if self._verified:
            return
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            exists = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", self._table)
        if not exists:
            raise MissingAuditTable(
                f"{self._table} does not exist -- apply infra/migrations "
                f"(0000_bootstrap_runtime_tables.sql creates it)"
            )
        self._verified = True

    async def append(self, record: AuditRecord) -> int:
        from madras.audit.chain import GENESIS, canonical_payload, compute_hash

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            # Chain to the session's latest record (within one connection so concurrent
            # appends to the same session don't interleave the prev_hash read+write).
            async with conn.transaction():
                prev = await conn.fetchval(LATEST_HASH_SQL, record.session_id)
                prev_hash = prev or GENESIS
                payload = canonical_payload(
                    agent_name=record.agent_name,
                    session_id=record.session_id,
                    action=record.action,
                    signals=record.signals,
                    tool_calls=record.tool_calls,
                    extras=record.extras,
                )
                record_hash = compute_hash(prev_hash, payload)
                row = await conn.fetchrow(
                    INSERT_SQL,
                    record.agent_name,
                    record.session_id,
                    record.action,
                    json.dumps(record.signals),
                    json.dumps(record.tool_calls),
                    json.dumps(record.extras),
                    prev_hash,
                    record_hash,
                )
            assert row is not None, "INSERT ... RETURNING id always returns exactly one row"
            return int(row["id"])

    async def verify_chain(self, *, session_id: str) -> dict[str, Any]:
        """Recompute the session's hash chain and report whether it is intact.

        Returns {ok, length, broken_at}. Tampering with or deleting any historical
        row breaks the chain at the first affected index.
        """
        from madras.audit.chain import verify_chain as _verify

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT agent_name, session_id, action, signals, tool_calls, extras, "
                "prev_hash, record_hash FROM madras_audit_log "
                "WHERE session_id = $1 ORDER BY id ASC",
                session_id,
            )
        records = [
            {
                "agent_name": r["agent_name"],
                "session_id": r["session_id"],
                "action": r["action"],
                "signals": _loads_obj(r["signals"]),
                "tool_calls": _loads_obj(r["tool_calls"]),
                "extras": _loads_obj(r["extras"]),
                "prev_hash": r["prev_hash"],
                "record_hash": r["record_hash"],
            }
            for r in rows
        ]
        res = _verify(records)
        return {"ok": res.ok, "length": res.length, "broken_at": res.broken_at}

    async def query(self, *, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Read-only: return appended rows for a session, ordered by id ascending.

        Immutable doctrine preserved — this only SELECTs; there is still no
        update() or delete(). JSONB columns are decoded to Python objects.
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT action, signals, tool_calls, extras, ts "
                "FROM madras_audit_log WHERE session_id = $1 "
                "ORDER BY id ASC LIMIT $2",
                session_id,
                limit,
            )
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "action": r["action"],
                    "signals": _loads_obj(r["signals"]),
                    "tool_calls": _loads_obj(r["tool_calls"]),
                    "extras": _loads_obj(r["extras"]),
                    "ts": r["ts"],
                }
            )
        return out

    async def query_by_agent(self, *, agent_name: str, limit: int = 100) -> list[dict[str, Any]]:
        """Read-only: the real activity feed for one agent across all its sessions
        (§ B7 Workspace) — newest first, unlike `query`'s chronological session replay."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT action, signals, tool_calls, extras, ts "
                "FROM madras_audit_log WHERE agent_name = $1 "
                "ORDER BY id DESC LIMIT $2",
                agent_name,
                limit,
            )
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "action": r["action"],
                    "signals": _loads_obj(r["signals"]),
                    "tool_calls": _loads_obj(r["tool_calls"]),
                    "extras": _loads_obj(r["extras"]),
                    "ts": r["ts"],
                }
            )
        return out

    async def usage_by_agent(self, *, agent_names: list[str]) -> list[dict[str, Any]]:
        """Read-only: real total cost + action count per agent (§ B10 Usage & Billing) —
        sums the same `signals->>'cost_usd'` every real turn already writes, nothing
        estimated. Empty list in -> empty list out (no accidental full-table scan)."""
        if not agent_names:
            return []
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT agent_name, COUNT(*) AS action_count, "
                "COALESCE(SUM((signals->>'cost_usd')::numeric), 0) AS total_cost_usd "
                "FROM madras_audit_log WHERE agent_name = ANY($1::text[]) "
                "GROUP BY agent_name",
                agent_names,
            )
        return [
            {
                "agent_name": r["agent_name"],
                "action_count": r["action_count"],
                "total_cost_usd": float(r["total_cost_usd"]),
            }
            for r in rows
        ]

    async def trail_by_agent(self, *, agent_name: str, limit: int = 200) -> list[dict[str, Any]]:
        """Read-only: the full audit trail for one agent (§ B12) — every record with its
        session (= "run"), action, tool calls, cost, and the record's own chain hash so
        the UI can group by run and show the tamper-evident linkage. Newest first."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT session_id, action, signals, tool_calls, ts, record_hash "
                "FROM madras_audit_log WHERE agent_name = $1 ORDER BY id DESC LIMIT $2",
                agent_name,
                limit,
            )
        out: list[dict[str, Any]] = []
        for r in rows:
            signals: dict[str, Any] = _loads_obj(r["signals"]) or {}
            out.append(
                {
                    "session_id": r["session_id"],
                    "action": r["action"],
                    "tool_calls": _loads_obj(r["tool_calls"]),
                    "cost_usd": signals.get("cost_usd"),
                    "task_completion": signals.get("task_completion"),
                    "ts": r["ts"],
                    "record_hash": r["record_hash"],
                }
            )
        return out

    async def verify_agent_chains(self, *, agent_name: str) -> dict[str, Any]:
        """The § B12 tamper-evidence proof: recompute the cryptographic hash chain for
        EVERY session this agent has, and report whether all are intact. The chain is
        per-session (prev_hash resets to GENESIS per session), so each session is
        verified independently and the result aggregated. Any altered or deleted
        historical row breaks its session's chain and is reported by session + index."""
        from madras.audit.chain import verify_chain as _verify

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT session_id, agent_name, action, signals, tool_calls, extras, "
                "prev_hash, record_hash FROM madras_audit_log "
                "WHERE agent_name = $1 ORDER BY session_id, id ASC",
                agent_name,
            )

        by_session: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_session.setdefault(r["session_id"], []).append(
                {
                    "agent_name": r["agent_name"],
                    "session_id": r["session_id"],
                    "action": r["action"],
                    "signals": _loads_obj(r["signals"]),
                    "tool_calls": _loads_obj(r["tool_calls"]),
                    "extras": _loads_obj(r["extras"]),
                    "prev_hash": r["prev_hash"],
                    "record_hash": r["record_hash"],
                }
            )

        total = 0
        broken: list[dict[str, Any]] = []
        for session_id, recs in by_session.items():
            res = _verify(recs)
            total += res.length
            if not res.ok:
                broken.append({"session_id": session_id, "broken_at": res.broken_at})

        return {
            "ok": len(broken) == 0,
            "total_records": total,
            "sessions_checked": len(by_session),
            "broken": broken,
        }

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
