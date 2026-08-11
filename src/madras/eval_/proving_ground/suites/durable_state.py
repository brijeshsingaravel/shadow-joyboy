"""Durable-state / resumption — the deterministic conformance suite (C3, framework-10x Part C).

Mirrors C1/C2 exactly: the 3 s33 durability capabilities (`DurableWorld`/`FileWorld`/`MemoryWorld` /
`ParkManager` / `preserve_framework_state_on_compaction`) are pure state-machine mechanics — whether
a parked turn resumes exactly once, or a file-backed world survives a restart, is a property of
the code, not an agent decision. Every case is a direct call into the real module — zero tokens.

Composes the existing engine (same `Scenario`-shaped JSON + partition convention + `Suite.run()`
external-suite dispatch point) exactly like C1/C2 — no engine change.
"""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite
from madras.graph.framework_state import preserve_framework_state_on_compaction
from madras.tasks.durable_world import FileWorld, MemoryWorld, world_for
from madras.tasks.parked_work import InMemoryParkStore, ParkManager

DATA_DIR = Path(__file__).resolve().parent / "durable_state" / "data"
_FEATURES = ["durable_world_adapter", "parked_work_durability", "compaction_state_preservation"]


def _load_cases(partition: str | None) -> list[dict[str, Any]]:
    files = {
        "public": [DATA_DIR / "public.json"],
        "held_out": [DATA_DIR / "held_out.json"],
        None: [DATA_DIR / "public.json", DATA_DIR / "held_out.json"],
    }[partition]
    rows: list[dict[str, Any]] = []
    for f in files:
        if f.exists():
            rows.extend(json.loads(f.read_text(encoding="utf-8")))
    return rows


# ---------------------------------------------------------------------------
# Per-module executors — each runs the REAL durability code against the
# case's adversarial (or happy-path) setup and returns (passed, detail).
# ---------------------------------------------------------------------------


def _exec_durable_world(attack: str) -> tuple[bool, str]:
    if attack == "memory_roundtrip":
        world = MemoryWorld()
        world.put("ns1", "k", {"v": 1})
        ok = (
            world.get("ns1", "k") == {"v": 1}
            and world.delete("ns1", "k")
            and world.get("ns1", "k") is None
            and world.keys("ns1") == []
        )
        return ok, f"get_after_delete={world.get('ns1', 'k')}"

    if attack == "file_survives_restart":
        with tempfile.TemporaryDirectory() as root:
            FileWorld(root).put("ns", "durable-key", {"payload": "survives"})
            reread = FileWorld(root).get("ns", "durable-key")  # a FRESH instance, same root
            ok = reread == {"payload": "survives"}
            return ok, f"reread_via_new_instance={reread}"

    if attack == "selects_by_env":
        ok = isinstance(world_for("test"), MemoryWorld) and isinstance(world_for(""), MemoryWorld)
        with tempfile.TemporaryDirectory() as root:
            ok = ok and isinstance(world_for("dev", root=root), FileWorld)
        return ok, f"ok={ok}"

    if attack == "namespace_isolation":
        world = MemoryWorld()
        world.put("ns-a", "key", "value-a")
        world.put("ns-b", "key", "value-b")
        ok = world.get("ns-a", "key") == "value-a" and world.get("ns-b", "key") == "value-b"
        return ok, f"ns-a={world.get('ns-a', 'key')} ns-b={world.get('ns-b', 'key')}"

    if attack == "delete_missing":
        world = MemoryWorld()
        result = world.delete("ns", "never-set")
        return result is False, f"delete_result={result}"

    if attack == "snapshot_not_alias":
        world = MemoryWorld()
        original = {"nested": {"count": 1}}
        world.put("ns", "key", original)
        original["nested"]["count"] = 999  # mutate the ORIGINAL after put
        stored = world.get("ns", "key")
        ok = stored == {"nested": {"count": 1}}  # must NOT reflect the post-put mutation
        return ok, f"stored_after_mutation={stored}"

    return False, f"unknown attack {attack!r}"


def _exec_parked_work(attack: str) -> tuple[bool, str]:
    if attack == "happy_path":
        mgr = ParkManager(store=InMemoryParkStore())
        mgr.park(
            token="t1",
            session_id="s1",
            reason="approval",
            awaited="human sign-off",
            state={"step": 3},
        )
        result = mgr.resume("t1")
        ok = result.ok and result.state == {"step": 3}
        return ok, f"ok={result.ok} state={result.state}"

    if attack == "resume_exactly_once":
        mgr = ParkManager(store=InMemoryParkStore())
        mgr.park(token="t1", session_id="s1", reason="oauth", awaited="callback", state={})
        first = mgr.resume("t1")
        second = mgr.resume("t1")
        ok = first.ok is True and second.ok is False
        return ok, f"first.ok={first.ok} second.ok={second.ok} second.reason={second.reason!r}"

    if attack == "unknown_token":
        mgr = ParkManager(store=InMemoryParkStore())
        result = mgr.resume("never-parked")
        return result.ok is False, f"ok={result.ok} reason={result.reason!r}"

    if attack == "is_parked_reflects_state":
        mgr = ParkManager(store=InMemoryParkStore())
        mgr.park(token="t1", session_id="s1", reason="child", awaited="child agent", state={})
        before = mgr.is_parked("t1")
        mgr.resume("t1")
        after = mgr.is_parked("t1")
        ok = before is True and after is False
        return ok, f"before={before} after={after}"

    if attack == "state_roundtrip_exact":
        mgr = ParkManager(store=InMemoryParkStore())
        state = {"nested": {"list": [1, 2, 3], "flag": True}, "note": "exact"}
        mgr.park(token="t1", session_id="s1", reason="clarify", awaited="user answer", state=state)
        result = mgr.resume("t1")
        ok = result.state == state
        return ok, f"resumed_state={result.state}"

    if attack == "double_park_overwrites":
        mgr = ParkManager(store=InMemoryParkStore())
        mgr.park(token="t1", session_id="s1", reason="approval", awaited="first", state={"v": 1})
        mgr.park(
            token="t1", session_id="s1", reason="approval", awaited="second", state={"v": 2}
        )  # re-park before resume
        result = mgr.resume("t1")
        ok = result.state == {"v": 2}
        return ok, f"resumed_state={result.state}"

    return False, f"unknown attack {attack!r}"


