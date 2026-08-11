"""Per-turn session log (W1·c) — the detailed, tagged turn-level record.

One row PER TURN (vs the Mind Palace ledger's one row/session): the exchange + intent +
tool-calls + files + tags, with an FTS vector for turn-level recall (3b) and a
`consolidated` flag so the nightly Memory Manager can distil un-consolidated turns into
atomic Fabric memories (3c). Pure storage — all ranking/temporal logic lives elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import asyncpg


@dataclass
class TurnRecord:
    session_id: str
    user_text: str = ""
    assistant_text: str = ""
    intent: str = ""
    agent_name: str = "shadow"
    project: str = "default"
    turn_idx: int = -1  # -1 -> auto-assign the next index for the session
    ts: float = 0.0
    tools_called: list[str] = field(default_factory=list[str])
    files_touched: list[str] = field(default_factory=list[str])
    tags: list[str] = field(default_factory=list[str])
    cost_usd: float = 0.0
    confidence: float = 0.0
    id: int | None = None  # set on read


# fts is built from user+assistant+intent+tags (params $6,$7,$8,$11).
_FTS = (
    "to_tsvector('english', coalesce($6,'')||' '||coalesce($7,'')||' '||"
    "coalesce($8,'')||' '||array_to_string($11::text[],' '))"
)

_INSERT = f"""
INSERT INTO madras_turn_log
    (session_id, agent_name, project, turn_idx, ts, user_text, assistant_text, intent,
     tools_called, files_touched, tags, cost_usd, confidence, fts)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::text[],$10::text[],$11::text[],$12,$13,{_FTS})
ON CONFLICT (session_id, turn_idx) DO UPDATE SET
    assistant_text = EXCLUDED.assistant_text, intent = EXCLUDED.intent,
    tools_called = EXCLUDED.tools_called, files_touched = EXCLUDED.files_touched,
    tags = EXCLUDED.tags, cost_usd = EXCLUDED.cost_usd,
    confidence = EXCLUDED.confidence, fts = EXCLUDED.fts
RETURNING turn_idx
"""
_NEXT_IDX = "SELECT COALESCE(MAX(turn_idx), -1) + 1 FROM madras_turn_log WHERE session_id=$1"
_RECENT = "SELECT * FROM madras_turn_log WHERE session_id=$1 ORDER BY turn_idx DESC LIMIT $2"
_SEARCH = """
SELECT *, ts_rank(fts, plainto_tsquery('english', $2)) AS rank
FROM madras_turn_log
WHERE agent_name=$1 AND fts @@ plainto_tsquery('english', $2)
ORDER BY rank DESC, ts DESC LIMIT $3
"""
_FOR_CONSOLIDATION = (
    "SELECT * FROM madras_turn_log WHERE agent_name=$1 AND consolidated=FALSE ORDER BY ts LIMIT $2"
)
_MARK = "UPDATE madras_turn_log SET consolidated=TRUE WHERE id = ANY($1::bigint[])"


def _from_row(r: asyncpg.Record) -> TurnRecord:
    return TurnRecord(
        id=int(r["id"]),
        session_id=r["session_id"],
        agent_name=r["agent_name"],
        project=r["project"],
        turn_idx=int(r["turn_idx"]),
        ts=float(r["ts"]),
        user_text=r["user_text"],
        assistant_text=r["assistant_text"],
        intent=r["intent"],
        tools_called=list(r["tools_called"]),
        files_touched=list(r["files_touched"]),
        tags=list(r["tags"]),
        cost_usd=float(r["cost_usd"]),
        confidence=float(r["confidence"]),
    )


class TurnLogLedger:
    """Per-turn session log store (migration 0022). Append-on-turn; recall + consolidate."""

    def __init__(self, *, postgres_url: str) -> None:
        self._url = postgres_url
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=4)
        return self._pool

    async def append(self, rec: TurnRecord) -> int:
        """Persist one turn; auto-assigns the next turn_idx when rec.turn_idx < 0. Returns it."""
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            idx = rec.turn_idx
            if idx < 0:
                idx = int(await conn.fetchval(_NEXT_IDX, rec.session_id))
            row = await conn.fetchrow(
                _INSERT,
                rec.session_id,
                rec.agent_name,
                rec.project,
                idx,
                rec.ts,
                rec.user_text,
                rec.assistant_text,
                rec.intent,
                rec.tools_called,
                rec.files_touched,
                rec.tags,
                rec.cost_usd,
                rec.confidence,
            )
        return int(row["turn_idx"])  # type: ignore[index]

    async def recent(self, *, session_id: str, limit: int = 20) -> list[TurnRecord]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_RECENT, session_id, limit)
        return [_from_row(r) for r in rows]

    async def search(
        self, *, query: str, agent_name: str = "shadow", k: int = 6
    ) -> list[TurnRecord]:
        """Turn-level full-text recall (3b) — the FTS half; the vector half folds in above."""
        if not query.strip():
            return []
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_SEARCH, agent_name, query, k)
        return [_from_row(r) for r in rows]

    async def for_consolidation(
        self, *, agent_name: str = "shadow", limit: int = 200
    ) -> list[TurnRecord]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_FOR_CONSOLIDATION, agent_name, limit)
        return [_from_row(r) for r in rows]

    async def mark_consolidated(self, ids: list[int]) -> int:
        if not ids:
            return 0
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(_MARK, ids)
        return len(ids)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
