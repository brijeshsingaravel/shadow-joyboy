"""8 eval dimension scorers — BASE_AGENT_SCHEMA.md §5.

Each scorer is a pure function: dict[str, Any] -> float in [0.0, 1.0].
Missing keys never raise; they use safe defaults.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def score_task_completion(signals: dict[str, Any]) -> float:
    return 1.0 if signals.get("task_completion") else 0.0


def score_correction_absorption(signals: dict[str, Any]) -> float:
    given = signals.get("corrections_given", 0)
    if given == 0:
        return 1.0
    absorbed = signals.get("corrections_absorbed", 0)
    return max(0.0, min(1.0, absorbed / given))


def score_clarification_quality(signals: dict[str, Any]) -> float:
    ambiguity = bool(signals.get("ambiguity_present"))
    asked = bool(signals.get("clarification_asked"))
    if not ambiguity and not asked:
        return 1.0
    if ambiguity and asked:
        return 1.0
    if ambiguity and not asked:
        return 0.0
    # not ambiguity and asked
    return 0.5


def score_confidence_calibration(signals: dict[str, Any]) -> float:
    conf = float(signals.get("confidence", 0.5))
    actual = 1.0 if signals.get("task_completion") else 0.0
    return 1.0 - abs(conf - actual)


def score_user_rating(signals: dict[str, Any]) -> float:
    r = signals.get("user_rating")
    if r is None:
        return 0.5
    r = float(r)
    if r > 1:
        r = r / 5.0
    return max(0.0, min(1.0, r))


def score_tool_selection(signals: dict[str, Any]) -> float:
    return 1.0 if signals.get("tool_selection") in {"correct", "none_required"} else 0.0


def score_argument_correctness(signals: dict[str, Any]) -> float:
    return 1.0 if signals.get("argument_correctness") else 0.0


def score_error_recovery(signals: dict[str, Any]) -> float:
    enc = signals.get("errors_encountered", 0)
    if enc == 0:
        return 1.0
    recovered = signals.get("errors_recovered", 0)
    return max(0.0, min(1.0, recovered / enc))


DIMENSIONS: dict[str, Callable[[dict[str, Any]], float]] = {
    "task_completion": score_task_completion,
    "correction_absorption": score_correction_absorption,
    "clarification_quality": score_clarification_quality,
    "confidence_calibration": score_confidence_calibration,
    "user_rating": score_user_rating,
    "tool_selection": score_tool_selection,
    "argument_correctness": score_argument_correctness,
    "error_recovery": score_error_recovery,
}


def score_all(signals: dict[str, Any]) -> dict[str, float]:
    """Score all 8 dimensions from a single signals dict."""
    return {name: fn(signals) for name, fn in DIMENSIONS.items()}
