"""Memory Experiences (Step 7) — the user-facing layer on top of the fabric.

* E1 WHISPER — ambient recall: already live (per-turn auto-recall injects relevant
  durable memories into the prompt). ``whisper()`` formats that for surfacing.
* E6 DRIFT FLAG — when the user states something that CONTRADICTS a stored current
  fact, surface it ("you previously said X — update to Y?") instead of silently
  overwriting. Built on the fabric's contradiction detection.
* E3 MEMORY IMPORT — ingest a memory dump (e.g. a ChatGPT/Claude export) into atomic
  fabric memories via the salient extractor.

Pure/deterministic here; the import store-write happens in the tool layer.
"""

from __future__ import annotations

from typing import Any

from madras.memory.extract import extract_salient
from madras.memory.retrieval import MemoryItem, find_contradictions


def whisper(recalled: list[MemoryItem], *, limit: int = 4) -> str:
    """Format recalled memories as an ambient 'I remember…' whisper. '' if none."""
    if not recalled:
        return ""
    lines = [f"- {it.content}" for it in recalled[:limit]]
    return "I remember about you:\n" + "\n".join(lines)


def drift_flags(existing: list[MemoryItem], statement: str, now: float) -> list[dict[str, Any]]:
    """For each atomic claim in `statement` that contradicts a stored current fact,
    return a drift flag {subject, old, new} — surfaced to the user, never auto-applied."""
    flags: list[dict[str, Any]] = []
    for cand in extract_salient(statement):
        probe = MemoryItem(
            id="_probe",
            kind=cand.kind,
            subject=cand.subject,
            content=cand.content,
            created_at=now,
            valid_from=now,
        )
        for stale in find_contradictions(existing, probe, now):
            flags.append({"subject": cand.subject, "old": stale.content, "new": cand.content})
    return flags


def import_candidates(dump: str) -> list[MemoryItem]:
    """Turn a memory dump into atomic, un-persisted MemoryItems (E3). The tool stamps
    provenance + ids and writes them through the fabric (contradiction-aware)."""
    out: list[MemoryItem] = []
    for c in extract_salient(dump):
        out.append(
            MemoryItem(
                id="",
                kind=c.kind,
                subject=c.subject,
                content=c.content,
                source="import",
                confidence=0.8,
            )
        )
    return out
