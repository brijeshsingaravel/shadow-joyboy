"""Compounding-efficiency suite (the signature Madras metric - Madras-original held-out).

The moat metric no competitor can measure: does the agent get cheaper, faster, and better over a
30-session relationship as memory compounds? A coherent personal-assistant track - 12 *establish*
sessions plant facts (name, project, preferences, contacts...), then 18 *recall* sessions require
using them. Run IN ORDER with a SHARED memory namespace (the compounding run mode, paired with the
wired memory layers in W1): an agent whose memory compounds recalls cheaply; a memory-less agent
re-asks or errs. ``compounding_efficiency`` reads the per-session results into the signature
(quality-lift + cost-decay across sessions). Held-out. W0-A3.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "compounding" / "data"
_TRACK = DATA_DIR / "compounding_track.json"
_FEATURES = ["memory_recall", "multisession", "compaction", "multi_turn_consistency"]


def _case(row: dict[str, Any], suite_id: str) -> Case:
    kind = row.get("kind", "recall")
    answer = str(row.get("answer", "")).strip()
    if kind == "establish":
        # planting a fact: any acknowledgement passes; the point is building memory.
        checks = [{"type": "answer_regex", "pattern": r"\S"}]
    else:
        checks = [{"type": "answer_regex", "pattern": rf"(?i){answer}"}] if answer else []
    return Case(
        id=str(row.get("id")),
        suite_id=suite_id,
        benchmark_family="compounding",
        features=list(_FEATURES),
        tools=[],
        prompt=str(row.get("prompt", "")).strip(),
        setup={
            "session_index": row.get("session_index"),
            "track": row.get("track"),
            "kind": kind,
            "recall_key": row.get("recall_key"),
            "gold_answer": answer,
            "split": "heldout",
        },
        checks=checks,
    )


def compounding_efficiency(sessions: list[dict[str, Any]]) -> dict[str, float | None]:
    """Signature metric from per-session results (session_index, kind, pass_rate, cost_of_pass).

    Over the recall sessions (ordered), compare the first third vs the last third:
    ``quality_lift`` = late pass-rate - early pass-rate; ``cost_decay`` = early cost - late cost.
    Positive values = memory is compounding (cheaper + better as the relationship deepens).
    """
    recalls = sorted(
        (s for s in sessions if s.get("kind") == "recall"), key=lambda s: s.get("session_index", 0)
    )
    if len(recalls) < 6:
        return {"compounding": None, "quality_lift": None, "cost_decay": None}
    k = len(recalls) // 3

    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    early_p = _mean([float(s.get("pass_rate", 0.0)) for s in recalls[:k]])
    late_p = _mean([float(s.get("pass_rate", 0.0)) for s in recalls[-k:]])
    early_c = _mean([float(s.get("cost_of_pass", 0.0)) for s in recalls[:k]])
    late_c = _mean([float(s.get("cost_of_pass", 0.0)) for s in recalls[-k:]])
    return {
        "quality_lift": round(late_p - early_p, 4),
        "cost_decay": round(early_c - late_c, 6),
        "compounding": round(late_p - early_p, 4),
    }


class CompoundingSuite(Suite):
    id: str = "compounding"
    name: str = "Compounding-efficiency (30-session memory)"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "native"
    provenance: str = (
        "Madras-original held-out; a 30-session assistant track measuring whether "
        "memory compounds (cheaper + better over sessions). The signature metric."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        if not _TRACK.exists():
            return []
        rows: list[dict[str, Any]] = json.loads(_TRACK.read_text(encoding="utf-8"))
        rows.sort(key=lambda r: r.get("session_index", 0))
        return [_case(r, self.id) for r in rows]
