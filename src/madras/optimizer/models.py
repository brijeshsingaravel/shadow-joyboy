"""Self-optimizer data model (W4·B1) — GEPA-style reflective evolution.

A `Target` is any optimizable text (an instruction/prompt, a tool description, or a skill
procedure). The optimizer evolves candidate texts and returns an `OptimProposal` carrying
the **measured lift** + provenance — **propose-not-dispose**: nothing is applied until gated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

TARGET_KINDS = ("prompt", "tool_desc", "skill")


@dataclass
class Target:
    kind: str  # one of TARGET_KINDS
    id: str  # agent name / tool name / skill name
    current_text: str


@dataclass
class Candidate:
    text: str
    scores: dict[str, float] = field(default_factory=dict[str, float])  # per-instance score

    def mean(self) -> float:
        return sum(self.scores.values()) / len(self.scores) if self.scores else 0.0


@dataclass
class OptimProposal:
    target_kind: str
    target_id: str
    old_text: str
    new_text: str
    baseline_score: float
    new_score: float
    instances: int
    rounds: int
    approved: bool = False  # propose-not-dispose: gated before apply

    @property
    def lift(self) -> float:
        return self.new_score - self.baseline_score

    @property
    def improved(self) -> bool:
        return self.new_text != self.old_text and self.lift > 0.0
