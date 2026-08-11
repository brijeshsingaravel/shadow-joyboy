"""compiler/turn.py — runs one real turn of a compiled agent through the production
tool-execution graph. Shared by compiler/simulate.py (B5, a generated sample task) and
§ B8's /agents/{agent_name}/invoke (a real message from an API caller or the embed
widget) -- one execution path, not two.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from madras.factory.spawn import AgentRecord
from madras.graph.build import build_llm_graph
from madras.graph.state import AgentState
from madras.llm.gateway import LLMGateway


@dataclass
class TurnResult:
    response_text: str
    tool_calls: list[str] = field(default_factory=list[str])
    cost_usd: float = 0.0
    passed: bool = False
    refused: bool = False  # True when the killswitch halted this turn before it ran
    commitments: list[Any] = field(default_factory=list[Any])  # s46: promises detected in this
    # turn's response (commitments.Commitment) -- the caller registers them into a durable
    # CommitmentMachine; kept out of this pure/testable helper by design.
    persona_drift_score: float = 0.0  # s46: Identity Anchor's PersonaDriftLint, 0=in-persona
    integrity_violated: bool = False  # s46: Identity Anchor's integrity monitor (opt-in)
    integrity_reason: str = ""


class KillSwitch(Protocol):
    """The invoke-door contract. `workspace/killswitch.QuarantineStore` satisfies it."""

    async def blocked(self, agent_name: str, *, owner_user_id: str | None = ...) -> Any: ...


class Moderator(Protocol):
    """Content-moderation contract. `security/moderation.ModerationEngine` satisfies it."""

    async def moderate(self, text: str) -> Any: ...


# Shown to the caller when an agent is quarantined — no capability leak, no persona.
QUARANTINE_REFUSAL = "This agent is currently suspended and cannot take requests."
MODERATION_REFUSAL = "That request was blocked by content policy."


async def run_agent_turn(
    *,
    agent: AgentRecord,
    user_input: str,
    gateway: LLMGateway,
    model: str,
    session_id: str = "turn",
    audit: Any = None,
    killswitch: KillSwitch | None = None,
    owner_user_id: str | None = None,
    moderator: Moderator | None = None,
) -> TurnResult:
    """`audit`: an AuditLogWriter (or None) -- when given, every tool call this turn
    makes writes a REAL immutable audit row (§ B7's Workspace activity feed and the
    per-channel activity both read from the same table).

    `killswitch`: a KillSwitch (or None) -- when given, the agent is checked for an
    active quarantine BEFORE the graph runs; a blocked agent is refused immediately
    (no model call, no tools) and the refusal is written to the audit log.

    `moderator`: a Moderator (or None) -- when given, the user input is content-moderated
    BEFORE the graph runs; a blocked input is refused (no model call) and audit-logged."""
    if killswitch is not None:
        block = await killswitch.blocked(agent.config.name, owner_user_id=owner_user_id)
        if block is not None:
            if audit is not None:
                from madras.audit.writer import AuditRecord

                await audit.append(
                    AuditRecord(
                        agent_name=agent.config.name,
                        session_id=session_id,
                        action="agent.quarantined.refused",
                        signals={"task_completion": False},
                        extras={
                            "scope": getattr(block, "scope", ""),
                            "target": getattr(block, "target", ""),
                            "reason": getattr(block, "reason", ""),
                            "by_actor": getattr(block, "by_actor", ""),
                        },
                    )
                )
            return TurnResult(response_text=QUARANTINE_REFUSAL, passed=False, refused=True)

    if moderator is not None:
        verdict = await moderator.moderate(user_input)
        if not getattr(verdict, "allowed", True):
            if audit is not None:
                from madras.audit.writer import AuditRecord

                await audit.append(
                    AuditRecord(
                        agent_name=agent.config.name,
                        session_id=session_id,
                        action="input.moderation.blocked",
                        signals={"task_completion": False},
                        extras={
                            "category": getattr(verdict, "category", "") or "",
                            "layer": getattr(verdict, "layer", ""),
                            "reason": getattr(verdict, "reason", ""),
                        },
                    )
                )
            return TurnResult(response_text=MODERATION_REFUSAL, passed=False, refused=True)

    executor = None
    if audit is not None:
        from madras.tools.registry import REGISTRY, GovernedExecutor

        executor = GovernedExecutor(registry=REGISTRY, audit=audit)

    # s46: HookRegistry was never constructed anywhere in the live path -- the
    # pre_tool_use hook system (incl. hooks/rails.py's already-tested user-authored
    # rails) was structurally unreachable from any real turn until this. The
    # tool-call rail (security/rails.py's deterministic destructive-args/secret-exfil
    # scan) registers here too, same live control point.
    from madras.hooks.registry import HookRegistry
    from madras.security.rails import GuardRails, register_tool_call_rail

    hook_registry = HookRegistry()
    register_tool_call_rail(hook_registry, GuardRails())

    # s46: capture_quick_adds() (memory/quick_add.py, row 14f) had no live caller -- a
    # `#remember`/`#mem`/`#note` directive in the user's message is now captured to the
    # agent's file-memory store immediately (idempotent, content-hashed); the nightly
    # MemoryManagerJob reconciles these files into the Fabric via import_from_files().
    try:
        import time as _qa_time

        from madras.memory.file_memory import FileMemoryStore
        from madras.memory.quick_add import capture_quick_adds
        from madras.memory_manager.job import default_file_memory_root

        capture_quick_adds(
            user_input,
            store=FileMemoryStore(
                root=str(default_file_memory_root()), agent_name=agent.config.name
            ),
            now=_qa_time.time(),
        )
    except Exception:
        pass  # capture is best-effort; never blocks a real turn

    state: AgentState = {
        "agent_name": agent.config.name,
        "session_id": session_id,
        "user_input": user_input,
        "messages": [],
    }

    async def _attempt(chosen_model: str) -> Any:
        graph = build_llm_graph(
            agent,
            gateway=gateway,
            model=chosen_model,
            tools_on=True,
            toolsets=agent.config.toolsets,
            max_iters=3,
            executor=executor,
            hooks=hook_registry,
        )
        return await graph.ainvoke(state)  # type: ignore[reportUnknownMemberType]

    # s46: select_model() puts `model` (the role's own configured choice) first --
    # routing only supplies fallback alternatives on a transient error, it never
    # silently overrides an explicit choice. free_only per settings.llm_free_only
    # (the single test-vs-launch switch).
    from madras.llm.fallback_chain import run_with_fallback_async
    from madras.llm.select import select_model

    # s46: Resource Awareness's cognitive-mode selector -- select_model()'s tradeoff knob
    # (0=quality..10=cheapest) was hardcoded to the default 7 on every real turn (only ever
    # exercised in an eval suite). Deterministic urgency language in the user's OWN message
    # now drives it -- "quickly"/"asap" -> cheaper+faster, "thoroughly"/"carefully" -> higher
    # quality, otherwise the same default as before (no behavior change for a plain request).
    from madras.metacog.resource_mode import tradeoff_for_input

    chain = select_model(configured_model=model, tradeoff=tradeoff_for_input(user_input))
    fb_result = await run_with_fallback_async(chain, _attempt)
    if not fb_result.ok:
        return TurnResult(response_text="", passed=False)
    out_state = cast(AgentState, fb_result.value)

    signals = out_state.get("eval_signals", {}) or {}
    messages = out_state.get("messages", []) or []
    response_text = str(messages[-1].content) if messages else ""

    # s46: extract_commitments() (commitments.py, row 75) had no live caller -- every real
    # turn's response is now scanned for promises the agent just made ("I'll deploy this
    # after you approve it."), so the caller can register them into a durable tracker.
    import time as _time

    from madras.commitments import extract_commitments

    detected = extract_commitments(response_text, session_id=session_id, now=_time.time())

    # s46: Identity Anchor -- PersonaDriftLint (persona/lint.py) existed only inside the
    # eval_/real_tests.py verification harness, never scored a REAL turn's response. Cheap
    # + deterministic (keyword heuristic, no LLM call) -- always on, no opt-in needed.
    from madras.persona.lint import PersonaDriftLint

    _north_star = agent.config.persona.north_star if agent.config.persona is not None else ""
    drift_score = PersonaDriftLint().score(voice_north_star=_north_star, messages=[response_text])

    # s46: Identity Anchor -- check_integrity() (metacog/integrity.py) applies
    # Constitutional AI's self-critique methodology at inference time against
    # CONSTITUTION.md's Prime Directives. Opt-in (an extra LLM call per turn).
    integrity_violated = False
    integrity_reason = ""
    _exec_cfg = agent.config.execution
    if _exec_cfg is not None and _exec_cfg.integrity_monitor and response_text.strip():
        from pathlib import Path as _Path

        from madras.metacog.integrity import check_integrity, load_prime_directives

        _constitution = _Path(__file__).resolve().parents[3] / "agents" / "CONSTITUTION.md"
        _directives = load_prime_directives(_constitution) if _constitution.is_file() else []
        if _directives:
            try:
                _verdict = await check_integrity(
                    gateway=gateway,
                    model=model,
                    action=response_text,
                    directives=_directives,
                )
                integrity_violated = _verdict.violated
                integrity_reason = _verdict.reason
            except Exception:
                integrity_violated = False

    return TurnResult(
        response_text=response_text,
        tool_calls=list(signals.get("trajectory_trace", []) or []),
        cost_usd=float(signals.get("cost_usd", 0.0)),
        passed=bool(signals.get("task_completion", False)),
        persona_drift_score=drift_score,
        integrity_violated=integrity_violated,
        integrity_reason=integrity_reason,
        commitments=detected,
    )
