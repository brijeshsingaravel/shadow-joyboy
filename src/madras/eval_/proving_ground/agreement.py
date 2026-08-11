"""Judge-vs-human agreement — the meta-evaluation loop that validates the validator.

The 2026 ceiling: strong judges agree with humans ~80% (about how often humans agree
with each other). We store a small set of human labels and measure how often the panel
matches them; below 0.80 flags the judge protocol as needing recalibration.
"""

from __future__ import annotations

from typing import Any

AGREEMENT_TARGET = 0.80


def judge_human_agreement(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """pairs: [{human_pass: bool, panel_pass: bool}]. Returns the agreement fraction,
    the count, and whether it is below the 0.80 target (needs recalibration)."""
    usable = [
        p for p in pairs if p.get("human_pass") is not None and p.get("panel_pass") is not None
    ]
    if not usable:
        return {"agreement": None, "n": 0, "below_target": False, "target": AGREEMENT_TARGET}
    match = sum(1 for p in usable if bool(p["human_pass"]) == bool(p["panel_pass"]))
    frac = match / len(usable)
    return {
        "agreement": round(frac, 3),
        "n": len(usable),
        "below_target": frac < AGREEMENT_TARGET,
        "target": AGREEMENT_TARGET,
    }
