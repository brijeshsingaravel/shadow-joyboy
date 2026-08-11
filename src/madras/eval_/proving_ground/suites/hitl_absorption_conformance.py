"""HITL answer-absorption conformance — Benchmark.md §6 axis #8 ("user clarification improves
downstream + is stored/retrieved"), confirmed s42 to have zero suite.

Zero-LLM, deterministic — drives the REAL `tools/builtin/clarify.py::clarify()` with mocked
context objects, proving: a headless call (no interactive channel) degrades cleanly instead of
hanging; an empty answer is rejected; a genuine answer triggers real absorption into the memory
fabric (`_absorb()` actually calls `fabric.remember()` with the question/answer — verified via a
spy, not assumed); and an absorption failure is best-effort — it never blocks the tool's success
result (matches the module's own "never blocks the result" design intent).
"""

from __future__ import annotations

import time
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, Literal, cast
from unittest.mock import AsyncMock, patch

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

_FEATURES = ["hitl_absorption"]


@dataclass
class _FakeClarifyCtx:
    ask: Any


def _case_headless_degrades_cleanly() -> tuple[bool, str]:
    import asyncio

    from madras.tools.builtin.clarify import ToolResult, clarify

    with patch("madras.tools.builtin.clarify.get_clarify_ctx", return_value=None):
        coro = cast("Coroutine[Any, Any, ToolResult]", clarify({"question": "Which region?"}))
        result = asyncio.run(coro)
    ok = result.ok is False and "NO-USER" in (result.error or "")
    return ok, f"ok={result.ok} error={result.error!r}"


def _case_empty_answer_rejected_no_absorption_attempted() -> tuple[bool, str]:
    import asyncio

    from madras.tools.builtin.clarify import ToolResult, clarify

    ctx = _FakeClarifyCtx(ask=AsyncMock(return_value="   "))
    with (
        patch("madras.tools.builtin.clarify.get_clarify_ctx", return_value=ctx),
        patch("madras.tools.builtin.clarify._absorb", new=AsyncMock()) as absorb_mock,
    ):
        coro = cast("Coroutine[Any, Any, ToolResult]", clarify({"question": "Which region?"}))
        result = asyncio.run(coro)
        ok = result.ok is False and not absorb_mock.called
        return ok, f"ok={result.ok} absorb_called={absorb_mock.called}"


def _case_real_answer_triggers_real_absorption() -> tuple[bool, str]:
    import asyncio

    from madras.tools.builtin.clarify import ToolResult, clarify

    ctx = _FakeClarifyCtx(ask=AsyncMock(return_value="us-east"))
    with (
        patch("madras.tools.builtin.clarify.get_clarify_ctx", return_value=ctx),
        patch("madras.tools.builtin.clarify._absorb", new=AsyncMock()) as absorb_mock,
    ):
        coro = cast("Coroutine[Any, Any, ToolResult]", clarify({"question": "Which region?"}))
        result = asyncio.run(coro)
        ok = result.ok is True and result.content == "us-east" and absorb_mock.called
        call_args = absorb_mock.call_args
        ok = ok and call_args is not None and call_args.args == ("Which region?", "us-east")
        return ok, f"ok={result.ok} absorb_args={call_args.args if call_args else None}"


def _case_absorption_calls_the_real_memory_fabric() -> tuple[bool, str]:
    # Don't mock _absorb itself here — mock one level deeper (the memory fabric context)
    # to prove _absorb's real body actually calls fabric.remember(), not just "was invoked."
    import asyncio

    from madras.tools.builtin.clarify import _absorb  # pyright: ignore[reportPrivateUsage]

    fake_fabric = AsyncMock()
    fake_mctx = type(
        "MCtx",
        (),
        {
            "fabric": fake_fabric,
            "session_id": "s1",
            "agent_name": "shadow",
        },
    )()
    with patch("madras.tools.memory_fabric_context.get_memory_fabric_ctx", return_value=fake_mctx):
        asyncio.run(_absorb("Which region?", "us-east"))
    ok = fake_fabric.remember.called
    if ok:
        item = fake_fabric.remember.call_args.args[0]
        ok = item.kind == "preference" and "us-east" in item.content
    return ok, f"remember_called={fake_fabric.remember.called}"


def _case_absorption_failure_never_blocks_the_result() -> tuple[bool, str]:
    # Mocks at the REAL protection boundary — get_memory_fabric_ctx, inside _absorb's own
    # try/except — not _absorb itself (which would bypass that protection entirely and test
    # an unrealistic scenario; caught live, s42: clarify() has no defensive layer of its own
    # around `await _absorb(...)`, the "never blocks" guarantee lives solely inside _absorb).
    import asyncio

    from madras.tools.builtin.clarify import ToolResult, clarify

    ctx = _FakeClarifyCtx(ask=AsyncMock(return_value="us-east"))
    with (
        patch("madras.tools.builtin.clarify.get_clarify_ctx", return_value=ctx),
        patch(
            "madras.tools.memory_fabric_context.get_memory_fabric_ctx",
            side_effect=RuntimeError("memory fabric down"),
        ),
    ):
        try:
            coro = cast("Coroutine[Any, Any, ToolResult]", clarify({"question": "Which region?"}))
            result = asyncio.run(coro)
        except Exception as exc:
            return False, f"clarify() raised despite absorption failure: {exc!r}"
    ok = result.ok is True and result.content == "us-east"
    return ok, f"ok={result.ok}"


_EXECUTORS: dict[str, Any] = {
    "headless_degrades_cleanly": _case_headless_degrades_cleanly,
    "empty_answer_rejected_no_absorption_attempted": (
        _case_empty_answer_rejected_no_absorption_attempted
    ),
    "real_answer_triggers_real_absorption": _case_real_answer_triggers_real_absorption,
    "absorption_calls_the_real_memory_fabric": _case_absorption_calls_the_real_memory_fabric,
    "absorption_failure_never_blocks_the_result": _case_absorption_failure_never_blocks_the_result,
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
        "suite_id": "hitl_absorption_conformance",
        "benchmark_family": "hitl_absorption_conformance",
        "features": _FEATURES,
        "k": 1,
        "passes": 1 if passed else 0,
        "pass_rate": 1.0 if passed else 0.0,
        "det": [{"type": "absorption_verdict", "passed": passed, "detail": detail}],
        "judge_pass": None,
        "verdict": "pass" if passed else "fail",
        "n_steps": 1,
        "tool_error_rate": 0.0,
        "latency_ms": round(latency_ms, 3),
        "tokens": 0,
    }


class HitlAbsorptionConformanceSuite(Suite):
    id: str = "hitl_absorption_conformance"
    name: str = "HITL answer-absorption conformance — deterministic"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "Madras-original, deterministic (zero LLM) — direct calls into the REAL "
        "tools/builtin/clarify.py::clarify()/_absorb() with mocked context objects. Fills "
        "Benchmark.md §6 axis #8 (HITL answer absorption), confirmed s42 to have zero suite."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id=f"hitl_absorption_conformance-{case_id}",
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
