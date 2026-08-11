"""Learning Engine's stagnation detector (Human-Aligned frame, row learning-engine).

The note's own gap: "plateau/stagnation detector (no such file — genuinely absent)."
Rather than inventing a heuristic, this is the proven, industry-standard PATIENCE-based
plateau algorithm (PyTorch's `ReduceLROnPlateau`, Keras `EarlyStopping`): track a running
best over a metric history; declare a plateau once `patience` consecutive values fail to
improve on that best by more than `min_delta`. Pure, deterministic, no ML dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

from madras.tasks.durable_world import DurableWorld


@dataclass
class StagnationVerdict:
    plateaued: bool
    streak: int  # consecutive non-improving values ending the history
    best: float | None
    detail: str = ""


@dataclass
class StagnationDetector:
    patience: int = 3
    min_delta: float = 0.0

    def check(self, history: list[float]) -> StagnationVerdict:
        """`history` is ordered oldest -> newest. A plateau needs at least
        `patience` + 1 points (a running best plus `patience` non-improving points)."""
        if len(history) <= self.patience:
            return StagnationVerdict(False, streak=0, best=None, detail="not enough history yet")

        best = history[0]
        streak = 0
        for value in history[1:]:
            if value > best + self.min_delta:
                best = value
                streak = 0
            else:
                streak += 1
                best = max(best, value)

        plateaued = streak >= self.patience
        detail = (
            f"{streak} consecutive check(s) with no improvement > {self.min_delta} over "
            f"the running best {best:g}"
            if plateaued
            else f"improving or too early ({streak}/{self.patience} non-improving)"
        )
        return StagnationVerdict(plateaued, streak=streak, best=best, detail=detail)


@dataclass
class LearningHistory:
    """Rolling per-agent learning-signal history, backed by a DurableWorld (row 87 --
    survives a restart, no Postgres required for this small counter series)."""

    world: DurableWorld
    ns: str = "learning_history"
    max_len: int = 30

    def append(self, agent_name: str, value: float) -> list[float]:
        history = list(self.world.get(self.ns, agent_name) or [])
        history.append(value)
        history = history[-self.max_len :]
        self.world.put(self.ns, agent_name, history)
        return history
