"""GEPA-evolve conformance — the Compiler's optimize→verify loop, confirmed s42 (§12k) to
have unit tests (tests/test_optimizer/test_evolve.py) but no Proving Ground suite measuring
whether the real optimize.evolve() loop genuinely converges/improves, as opposed to that
being merely inferred from unit-test passage.

Zero-LLM, deterministic — drives the REAL `optimizer/evolve.py::evolve()` with injected
deterministic `evaluate`/`reflect` fakes (the module's own designed seam — hermetically
testable by construction, per its own docstring), proving: a genuinely better reflection
produces measured positive lift; a worse reflection never regresses the returned proposal
below baseline (evolve keeps `best`, never accepts a regression); a reflection that returns
unchanged text is skipped rather than wasting a round; every returned proposal is
propose-not-dispose (`approved=False`) regardless of lift; and the underlying Pareto-frontier
selection genuinely keeps non-dominated candidates rather than collapsing to a single winner.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

_FEATURES = ["gepa_evolve"]


def _case_evolve_improves_over_baseline_when_better_reflection_exists() -> tuple[bool, str]:
    from madras.optimizer.evolve import evolve
    from madras.optimizer.models import Target

    async def evaluate(text: str) -> dict[str, float]:
        return {"i1": 1.0} if text == "better prompt" else {"i1": 0.2}

    async def reflect(text: str, failures: list[str]) -> str:
        del text, failures
        return "better prompt"

    target = Target(kind="prompt", id="t1", current_text="baseline prompt")
    proposal = asyncio.run(evolve(target, evaluate=evaluate, reflect=reflect, rounds=2))
    ok = proposal.improved and proposal.lift > 0.0 and proposal.new_text == "better prompt"
    return ok, f"lift={proposal.lift} new_text={proposal.new_text!r} improved={proposal.improved}"


def _case_evolve_never_regresses_below_baseline() -> tuple[bool, str]:
    from madras.optimizer.evolve import evolve
    from madras.optimizer.models import Target

    async def evaluate(text: str) -> dict[str, float]:
        return {"i1": 0.8} if text == "baseline prompt" else {"i1": 0.1}

    async def reflect(text: str, failures: list[str]) -> str:
        del text, failures
        return "worse prompt"

    target = Target(kind="prompt", id="t1", current_text="baseline prompt")
    proposal = asyncio.run(evolve(target, evaluate=evaluate, reflect=reflect, rounds=3))
    ok = proposal.new_score >= proposal.baseline_score and not proposal.improved
    return (
        ok,
        f"baseline={proposal.baseline_score} new={proposal.new_score} improved={proposal.improved}",
    )


def _case_evolve_skips_round_when_reflection_is_unchanged() -> tuple[bool, str]:
    from madras.optimizer.evolve import evolve
    from madras.optimizer.models import Target

    calls = {"evaluate": 0}

    async def evaluate(text: str) -> dict[str, float]:
        calls["evaluate"] += 1
        return {"i1": 0.5}

    async def reflect(text: str, failures: list[str]) -> str:
        del failures
        return text  # always echoes back the same text — never actually reflects

    target = Target(kind="prompt", id="t1", current_text="baseline prompt")
    asyncio.run(evolve(target, evaluate=evaluate, reflect=reflect, rounds=3))
    # evaluate() must be called exactly once (the baseline) — every round's unchanged
    # reflection should be skipped, not re-evaluated as a "new" candidate.
    ok = calls["evaluate"] == 1
    return ok, f"evaluate_call_count={calls['evaluate']}"


def _case_proposal_is_always_gated_not_auto_applied() -> tuple[bool, str]:
    from madras.optimizer.evolve import evolve
    from madras.optimizer.models import Target

    async def evaluate(text: str) -> dict[str, float]:
        return {"i1": 1.0} if text == "great prompt" else {"i1": 0.0}

    async def reflect(text: str, failures: list[str]) -> str:
        del text, failures
        return "great prompt"

    target = Target(kind="prompt", id="t1", current_text="baseline prompt")
    proposal = asyncio.run(evolve(target, evaluate=evaluate, reflect=reflect, rounds=1))
    ok = proposal.approved is False and proposal.lift > 0.0
    return ok, f"approved={proposal.approved} lift={proposal.lift}"


def _case_pareto_frontier_keeps_non_dominated_candidates() -> tuple[bool, str]:
    from madras.optimizer.evolve import pareto
    from madras.optimizer.models import Candidate

    # A wins on i1, B wins on i2 — neither dominates the other, both must survive.
    a = Candidate("A", {"i1": 1.0, "i2": 0.2})
    b = Candidate("B", {"i1": 0.2, "i2": 1.0})
    # C is dominated by A on every instance — must be dropped.
    c = Candidate("C", {"i1": 0.5, "i2": 0.1})
    frontier = pareto([a, b, c])
    texts = {cand.text for cand in frontier}
    ok = texts == {"A", "B"}
    return ok, f"frontier={sorted(texts)}"


_EXECUTORS: dict[str, Any] = {
    "evolve_improves_over_baseline_when_better_reflection_exists": (
        _case_evolve_improves_over_baseline_when_better_reflection_exists
    ),
    "evolve_never_regresses_below_baseline": _case_evolve_never_regresses_below_baseline,
    "evolve_skips_round_when_reflection_is_unchanged": (
        _case_evolve_skips_round_when_reflection_is_unchanged
    ),
    "proposal_is_always_gated_not_auto_applied": _case_proposal_is_always_gated_not_auto_applied,
    "pareto_frontier_keeps_non_dominated_candidates": (
        _case_pareto_frontier_keeps_non_dominated_candidates
    ),
}


def _run_case(case_id: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        passed, detail = _EXECUTORS[case_id]()
    except Exception as exc:
        passed, detail = False, f"executor raised: {exc!r}"
    latency_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "scenario_id": case_id,
        "suite_id": "gepa_evolve_conformance",
        "benchmark_family": "gepa_evolve_conformance",
        "features": _FEATURES,
        "k": 1,
        "passes": 1 if passed else 0,
        "pass_rate": 1.0 if passed else 0.0,
        "det": [{"type": "gepa_evolve_verdict", "passed": passed, "detail": detail}],
        "judge_pass": None,
        "verdict": "pass" if passed else "fail",
        "n_steps": 1,
        "tool_error_rate": 0.0,
        "latency_ms": round(latency_ms, 3),
        "tokens": 0,
    }


class GepaEvolveConformanceSuite(Suite):
    id: str = "gepa_evolve_conformance"
    name: str = "GEPA-evolve conformance — deterministic"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "Madras-original, deterministic (zero LLM) — direct calls into the REAL "
        "optimizer/evolve.py::evolve() with injected deterministic evaluate/reflect fakes "
        "(the module's own designed testable seam). Fills the confirmed s42 gap: the "
        "compile->verify->optimize loop's convergence was previously only unit-tested, "
        "never measured as a Proving Ground suite."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id=f"gepa_evolve_conformance-{case_id}",
                suite_id=self.id,
                benchmark_family=self.id,
                features=list(_FEATURES),
                tools=[],
                prompt=f"[conformance] {case_id}",
                setup={},
                checks=[],
            )
            for case_id in _EXECUTORS
        ]

    def run(self, model: str, k: int, concurrency: int) -> list[dict[str, Any]]:
        del model, k, concurrency
        return [_run_case(case_id) for case_id in _EXECUTORS]
