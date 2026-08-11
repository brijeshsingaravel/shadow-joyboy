"""CI eval gate (W5·F6) — merge-blocking quality gate over the proving-ground.

Native gate (reuses `BENCHMARK_TARGETS` + regression detection) instead of a duplicate
promptfoo platform: each benchmark's score is checked against its target; the gate fails if
ANY benchmark is below target OR any regression is present. `scripts/ci_eval_gate.py` exits
non-zero on failure (GitHub-Actions-ready). Pure.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GateItem:
    benchmark: str
    score: float
    target: float

    @property
    def passed(self) -> bool:
        return self.score >= self.target


@dataclass
class GateReport:
    items: list[GateItem] = field(default_factory=list[GateItem])
    regressions: list[str] = field(default_factory=list[str])
    skipped: list[str] = field(default_factory=list[str])  # benchmarks with no target

    @property
    def passed(self) -> bool:
        return all(i.passed for i in self.items) and not self.regressions

    def summary(self) -> str:
        fails = [f"{i.benchmark} {i.score:.2f}<{i.target:.2f}" for i in self.items if not i.passed]
        parts = [f"{len(self.items)} gated, {sum(i.passed for i in self.items)} pass"]
        if fails:
            parts.append("BELOW: " + ", ".join(fails))
        if self.regressions:
            parts.append("REGRESSIONS: " + ", ".join(self.regressions))
        return " | ".join(parts)


def evaluate_gate(
    scores: dict[str, float],
    targets: dict[str, float],
    *,
    regressions: list[str] | None = None,
) -> GateReport:
    """Compare per-benchmark scores against targets; collect failures + regressions."""
    items: list[GateItem] = []
    skipped: list[str] = []
    for bm, score in sorted(scores.items()):
        target = targets.get(bm)
        if target is None:
            skipped.append(bm)
            continue
        items.append(GateItem(benchmark=bm, score=float(score), target=float(target)))
    return GateReport(items=items, regressions=list(regressions or []), skipped=skipped)
