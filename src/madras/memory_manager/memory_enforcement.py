"""Nightly memory-health enforcement — the fabric's Lighthouse step.

Computes a memory-health briefing and applies decay-with-dignity (archives stale,
low-value memories — recoverable, never deleted). Surfaces unresolved contradictions
for the arbiter + provenance gaps. Pure analysis lives in ``memory.health``.
"""

from __future__ import annotations

from typing import Any

from madras.memory.health import decay_candidates, memory_health


async def enforce_memory(
    fabric: Any,
    *,
    now: float,
    half_life_days: float = 30.0,
    floor: float = 0.2,
    apply_decay: bool = True,
) -> dict[str, Any]:
    """Build the memory-health briefing; archive decay candidates with dignity."""
    items = await fabric.all_items(include_expired=False)
    briefing = memory_health(items, now, half_life_days=half_life_days, floor=floor)
    archived = 0
    if apply_decay:
        for it in decay_candidates(items, now, half_life_days=half_life_days, floor=floor):
            try:
                await fabric.archive(it.id, now=now)
                archived += 1
            except Exception:
                pass
    briefing["archived_this_run"] = archived
    return briefing
