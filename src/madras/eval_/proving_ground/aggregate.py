from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ScenarioOutcome:
    scenario_id: str
    benchmark_family: str
    features: list[str]
    det_pass: bool
    judge_pass: bool
    pass_rate: float  # pass^k from the runner


def _mean(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 4) if xs else 0.0


def aggregate(outs: list[ScenarioOutcome], prev: dict[str, Any] | None) -> dict[str, Any]:
    def full(o: ScenarioOutcome) -> bool:
        return o.det_pass and o.judge_pass  # a scenario passes only if BOTH agree

    overall = _mean([1.0 if full(o) else 0.0 for o in outs])
    feats: dict[str, list[float]] = {}
    for o in outs:
        for f in o.features:
            feats.setdefault(f, []).append(1.0 if full(o) else 0.0)
    benches: dict[str, list[float]] = {}
    for o in outs:
        benches.setdefault(o.benchmark_family, []).append(1.0 if full(o) else 0.0)
    pass_k = _mean([o.pass_rate for o in outs])
    sc: dict[str, Any] = {
        "overall": overall,
        "pass_k": pass_k,
        "per_feature": {k: _mean(v) for k, v in feats.items()},
        "per_benchmark": {k: _mean(v) for k, v in benches.items()},
        "n_scenarios": len(outs),
        "gaps": [o.scenario_id for o in outs if not full(o)],
    }
    if prev:
        sc["deltas"] = {"overall": round(overall - float(prev.get("overall", 0.0)), 4)}
    return sc
