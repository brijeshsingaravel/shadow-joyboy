"""Credibility Scorecard + 5-part verification harness (W5·F7).

Assembles the **five-part verification harness** into one refreshable artifact, so "best
methods" is a dashboard, not a claim:
  1. **Scorecard rows** — per capability: best-known method · Madras status · verdict · backing.
  2. **CI-gate** — the merge-blocking gate result (F6).
  3. **Index** — madras_index / scaffold_lift (the public leaderboard numbers).
  4. **Published numbers** — benchmark scores vs targets.
  5. **Audit integrity** — the tamper-evident audit hash-chain verdict.

Refresh each phase + at every Tier-1 change (operating-discipline cadence). Pure: the live
parts (gate/index/audit/published) are injected; the rows seed from the credibility study.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

VERDICTS = ("above", "at", "below")


@dataclass
class ScorecardRow:
    capability: str
    best_method: str
    madras_status: str
    verdict: str  # above | at | below
    backing: str = ""  # a benchmark name or citation


# Seeded from research/methods-credibility-study.md. The four frontier rows the study marked
# "below" are now BUILT this session (B1 self-optimizer · F3 deception · F4 metacognition ·
# W2 goal-science loop) -> flipped to at/above.
CREDIBILITY_ROWS: list[ScorecardRow] = [
    ScorecardRow(
        "Harnessing/orchestration",
        "LangGraph durable + supervisor",
        "durable checkpointer + tiered orchestration",
        "above",
        "tau2/BFCL",
    ),
    ScorecardRow(
        "Governance/agent-harm",
        "deterministic action-boundary + classifier rails",
        "ASI01-10 + rank-gate/JIT + guardrails + audit chain",
        "above",
        "ASI red-team",
    ),
    ScorecardRow(
        "Eval/proof",
        "scaffold-aware HAL + CI-gating",
        "Proving Ground + CI gate (F6) + calibration",
        "at",
        "Madras Index",
    ),
    ScorecardRow(
        "Self-improving memory",
        "6-layer fabric + reinforcement",
        "fabric + strength/sleep-time/community (E-X4/B3)",
        "above",
        "LongMemEval",
    ),
    ScorecardRow(
        "Self-optimizer",
        "DSPy/GEPA reflective evolution",
        "in-house GEPA loop (B1)",
        "at",
        "scaffold_lift",
    ),
    ScorecardRow(
        "Deception/sandbagging",
        "behavioral + probe detection",
        "universal detector + AgentDojo (F3)",
        "at",
        "AgentDojo",
    ),
    ScorecardRow(
        "Metacognition",
        "SOAR impasse->subgoal + self-monitor",
        "explicit metacog layer (F4)",
        "at",
        "reasoning-trace audits",
    ),
    ScorecardRow(
        "Goal science",
        "WOOP/implementation-intentions/OODA loops",
        "29-lens methodology registry (W2)",
        "above",
        "Analyst lenses",
    ),
]


@dataclass
class Scorecard:
    rows: list[ScorecardRow] = field(default_factory=list[ScorecardRow])
    gate_passed: bool | None = None
    index: dict[str, float] = field(default_factory=dict[str, float])
    published: dict[str, float] = field(default_factory=dict[str, float])
    audit_intact: bool | None = None

    @property
    def harness_parts(self) -> dict[str, bool]:
        """Which of the 5 verification parts are present in this refresh."""
        return {
            "scorecard": bool(self.rows),
            "ci_gate": self.gate_passed is not None,
            "index": bool(self.index),
            "published_numbers": bool(self.published),
            "audit_integrity": self.audit_intact is not None,
        }

    @property
    def verified(self) -> bool:
        """The harness is satisfied when all 5 parts are present, the gate passes, and the
        audit chain is intact."""
        return (
            all(self.harness_parts.values())
            and self.gate_passed is True
            and self.audit_intact is True
        )

    def counts(self) -> dict[str, int]:
        c = {v: 0 for v in VERDICTS}
        for r in self.rows:
            if r.verdict in c:
                c[r.verdict] += 1
        return c

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [vars(r) for r in self.rows],
            "counts": self.counts(),
            "harness_parts": self.harness_parts,
            "gate_passed": self.gate_passed,
            "index": self.index,
            "published": self.published,
            "audit_intact": self.audit_intact,
            "verified": self.verified,
        }


def build_scorecard(
    *,
    rows: list[ScorecardRow] | None = None,
    gate_passed: bool | None = None,
    index: dict[str, float] | None = None,
    published: dict[str, float] | None = None,
    audit_intact: bool | None = None,
) -> Scorecard:
    """Assemble the 5-part verification harness into a Scorecard (rows default to study seed)."""
    return Scorecard(
        rows=rows if rows is not None else list(CREDIBILITY_ROWS),
        gate_passed=gate_passed,
        index=index or {},
        published=published or {},
        audit_intact=audit_intact,
    )
