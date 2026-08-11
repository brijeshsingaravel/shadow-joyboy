"""Memory consolidator — converts SessionRecord rows into L2 episodes (idempotent).

For each session in the input list: skip if an episode with that session_id already
exists in madras_episodes; otherwise write a new Episode carrying the session's
summary, decisions, tags, and token/cost extras.

Returns the number of new episodes written.
"""

from __future__ import annotations

from madras.memory.episodic import Episode, EpisodicMemory
from madras.memory.tenant_context import require_tenant
from madras.mindpalace.ledger import SessionRecord

# s63: `tenant` added. Without it this asked "has ANYONE already got a note for this
# conversation?" and skipped SILENTLY on a hit -- no error, no warning, not even a count.
# Harmless with one user; with two it loses conversations, because
# `ChatCompletionRequest.session_id` DEFAULTS to "cockpit-001" for every caller, so the second
# family member to talk to Shadow shares a session id with the first and their memory is
# discarded. The ninth instance of the tenant-less-uniqueness defect CLAUDE.md records from s61,
# and the only one sitting in the path every nightly run takes. Proven by a failing test before
# this line changed, not by reading.
_HAS_SESSION_SQL = "SELECT 1 FROM madras_episodes WHERE session_id = $1 AND tenant = $2 LIMIT 1"


async def consolidate(
    sessions: list[SessionRecord],
    *,
    episodic: EpisodicMemory,
) -> int:
    """Write one Episode per SessionRecord that has not yet been persisted.

    Idempotent: sessions whose session_id already appears in madras_episodes are skipped.
    Returns the count of newly written episodes.
    """
    # Resolved ONCE, before any work: the nightly job runs with nobody at the door and is the
    # most likely caller to arrive unbound. Failing here costs nothing; failing halfway through
    # would leave a person's memories half-consolidated.
    tenant = require_tenant()
    pool = await episodic.get_pool()
    written = 0
    for rec in sessions:
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(_HAS_SESSION_SQL, rec.session_id, tenant)
        if existing is not None:
            continue
        episode = Episode(
            session_id=rec.session_id,
            agent_name=rec.agent_name,
            summary=rec.summary,
            decisions=rec.decisions,
            tags=rec.tags,
            extras={
                "cost_usd": rec.cost_usd,
                "tokens_in": rec.tokens_in,
                "tokens_out": rec.tokens_out,
            },
        )
        await episodic.write(episode)
        written += 1
    return written
