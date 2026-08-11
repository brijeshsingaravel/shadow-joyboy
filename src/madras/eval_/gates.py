"""Per-dimension threshold gating — 8 independent gates, no composite score.

Design decision (locked): we never collapse the 8 dimensions into a single
composite score. Each dimension gates independently so failures are visible
and actionable. See BASE_AGENT_SCHEMA.md §5 and STATUS.json decisions.
"""

from __future__ import annotations

from typing import Any

from madras.eval_.dimensions import score_all

DEFAULT_THRESHOLDS: dict[str, float] = {
    "task_completion": 0.7,
    "correction_absorption": 0.7,
    "clarification_quality": 0.7,
    "confidence_calibration": 0.7,
    "user_rating": 0.6,
    "tool_selection": 0.7,
    "argument_correctness": 0.7,
    "error_recovery": 0.7,
}


def gate(
    signals: dict[str, Any],
    thresholds: dict[str, float] = DEFAULT_THRESHOLDS,
) -> dict[str, bool]:
    """Score all 8 dimensions and return per-dimension pass/fail booleans.

    A dimension passes when its score >= its threshold.
    Dimensions absent from *thresholds* are always treated as passing.
    """
    scores = score_all(signals)
    return {dim: score >= thresholds.get(dim, 0.0) for dim, score in scores.items()}


def all_pass(
    signals: dict[str, Any],
    thresholds: dict[str, float] = DEFAULT_THRESHOLDS,
) -> bool:
    """Return True only when every dimension passes its threshold gate."""
    return all(gate(signals, thresholds).values())
