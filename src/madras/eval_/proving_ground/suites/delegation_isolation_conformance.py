"""Delegation isolation conformance — Benchmark.md §6 axis #6 ("children can't escape;
summary-only; MAX_DEPTH=2 adversarial breach"), confirmed s42 to have zero suite.

Zero-LLM, deterministic — drives the REAL `graph/model_workflow.py::run_workflow()` (already
designed pure/injectable, per its own docstring) with a fake delegate callable and a fake
TurnBudget, proving: depth-bounding halts (does not raise) at MAX_DEPTH with a partial trace
intact; budget-exhaustion halts the same way; a failed subagent is recorded in the trace, not
fatal to the whole workflow (isolation — one child's failure can't crash the parent).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

_FEATURES = ["delegation_isolation"]


@dataclass
class _FakeBudget:
    remaining: int

    def can_spawn(self, n: int) -> bool:
        return self.remaining >= n

    def charge(self, n: int) -> None:
        self.remaining -= n


def _case_depth_bound_halts_not_raises() -> tuple[bool, str]:
    from madras.graph.model_workflow import ModelWorkflow, WorkflowStep, run_workflow

    workflow = ModelWorkflow(steps=[WorkflowStep(task="t1"), WorkflowStep(task="t2")])
    result = run_workflow(
        workflow,
        delegate=lambda task, role: f"done:{task}",
        budget=_FakeBudget(remaining=10),
        max_depth=2,
        depth=2,  # already AT the bound — an adversarial breach attempt
    )
    ok = result.halted is True and "max delegation depth" in result.reason and result.results == []
    return ok, f"halted={result.halted} reason={result.reason!r}"


def _case_budget_exhaustion_halts_with_partial_trace() -> tuple[bool, str]:
    from madras.graph.model_workflow import ModelWorkflow, WorkflowStep, run_workflow

    workflow = ModelWorkflow(
        steps=[
            WorkflowStep(task="t1", label="a"),
            WorkflowStep(task="t2", label="b"),
            WorkflowStep(task="t3", label="c"),
        ]
    )
    result = run_workflow(
        workflow,
        delegate=lambda task, role: f"done:{task}",
        budget=_FakeBudget(remaining=1),
        max_depth=2,
        depth=0,
    )
    ok = (
        result.halted is True
        and result.reason == "turn budget exhausted"
        and len(result.results) == 1  # only the first step ran before the budget ran out
        and any(t.reason == "budget exhausted" for t in result.trace)
    )
    return ok, f"halted={result.halted} results={len(result.results)} trace={len(result.trace)}"


def _case_failed_child_is_isolated_not_fatal() -> tuple[bool, str]:
    from madras.graph.model_workflow import ModelWorkflow, WorkflowStep, run_workflow

    def flaky_delegate(task: str, role: str) -> str:
        if task == "boom":
            raise RuntimeError("child subagent crashed")
        return f"done:{task}"

    workflow = ModelWorkflow(
        steps=[
            WorkflowStep(task="ok1", label="a"),
            WorkflowStep(task="boom", label="b"),
            WorkflowStep(task="ok2", label="c"),
        ]
    )
    result = run_workflow(
        workflow,
        delegate=flaky_delegate,
        budget=_FakeBudget(remaining=10),
        max_depth=2,
        depth=0,
    )
    # A failed child must not halt the whole workflow — siblings still run (isolation).
    ok = (
        result.halted is False
        and len(result.trace) == 3
        and result.trace[1].ok is False
        and "crashed" in result.trace[1].reason
        and result.trace[2].ok is True
    )
    return ok, f"trace_oks={[t.ok for t in result.trace]}"


def _case_no_depth_bound_no_budget_runs_clean() -> tuple[bool, str]:
    from madras.graph.model_workflow import ModelWorkflow, WorkflowStep, run_workflow

    workflow = ModelWorkflow(steps=[WorkflowStep(task="t1"), WorkflowStep(task="t2")])
    result = run_workflow(
        workflow,
        delegate=lambda task, role: f"done:{task}",
        budget=_FakeBudget(remaining=10),
        max_depth=2,
        depth=0,
    )
    ok = result.halted is False and len(result.results) == 2 and all(t.ok for t in result.trace)
    return ok, f"halted={result.halted} results={result.results}"


_EXECUTORS: dict[str, Any] = {
    "depth_bound_halts_not_raises": _case_depth_bound_halts_not_raises,
    "budget_exhaustion_halts_with_partial_trace": _case_budget_exhaustion_halts_with_partial_trace,
    "failed_child_is_isolated_not_fatal": _case_failed_child_is_isolated_not_fatal,
    "no_depth_bound_no_budget_runs_clean": _case_no_depth_bound_no_budget_runs_clean,
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
        "suite_id": "delegation_isolation_conformance",
        "benchmark_family": "delegation_isolation_conformance",
        "features": _FEATURES,
        "k": 1,
        "passes": 1 if passed else 0,
        "pass_rate": 1.0 if passed else 0.0,
        "det": [{"type": "isolation_verdict", "passed": passed, "detail": detail}],
        "judge_pass": None,
        "verdict": "pass" if passed else "fail",
        "n_steps": 1,
        "tool_error_rate": 0.0,
        "latency_ms": round(latency_ms, 3),
        "tokens": 0,
    }


class DelegationIsolationConformanceSuite(Suite):
    id: str = "delegation_isolation_conformance"
    name: str = "Delegation isolation conformance — deterministic"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "Madras-original, deterministic (zero LLM) — direct calls into the REAL "
        "graph/model_workflow.py::run_workflow() (pure/injectable by design) with a fake "
        "delegate + TurnBudget. Fills Benchmark.md §6 axis #6 (delegation isolation), "
        "confirmed s42 to have zero suite."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id=f"delegation_isolation_conformance-{case_id}",
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
