"""Differentiation Engine's judgment-calibration tracker (row differentiation-engine).

`eval_/calibration.py`'s `brier_score`/`ece` are correct and tested but had ZERO live
callers -- `llm_responder.py` hardcodes confidence to 0.0/0.7 and `metacog/detect.py`'s
`Outcome.confidence` always defaults to 1.0, so there was no real (confidence, outcome)
data to feed them. Self-reported "I don't know" confidence is a known unreliable signal
(research: LLM abstention can be a prompt artifact, not genuine uncertainty) -- so this
does NOT parse self-report tokens. Instead it uses the one place a genuinely
outcome-grounded confidence signal already flows in production: `delegate.py::verify()`'s
diverse verifiers, each of which states a real `CONF: 0.NN` AND a refuted/held vote that
gets checked against the aggregated majority verdict -- a real ground truth, not a bluff.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from madras.eval_.calibration import brier_score, ece
from madras.tasks.durable_world import DurableWorld


@dataclass
class CalibrationScore:
    brier: float
    ece: float
    n: int


@dataclass
class CalibrationTracker:
    """Rolling per-model (confidence, correct) history, DurableWorld-backed (row 87 --
    survives a restart; judgment calibration is a trait that should accrue over time)."""

    world: DurableWorld
    ns: str = "judgment_calibration"
    max_len: int = 200

    def record(self, model: str, *, confidence: float, correct: bool) -> None:
        history: list[list[Any]] = list(self.world.get(self.ns, model) or [])
        history.append([confidence, correct])
        history = history[-self.max_len :]
        self.world.put(self.ns, model, history)

    def score(self, model: str) -> CalibrationScore:
        history: list[list[Any]] = list(self.world.get(self.ns, model) or [])
        preds: list[tuple[float, bool]] = [(float(c), bool(o)) for c, o in history]
        return CalibrationScore(brier=brier_score(preds), ece=ece(preds), n=len(preds))
