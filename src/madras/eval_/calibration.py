"""Calibration metrics (W5·F6) — is the agent's confidence honest?

`brier_score` = mean squared error between predicted confidence and the binary outcome
(lower = better calibrated). `ece` = expected calibration error (binned reliability gap).
Pure; fed by the verify/confidence outputs to produce the Scorecard's calibration number.
"""

from __future__ import annotations


def brier_score(preds: list[tuple[float, bool]]) -> float:
    """Mean (confidence - outcome)^2 over (confidence in [0,1], correct?) pairs. 0 = perfect."""
    if not preds:
        return 0.0
    return sum((c - (1.0 if o else 0.0)) ** 2 for c, o in preds) / len(preds)


def ece(preds: list[tuple[float, bool]], *, bins: int = 10) -> float:
    """Expected calibration error: weighted mean |avg_confidence - accuracy| over bins."""
    if not preds:
        return 0.0
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for c, o in preds:
        idx = min(bins - 1, max(0, int(c * bins)))
        buckets[idx].append((c, o))
    n = len(preds)
    total = 0.0
    for b in buckets:
        if not b:
            continue
        avg_conf = sum(c for c, _ in b) / len(b)
        acc = sum(1 for _, o in b if o) / len(b)
        total += (len(b) / n) * abs(avg_conf - acc)
    return total
