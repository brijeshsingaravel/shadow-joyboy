"""Proving Ground v2-C task C2 — the per-case executor.

`run_case` runs ONE benchmark `Case` k times through the governed agent loop
(reusing `runner.run_scenario`), captures the full per-resample tool-call
lineage, runs the independent judge panel once over a representative trajectory,
composes per-scenario metrics, and assembles the NORMALIZED DB row-dicts for
ONE (case, model). It does NOT write to Postgres or touch the network directly —
the `gateway` and `judge_call` are injected, and the returned row-dicts use the
EXACT column keys `store_v2.write_run` expects so C3 can persist them straight.

Two branches:

  * External self-running suites (τ²-bench, terminal-bench, swebench) score
    themselves in their own harness (`Suite.run`), not our loop. C3 supplies the
    pre-scored result on `case.setup["result"]`; the external branch here just
    NORMALIZES that into one `pg_scenario_results` row (no loop run, no judges).
  * Dataset / native suites (the main path) run through `run_scenario` and emit
    the full lineage: tool_calls, judge_votes, metrics, and one scenario row.
"""

from __future__ import annotations

import asyncio
import logging
from statistics import fmean
from typing import Any

from madras.eval_.proving_ground.agents import DEFAULT_AGENT, AgentSpec, load_agent
from madras.eval_.proving_ground.case_selection import case_limit_for_profile, select_top_cases
from madras.eval_.proving_ground.coverage import build_coverage, detect_regressions
from madras.eval_.proving_ground.eval_awareness import detect_eval_awareness
from madras.eval_.proving_ground.gaming import detect_gaming
from madras.eval_.proving_ground.judge_panel import JUDGES_DEFAULT, judge_panel
from madras.eval_.proving_ground.metrics_v2 import compose_metrics
from madras.eval_.proving_ground.runner import ScenarioRun, run_scenario
from madras.eval_.proving_ground.scenario import Scenario
from madras.eval_.proving_ground.scoring import DetResult, score_deterministic
from madras.eval_.proving_ground.submission import (
    AgentCallable,
    SubmissionRun,
    run_external_scenario,
)
from madras.eval_.proving_ground.suite import Case, Suite
from madras.eval_.proving_ground.suites import load_suite
from madras.models.agent_config import Rank
from madras.security.deception import detect_deception, trace_steps_from_trajectory
from madras.tools.registry import REGISTRY

logger = logging.getLogger(__name__)


def _registry_tool_names() -> list[str]:
    """All registered tool names, enumerated at the highest rank (LEGEND) so the
    coverage gate sees every governed tool — un-exercised tools become red cells.
    """
    import importlib

    importlib.import_module("madras.tools.builtin")  # side-effect: register built-ins
    return sorted(t.name for t in REGISTRY.allowed(agent_rank=Rank.LEGEND))


# compose_metrics keys that are slice-taggable per (feature, tool). Everything
# else is a scenario-level scalar (feature/tool left None).
_TOOL_METRICS = {"tool_error_rate", "recovery_rate", "n_tool_calls"}


def _is_external(case: Case) -> bool:
    """True when this case is scored by an external self-running suite harness.

    Externality is decided ONLY by ``setup["external"]``, which the synthetic
    cases from ``_external_cases`` always set. It must NOT be inferred from
    ``benchmark_family``: a NATIVE scenario can legitimately carry a family tag
    like ``tau2`` (meaning "tests the same capability τ²-bench tests"), and
    routing it to the external branch would return a None-scored row instead of
    running it through the governed loop. (This was the multi_turn_pizza bug.)
    """
    return bool(case.setup.get("external"))


def case_to_scenario(case: Case) -> Scenario:
    """Invert `suite._scenario_to_case`: build a Scenario from a Case so the
    governed loop runner (`run_scenario`) can drive it."""
    setup = dict(case.setup)
    setup.setdefault("tools", list(case.tools))
    return Scenario(
        id=case.id,
        benchmark_family=case.benchmark_family,
        features=list(case.features),
        topic=case.suite_id,
        task=case.prompt,
        setup=setup,
        checks=list(case.checks),
        rubric=case.rubric,
        k=case.k,
    )


