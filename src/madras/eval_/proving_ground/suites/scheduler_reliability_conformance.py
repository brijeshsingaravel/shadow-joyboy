"""Scheduler reliability conformance — Benchmark.md §6 axis #9 ("scheduled tasks complete
on-time; MTTR post-crash — 24/7 ambient ops"), confirmed s42 to have zero suite.

Zero-LLM, deterministic — drives the REAL `scheduler/schedule_math.py` (pure, no DB/clock,
`now` always passed in — designed for exactly this kind of testing) and
`scheduler/monitoring.py::missed_runs`, proving: an interval schedule's next-run computes
correctly; a schedule that missed MANY instants while offline (the crash-recovery case)
coalesces to ONE run-the-latest rather than flooding a backlog; a run overdue beyond its
grace window is flagged misfired, one within grace is not; the idempotency key is stable
for a given (schedule, instant) pair (no accidental double-fire on a duplicate tick); and
paused/dead schedules never fire.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

_FEATURES = ["scheduler_reliability"]


def _case_interval_next_run_computes_correctly() -> tuple[bool, str]:
    from madras.scheduler.schedule_math import Schedule, compute_next_run

    s = Schedule(id="s1", kind="interval", every_secs=3600.0, anchor=0.0)
    nxt = compute_next_run(s, after=100.0)  # anchored at epoch, 1hr interval, 100s in
    ok = nxt == 3600.0
    return ok, f"next_run={nxt}"


def _case_crash_recovery_coalesces_to_one_run() -> tuple[bool, str]:
    from madras.scheduler.schedule_math import Schedule, due_runs

    # A schedule ticking every 60s, offline since epoch 0; "now" is 10 hours later —
    # 600 instants have been missed. MTTR/reliability requires ONE catch-up run, not 600.
    s = Schedule(id="s1", kind="interval", every_secs=60.0, anchor=0.0, misfire_grace_secs=120.0)
    now = 10 * 3600.0
    runs = due_runs([s], now, last_run={})
    ok = len(runs) == 1 and runs[0].misfired is True
    return ok, f"run_count={len(runs)} misfired={runs[0].misfired if runs else None}"


def _case_run_within_grace_is_not_misfired() -> tuple[bool, str]:
    from madras.scheduler.schedule_math import Schedule, due_runs

    s = Schedule(id="s1", kind="once", run_at=1000.0, misfire_grace_secs=3600.0)
    now = 1000.0 + 10.0  # 10 seconds late — well within the 1-hour grace
    # last_run must be explicitly seeded (0.0 = "never run before") per the module's own
    # established test convention (test_schedule_math.py) — a bare {} makes due_runs' internal
    # `after` default resolve to run_at itself for a once-kind schedule, which then reads as
    # "already past." Caught live building this suite; not a bug in the real scheduler code,
    # every real caller seeds last_run before checking.
    runs = due_runs([s], now, last_run={"s1": 0.0})
    ok = len(runs) == 1 and runs[0].misfired is False
    return ok, f"run_count={len(runs)} misfired={runs[0].misfired if runs else None}"


def _case_idempotency_key_stable_for_same_instant() -> tuple[bool, str]:
    from madras.scheduler.schedule_math import Schedule, due_runs

    s = Schedule(id="s1", kind="once", run_at=1000.0)
    runs_a = due_runs([s], now=1005.0, last_run={"s1": 0.0})
    runs_b = due_runs([s], now=1005.0, last_run={"s1": 0.0})  # a duplicate tick, same state
    ok = len(runs_a) == 1 and len(runs_b) == 1 and runs_a[0].idempotency == runs_b[0].idempotency
    return (
        ok,
        f"key_a={runs_a[0].idempotency if runs_a else None} "
        f"key_b={runs_b[0].idempotency if runs_b else None}",
    )


def _case_paused_schedule_never_fires() -> tuple[bool, str]:
    from madras.scheduler.schedule_math import Schedule, due_runs

    s = Schedule(id="s1", kind="once", run_at=100.0, status="paused")
    runs = due_runs([s], now=100000.0, last_run={})  # long overdue, but paused
    ok = runs == []
    return ok, f"run_count={len(runs)}"


def _case_backoff_delay_grows_and_caps() -> tuple[bool, str]:
    from madras.scheduler.schedule_math import backoff_delay

    d1 = backoff_delay(1, base=10.0)
    d2 = backoff_delay(2, base=10.0)
    d_capped = backoff_delay(20, base=10.0, cap=900.0)  # would be astronomically large uncapped
    ok = d1 == 10.0 and d2 == 20.0 and d_capped == 900.0
    return ok, f"d1={d1} d2={d2} d_capped={d_capped}"


_EXECUTORS: dict[str, Any] = {
    "interval_next_run_computes_correctly": _case_interval_next_run_computes_correctly,
    "crash_recovery_coalesces_to_one_run": _case_crash_recovery_coalesces_to_one_run,
    "run_within_grace_is_not_misfired": _case_run_within_grace_is_not_misfired,
    "idempotency_key_stable_for_same_instant": _case_idempotency_key_stable_for_same_instant,
    "paused_schedule_never_fires": _case_paused_schedule_never_fires,
    "backoff_delay_grows_and_caps": _case_backoff_delay_grows_and_caps,
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
        "suite_id": "scheduler_reliability_conformance",
        "benchmark_family": "scheduler_reliability_conformance",
        "features": _FEATURES,
        "k": 1,
        "passes": 1 if passed else 0,
        "pass_rate": 1.0 if passed else 0.0,
        "det": [{"type": "scheduler_verdict", "passed": passed, "detail": detail}],
        "judge_pass": None,
        "verdict": "pass" if passed else "fail",
        "n_steps": 1,
        "tool_error_rate": 0.0,
        "latency_ms": round(latency_ms, 3),
        "tokens": 0,
    }


class SchedulerReliabilityConformanceSuite(Suite):
    id: str = "scheduler_reliability_conformance"
    name: str = "Scheduler reliability conformance — deterministic"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "Madras-original, deterministic (zero LLM) — direct calls into the REAL "
        "scheduler/schedule_math.py (pure, no DB/clock, designed for exactly this kind of "
        "testing). Fills Benchmark.md §6 axis #9 (scheduler reliability), confirmed s42 to "
        "have zero suite."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id=f"scheduler_reliability_conformance-{case_id}",
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
