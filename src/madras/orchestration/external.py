"""Supervise an EXTERNAL agent runtime (Codex sub-runtime, an ACP agent, a CLI) under Madras
governance — the cross-agent orchestration capability.

Madras doesn't have to BE the agent to govern it: a `SupervisedRuntime` wraps any external
runner with the same guarantees Madras applies to itself — an **approval gate** before the run,
an **audit** trail, a **loop guard** ([[Tool-Loop Guard]]) over the external agent's tool events,
and a **step bound**. The runner is injectable (a fake in tests, a real subprocess/ACP client in
prod), so this is pure + deterministic. The external agent emits events; the supervisor observes
them and can halt the run by raising through the event sink.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from madras.graph.loop_guard import LoopGuard

EventSink = Callable[[dict[str, Any]], None]
Runner = Callable[[str, EventSink], Awaitable[str]]  # (task, emit) -> output


class _Halt(Exception):
    """Raised through the event sink to abort a supervised external run."""


@dataclass
class RuntimeResult:
    ok: bool
    output: str = ""
    events: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    steps: int = 0
    exit_reason: str = "completed"  # completed | denied | looped | max_steps | error


@dataclass
class SupervisedRuntime:
    runner: Runner
    name: str = "external"
    approve: Callable[[str], bool] | None = None  # approve(task) -> bool
    audit: Callable[[dict[str, Any]], None] | None = None
    loop_guard: LoopGuard | None = None
    max_steps: int = 25

    def _audit(self, record: dict[str, Any]) -> None:
        if self.audit is not None:
            self.audit({"runtime": self.name, **record})

    async def run(self, task: str) -> RuntimeResult:
        if self.approve is not None and not self.approve(task):
            self._audit({"event": "denied", "task": task})
            return RuntimeResult(ok=False, exit_reason="denied")

        self._audit({"event": "start", "task": task})
        events: list[dict[str, Any]] = []
        halt_reason: dict[str, str] = {}

        def emit(ev: dict[str, Any]) -> None:
            events.append(ev)
            if len(events) > self.max_steps:
                halt_reason["why"] = "max_steps"
                raise _Halt("max_steps")
            if self.loop_guard is not None and ev.get("type") == "tool":
                verdict = self.loop_guard.observe(
                    str(ev.get("tool", "")), ev.get("args", {}) or {}, bool(ev.get("ok", True))
                )
                if verdict.action == "halt":
                    halt_reason["why"] = "looped"
                    raise _Halt(verdict.reason)

        try:
            output = await self.runner(task, emit)
        except _Halt as halt:
            reason = halt_reason.get("why", "looped")
            self._audit({"event": "halt", "reason": reason, "detail": str(halt)})
            return RuntimeResult(ok=False, events=events, steps=len(events), exit_reason=reason)
        except Exception as exc:  # the external runtime crashed
            self._audit({"event": "error", "error": f"{type(exc).__name__}: {exc}"})
            return RuntimeResult(ok=False, events=events, steps=len(events), exit_reason="error")

        self._audit({"event": "finish", "steps": len(events)})
        return RuntimeResult(ok=True, output=output, events=events, steps=len(events))
