"""Verify-pool robustness conformance — Benchmark.md §6 axis #10 ("model-diverse judge panel
holds when any verifier is dropped"), confirmed s42 to have zero suite — the last of the 5
Framework proprietary axes found missing.

Zero-LLM, deterministic — drives the REAL `eval_/proving_ground/judge_panel.py::judge_panel()`
(already designed injectable/testable per its own docstring: "call and meta_call are injected
so the protocol is testable without network"), proving: a judge that raises is recorded as a
fail vote rather than crashing the panel (the core robustness claim); the supermajority tally
is correct in both directions; a close split triggers meta-adjudication whose verdict is
authoritative; a down meta-judge falls back to the panel tally rather than crashing; and clear
consensus is left untouched even when a meta_call is available.
"""

from __future__ import annotations

import time
from typing import Any, Literal, cast

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

_FEATURES = ["verify_pool_robustness"]

_JUDGES = ["judge_a", "judge_b", "judge_c", "judge_d", "judge_e"]


def _fixed_call(votes_by_judge: dict[str, dict[str, Any] | str]):
    async def call(name: str, rubric: str, task: str, trajectory: dict[str, Any]) -> dict[str, Any]:
        vote = votes_by_judge[name]
        if vote == "RAISE":
            raise RuntimeError(f"{name} is down")
        return cast("dict[str, Any]", vote)

    return call


def _case_a_raising_judge_never_crashes_the_panel() -> tuple[bool, str]:
    import asyncio

    from madras.eval_.proving_ground.judge_panel import judge_panel

    votes: dict[str, dict[str, Any] | str] = {
        "judge_a": {"pass": True, "score": 1.0},
        "judge_b": {"pass": True, "score": 1.0},
        "judge_c": {"pass": True, "score": 1.0},
        "judge_d": "RAISE",  # this judge is down
        "judge_e": {"pass": True, "score": 1.0},
    }
    try:
        verdict = asyncio.run(
            judge_panel(
                "rubric",
                "task",
                {"answer": "x"},
                judges=_JUDGES,
                call=_fixed_call(votes),
                threshold=4,
            )
        )
    except Exception as exc:
        return (
            False,
            f"judge_panel raised despite the injected failure being a single judge: {exc!r}",
        )
    # 4 real passes + 1 recorded-fail-on-raise = n_pass=4, still clears threshold=4.
    ok = verdict.n_pass == 4 and verdict.passed is True
    down_vote = next(v for v in verdict.votes if v["judge"] == "judge_d")
    ok = ok and down_vote["pass"] is False and "judge_d is down" in down_vote["reason"]
    return ok, f"n_pass={verdict.n_pass} passed={verdict.passed} down_vote={down_vote}"


def _case_supermajority_fails_below_threshold() -> tuple[bool, str]:
    import asyncio

    from madras.eval_.proving_ground.judge_panel import judge_panel

    votes: dict[str, dict[str, Any] | str] = {n: {"pass": False, "score": 0.0} for n in _JUDGES}
    votes["judge_a"] = {"pass": True, "score": 1.0}  # only 1 of 5 passes
    verdict = asyncio.run(
        judge_panel(
            "rubric",
            "task",
            {"answer": "x"},
            judges=_JUDGES,
            call=_fixed_call(votes),
            threshold=4,
        )
    )
    ok = verdict.n_pass == 1 and verdict.passed is False
    return ok, f"n_pass={verdict.n_pass} passed={verdict.passed}"


