"""Reflex extractor — detects repeated task shapes and promotes them to L4 reflexes.

Phase 1 note: success_rate is always 1.0. Failure tracking (eval gating) arrives in M1F.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict

from madras.memory.reflex import ReflexCandidate, ReflexMemory
from madras.mindpalace.ledger import SessionRecord


def task_shape_hash(*, tags: list[str], tools: list[str]) -> str:
    """Stable 16-char hex hash for a task shape.

    Tag order is ignored (sorted before hashing); tool order is preserved.
    """
    payload = ",".join(sorted(tags)) + "|" + ",".join(tools)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _tags_key(tags: list[str]) -> str:
    return ",".join(sorted(tags))


def extract_candidates(
    sessions: list[SessionRecord],
    *,
    min_repeats: int = 3,
) -> list[ReflexCandidate]:
    """Group sessions by task shape; promote shapes with >= min_repeats — MAJORITY-VOTED.

    For each task KIND (same tags), sessions may take different tool approaches. We promote
    only the shape whose tool sequence is the *majority* approach for that kind, and record
    ``success_rate`` as the VOTE SHARE (majority count / all same-kind sessions) — so a noisy
    minority approach never becomes a reflex, and the rate reflects how settled the habit is.
    (A frequency vote — no failure signal needed; success/eval gating is a later refinement.)
    """
    by_shape: dict[str, list[SessionRecord]] = defaultdict(list)
    by_tags: dict[str, list[SessionRecord]] = defaultdict(list)
    for s in sessions:
        by_shape[task_shape_hash(tags=s.tags, tools=s.tools_used)].append(s)
        by_tags[_tags_key(s.tags)].append(s)

    candidates: list[ReflexCandidate] = []
    for shape_hash, group in by_shape.items():
        if len(group) < min_repeats:
            continue
        kind = by_tags[_tags_key(group[0].tags)]
        winner_seq, _votes = Counter(tuple(s.tools_used) for s in kind).most_common(1)[0]
        if tuple(group[0].tools_used) != winner_seq:
            continue  # a minority approach for this task-kind — outvoted, not a reflex
        candidates.append(
            ReflexCandidate(
                task_shape_hash=shape_hash,
                tool_sequence=group[0].tools_used,
                success_count=len(group),
                success_rate=len(group) / len(kind),  # vote share among same-kind tasks
            )
        )
    return candidates


async def inherit_reflexes(reflex: ReflexMemory, *, mentor: str, mentee: str) -> int:
    """Mentee inherits the mentor's reflexes — muscle-memory transfer down a mentorship
    edge (the L6 ``mentored`` relation). Returns the number of reflexes copied. The full
    career/lifecycle that decides WHO mentors whom lives in W5; this is the primitive."""
    copied = await reflex.all_for_agent(mentor)
    for c in copied:
        await reflex.write_candidate(mentee, c)
    return len(copied)


async def promote(
    candidates: list[ReflexCandidate],
    *,
    agent_name: str,
    reflex: ReflexMemory,
) -> int:
    """Write each candidate to L4 reflex memory. Returns count of candidates written."""
    for candidate in candidates:
        await reflex.write_candidate(agent_name, candidate)
    return len(candidates)