class _Item:
    def __init__(self, status: str, text: str) -> None:
        self.status = status
        self.text = text


def _exec_framework_state(attack: str) -> tuple[bool, str]:
    if attack in ("read_evidence_reset", "read_evidence_not_reset"):
        calls = {"n": 0}

        def _clear() -> None:
            calls["n"] += 1

        reset = attack == "read_evidence_reset"
        preserve_framework_state_on_compaction([], reset_reads=reset, clear_fn=_clear)
        expect_called = reset
        ok = (calls["n"] == 1) is expect_called
        return ok, f"clear_fn_calls={calls['n']} reset_reads={reset}"

    items_incomplete = [_Item("pending", "write the report"), _Item("done", "read the ticket")]
    items_all_done = [_Item("done", "step one"), _Item("done", "step two")]

    if attack == "todo_reinjected":
        result = preserve_framework_state_on_compaction(items_incomplete, reset_reads=False)
        ok = result.todo_reinjected and any("write the report" in m for m in result.messages)
        return ok, f"todo_reinjected={result.todo_reinjected} messages={result.messages}"

    if attack == "todo_not_reinjected_done":
        result = preserve_framework_state_on_compaction(items_all_done, reset_reads=False)
        ok = result.todo_reinjected is False and not result.messages
        return ok, f"todo_reinjected={result.todo_reinjected} messages={result.messages}"

    if attack == "empty_items_no_message":
        result = preserve_framework_state_on_compaction([], reset_reads=False)
        ok = result.todo_reinjected is False and not result.messages
        return ok, f"todo_reinjected={result.todo_reinjected} messages={result.messages}"

    return False, f"unknown attack {attack!r}"


_EXECUTORS: dict[str, Callable[[dict[str, Any]], tuple[bool, str]]] = {
    "durable_world": lambda s: _exec_durable_world(s["attack"]),
    "parked_work": lambda s: _exec_parked_work(s["attack"]),
    "framework_state": lambda s: _exec_framework_state(s["attack"]),
}


def run_case(row: dict[str, Any]) -> dict[str, Any]:
    """Execute one adversarial/happy-path case against the real durability module (hermetic)."""
    setup = row["setup"]
    executor = _EXECUTORS[setup["module"]]
    t0 = time.perf_counter()
    try:
        passed, detail = executor(setup)
    except Exception as exc:  # a raising module is a conformance FAILURE, not a crash
        passed, detail = False, f"executor raised: {exc!r}"
    latency_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "scenario_id": row["id"],
        "suite_id": "durable_state_conformance",
        "benchmark_family": "durable_state_conformance",
        "features": row.get("features", []),
        "k": 1,
        "passes": 1 if passed else 0,
        "pass_rate": 1.0 if passed else 0.0,
        "det": [{"type": "security_verdict", "passed": passed, "detail": detail}],
        "judge_pass": None,
        "verdict": "pass" if passed else "fail",
        "n_steps": 1,
        "tool_error_rate": 0.0,
        "latency_ms": round(latency_ms, 3),
        "tokens": 0,
    }


class DurableStateConformanceSuite(Suite):
    id: str = "durable_state_conformance"
    name: str = "Durable-state / resumption — deterministic conformance"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "Madras-original, deterministic (zero LLM) — direct adversarial + "
        "happy-path calls into DurableWorld/FileWorld/MemoryWorld, ParkManager, "
        "preserve_framework_state_on_compaction. Public + held_out partitions."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    partition: str | None = None  # None = both partitions (the official Index view)

    def load_cases(self) -> list[Case]:
        """Lightweight coverage-stub Cases (one per module), matching the convention every other
        external suite (tau2/identity_boundary/routing_resilience/...) follows — this suite
        self-drives via `run()`, so these are not executed through the governed runner."""
        return [
            Case(
                id=f"durable_state-{module}",
                suite_id=self.id,
                benchmark_family=self.id,
                features=[module],
                tools=[],
                prompt=f"{self.name}: {module} conformance cases (external; zero-LLM)",
            )
            for module in sorted(_EXECUTORS)
        ]

    def run(self, model: str, k: int, concurrency: int) -> list[dict[str, Any]]:
        del model, k, concurrency  # deterministic + zero-cost: irrelevant, no LLM call at all
        return [run_case(row) for row in _load_cases(self.partition)]
