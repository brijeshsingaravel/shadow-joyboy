"""Nightly principle reflection — the Memory Manager's L5 self-evolving step.

Reads the fabric's currently-valid memories, finds reflection-worthy clusters, and
writes a durable principle back per new cluster (kind="principle", provenance stamped).
Idempotent: a subject that already has a principle is skipped. Pure logic lives in
``memory.reflection``; this just orchestrates the read → distil → write over a fabric.
"""

from __future__ import annotations

import uuid
from typing import Any

from madras.memory.reflection import (
    draft_principle,
    principle_exists,
    reflection_clusters,
)
from madras.memory.retrieval import MemoryItem


async def reflect(
    fabric: Any,
    *,
    now: float,
    min_support: int = 2,
    agent_name: str = "shadow",
    drafter: Any = None,
) -> dict[str, Any]:
    """Distil new principles from the fabric's current memories. Returns a summary.

    ``drafter`` (optional) is an async ``(subject, members) -> str`` for richer LLM
    phrasing; without it, a deterministic template is used.
    """
    items = await fabric.all_items(include_expired=False)
    clusters = reflection_clusters(items, now, min_support=min_support)
    written: list[str] = []
    for subject, members in clusters:
        if principle_exists(items, subject):
            continue  # accrue, don't duplicate
        if drafter is not None:
            try:
                text = await drafter(subject, members)
            except Exception:
                text = draft_principle(subject, members)
        else:
            text = draft_principle(subject, members)
        if not (text or "").strip():
            continue
        item = MemoryItem(
            id=uuid.uuid4().hex,
            kind="principle",
            subject=subject,
            content=text.strip(),
            source="reflection",
            agent_name=agent_name,
            confidence=0.9,
            created_at=now,
            valid_from=now,
        )
        await fabric.remember(item, now=now)
        written.append(subject)
    return {"clusters": len(clusters), "principles_written": len(written), "subjects": written}


async def reflect_skills(
    fabric: Any,
    skill_store: Any,
    *,
    now: float,
    agent_name: str = "shadow",
    project: str = "default",
) -> dict[str, Any]:
    """Distil a durable principle from each ACTIVE skill (skills→principles).

    A skill the agent learned/refined becomes a generalized principle in the Fabric (so it
    is auto-recalled, not just invoked as a tool). Idempotent on ``skill:<name>``. Mirrors
    ``reflect`` (memories→principles); together they cover sessions/skills→principles."""
    skills = await skill_store.list_active(project=project)
    existing = await fabric.all_items(include_expired=False)
    written = 0
    for sk in skills:
        subject = f"skill:{sk.name}"
        if principle_exists(existing, subject):
            continue
        desc = (getattr(sk, "description", "") or "").strip()
        text = f"Learned skill — {sk.name}: {desc}".strip().rstrip(":").strip()
        if not text:
            continue
        await fabric.remember(
            MemoryItem(
                id=uuid.uuid4().hex,
                kind="principle",
                subject=subject,
                content=text,
                source=f"skill:{sk.name}",
                agent_name=agent_name,
                confidence=0.85,
                created_at=now,
                valid_from=now,
            ),
            now=now,
        )
        written += 1
    return {"skills": len(skills), "principles_written": written}
