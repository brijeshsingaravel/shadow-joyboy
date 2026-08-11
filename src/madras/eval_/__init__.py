"""Eval signal emitter — produces the per-action signal dict the contract requires."""

from madras.eval_.dimensions import DIMENSIONS, score_all
from madras.eval_.emitter import emit_action_signals
from madras.eval_.gates import DEFAULT_THRESHOLDS, all_pass, gate
from madras.eval_.judge import JudgeDispatcher, JudgeTrigger, JudgeVerdict
from madras.eval_.real_tests import RealTestResult, run_all

__all__ = [
    "DEFAULT_THRESHOLDS",
    "DIMENSIONS",
    "JudgeDispatcher",
    "JudgeTrigger",
    "JudgeVerdict",
    "RealTestResult",
    "all_pass",
    "emit_action_signals",
    "gate",
    "run_all",
    "score_all",
]
