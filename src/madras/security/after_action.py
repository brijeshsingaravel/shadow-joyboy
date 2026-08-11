"""Defense Engine's after-action review (row defense-engine).

An after-action record fits cleanly as one more `audit/writer.py::AuditLogWriter`
entry (`action="after_action_review"`) -- no new store needed, per the note's own
"built on top of existing mechanisms" conclusion. Written once an incident's response
has resolved; the incident summary + response + outcome live in `extras` so the
existing hash-chain (`audit/chain.py::verify_chain`) covers after-action records too.
"""

from __future__ import annotations

from typing import Protocol

from madras.security.incident_response import IncidentResponse


class AuditWriter(Protocol):
    async def append(self, record: object) -> int: ...


async def write_after_action_review(
    *,
    writer: AuditWriter,
    agent_name: str,
    session_id: str,
    response: IncidentResponse,
    timeline: str,
    outcome: str,
) -> int:
    """Records one immutable after-action entry: what happened (incident class +
    action taken), the timeline, and the outcome (resolved/false_positive/escalated)."""
    from madras.audit.writer import AuditRecord

    record = AuditRecord(
        agent_name=agent_name,
        session_id=session_id,
        action="after_action_review",
        signals={"incident_class": response.incident_class.value, "outcome": outcome},
        extras={
            "action_taken": response.action_taken,
            "detail": response.detail,
            "timeline": timeline,
        },
    )
    return await writer.append(record)
