"""Judge reliability — cascaded selective evaluation, calibrated abstention, and
coarse-scale snapping. Grounded in the 2026 findings (LLM judges are biased and
miscalibrated; escalate to the expensive panel only when uncertain; coarse scales
beat fine ones; let judges abstain rather than guess).
"""

from __future__ import annotations

from typing import Any

# Cheap judge confidence below this -> not trusted -> escalate to the full panel.
ESCALATE_CONF = 0.6


def should_escalate(det_result: dict[str, Any], cheap_vote: dict[str, Any] | None) -> bool:
    """Escalate to the 5-judge panel ONLY when the cheap tiers are uncertain.

    Decisive deterministic verdict (all checks pass or all fail) -> trust it, skip
    the panel. Mixed checks (ambiguous) -> escalate. Otherwise escalate when the
    cheap judge is low-confidence.
    """
    per: list[dict[str, Any]] = det_result.get("per_check") or []
    if per:
        passes = [bool(c.get("passed")) for c in per]
        if all(passes) or not any(passes):
            return False  # decisive — no panel needed
        return True  # mixed — ambiguous, escalate
    if cheap_vote is None:
        return True
    return float(cheap_vote.get("confidence", 0.0)) < ESCALATE_CONF


def snap_score(x: float) -> float:
    """Snap a fine 0..1 score to the coarse {0, 0.5, 1} scale (more reliable)."""
    x = max(0.0, min(1.0, float(x)))
    return min((0.0, 0.5, 1.0), key=lambda b: abs(b - x))


def panel_decision(votes: list[dict[str, Any]], *, threshold_frac: float = 0.6) -> dict[str, Any]:
    """Supermajority over NON-abstaining votes. If most judges abstain, the case is
    low-confidence (route to a meta-judge / human, don't trust the thin verdict).
    """
    abstained = sum(1 for v in votes if v.get("abstain"))
    counted = [v for v in votes if not v.get("abstain")]
    n_pass = sum(1 for v in counted if v.get("pass"))
    frac = (n_pass / len(counted)) if counted else 0.0
    low_conf = (not counted) or (abstained * 2 > len(votes))
    return {
        "passed": (frac >= threshold_frac) and bool(counted),
        "n_pass": n_pass,
        "counted": len(counted),
        "abstained": abstained,
        "frac": round(frac, 3),
        "low_confidence": low_conf,
    }