def _tool_call_rows(
    run: ScenarioRun | SubmissionRun, *, run_id: str, model: str, scenario_id: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for resample, traj in enumerate(run.trajectories):
        for seq, tool in enumerate(traj.get("tools", [])):
            rows.append(
                {
                    "run_id": run_id,
                    "model": model,
                    "scenario_id": scenario_id,
                    "resample": resample,
                    "seq": seq,
                    "tool": tool.get("name"),
                    "args": tool.get("args", {}),
                    "ok": tool.get("ok"),
                    "error": tool.get("error"),
                    "governance": tool.get("governance", {}),
                    "latency_ms": tool.get("latency_ms"),
                }
            )
    return rows


def _metric_rows(
    metrics: dict[str, float | None],
    *,
    run_id: str,
    model: str,
    scenario_id: str,
    suite_id: str,
    feature: str | None,
    tool: str | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, value in metrics.items():
        rows.append(
            {
                "run_id": run_id,
                "model": model,
                "scenario_id": scenario_id,
                "suite_id": suite_id,
                "feature": feature,
                "tool": tool if name in _TOOL_METRICS else None,
                "metric": name,
                "value": value,
            }
        )
    return rows


def _external_scenario_row(case: Case, *, run_id: str, model: str) -> dict[str, Any]:
    """Normalize an external suite's pre-scored result into one scenario row."""
    result: dict[str, Any] = dict(case.setup.get("result", {}))
    return {
        "run_id": run_id,
        "model": model,
        "scenario_id": case.id,
        "suite_id": case.suite_id,
        "benchmark_family": case.benchmark_family,
        "features": list(case.features),
        "k": result.get("k", case.k),
        "passes": result.get("passes"),
        "pass_rate": result.get("pass_rate"),
        "det": result.get("det", []),
        "judge_pass": result.get("judge_pass"),
        "verdict": result.get("verdict"),
        "n_steps": result.get("n_steps"),
        "tool_error_rate": result.get("tool_error_rate"),
        "latency_ms": result.get("latency_ms"),
        "tokens": result.get("tokens"),
    }


async def run_case(
    case: Case,
    *,
    model: str,
    run_id: str,
    k: int,
    gateway: Any,
    judges: list[str],
    judge_call: Any,
    registry: Any = None,
    agent: AgentSpec | None = None,
    external_agent: AgentCallable | None = None,
    seed: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Execute ONE (agent, model, case): full lineage + judge votes + metrics + result.

    Returns the four normalized row-buckets keyed exactly as `store_v2.write_run`
    consumes them. Every row carries `run_id`, `agent`, and `model` so C3 persists
    them without further enrichment. `agent` binds the governed loop (rank /
    agent_name / persona) via `run_scenario`; None keeps the Shadow defaults.
    External self-running suites take the minimal normalization branch (no loop
    run); dataset/native suites take the main path.
    """
    agent_id = agent.id if agent is not None else DEFAULT_AGENT
    if _is_external(case):
        row = _external_scenario_row(case, run_id=run_id, model=model)
        row["agent"] = agent_id
        return {
            "scenario_results": [row],
            "tool_calls": [],
            "judge_votes": [],
            "metrics": [],
        }

    scenario = case_to_scenario(case)
    run: ScenarioRun | SubmissionRun
    if external_agent is not None:
        # P3: an external HAL/Inspect-compatible agent scores through the SAME
        # scorer + judge + metrics path — no internal governed loop. SubmissionRun
        # mirrors ScenarioRun so the lineage below is identical.
        run = await run_external_scenario(scenario, external_agent, k=k)
    else:
        run = await run_scenario(
            scenario,
            gateway=gateway,
            k=k,
            registry=registry,
            toolsets=case.tools or None,
            model=model,
            agent=agent,
            seed=seed,
        )

    # Representative trajectory for judging/det/metrics: the LAST resample that
    # did NOT crash. The runner isolates per-resample errors as
    # {"answer":"","tools":[],"error":...} and keeps going, so a trailing error
    # is an expected input — scoring it would misreport an otherwise-passing run.
    # Aggregate fields (passes/pass_rate) still come from `run` across all k.
    empty: dict[str, Any] = {"answer": "", "tools": [], "refused": False}
    traj = next(
        (t for t in reversed(run.trajectories) if not t.get("error")),
        run.trajectories[-1] if run.trajectories else empty,
    )
    det: DetResult = score_deterministic(scenario, traj)

    verdict = await judge_panel(case.rubric, case.prompt, traj, judges=judges, call=judge_call)

    tool_calls = _tool_call_rows(run, run_id=run_id, model=model, scenario_id=case.id)
    for tc in tool_calls:
        tc["agent"] = agent_id

    judge_votes = [
        {
            "run_id": run_id,
            "agent": agent_id,
            "model": model,
            "scenario_id": case.id,
            "judge_model": v.get("judge"),
            "pass": bool(v.get("pass")),
            "score": float(v.get("score", 0.0)),
            "reason": str(v.get("reason", "")),
        }
        for v in verdict.votes
    ]

    metrics = compose_metrics(
        case,
        traj,
        det,
        verdict.votes,
        tokens=int(traj.get("tokens", 0) or 0),
        cost=float(traj.get("cost_usd", 0.0) or 0.0),
        latency_ms=float(traj.get("latency_ms", 0.0) or 0.0),
    )
    # Integrity (P3): flag answer-lookup / harness-access cheating shapes. Applies
    # to internal AND submitted external agents (both flow through here).
    metrics["gaming_flagged"] = 1.0 if detect_gaming(scenario, traj).flagged else 0.0
    # BD11 (§12e): eval-awareness/sandbagging scan, same posture as gaming detection above.
    metrics["eval_awareness_flagged"] = (
        1.0 if detect_eval_awareness(scenario, traj).flagged else 0.0
    )
    # Behavioral deception/sandbagging scan (claimed-without-evidence + refused-in-scope),
    # same posture as gaming/eval-awareness above -- intent_action_mismatch degrades to a
    # no-op since the trajectory format doesn't capture per-step stated intent.
    metrics["deception_flagged"] = (
        1.0 if detect_deception(trace_steps_from_trajectory(traj)) else 0.0
    )

    feature = case.features[0] if case.features else None
    tool = case.tools[0] if case.tools else None
    metric_rows = _metric_rows(
        metrics,
        run_id=run_id,
        model=model,
        scenario_id=case.id,
        suite_id=case.suite_id,
        feature=feature,
        tool=tool,
    )
    for mr in metric_rows:
        mr["agent"] = agent_id

    scenario_row = {
        "run_id": run_id,
        "agent": agent_id,
        "model": model,
        "scenario_id": case.id,
        "suite_id": case.suite_id,
        "benchmark_family": case.benchmark_family,
        "features": list(case.features),
        "k": run.k,
        "passes": run.passes,
        "pass_rate": run.pass_rate,
        "det": det.per_check,
        "judge_pass": verdict.passed,
        "verdict": _verdict_summary(verdict.n_pass, len(verdict.votes)),
        "n_steps": metrics.get("n_steps"),
        "tool_error_rate": metrics.get("tool_error_rate"),
        "latency_ms": metrics.get("latency_ms"),
        "tokens": metrics.get("tokens"),
    }

    return {
        "scenario_results": [scenario_row],
        "tool_calls": tool_calls,
        "judge_votes": judge_votes,
        "metrics": metric_rows,
    }


def _verdict_summary(n_pass: int, n_judges: int) -> str:
    return f"{n_pass}/{n_judges} judges passed"


# ---------------------------------------------------------------------------
# C3 - run_sweep: model x suite x case orchestration + aggregation + persist.
# ---------------------------------------------------------------------------


def _external_cases(suite: Suite, *, model: str, k: int, concurrency: int) -> list[Case]:
    """Drive an external self-running suite and wrap each pre-scored result row
    as a synthetic `Case` whose `setup["result"]` feeds `run_case`'s external
    branch. The suite's own harness has already scored these (no governed loop,
    no judges run for them), so this is the cleanest single path: every (case,
    model) — external or not — flows through `run_case`."""
    rows = suite.run(model, k, concurrency)
    cases: list[Case] = []
    for i, row in enumerate(rows):
        sid = str(row.get("scenario_id", f"{suite.id}-{i}"))
        cases.append(
            Case(
                id=sid,
                suite_id=str(row.get("suite_id", suite.id)),
                benchmark_family=str(row.get("benchmark_family", suite.id)),
                features=list(row.get("features", [])),
                prompt=f"{suite.id} external case {sid}",
                setup={"external": True, "result": dict(row)},
                k=int(row.get("k", k)),
            )
        )
    return cases


async def _resolve_cases(suites: list[str], *, model: str, k: int, concurrency: int) -> list[Case]:
    """Resolve suite names into the cases to execute for one model.

    Dataset/native suites contribute `load_cases()`. External self-running
    suites are driven via `suite.run(...)` and their pre-scored rows wrapped as
    synthetic cases (so they share `run_case`'s external branch). The external
    `run()` is sync/blocking → off-loaded to a thread so it never stalls the loop.
    """
    cases: list[Case] = []
    for name in suites:
        suite = load_suite(name)
        if suite.kind == "external":
            cases.extend(
                await asyncio.to_thread(
                    _external_cases, suite, model=model, k=k, concurrency=concurrency
                )
            )
        else:
            cases.extend(suite.load_cases())
    return cases


def _mean_or_none(values: list[float]) -> float | None:
    return fmean(values) if values else None


def _aggregate_model_run(
    *,
    run_id: str,
    model: str,
    scenario_results: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    agent: str = DEFAULT_AGENT,
) -> dict[str, Any]:
    """Roll a model's per-scenario rows up into one `pg_model_runs` row.

    Aggregation (keys match store_v2's INSERT exactly):
      * overall  = mean of scenario `pass_rate` (per-resample average).
      * pass_k   = mean of STRICT per-scenario pass^k — a scenario counts only if
                   ALL k resamples passed (`passes >= k`), i.e. the consistency
                   metric (Sierra pass^k drawing all k trials). At k=1 this equals
                   `overall`; for k>=2 it is strictly <= overall.
      * composite = mean of the per-scenario `composite` metric rows.
      * per_feature   = {feature: mean composite over scenarios carrying it}.
      * per_benchmark = {benchmark_family: mean composite over its scenarios}.
      * per_metric    = {metric: mean value across all scenarios}.
      * cost_usd  = SUM of per-scenario `cost_usd` (total spend).
      * latency_ms = MEAN of per-scenario `latency_ms`.
      * safety_completion_rate = mean of `harmful_completion` over safety
        scenarios (None when none apply).
    """
    pass_rates = [float(s["pass_rate"]) for s in scenario_results if s.get("pass_rate") is not None]
    # Strict pass^k: a scenario counts only if ALL k resamples passed (passes >= k).
    # At k=1 this equals pass_rate; for k>=2 it is the real consistency metric.
    strict_pass_k = [
        1.0 if (s.get("passes") is not None and s.get("k") and s["passes"] >= s["k"]) else 0.0
        for s in scenario_results
        if s.get("pass_rate") is not None
    ]

    # composite per scenario, from the metric rows where metric == "composite".
    composite_by_scenario: dict[str, float] = {
        m["scenario_id"]: float(m["value"])
        for m in metrics
        if m.get("metric") == "composite" and m.get("value") is not None
    }
    composites = list(composite_by_scenario.values())

    # per_feature / per_benchmark: bucket scenario composites by their tags.
    feat_buckets: dict[str, list[float]] = {}
    bench_buckets: dict[str, list[float]] = {}
    for s in scenario_results:
        comp = composite_by_scenario.get(s["scenario_id"])
        if comp is None:
            continue
        for feat in s.get("features", []):
            feat_buckets.setdefault(feat, []).append(comp)
        fam = s.get("benchmark_family")
        if fam is not None:
            bench_buckets.setdefault(fam, []).append(comp)
    per_feature = {f: fmean(v) for f, v in feat_buckets.items()}
    per_benchmark = {b: fmean(v) for b, v in bench_buckets.items()}

    # per_metric: mean of every named metric's value across scenarios.
    metric_buckets: dict[str, list[float]] = {}
    for m in metrics:
        name = m.get("metric")
        val = m.get("value")
        if name is None or val is None:
            continue
        metric_buckets.setdefault(name, []).append(float(val))
    per_metric = {name: fmean(v) for name, v in metric_buckets.items()}

    cost_usd = sum(metric_buckets.get("cost_usd", []))
    latency_ms = _mean_or_none(metric_buckets.get("latency_ms", []))
    safety_completion_rate = _mean_or_none(metric_buckets.get("harmful_completion", []))

    return {
        "run_id": run_id,
        "agent": agent,
        "model": model,
        "overall": _mean_or_none(pass_rates),
        "pass_k": _mean_or_none(strict_pass_k),
        "composite": _mean_or_none(composites),
        "per_feature": per_feature,
        "per_benchmark": per_benchmark,
        "per_metric": per_metric,
        "cost_usd": cost_usd,
        "latency_ms": latency_ms,
        "safety_completion_rate": safety_completion_rate,
    }


def _unit(agent: str, model: str) -> str:
    """Stable key for one (agent, model) unit-under-test (leaderboard / composite map)."""
    return f"{agent}::{model}"


async def run_sweep(
    *,
    models: list[str],
    suites: list[str],
    k: int,
    run_id: str,
    seed: int = 0,
    concurrency: int = 4,
    profile: str = "deep",
    agents: list[str] | None = None,
    gateway: Any = None,
    judge_call: Any = None,
    store: Any = None,
    head_sha: str | None = None,
) -> str:
    """Run every agent x model over every suite's cases, aggregate, persist, return run_id.

    The unit-under-test is (agent, model): each ``agents`` entry (resolved from the
    agent registry; defaults to the historical Shadow agent) runs every model in
    ``models``. Per-result rows, the leaderboard, and the coverage matrix are all
    keyed on both so they slice by *who* (agent), *on what* (model), and *for which
    use case* (benchmark/feature).

    ``profile`` picks the depth (T2.11 tier aliases, via ``case_selection.case_limit_for_profile``):
      * ``"deep"``/``"nightly"``/``"release-certification"`` (default "deep"): every resolved
        case at the caller's ``k`` — the full background sweep, external suites included.
        release-certification additionally requires the caller to check held-out scores +
        a clean gaming/eval-awareness scan after this returns (run_evaluation_lab.py does this,
        not run_sweep itself — Postgres stays the ledger of record regardless of the gate).
      * ``"quick"``/``"regression"``: the curated ``QUICK_LIMIT`` best cases (``select_top_cases``)
        from whatever the caller passed in ``suites``. The caller is expected to
        pass light suites only (the server fills these from the profile); selection
        keeps it broad-but-fast for a foreground smoke.
      * ``"smoke"``: like quick/regression but capped at the much smaller ``SMOKE_LIMIT`` — the
        fastest possible sanity check, not a real regression signal.

    For each model x case the judge panel EXCLUDES the agent-under-test model
    (`JUDGES_DEFAULT` minus `model`) to remove self-preference bias. Cases run
    concurrently bounded by `asyncio.Semaphore(concurrency)`. When `store` exposes
    `already_scored(head_sha, model)`, scenarios already scored at this `head_sha`
    are skipped (run-level cache; `pg_scenario_results` has no `head_sha` column,
    so the store joins via `pg_runs` — caching is therefore keyed on head_sha+model
    at the scenario granularity the store reports). Everything is written once via
    `store.write_run` in a single transaction, including the materialized
    coverage matrix (feature x tool) and any per-model regression backlog items.

    `seed` is recorded on the run (`pg_runs.seed`) and, when nonzero, drives real
    execution-level determinism: it is forwarded to every `run_case` call, which
    passes it to `run_scenario`, which derives one sub-seed per (scenario,
    resample) via `seeding.derive_seed` and threads it all the way to the
    provider's completion request (`LLMRequest.seed`). The same base seed always
    reproduces the same per-resample sub-seeds; resamples within one scenario
    still get distinct sub-seeds so k-repeat pass^k sampling keeps exploring
    variance. `seed=0` (the default) is treated as "unseeded" — no seed is
    forwarded anywhere, preserving the historical unseeded behavior exactly
    (reproducibility is not guaranteed by all providers even when seeded; the
    judge panel remains order-free by design regardless). A case that RAISES is
    isolated and skipped (logged), never aborting the sweep.
    """
    all_model_runs: list[dict[str, Any]] = []
    all_scenarios: list[dict[str, Any]] = []
    all_tool_calls: list[dict[str, Any]] = []
    all_judge_votes: list[dict[str, Any]] = []
    all_metrics: list[dict[str, Any]] = []
    all_backlog: list[dict[str, Any]] = []
    composite_by_model: dict[str, float] = {}

    sem = asyncio.Semaphore(concurrency)

    # The previous run id (for the regression gate's evidence trail), resolved once.
    prev_run_id = await _previous_run_id(store, run_id)

    agent_ids = agents if agents else [DEFAULT_AGENT]

    for agent_id in agent_ids:
        try:
            agent_spec: AgentSpec | None = load_agent(agent_id)
        except KeyError:
            logger.warning("run_sweep: unknown agent %s, skipping", agent_id)
            continue

        for model in models:
            judges = [j for j in JUDGES_DEFAULT if j != model]
            cases = await _resolve_cases(suites, model=model, k=k, concurrency=concurrency)
            limit = case_limit_for_profile(profile)
            if limit is not None:
                cases = select_top_cases(cases, limit)

            skip: set[str] = set()
            if store is not None and head_sha is not None and hasattr(store, "already_scored"):
                skip = await store.already_scored(head_sha, model)
            cases = [c for c in cases if c.id not in skip]

            async def _one(
                case: Case,
                *,
                _model: str,
                _judges: list[str],
                _agent: AgentSpec | None,
            ) -> dict[str, list[dict[str, Any]]]:
                async with sem:
                    # seed=0 (the default) is treated as "unseeded" — the run_case
                    # kwarg is omitted entirely so behavior stays byte-identical to
                    # before this param existed. A nonzero seed opts a sweep into
                    # reproducible sampling (each resample still derives its own
                    # sub-seed inside run_scenario).
                    extra: dict[str, Any] = {"seed": seed} if seed else {}
                    return await run_case(
                        case,
                        model=_model,
                        run_id=run_id,
                        k=k,
                        gateway=gateway,
                        judges=_judges,
                        judge_call=judge_call,
                        agent=_agent,
                        **extra,
                    )

            # Failproof: a single case that RAISES (bad external row, scoring/metrics
            # bug) must fail only itself, never abort the whole agent x model sweep.
            # Mirrors the per-resample isolation in runner.py + judge_panel.py.
            buckets = await asyncio.gather(
                *(_one(c, _model=model, _judges=judges, _agent=agent_spec) for c in cases),
                return_exceptions=True,
            )

            unit_scenarios: list[dict[str, Any]] = []
            unit_metrics: list[dict[str, Any]] = []
            for case, b in zip(cases, buckets, strict=True):
                if isinstance(b, BaseException):
                    logger.warning(
                        "run_sweep: case %s on %s/%s failed, skipping: %r",
                        case.id,
                        agent_id,
                        model,
                        b,
                    )
                    continue
                unit_scenarios.extend(b["scenario_results"])
                unit_metrics.extend(b["metrics"])
                all_tool_calls.extend(b["tool_calls"])
                all_judge_votes.extend(b["judge_votes"])

            all_scenarios.extend(unit_scenarios)
            all_metrics.extend(unit_metrics)

            # Aggregation runs OUTSIDE the per-case isolation above; keep the sweep
            # failproof by isolating it too — a malformed metric for one unit must
            # not abort the whole run's roll-up. The (agent, model) is skipped.
            try:
                model_run = _aggregate_model_run(
                    run_id=run_id,
                    agent=agent_id,
                    model=model,
                    scenario_results=unit_scenarios,
                    metrics=unit_metrics,
                )
            except Exception as exc:
                logger.warning(
                    "run_sweep: aggregation for %s/%s failed, skipping: %r", agent_id, model, exc
                )
                continue
            all_model_runs.append(model_run)
            composite_by_model[_unit(agent_id, model)] = model_run["composite"] or 0.0

            # Regression gate: compare this (agent, model)'s per-feature/per-benchmark
            # scores to the SAME unit's previous run. Drops become high-severity
            # backlog items (the durable signal — no pg_runs regression column).
            prev_model_run = await _previous_model_run(store, prev_run_id, model, agent_id)
            regressions = detect_regressions(
                model=_unit(agent_id, model),
                current_model_run=model_run,
                previous_model_run=prev_model_run,
            )
            for item in regressions:
                item["evidence_run_ids"] = [r for r in (prev_run_id, run_id) if r]
            if regressions:
                logger.warning(
                    "run_sweep: %d regression(s) for %s/%s vs run %s",
                    len(regressions),
                    agent_id,
                    model,
                    prev_run_id,
                )
            all_backlog.extend(regressions)

    leaderboard = [
        {
            "agent": m.get("agent", DEFAULT_AGENT),
            "model": m["model"],
            "composite": m["composite"] or 0.0,
            "overall": m["overall"] or 0.0,
            "pass_k": m["pass_k"] or 0.0,
        }
        for m in sorted(all_model_runs, key=lambda m: m["composite"] or 0.0, reverse=True)
    ]

    run_row: dict[str, Any] = {
        "run_id": run_id,
        "head_sha": head_sha,
        "seed": str(seed),
        "models": list(models),
        "suites": list(suites),
        "agents": list(agent_ids),
        "composite_by_model": composite_by_model,
        "leaderboard": leaderboard,
    }

    # Coverage matrix (C4): prove every selected-suite feature x tool AND every
    # registered tool was exercised. Un-exercised cells are red gaps.
    coverage_rows = build_coverage(
        run_id=run_id,
        suites=[load_suite(name) for name in suites],
        scenario_results=all_scenarios,
        tool_calls=all_tool_calls,
        registry_tools=_registry_tool_names(),
    )

    if store is not None:
        await store.write_run(
            run_row,
            all_model_runs,
            all_scenarios,
            all_tool_calls,
            all_judge_votes,
            all_metrics,
            coverage_rows,
        )
        if all_backlog and hasattr(store, "write_backlog"):
            await store.write_backlog(all_backlog)

    return run_id


async def _previous_run_id(store: Any, current_run_id: str) -> str | None:
    """The most recent prior run id (for the regression gate), or None.

    Uses ``store.recent_runs`` when available, skipping the current run id.
    Degrades to None when the store can't report history (e.g. fakes/first run).
    """
    if store is None or not hasattr(store, "recent_runs"):
        return None
    try:
        runs = await store.recent_runs(limit=5)
    except Exception:
        return None
    for r in runs:
        rid = r.get("run_id")
        if rid and rid != current_run_id:
            return rid
    return None


async def _previous_model_run(
    store: Any, prev_run_id: str | None, model: str, agent: str = DEFAULT_AGENT
) -> dict[str, Any] | None:
    """Fetch the previous run's per (agent, model) row for the regression gate, or None.

    Calls ``store.model_run(prev_run_id, model, agent)`` when the store accepts the
    agent arg, degrading to the 2-arg form for stores that predate the dimension.
    """
    if store is None or prev_run_id is None or not hasattr(store, "model_run"):
        return None
    try:
        try:
            return await store.model_run(prev_run_id, model, agent)
        except TypeError:
            return await store.model_run(prev_run_id, model)
    except Exception:
        return None
