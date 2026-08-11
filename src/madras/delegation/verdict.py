"""Pure aggregation for adversarial verify — confidence-weighted quorum.

Each verifier returns a verdict {refuted: bool, confidence: float, ...}. Rather than
a flat count, we weight each REFUTED vote by the verifier's stated confidence (a
low-confidence refutation should not sink a claim a high-confidence panel accepts).
A claim is REJECTED when the summed refute-confidence reaches the quorum weight, or
(safety floor) when a strict majority of verifiers refute regardless of confidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# "CONF: 0.8" / "confidence: 80%" — grab the verifier's self-reported confidence.
_CONF_RE = re.compile(r"conf(?:idence)?\s*[:=]\s*(\d*\.?\d+)\s*(%?)", re.IGNORECASE)


def parse_confidence(text: str, default: float = 0.6) -> float:
    """Extract a 0..1 confidence from a verifier reply; default if absent."""
    m = _CONF_RE.search(text or "")
    if not m:
        return default
    val = float(m.group(1))
    if m.group(2) == "%" or val > 1.0:
        val = val / 100.0
    return max(0.0, min(1.0, val))


@dataclass
class Aggregate:
    holds: bool
    refuted_count: int
    n: int
    refute_weight: float
    quorum_weight: float
    majority_refuted: bool


def aggregate(verdicts: list[dict[str, Any]], n_verifiers: int) -> Aggregate:
    """Confidence-weighted quorum. quorum_weight = ceil(n/2) (one full-confidence
    refuter per two verifiers). Tie/edge favours REJECT (safety-stricter)."""
    n = max(n_verifiers, len(verdicts), 1)
    refuted = [v for v in verdicts if v.get("refuted")]
    refuted_count = len(refuted)
    refute_weight = sum(float(v.get("confidence", 0.6)) for v in refuted)
    quorum_weight = float(-(-n // 2))  # ceil(n/2)
    majority_refuted = refuted_count * 2 >= n  # tie rejects
    holds = not (refute_weight >= quorum_weight or majority_refuted)
    return Aggregate(
        holds=holds,
        refuted_count=refuted_count,
        n=n,
        refute_weight=round(refute_weight, 3),
        quorum_weight=quorum_weight,
        majority_refuted=majority_refuted,
    )