def _case_close_split_triggers_meta_adjudication() -> tuple[bool, str]:
    import asyncio

    from madras.eval_.proving_ground.judge_panel import judge_panel

    # 3 of 5 pass — one vote below threshold=4, a close split per is_split's definition.
    votes: dict[str, dict[str, Any] | str] = {n: {"pass": False, "score": 0.0} for n in _JUDGES}
    for n in ("judge_a", "judge_b", "judge_c"):
        votes[n] = {"pass": True, "score": 1.0}

    async def meta_call(
        rubric: str, task: str, trajectory: dict[str, Any], panel_votes: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {"pass": True, "reason": "meta overrides on dissent review"}

    verdict = asyncio.run(
        judge_panel(
            "rubric",
            "task",
            {"answer": "x"},
            judges=_JUDGES,
            call=_fixed_call(votes),
            threshold=4,
            meta_call=meta_call,
        )
    )
    ok = verdict.meta_used is True and verdict.passed is True and verdict.n_pass == 3
    return ok, f"meta_used={verdict.meta_used} passed={verdict.passed} n_pass={verdict.n_pass}"


def _case_meta_judge_down_falls_back_to_panel_tally() -> tuple[bool, str]:
    import asyncio

    from madras.eval_.proving_ground.judge_panel import judge_panel

    votes: dict[str, dict[str, Any] | str] = {n: {"pass": False, "score": 0.0} for n in _JUDGES}
    for n in ("judge_a", "judge_b", "judge_c"):
        votes[n] = {"pass": True, "score": 1.0}

    async def dead_meta_call(
        rubric: str, task: str, trajectory: dict[str, Any], panel_votes: list[dict[str, Any]]
    ) -> dict[str, Any]:
        raise RuntimeError("meta backend down")

    try:
        verdict = asyncio.run(
            judge_panel(
                "rubric",
                "task",
                {"answer": "x"},
                judges=_JUDGES,
                call=_fixed_call(votes),
                threshold=4,
                meta_call=dead_meta_call,
            )
        )
    except Exception as exc:
        return False, f"judge_panel raised despite meta-judge failure being handled: {exc!r}"
    # Falls back to the raw tally: 3 < threshold=4 -> fails, meta_used stays False.
    ok = verdict.meta_used is False and verdict.passed is False and verdict.n_pass == 3
    return ok, f"meta_used={verdict.meta_used} passed={verdict.passed}"


def _case_clear_consensus_left_untouched_by_meta() -> tuple[bool, str]:
    import asyncio

    from madras.eval_.proving_ground.judge_panel import judge_panel

    votes: dict[str, dict[str, Any] | str] = {
        n: {"pass": True, "score": 1.0} for n in _JUDGES
    }  # 5/5 unanimous pass

    async def meta_call(
        rubric: str, task: str, trajectory: dict[str, Any], panel_votes: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {"pass": False, "reason": "should never be consulted"}

    verdict = asyncio.run(
        judge_panel(
            "rubric",
            "task",
            {"answer": "x"},
            judges=_JUDGES,
            call=_fixed_call(votes),
            threshold=4,
            meta_call=meta_call,
        )
    )
    ok = verdict.meta_used is False and verdict.passed is True and verdict.n_pass == 5
    return ok, f"meta_used={verdict.meta_used} passed={verdict.passed} n_pass={verdict.n_pass}"


_EXECUTORS: dict[str, Any] = {
    "a_raising_judge_never_crashes_the_panel": _case_a_raising_judge_never_crashes_the_panel,
    "supermajority_fails_below_threshold": _case_supermajority_fails_below_threshold,
    "close_split_triggers_meta_adjudication": _case_close_split_triggers_meta_adjudication,
    "meta_judge_down_falls_back_to_panel_tally": _case_meta_judge_down_falls_back_to_panel_tally,
    "clear_consensus_left_untouched_by_meta": _case_clear_consensus_left_untouched_by_meta,
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
        "suite_id": "verify_pool_robustness_conformance",
        "benchmark_family": "verify_pool_robustness_conformance",
        "features": _FEATURES,
        "k": 1,
        "passes": 1 if passed else 0,
        "pass_rate": 1.0 if passed else 0.0,
        "det": [{"type": "verify_pool_verdict", "passed": passed, "detail": detail}],
        "judge_pass": None,
        "verdict": "pass" if passed else "fail",
        "n_steps": 1,
        "tool_error_rate": 0.0,
        "latency_ms": round(latency_ms, 3),
        "tokens": 0,
    }


class VerifyPoolRobustnessConformanceSuite(Suite):
    id: str = "verify_pool_robustness_conformance"
    name: str = "Verify-pool robustness conformance — deterministic"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "Madras-original, deterministic (zero LLM) — direct calls into the REAL "
        "eval_/proving_ground/judge_panel.py::judge_panel() (already injectable/testable by "
        "its own design). Fills Benchmark.md §6 axis #10 (verify-pool robustness), confirmed "
        "s42 to have zero suite — the last of the 5 Framework proprietary axes found missing."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id=f"verify_pool_robustness_conformance-{case_id}",
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
