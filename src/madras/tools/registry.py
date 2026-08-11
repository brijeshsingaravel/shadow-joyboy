"""Self-registering tool registry + governed executor.

Pattern adopted from Hermes (tools self-register at import; agents declare TOOLSET
names as capabilities). Wrapped in Madras's governance: ASI03 rank gate + an
8-dimension eval signal + an immutable audit-log entry on EVERY tool call.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from madras.eval_.emitter import emit_action_signals
from madras.models.agent_config import Rank
from madras.obs.langfuse_client import push_action_scores, push_tool_span

_RANK_ORDER = [
    Rank.INTERN,
    Rank.JUNIOR,
    Rank.SPECIALIST,
    Rank.SENIOR,
    Rank.PRINCIPAL,
    Rank.LEGEND,
]


def _rank_at_least(actual: Rank, required: Rank) -> bool:
    return _RANK_ORDER.index(actual) >= _RANK_ORDER.index(required)


# Toolsets that mutate external / irreversible state (side effects beyond reading). PLAN MODE
# (read-only exploration) blocks these until the plan is approved. Superset of the sandbox set
# {shell, code, file_write}; reads / planning / memory / discovery stay allowed.
MUTATING_TOOLSETS = frozenset(
    {
        "shell",
        "code",
        "file_write",
        "browser",
        "messaging",
        "schedule",
        "delegation",
        "mcp",
        "kanban",
        "image_gen",
        "media",
        "tts",
    }
)


# Toolsets always exposed IN FULL (never deferred behind the discovery bridge) — the small
# always-needed core, plus 'discovery' itself. Mirrors Hermes "core tools never defer".
DEFAULT_CORE_TOOLSETS = frozenset(
    {
        "file",
        "file_write",
        "terminal",
        "shell",
        "code",
        "code_execution",
        "search",
        "plan",
        "clarify",
        "memory",
        "discovery",
    }
)


# Substrings (case-insensitive) in an exception's class name or message that mark
# a failure as transient — exactly the flakiness we see against the shared stack.
_TRANSIENT_HINTS = (
    "timeout",
    "timed out",
    "connection",
    "temporarily",
    "econnreset",
    "503",
    "502",
    "eof",
)
_TRANSIENT_TYPES = (TimeoutError, ConnectionError, ConnectionResetError)


def _is_retryable(exc: BaseException | None, result: ToolResult | None) -> bool:
    """A failure is retryable if the tool flagged it OR the exception looks transient."""
    if result is not None and result.extras.get("retryable") is True:
        return True
    if exc is None:
        return False
    if isinstance(exc, _TRANSIENT_TYPES):
        return True
    blob = f"{type(exc).__name__} {exc}".lower()
    return any(hint in blob for hint in _TRANSIENT_HINTS)


@dataclass
class ToolResult:
    ok: bool
    content: str = ""
    error: str | None = None
    extras: dict[str, Any] = field(default_factory=dict[str, Any])


# A tool's run function: async (args: dict) -> ToolResult
ToolRun = Callable[[dict[str, Any]], Awaitable["ToolResult"]]


@dataclass
class ToolSpec:
    name: str
    toolset: str
    description: str
    parameters: dict[str, Any]  # JSON-schema-style param spec for the LLM
    run: ToolRun
    rank_required: Rank = Rank.INTERN


class ToolDenied(Exception):
    """Raised when the rank gate denies a tool call (ASI03)."""


class ToolRegistry:
    """Holds ToolSpecs; resolves the allowed set per agent rank + declared toolsets."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def toolsets(self) -> set[str]:
        return {t.toolset for t in self._tools.values()}

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def allowed(self, *, agent_rank: Rank, toolsets: list[str] | None = None) -> list[ToolSpec]:
        out: list[ToolSpec] = []
        for t in self._tools.values():
            if toolsets is not None and t.toolset not in toolsets:
                continue
            if _rank_at_least(agent_rank, t.rank_required):
                out.append(t)
        return out

    @staticmethod
    def _schema(t: ToolSpec) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }

    def schemas(
        self, *, agent_rank: Rank, toolsets: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """OpenAI-style tool schemas for the LLM, for the allowed set."""
        return [self._schema(t) for t in self.allowed(agent_rank=agent_rank, toolsets=toolsets)]

    def deferrable(
        self,
        *,
        agent_rank: Rank,
        toolsets: list[str] | None = None,
        core_toolsets: frozenset[str] | None = None,
    ) -> list[ToolSpec]:
        """The allowed tools whose schemas may be deferred (everything outside the core set)."""
        core = DEFAULT_CORE_TOOLSETS if core_toolsets is None else core_toolsets
        return [
            t
            for t in self.allowed(agent_rank=agent_rank, toolsets=toolsets)
            if t.toolset not in core
        ]

    def progressive_schemas(
        self,
        *,
        agent_rank: Rank,
        toolsets: list[str] | None = None,
        core_toolsets: frozenset[str] | None = None,
        defer_threshold: int = 12,
    ) -> list[dict[str, Any]]:
        """Progressive tool disclosure: expose the core toolsets in full + the discovery
        bridge (tool_find/tool_describe/tool_call); the long tail is deferred (discoverable
        on demand). Falls back to full schemas when too few tools would be deferred to be
        worth the indirection (the threshold gate — mirrors Hermes' tool_search 10% rule).
        Keeps the model-visible array small + STABLE, so prompt caching is not broken.
        """
        core = DEFAULT_CORE_TOOLSETS if core_toolsets is None else core_toolsets
        deferred = self.deferrable(agent_rank=agent_rank, toolsets=toolsets, core_toolsets=core)
        if len(deferred) < defer_threshold:
            return self.schemas(agent_rank=agent_rank, toolsets=toolsets)
        out = [
            self._schema(t)
            for t in self.allowed(agent_rank=agent_rank, toolsets=toolsets)
            if t.toolset in core
        ]
        present = {s["function"]["name"] for s in out}
        # The discovery bridge must always be visible in progressive mode, even when the
        # agent did not declare the 'discovery' toolset.
        for t in self.allowed(agent_rank=agent_rank, toolsets=["discovery"]):
            if t.name not in present:
                out.append(self._schema(t))
        return out


# Global default registry; tools self-register into it at import (Hermes pattern).
REGISTRY = ToolRegistry()


def tool(
    *,
    name: str,
    toolset: str,
    description: str,
    parameters: dict[str, Any],
    rank_required: Rank = Rank.INTERN,
    registry: ToolRegistry | None = None,
) -> Callable[[ToolRun], ToolRun]:
    """Decorator: register an async run-function as a tool."""

    def deco(fn: ToolRun) -> ToolRun:
        (registry or REGISTRY).register(
            ToolSpec(
                name=name,
                toolset=toolset,
                description=description,
                parameters=parameters,
                run=fn,
                rank_required=rank_required,
            )
        )
        return fn

    return deco


class GovernedExecutor:
    """Executes a tool through Madras's governance: ASI03 rank gate, 8-dim eval
    signal, and an immutable audit entry. The eval signal + audit record are the
    side effects; the ToolResult is returned.
    """

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        audit: Any = None,
        emit: Callable[[dict[str, Any]], dict[str, Any]] = emit_action_signals,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        plan_mode: bool = False,
    ) -> None:
        self._registry = registry
        self._audit = audit  # AuditLogWriter or None (degrade gracefully)
        self._emit = emit
        self._sleep = sleep  # injectable so tests don't actually wait
        self._plan_mode = plan_mode  # read-only exploration gate (blocks MUTATING_TOOLSETS)

    def set_plan_mode(self, on: bool) -> None:
        """Flip the read-only plan gate (e.g. exit plan mode once a plan is approved)."""
        self._plan_mode = on

    @property
    def plan_mode(self) -> bool:
        return self._plan_mode

    async def execute(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        agent_name: str,
        session_id: str,
        agent_rank: Rank,
        langfuse_trace_id: str | None = None,
    ) -> ToolResult:
        spec = self._registry.get(tool_name)
        start = time.perf_counter()
        if spec is None:
            result = ToolResult(ok=False, error=f"unknown tool: {tool_name}")
        elif self._plan_mode and spec.toolset in MUTATING_TOOLSETS:
            # Plan mode = read-only exploration. Mutating tools are blocked (soft teach-back,
            # not a hard ToolDenied) so the model proposes a plan; mutation needs approval.
            await self._record(
                agent_name,
                session_id,
                tool_name,
                args,
                ok=False,
                denied=True,
                latency_ms=0.0,
                langfuse_trace_id=langfuse_trace_id,
            )
            result = ToolResult(
                ok=False,
                error=(
                    f"[PLAN_MODE] read-only — '{tool_name}' ({spec.toolset}) mutates state. "
                    "Propose a plan first; mutation requires approval (exit plan mode)."
                ),
            )
        elif not _rank_at_least(agent_rank, spec.rank_required):
            # ASI03 privilege gate
            await self._record(
                agent_name,
                session_id,
                tool_name,
                args,
                ok=False,
                denied=True,
                latency_ms=0.0,
                langfuse_trace_id=langfuse_trace_id,
            )
            raise ToolDenied(
                f"rank gate: {agent_rank.value!r} below required "
                f"{spec.rank_required.value!r} for tool {tool_name!r}"
            )
        else:
            # Bounded retry: up to 3 attempts total. Transient failures (network
            # blips vs. the shared stack) are retried invisibly; the model sees
            # only the final ToolResult. Permanent failures fail fast.
            _backoffs = [0.1, 0.2]  # waits between attempts 1->2 and 2->3
            attempts = 0
            result = ToolResult(ok=False, error="not run")
            retryable = False
            while True:
                attempts += 1
                exc: BaseException | None = None
                try:
                    result = await spec.run(args)
                except Exception as e:  # a tool failure is a result, not a crash
                    exc = e
                    result = ToolResult(ok=False, error=f"{type(e).__name__}: {e}")
                if result.ok:
                    retryable = False
                    break
                retryable = _is_retryable(exc, result)
                if not retryable or attempts >= 3:
                    break
                await self._sleep(_backoffs[attempts - 1])
            result.extras["attempts"] = attempts
            if not result.ok:
                prefix = "[RETRIES-EXHAUSTED] " if retryable else "[NON-RETRYABLE] "
                current = result.error or ""
                if not current.startswith(("[RETRIES-EXHAUSTED] ", "[NON-RETRYABLE] ")):
                    result.error = prefix + current
        latency_ms = (time.perf_counter() - start) * 1000.0
        _attempts = int(result.extras.get("attempts", 1) or 1)
        _cost = float(result.extras.get("cost_usd", 0.0) or 0.0)
        await self._record(
            agent_name,
            session_id,
            tool_name,
            args,
            ok=result.ok,
            denied=False,
            latency_ms=latency_ms,
            cost_usd=_cost,
            attempts=_attempts,
            langfuse_trace_id=langfuse_trace_id,
            result_summary={"ok": result.ok, "error": (result.error or "")[:200]},
        )
        return result

    async def _record(
        self,
        agent_name: str,
        session_id: str,
        tool_name: str,
        args: dict[str, Any],
        *,
        ok: bool,
        denied: bool,
        latency_ms: float,
        cost_usd: float = 0.0,
        attempts: int = 1,
        langfuse_trace_id: str | None = None,
        result_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Retry accounting feeds the error_recovery dimension: a call that needed >1
        # attempt encountered an error; if it ultimately succeeded it recovered.
        errors_encountered = max(0, attempts - 1)
        errors_recovered = errors_encountered if (ok and errors_encountered) else 0
        signals = self._emit(
            {
                "task_completion": bool(ok),
                "trajectory_trace": [f"tool:{tool_name}" + ("(denied)" if denied else "")],
                "tool_calls": [
                    {
                        "name": tool_name,
                        "args": {k: str(v)[:80] for k, v in args.items()},
                    }
                ],
                "tool_selection": "denied" if denied else "correct",
                "argument_correctness": not denied,
                "confidence": 0.0 if denied else (0.8 if ok else 0.3),
                "latency_ms": round(latency_ms, 3),
                "cost_usd": round(cost_usd, 6),
            }
        )
        # emit() returns only the 8 required keys; attach the error-recovery signals
        # (read by score_error_recovery) onto the audited record without changing the
        # required-signal contract.
        signals["errors_encountered"] = errors_encountered
        signals["errors_recovered"] = errors_recovered
        signals["attempts"] = attempts
        # Langfuse: a span per tool call + the 8-dim signals as scores, both
        # tied to the turn's trace. No-op (never raises) if tracing is off —
        # tracing must never be able to break a tool call.
        push_tool_span(
            trace_id=langfuse_trace_id,
            tool_name=tool_name,
            args=args,
            ok=ok,
            denied=denied,
            result_summary=result_summary or {"denied": denied},
            latency_ms=latency_ms,
        )
        push_action_scores(trace_id=langfuse_trace_id, signals=signals)
        if self._audit is not None:
            try:
                from madras.audit.writer import AuditRecord

                await self._audit.append(
                    AuditRecord(
                        agent_name=agent_name,
                        session_id=session_id,
                        action=f"tool_call:{tool_name}",
                        signals=signals,
                        tool_calls=signals["tool_calls"],
                        extras={"denied": denied, "ok": ok},
                    )
                )
            except Exception:
                pass  # audit must never break a tool call (degrade if Postgres down)
        return signals
