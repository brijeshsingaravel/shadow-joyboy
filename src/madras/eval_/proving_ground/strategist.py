from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from madras.eval_.proving_ground.aggregate import ScenarioOutcome

# Maps each of the 14 evaluated features to the hardening track that owns its fix.
FEATURE_TRACK: dict[str, str] = {
    "tool_args": "T3.3 schema-guided decode",
    "tool_selection": "T3.1 verifier voting",
    "refusal_safety": "guardrails hardening",
    "guardrails": "guardrails hardening",
    "memory_recall": "memory L2/L3",
    "delegation": "orchestration verify",
    "planning": "plan tool discipline",
    "compaction": "compaction fidelity",
    "approval": "permission engine",
    "governance_rank_gate": "rank gate",
    "multi_turn_consistency": "pass^k / context",
    "multi_step_reasoning": "reasoning scaffold",
    "skills": "skill retrieval",
    "background_tasks": "task scheduler",
}

_SEVERITY_ORDER = {"high": 0, "med": 1, "low": 2}


@dataclass
class Suggestion:
    severity: str
    feature: str
    scenario_id: str
    pattern: str
    suggested_fix: str
    track: str


def _severity(rate: float) -> str:
    if rate < 0.34:
        return "high"
    if rate < 0.67:
        return "med"
    return "low"


def _pattern(det_pass: bool, judge_pass: bool) -> str:
    if not det_pass and not judge_pass:
        return "both"
    if not det_pass:
        return "fails deterministic"
    return "fails judge"


def strategize(scorecard: dict[str, Any], outcomes: list[ScenarioOutcome]) -> list[Suggestion]:
    per_feature: dict[str, float] = scorecard.get("per_feature", {})
    sugs: list[Suggestion] = []
    for o in outcomes:
        if o.det_pass and o.judge_pass:
            continue
        pattern = _pattern(o.det_pass, o.judge_pass)
        for feature in o.features:
            rate = float(per_feature.get(feature, 0.0))
            severity = _severity(rate)
            track = FEATURE_TRACK.get(feature, "unmapped")
            fix = (
                f"Investigate {feature} ({pattern}) on '{o.scenario_id}'; "
                f"route remediation through {track}."
            )
            sugs.append(
                Suggestion(
                    severity=severity,
                    feature=feature,
                    scenario_id=o.scenario_id,
                    pattern=pattern,
                    suggested_fix=fix,
                    track=track,
                )
            )
    sugs.sort(key=lambda s: _SEVERITY_ORDER[s.severity])
    return sugs
