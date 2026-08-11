"""Tool-reliability conformance — the deterministic conformance suite for the Tools subsystem.

benchmark-design.md §12f identified a gap: BFCL/mcptoolbench/mcpuniverse/mcpatlas cover generic
function-calling *mechanics* well, but no suite tests the long tail of individual builtin tools'
own reliability contract (malformed input handling, path-escape protection, etc.) — a different
failure mode than "does the model call the right function signature."

Two case classes, both zero-LLM and driving REAL code:
1. **Registry-wide contract conformance** — importing ``tools.builtin`` self-registers every
   built-in tool into ``tools.registry.REGISTRY`` (Hermes pattern); every registered tool must
   carry a well-formed name/toolset/description/JSON-schema parameters and a real callable —
   checked across all 33+ tools at once, not a hand-picked few.
2. **`file_read` behavioral reliability** (the one tool whose contract was read in full, s42) —
   path-traversal is actually blocked, missing/invalid input degrades to a clear ``ToolResult``
   error rather than raising, matching the module's own documented behavior.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Coroutine
from typing import Any, Literal, cast

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

_FEATURES = ["tool_reliability"]


def _case_registry_wide_contract() -> tuple[bool, str]:
    import madras.tools.builtin  # noqa: F401  — self-registers every builtin tool  # pyright: ignore[reportUnusedImport]
    from madras.tools.registry import REGISTRY

    tools = REGISTRY.all()
    if len(tools) < 20:
        return False, f"expected 20+ registered builtin tools, found {len(tools)}"
    bad: list[str] = []
    for t in tools:
        if not t.name or not t.toolset or not t.description:
            bad.append(f"{t.name or '?'}: missing name/toolset/description")
            continue
        if t.parameters.get("type") != "object":
            bad.append(f"{t.name}: parameters is not a JSON-object schema")
            continue
        if not callable(t.run):
            bad.append(f"{t.name}: run is not callable")
    ok = not bad
    return ok, f"{len(tools)} tools checked" if ok else "; ".join(bad)


def _case_file_read_blocks_path_traversal() -> tuple[bool, str]:
    from madras.tools.builtin.files import ToolResult, file_read

    coro = cast("Coroutine[Any, Any, ToolResult]", file_read({"path": "../../../../etc/passwd"}))
    result = asyncio.run(coro)
    ok = result.ok is False and "escape" in (result.error or "").lower()
    return ok, f"ok={result.ok} error={result.error!r}"


def _case_file_read_missing_arg_degrades_cleanly() -> tuple[bool, str]:
    from madras.tools.builtin.files import ToolResult, file_read

    coro = cast("Coroutine[Any, Any, ToolResult]", file_read({}))
    result = asyncio.run(coro)
    ok = result.ok is False and "required" in (result.error or "").lower()
    return ok, f"ok={result.ok} error={result.error!r}"


def _case_file_read_nonexistent_file_degrades_cleanly() -> tuple[bool, str]:
    from madras.tools.builtin.files import ToolResult, file_read

    coro = cast(
        "Coroutine[Any, Any, ToolResult]",
        file_read({"path": "definitely_does_not_exist_9f8e7d.txt"}),
    )
    result = asyncio.run(coro)
    ok = result.ok is False and result.error is not None
    return ok, f"ok={result.ok} error={result.error!r}"


_EXECUTORS: dict[str, Any] = {
    "registry_wide_contract": _case_registry_wide_contract,
    "file_read_blocks_path_traversal": _case_file_read_blocks_path_traversal,
    "file_read_missing_arg_degrades_cleanly": _case_file_read_missing_arg_degrades_cleanly,
    "file_read_nonexistent_file_degrades_cleanly": (
        _case_file_read_nonexistent_file_degrades_cleanly
    ),
}


def _run_case(case_id: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        passed, detail = _EXECUTORS[case_id]()
    except Exception as exc:  # a raising executor is a conformance FAILURE, not a crash
        passed, detail = False, f"executor raised: {exc!r}"
    latency_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "scenario_id": case_id,
        "suite_id": "tool_reliability_conformance",
        "benchmark_family": "tool_reliability_conformance",
        "features": _FEATURES,
        "k": 1,
        "passes": 1 if passed else 0,
        "pass_rate": 1.0 if passed else 0.0,
        "det": [{"type": "tool_verdict", "passed": passed, "detail": detail}],
        "judge_pass": None,
        "verdict": "pass" if passed else "fail",
        "n_steps": 1,
        "tool_error_rate": 0.0,
        "latency_ms": round(latency_ms, 3),
        "tokens": 0,
    }


class ToolReliabilityConformanceSuite(Suite):
    id: str = "tool_reliability_conformance"
    name: str = "Tool-reliability conformance — deterministic"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "Madras-original, deterministic (zero LLM) — a registry-wide contract check over every "
        "self-registered builtin tool + real behavioral checks against tools/builtin/files.py. "
        "Fills the Tools subsystem gap confirmed in benchmark-design.md §12f."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id=f"tool_reliability_conformance-{case_id}",
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
        del model, k, concurrency  # deterministic + zero-cost: irrelevant, no LLM call at all
        return [_run_case(case_id) for case_id in _EXECUTORS]
