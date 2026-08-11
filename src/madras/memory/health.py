"""Memory health (Step 6) — the nightly Lighthouse enforcement for the fabric.

Pure analysis over the fabric's items: decay-with-dignity candidates (stale, low-value
memories to ARCHIVE — never delete), unresolved contradictions (same subject, multiple
distinct current values that escaped supersede-on-write), provenance gaps, and a
morning briefing. Mirrors the Canon enforcement pattern. Deterministic + testable.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from madras.memory.retrieval import MemoryItem, is_current, recency

_NEVER_DECAY = {"principle"}  # principles accrue; they don't decay out


def decay_candidates(
    items: list[MemoryItem],
    now: float,
    *,
    half_life_days: float = 30.0,
    floor: float = 0.2,
) -> list[MemoryItem]:
    """Currently-valid, non-principle memories whose value (recency x confidence) has
    fallen below the floor — archive with dignity (recoverable), not delete."""
    out: list[MemoryItem] = []
    for it in items:
        if it.kind in _NEVER_DECAY or not is_current(it, now):
            continue
        value = recency(it.created_at, now, half_life_days=half_life_days) * max(
            0.0, min(1.0, it.confidence)
        )
        if value < floor:
            out.append(it)
    return out


def open_contradictions(items: list[MemoryItem], now: float) -> list[tuple[str, int]]:
    """Subjects with >1 distinct current value among facts/preferences — a contradiction
    that escaped supersede-on-write (the arbiter should resolve). Returns (subject, n)."""
    by_subj: dict[str, set[str]] = defaultdict(set)
    for it in items:
        if it.kind in ("fact", "preference") and is_current(it, now) and it.subject.strip():
            by_subj[it.subject.strip().lower()].add(" ".join(it.content.lower().split()))
    return [(s, len(v)) for s, v in by_subj.items() if len(v) > 1]


def memory_health(
    items: list[MemoryItem], now: float, *, half_life_days: float = 30.0, floor: float = 0.2
) -> dict[str, Any]:
    """A morning briefing on memory health (pure)."""
    current = [i for i in items if is_current(i, now)]
    by_kind: dict[str, int] = defaultdict(int)
    for i in current:
        by_kind[i.kind] += 1
    decay = decay_candidates(current, now, half_life_days=half_life_days, floor=floor)
    contras = open_contradictions(current, now)
    no_prov = [i for i in current if not (i.source or "").strip()]
    issues = len(decay) + len(contras) + len(no_prov)
    headline = "Memory healthy" if issues == 0 else f"Memory needs tidying — {issues} item(s)"
    nudge = ""
    if contras:
        nudge = (
            f"{len(contras)} subject(s) hold conflicting current values "
            f"(e.g. “{contras[0][0]}”) — resolve which is true."
        )
    elif decay:
        nudge = f"{len(decay)} stale low-value memories ready to archive."
    return {
        "exists": bool(current),
        "headline": headline,
        "total_current": len(current),
        "total_archived": len(items) - len(current),
        "by_kind": dict(by_kind),
        "principles": by_kind.get("principle", 0),
        "decay_candidates": len(decay),
        "open_contradictions": len(contras),
        "provenance_gaps": len(no_prov),
        "nudge": nudge or "Memory is clean and provenance-stamped.",
    }
