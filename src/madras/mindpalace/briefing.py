"""Mind Palace briefing pre-generator.

Produces a structured briefing for an agent session:
  - Deterministic context digest from recent sessions (last 3)
  - LLM narrative synthesis appended after the digest
  - Persisted to madras_mindpalace_briefings; returns the row id
"""

from __future__ import annotations

from datetime import date

from madras.llm.gateway import LLMGateway, LLMRequest
from madras.mindpalace.ledger import MindPalaceLedger, SessionRecord

# `tenant` is written explicitly (s61, D83 step 7). The column defaults to 'default', so an
# insert that omitted it from a ledger scoped to any OTHER tenant would be REJECTED by the RLS
# policy's WITH CHECK -- correctly, but confusingly, since nothing in this file mentioned tenancy.
# The value comes from the ledger this store borrows its pool from, which is also what binds
# `madras.tenant` on the connection -- so the row written and the policy checking it agree by
# construction rather than by coincidence.
_INSERT_SQL = """
INSERT INTO madras_mindpalace_briefings (project, agent_name, target_date, briefing_text, tenant)
VALUES ($1, $2, $3, $4, $5)
RETURNING id
"""

_SELECT_SQL = """
SELECT briefing_text FROM madras_mindpalace_briefings
WHERE project = $1 AND agent_name = $2 AND target_date = $3
ORDER BY ts DESC, id DESC
LIMIT 1
"""


def _build_digest(sessions: list[SessionRecord]) -> str:
    # ASI02: session content is DATA — wrap in <retrieved> so downstream LLM prompts
    # cannot be hijacked by injected instructions inside session summaries/decisions.
    content_lines: list[str] = []
    for s in sessions:
        content_lines.append(f"### {s.session_id}")
        content_lines.append(s.summary)
        if s.decisions:
            content_lines.append("Decisions:")
            for d in s.decisions:
                content_lines.append(f"- {d}")
        if s.open_items:
            content_lines.append("Open items:")
            for o in s.open_items:
                content_lines.append(f"- {o}")
    content = "\n".join(content_lines)
    return "## Context digest (last 3 sessions)\n<retrieved>\n" + content + "\n</retrieved>"


class BriefingGenerator:
    """Generate and store Mind Palace briefings for agent sessions."""

    def __init__(self, *, gateway: LLMGateway, ledger: MindPalaceLedger) -> None:
        self._gateway = gateway
        self._ledger = ledger

    async def generate(
        self,
        *,
        project: str,
        agent_name: str,
        target_date: date,
    ) -> int:
        """Generate a briefing and persist it; returns the new row id."""
        sessions = await self._ledger.recent(project=project, agent_name=agent_name, limit=3)
        digest = _build_digest(sessions)

        prompt = (
            f"You are preparing a briefing for agent '{agent_name}' working on project '{project}' "
            f"for {target_date}.\n\n"
            f"{digest}\n\n"
            "Synthesize the above context into a concise narrative briefing that highlights "
            "key decisions, open items, and recommended focus areas for the upcoming session."
        )
        resp = await self._gateway.complete(
            LLMRequest(
                model="anthropic/claude-haiku-4-5",
                messages=[{"role": "user", "content": prompt}],
            )
        )
        briefing_text = digest + "\n\n## Narrative\n" + resp.text

        pool = await self._ledger.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                _INSERT_SQL, project, agent_name, target_date, briefing_text, self._ledger.tenant
            )
        return int(row["id"])  # type: ignore[index]

    async def fetch(
        self,
        *,
        project: str,
        agent_name: str,
        target_date: date,
    ) -> str | None:
        """Return the most recent briefing text for the given project/agent/date, or None."""
        pool = await self._ledger.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_SQL, project, agent_name, target_date)
        return str(row["briefing_text"]) if row else None
