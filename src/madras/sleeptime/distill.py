"""Distill recent raw memories into a shareable learned-context block (W4·B3).

Pure: selects the highest-signal *current* items (principles + high-confidence facts/semantic),
synthesizes one ``learned-context`` MemoryItem (provenance-tracked, tagged ``shareable``), and
returns it for the nightly agent to persist. The block is exportable via E-X4b and shareable
within the tenant. Returns ``None`` when there's nothing worth distilling.
"""

from __future__ import annotations

from madras.memory.retrieval import MemoryItem, is_current

_SIGNAL_KINDS = ("principle", "semantic", "fact")

# A distillation must not consume its own distillations (s59). This pass WRITES
# `kind="semantic"` at `confidence=1.0` and READS `kind in _SIGNAL_KINDS` above
# `min_confidence` -- so, sorted by `-confidence`, its own previous output sat at the top of
# the non-principle group on every subsequent run and could never fall out of
# `picks[:max_points]`. Each night therefore re-emitted the whole previous block as ONE of its
# points: `max_points` bounds the number of points, not their bytes. Left running, the growth
# is unbounded -- the live dev DB held six generations at ~1.85x each, ending at 12.25 MB in a
# single row, which `MemoryFabric.remember` then re-read on every write.
#
# Excluded by SOURCE rather than by matching the `learned-context` subject, so any future
# sleeptime-derived product inherits the guarantee instead of reopening the same hole under a
# different name.
_DERIVED_SOURCE = "sleeptime"


def distill_learned_context(
    items: list[MemoryItem],
    *,
    now: float,
    agent: str = "shadow",
    max_points: int = 8,
    min_confidence: float = 0.5,
) -> MemoryItem | None:
    """Synthesize a shareable learned-context block from current high-signal items."""
    picks = [
        it
        for it in items
        if is_current(it, now)
        and it.kind in _SIGNAL_KINDS
        and it.confidence >= min_confidence
        and it.source != _DERIVED_SOURCE  # never re-distil this pass's own output
    ]
    if not picks:
        return None
    # principles first, then by confidence (stable tie-break on content)
    picks.sort(key=lambda it: (it.kind != "principle", -it.confidence, it.content))
    picks = picks[:max_points]
    points = "\n".join(f"- {it.content}" for it in picks)
    content = f"Learned context ({len(picks)} points):\n{points}"
    return MemoryItem(
        id=f"lc-{agent}-{int(now)}",
        kind="semantic",
        subject="learned-context",
        content=content,
        tags=["learned-context", "shareable"],
        confidence=1.0,
        source="sleeptime",
        agent_name=agent,
        created_at=now,
        valid_from=now,
    )
