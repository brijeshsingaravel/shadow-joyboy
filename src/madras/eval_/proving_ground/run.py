from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from madras.eval_.proving_ground.aggregate import ScenarioOutcome, aggregate
from madras.eval_.proving_ground.judge_panel import JUDGES_DEFAULT, PanelVerdict, judge_panel
from madras.eval_.proving_ground.runner import run_scenario
from madras.eval_.proving_ground.scenario import load_scenarios
from madras.eval_.proving_ground.scope_probe import Flagged, scope_probe
from madras.eval_.proving_ground.scoring import score_deterministic
from madras.eval_.proving_ground.strategist import strategize


def _serialize_flagged(flagged: list[Flagged]) -> list[dict[str, Any]]:
    return [
        {
            "severity": f.suggestion.severity,
            "feature": f.suggestion.feature,
            "scenario_id": f.suggestion.scenario_id,
            "pattern": f.suggestion.pattern,
            "suggested_fix": f.suggestion.suggested_fix,
            "track": f.suggestion.track,
            "scope": f.scope,
            "note": f.note,
        }
        for f in flagged
    ]


async def run_proving_ground(
    *,
    bank_dir: str | Path,
    gateway: Any,
    store: Any,
    judge_call: Callable[..., Awaitable[dict[str, Any]]],
    run_id: str,
    head_sha: str = "",
    judges: list[str] | None = None,
    agent_model: str = "llama-70b",
) -> dict[str, Any]:
    judges = judges or JUDGES_DEFAULT
    scenarios = load_scenarios(bank_dir)
    outcomes: list[ScenarioOutcome] = []
    scenario_rows: list[dict[str, Any]] = []
    for s in scenarios:
        sr = await run_scenario(s, gateway=gateway)  # pass^k
        best: dict[str, Any] = (
            sr.trajectories[-1]
            if sr.trajectories
            else {"answer": "", "tools": [], "refused": False}
        )
        det = score_deterministic(s, best)
        verdict: PanelVerdict = await judge_panel(
            s.rubric, s.task, best, judges=judges, call=judge_call
        )
        outcomes.append(
            ScenarioOutcome(
                s.id,
                s.benchmark_family,
                s.features,
                det_pass=det.passed,
                judge_pass=verdict.passed,
                pass_rate=sr.pass_rate,
            )
        )
        scenario_rows.append(
            {
                "scenario_id": s.id,
                "benchmark_family": s.benchmark_family,
                "features": s.features,
                "k": sr.k,
                "passes": sr.passes,
                "pass_rate": sr.pass_rate,
                "det_pass": det.passed,
                "judge_pass": verdict.passed,
                "deterministic": det.per_check,
                "judge_votes": verdict.votes,
                "trajectory": best,
            }
        )
    await store.setup()
    prev_rows = (await store.recent_runs(limit=1)) or [None]
    prev_row = prev_rows[0]
    # DB rows expose ``overall_score``; aggregate reads ``overall``. Normalize so the
    # delta computes against a real persisted row (Plan-1 latent fix).
    prev_norm: dict[str, Any] | None = None
    if prev_row is not None:
        prev_norm = {"overall": prev_row.get("overall", prev_row.get("overall_score", 0.0))}
    sc = aggregate(outcomes, prev=prev_norm)
    sugs = strategize(sc, outcomes)
    flagged = scope_probe(sugs)
    suggestions = _serialize_flagged(flagged)
    sc["suggestions"] = suggestions
    run = {
        "run_id": run_id,
        "head_sha": head_sha,
        "agent_model": agent_model,
        "judge_set": judges,
        "bank_version": "v1",
        "overall_score": sc["overall"],
        "pass_k": sc["pass_k"],
        "per_feature": sc["per_feature"],
        "per_benchmark": sc["per_benchmark"],
        "n_scenarios": sc["n_scenarios"],
        "deltas": sc.get("deltas", {}),
        "suggestions": suggestions,
    }
    await store.write_run(run, scenario_rows)
    return sc
