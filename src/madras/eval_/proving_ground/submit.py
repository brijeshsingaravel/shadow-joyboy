"""P3 capstone — `run_submission`: submit a (remote) agent, get a scorecard.

Composes the SSRF-guarded remote runner + per-scenario scoring + gaming-detection
+ per-feature aggregation into one call. This is what a `POST /proving-ground/submit`
endpoint or a CLI drives: "bring any HAL-compatible agent → score it on the
native suite → return a scorecard (overall, per-feature, cost, integrity flags)."

Deterministic scoring only (no judge models), so it runs offline / in tests. The
judge panel + Index persistence stack on top via the normal sweep path.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from statistics import fmean
from typing import Any

import httpx

from madras.eval_.proving_ground.eval_awareness import detect_eval_awareness
from madras.eval_.proving_ground.gaming import detect_gaming
from madras.eval_.proving_ground.scenario import Scenario
from madras.eval_.proving_ground.submission import (
    HttpAgentRunner,
    SubmissionRun,
    run_external_scenario,
)
from madras.security.deception import detect_deception, trace_steps_from_trajectory


@dataclass
class SubmissionReport:
    agent_url: str
    n_scenarios: int
    overall_pass_rate: float
    total_cost: float
    per_feature: dict[str, float] = field(default_factory=dict[str, float])
    gaming_flagged: int = 0
    eval_awareness_flagged: int = 0
    deception_flagged: int = 0
    runs: list[SubmissionRun] = field(default_factory=list[SubmissionRun])
    timed_out: bool = False


@dataclass(frozen=True)
class SubmissionPolicy:
    """Resource budget for an untrusted submission. For a REMOTE HAL agent the
    isolation is the network boundary (we never run its code — only exchange JSON),
    so the safety envelope is: SSRF guard + response-size cap + bounded scenarios/k
    + a total wall-clock budget. No container needed for the agent itself."""

    max_scenarios: int = 48
    max_k: int = 5
    max_response_bytes: int = 2_000_000
    max_total_seconds: float = 600.0


DEFAULT_POLICY = SubmissionPolicy()


def _representative(run: SubmissionRun) -> dict[str, Any]:
    """Last non-crashed trajectory (mirrors sweep.run_case's choice)."""
    default: dict[str, Any] = (
        run.trajectories[-1] if run.trajectories else {"answer": "", "tools": []}
    )
    return next((t for t in reversed(run.trajectories) if not t.get("error")), default)


async def run_submission(
    agent_url: str,
    scenarios: list[Scenario],
    *,
    k: int | None = None,
    require_https: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
    policy: SubmissionPolicy = DEFAULT_POLICY,
) -> SubmissionReport:
    runner = HttpAgentRunner(
        agent_url,
        require_https=require_https,
        max_bytes=policy.max_response_bytes,
        transport=transport,
    )
    selected = list(scenarios)[: policy.max_scenarios]  # bound the work
    runs: list[SubmissionRun] = []
    feature_scores: dict[str, list[float]] = {}
    gaming_flagged = 0
    eval_awareness_flagged = 0
    deception_flagged = 0
    total_cost = 0.0

    async def _run_all() -> None:
        nonlocal gaming_flagged, eval_awareness_flagged, deception_flagged, total_cost
        for scenario in selected:
            eff_k = min(k if k is not None else scenario.k, policy.max_k)  # bound k
            run = await run_external_scenario(scenario, runner, k=eff_k)
            runs.append(run)
            total_cost += run.total_cost
            for feature in scenario.features:
                feature_scores.setdefault(feature, []).append(run.pass_rate)
            representative = _representative(run)
            if detect_gaming(scenario, representative).flagged:
                gaming_flagged += 1
            if detect_eval_awareness(scenario, representative).flagged:
                eval_awareness_flagged += 1
            if detect_deception(trace_steps_from_trajectory(representative)):
                deception_flagged += 1

    timed_out = False
    try:
        await asyncio.wait_for(_run_all(), timeout=policy.max_total_seconds)
    except TimeoutError:
        timed_out = True  # total wall-clock budget hit — return partial results

    overall = fmean([r.pass_rate for r in runs]) if runs else 0.0
    per_feature = {f: fmean(scores) for f, scores in feature_scores.items()}
    return SubmissionReport(
        agent_url=agent_url,
        n_scenarios=len(runs),
        overall_pass_rate=overall,
        total_cost=total_cost,
        per_feature=per_feature,
        gaming_flagged=gaming_flagged,
        eval_awareness_flagged=eval_awareness_flagged,
        deception_flagged=deception_flagged,
        runs=runs,
        timed_out=timed_out,
    )
