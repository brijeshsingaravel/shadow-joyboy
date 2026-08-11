"""Mind Palace search — tag lookup and full-text search over session records."""

from __future__ import annotations

from madras.mindpalace.ledger import MindPalaceLedger, SessionRecord, session_record_from_row

_TAG_SQL = """
SELECT * FROM madras_mindpalace_sessions
WHERE project = $1 AND $2 = ANY(tags)
ORDER BY ts DESC LIMIT $3
"""

_FTS_SQL = """
SELECT * FROM madras_mindpalace_sessions
WHERE project = $1 AND to_tsvector('english', summary) @@ plainto_tsquery('english', $2)
ORDER BY ts DESC LIMIT $3
"""


async def search_by_tag(
    ledger: MindPalaceLedger, *, tag: str, project: str, limit: int = 10
) -> list[SessionRecord]:
    """Return sessions for *project* that carry *tag*, newest first."""
    pool = await ledger.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_TAG_SQL, project, tag, limit)
    return [session_record_from_row(r) for r in rows]


async def search_fts(
    ledger: MindPalaceLedger, *, query: str, project: str, limit: int = 10
) -> list[SessionRecord]:
    """Return sessions for *project* whose summary matches *query* via Postgres FTS."""
    pool = await ledger.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_FTS_SQL, project, query, limit)
    return [session_record_from_row(r) for r in rows]
