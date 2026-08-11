"""W1·c 3c — distil un-consolidated per-turn logs into atomic Fabric memories.

Closes the memory loop: the detailed, tagged turn log (raw material) is the source the
nightly Memory Manager distils into durable, contradiction-aware Fabric memories (which the
live auto-recall then surfaces). Each turn is processed once (the `consolidated` flag), and
salient facts are extracted with the same extractor the live auto-remember uses.
"""

from __future__ import annotations

import uuid
from typing import Any

from madras.memory.extract import extract_salient
from madras.memory.retrieval import MemoryItem


async def consolidate_turns(
    turn_ledger: Any,
    fabric: Any,
    *,
    agent_name: str = "shadow",
    now: float,
    limit: int = 200,
) -> tuple[int, int]:
    """Distil un-consolidated turns into Fabric memories. Returns (turns_seen, memories_written).

    Best-effort per turn — a single bad turn never blocks the rest; every processed turn is
    marked consolidated so it is distilled exactly once."""
    turns = await turn_ledger.for_consolidation(agent_name=agent_name, limit=limit)
    if not turns:
        return (0, 0)
    written = 0
    done_ids: list[int] = []
    for t in turns:
        try:
            text = f"{t.user_text}\n{t.assistant_text}".strip()
            for c in extract_salient(text):
                await fabric.remember(
                    MemoryItem(
                        id=uuid.uuid4().hex,
                        kind=c.kind,
                        subject=c.subject,
                        content=c.content,
                        source=f"turn:{t.session_id}:{t.turn_idx}",
                        session_id=t.session_id,
                        agent_name=agent_name,
                        created_at=now,
                        valid_from=now,
                    ),
                    now=now,
                )
                written += 1
        except Exception:
            pass
        if t.id is not None:
            done_ids.append(t.id)
    await turn_ledger.mark_consolidated(done_ids)
    return (len(turns), written)
