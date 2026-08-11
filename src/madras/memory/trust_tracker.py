"""Social Intelligence's trust tracker (row social-intelligence).

Research (2025/26, no forkable OSS -- drift-diffusion trust models, contextual-bandit
trust calibration): trust in [0,1] moves on calibrated evidence, decays over inaction,
avoids both over- and under-trust. Built native. `had_correction` (skills/generator.py)
was a dead field -- never set True anywhere -- so `detect_correction` is also this
faculty's first real correction-detection logic, not just a trust-tracker input.
"""

from __future__ import annotations

from dataclasses import dataclass

from madras.tasks.durable_world import DurableWorld

_CORRECTION_MARKERS = (
    "no, ",
    "no,",
    "that's not",
    "that's wrong",
    "actually,",
    "actually i",
    "i meant",
    "not what i asked",
    "not what i meant",
    "you misunderstood",
    "that's incorrect",
    "wrong, ",
    "let me clarify",
    "i said",
    "that isn't",
)


def detect_correction(user_message: str) -> bool:
    """Deterministic keyword-marker heuristic (same idiom as metacog/resource_mode.py):
    does this user message read as pushback/correction on the prior turn?"""
    text = (user_message or "").strip().lower()
    return any(marker in text for marker in _CORRECTION_MARKERS)


@dataclass
class TrustTracker:
    """Rolling per-user trust evidence, DurableWorld-backed (row 87 -- trust is a
    relationship property meant to persist across sessions, same reasoning as the
    Differentiation Engine's cross-session CalibrationTracker)."""

    world: DurableWorld
    ns: str = "trust_evidence"
    max_len: int = 50

    def record(self, user_id: str, *, positive: bool) -> None:
        history: list[float] = list(self.world.get(self.ns, user_id) or [])
        history.append(1.0 if positive else 0.0)
        history = history[-self.max_len :]
        self.world.put(self.ns, user_id, history)

    def score(self, user_id: str) -> float | None:
        """Recency-weighted trust: an exponential moving average so recent evidence
        counts more than old evidence (matches the research's "moves on evidence,
        decays over inaction" framing) -- None when there's no evidence yet (an
        unknown user gets no trust CLAIM, not a fabricated neutral score)."""
        history: list[float] = list(self.world.get(self.ns, user_id) or [])
        if not history:
            return None
        alpha = 0.3
        score = history[0]
        for value in history[1:]:
            score = alpha * value + (1 - alpha) * score
        return round(score, 4)
