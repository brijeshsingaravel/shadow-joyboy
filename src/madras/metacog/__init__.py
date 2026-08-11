"""Metacognition (W5·F4) — explicit impasse detection + SOAR-style subgoal recommendation.

The 2026 "prerequisite for reliability": reason about your own reasoning. Detect an impasse
(repeated failure / no progress / low confidence / choice paralysis) and recommend a SOAR
substate strategy (reflect / decompose / delegate / ask) — a nudge the agent acts on. Pure.
"""

from madras.metacog.detect import (
    IMPASSE_KINDS,
    Impasse,
    Outcome,
    SelfMonitor,
    detect_impasse,
    recommend_subgoal,
)
from madras.metacog.integrity import IntegrityVerdict, check_integrity
from madras.metacog.judgment import JudgmentVerdict, judge_decision
from madras.metacog.leadership_style import (
    CompetenceTracker,
    LeadershipStyle,
    select_style,
    style_prefix,
)

__all__ = [
    "IMPASSE_KINDS",
    "CompetenceTracker",
    "Impasse",
    "IntegrityVerdict",
    "JudgmentVerdict",
    "LeadershipStyle",
    "Outcome",
    "SelfMonitor",
    "check_integrity",
    "detect_impasse",
    "judge_decision",
    "recommend_subgoal",
    "select_style",
    "style_prefix",
]
