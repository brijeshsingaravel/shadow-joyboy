"""Health Manager's momentum/streak tracker (row health-manager).

"momentum/streak" was confirmed absent anywhere in Madras (s46 research). The clean,
zero-new-infra build: `memory_manager/job.py` already appends a nightly
`learning_signal` scalar to a `LearningHistory` series and runs `StagnationDetector`
over it (row learning-engine). Momentum is the literal INVERSE read of that same
series -- consecutive IMPROVING nights instead of consecutive non-improving ones. Same
patience-style algorithm shape as `StagnationDetector` (`metacog/stagnation.py`), no
new storage, no new writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise


@dataclass
class MomentumVerdict:
    streak: int  # consecutive improving checks ending the history
    best_streak: int  # longest improving run anywhere in the history
    detail: str = ""


@dataclass
class MomentumTracker:
    min_delta: float = 0.0

    def check(self, history: list[float]) -> MomentumVerdict:
        """`history` is ordered oldest -> newest, the SAME series
        `StagnationDetector.check` reads. A streak counts consecutive values that
        each improve on the one before by more than `min_delta`."""
        if len(history) < 2:
            return MomentumVerdict(0, 0, detail="not enough history yet")

        streak = 0
        best = 0
        for prev, cur in pairwise(history):
            if cur > prev + self.min_delta:
                streak += 1
                best = max(best, streak)
            else:
                streak = 0

        detail = (
            f"{streak} consecutive improving check(s), best run {best}"
            if streak
            else f"no active streak (best run was {best})"
        )
        return MomentumVerdict(streak=streak, best_streak=best, detail=detail)
