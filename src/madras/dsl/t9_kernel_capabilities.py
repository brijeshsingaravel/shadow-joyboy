"""T9 -- the first real Tier-0 kernel operations reachable from `.tamil`'s compute-substrate.

D49 froze 6 Ring-0 subsystems (`models`/`audit`/`obs`/`llm`/`memory`/`security`) as the engine's
"don't break userspace" kernel. "Re-host Madras in `.tamil`" (T9) means a `.tamil` goal can
actually drive one of these -- but none of the 6 have ever been callable from `.tamil` at all
(they're internal Python modules, not capabilities). `append_and_verify_audit_entry` (T9.1) was
the first: a real, synchronous, 0-arg wrapper around `audit.writer.AuditLogWriter` -- append a
real record, verify the real tamper-evident hash chain. `emit_and_verify_trace_span` (T9.3) is
the second, `obs` -- D49's own mapping pairs `audit`+`obs` together as "cross-cutting tracing,"
and this is the smallest, safest real slice of `obs` for the exact same reason `audit` was
picked first for T9.1: it's observation, not action -- emitting a span has no side effect on any
OTHER system's state, only on Madras's own telemetry. `remember_and_recall_quick_add` (T9.4) is
the third, `memory` -- D49 maps `memory` onto `.tamil`'s own `memory-ref` kernel node
(Recall/Remember), a genuinely direct correspondence: this capability IS a `memory-ref`
write-then-read, just of Madras's real memory fabric instead of a synthetic Python provider (the
kind T8.14's `Recall` wiring uses). `check_tool_permissions` (T9.5) is the fourth, `security` --
D49 maps `security` onto `.tamil`'s own `governance-check` kernel node, another direct
correspondence: `interpreter.py`'s `_rank_from_govern` already implements a LOCAL rank-ladder
comparison for `.tamil`'s `Govern` node, but never calls into the real `security` subsystem at
all; this capability closes that gap by exercising the REAL, production `PermissionEngine` (the
same engine gating every real tool call) directly. `validate_rank_against_real_model` (T9.6) is
the fifth, `models` -- D49 maps `models` onto `.tamil`'s own typed `goal`/`compose-bind` AST
(both are "governed by construction": malformed input fails at construction time, not at
runtime); the most directly-touched real model is `Rank` (`_rank_from_govern`'s own output
feeds straight into `spawn_agent_preview`'s `role_data["rank"]`), so this capability proves
`.tamil`'s own rank ladder and the REAL `Rank` enum are in exact agreement, not just
conceptually similar. `select_model_and_escalate_cost_tier` (T9.7) is the sixth and last,
`llm` -- D49 maps `llm` onto `.tamil`'s own `capability-call` node when it targets a MODEL
rather than a tool/faculty: selects a real model from Madras's real, offline, zero-cost
free-fleet catalog and exercises the real cost-tier escalation cascade, WITHOUT any live network
call or real spend (unlike a genuine model completion, which needs both). All six are callable
through Kollan's CPython bridge (`kollan_bridge.call_python_object`) exactly like any other live
Python object -- D49's full 6-subsystem kernel now has a real, live-proven `.tamil`-reachable
capability, every one of them.

**Honest scope of what this proves, and what it doesn't yet:** this proves Kollan's native
bridge can invoke a genuine Tier-0 kernel operation and get a real result back -- the concrete
meaning of "re-host a piece of Madras's kernel." Wiring these into `compile_goal`'s
`capability_addresses` resolution (T9.2 already proved this works, generically, for
`append_and_verify_audit_entry` specifically -- extending it to the other capabilities here is a
small, separate follow-up) and registering any of them in the Capability Catalog are still not
done here. Capability Catalog registration and T9's "minimal agent loop" remain undone.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from madras.audit.writer import AuditLogWriter, AuditRecord
from madras.config import settings
from madras.dsl.interpreter import RANK_LEVELS
from madras.llm.cost import CostTier, escalate
from madras.llm.model_catalog import ModelCatalog
from madras.memory.file_memory import FileMemoryStore
from madras.memory.quick_add import quick_add
from madras.memory_manager.job import default_file_memory_root
from madras.models.agent_config import Rank
from madras.obs.tracing import GEN_AI_OPERATION_NAME, MADRAS_COST_USD, get_tracer
from madras.security.permissions import Decision, PermissionEngine

_T9_SESSION_ID = "t9-kollan-bridge-proof"
_T9_AGENT_NAME = "t9_probe"
_T9_TRACER_NAME = "t9_probe"
_T9_SPAN_NAME = "t9_kollan_bridge_proof"
_T9_MEMORY_CONTENT = "t9 kollan bridge proof -- memory subsystem smoke content"
_T9_DANGEROUS_CMD = "rm -rf /"
_T9_INVALID_RANK = "not_a_real_rank"


async def _append_and_verify() -> dict[str, Any]:
    writer = AuditLogWriter(postgres_url=settings.postgres_url)
    try:
        await writer.setup()
        record_id = await writer.append(
            AuditRecord(
                agent_name=_T9_AGENT_NAME,
                session_id=_T9_SESSION_ID,
                action="t9_kollan_bridge_proof",
                signals={"source": "kollan_bridge", "cost_usd": 0.0},
            )
        )
        chain = await writer.verify_chain(session_id=_T9_SESSION_ID)
        return {"record_id": record_id, "chain_ok": chain["ok"], "chain_length": chain["length"]}
    finally:
        await writer.close()


def append_and_verify_audit_entry() -> dict[str, Any]:
    """The real, synchronous, 0-arg capability: appends one real record to the real
    tamper-evident audit log (`madras_audit_log`, live Postgres) and re-verifies that
    session's whole hash chain, returning `{record_id, chain_ok, chain_length}` -- all three
    values genuinely computed by `audit.writer`'s own real code, not simulated."""
    return asyncio.run(_append_and_verify())


def emit_and_verify_trace_span() -> dict[str, Any]:
    """The real, synchronous, 0-arg `obs` capability: emits ONE real OpenTelemetry span through
    `obs.tracing.get_tracer()` -- the SAME seam every LLM call already goes through
    (`LLMGateway.complete`) -- with real GenAI-semconv attributes, and returns the span's own
    real trace/span IDs (genuinely computed by the OTel SDK's own span-context machinery, not
    simulated). Whether `recorded` is `True` depends entirely on whether a real `TracerProvider`
    is configured in THIS process (`obs.tracing.setup_tracing()`, or a test's own) -- with none
    configured, OTel's own no-op default correctly reports `recorded=False`/all-zero IDs, an
    honest result, not a failure to build the capability."""
    tracer = get_tracer(_T9_TRACER_NAME)
    with tracer.start_as_current_span(_T9_SPAN_NAME) as span:
        span.set_attribute(MADRAS_COST_USD, 0.0)
        span.set_attribute(GEN_AI_OPERATION_NAME, _T9_SPAN_NAME)
        ctx = span.get_span_context()
        return {
            "trace_id": format(ctx.trace_id, "032x"),
            "span_id": format(ctx.span_id, "016x"),
            "recorded": ctx.is_valid,
        }


def remember_and_recall_quick_add() -> dict[str, Any]:
    """The real, synchronous, 0-arg `memory` capability: writes ONE real quick-add memory item
    to the real file-memory store (`default_file_memory_root()`, `<workspace>/memory` -- the
    SAME root `compiler/turn.py`'s own live `#remember` capture already writes to) under an
    isolated `t9_probe` agent name (never reconciled by the real nightly `MemoryManagerJob`,
    which only ever runs for real agent names -- no risk of polluting a real agent's memory), and
    reads it straight back from disk. `quick_add`'s content-hashed id makes this idempotent: every
    call with the SAME content writes/overwrites the SAME real file, no unbounded growth.
    Pure stdlib file I/O -- no live Postgres/Qdrant needed, unlike `audit`'s capability."""
    store = FileMemoryStore(root=str(default_file_memory_root()), agent_name=_T9_AGENT_NAME)
    written = quick_add(
        _T9_MEMORY_CONTENT, store=store, now=time.time(), source="t9_kollan_bridge_proof"
    )
    recalled = store.read(written.id)
    return {
        "memory_id": written.id,
        "recalled_content": recalled.content if recalled is not None else None,
        "round_trip_ok": recalled is not None and recalled.content == _T9_MEMORY_CONTENT,
    }


def check_tool_permissions() -> dict[str, Any]:
    """The real, synchronous, 0-arg `security` capability: runs Madras's REAL `PermissionEngine`
    (the exact same decision engine gating every real tool call, not a stub) against two fixed,
    representative probes -- a hard-denied dangerous command (`rm -rf /`, one of `default_rules
    ()`'s own real safety denials) and a genuinely safe read-only tool call -- proving the real
    security logic actually discriminates between them, not simulated. Pure in-memory decision
    logic (`PermissionEngine()` with no `PermissionStore`) -- no live Postgres needed at all,
    unlike `audit`'s capability."""
    engine = PermissionEngine()
    dangerous = engine.check(tool="terminal", toolset="shell", args={"cmd": _T9_DANGEROUS_CMD})
    safe = engine.check(tool="web_search", toolset="web", args={})
    return {
        "dangerous_decision": dangerous.value,
        "safe_decision": safe.value,
        "dangerous_denied": dangerous is Decision.DENY,
        "safe_allowed": safe is Decision.ALLOW,
    }


def validate_rank_against_real_model() -> dict[str, Any]:
    """The real, synchronous, 0-arg `models` capability: constructs a REAL `models.agent_config.
    Rank` enum member from EVERY string in `.tamil`'s OWN rank ladder (`interpreter.py`'s
    `RANK_LEVELS`, the same list `_rank_from_govern` derives a goal's rank from), proving the
    two are in EXACT agreement -- not just conceptually similar -- and confirms a genuinely
    invalid rank string is correctly REJECTED by real Pydantic validation (`ValueError`), not
    simulated. Pure in-memory validation -- no live infra needed at all."""
    try:
        for level in RANK_LEVELS:
            Rank(level)
        all_valid = True
    except ValueError:
        all_valid = False
    matches_exactly = {r.value for r in Rank} == set(RANK_LEVELS)
    try:
        Rank(_T9_INVALID_RANK)
        invalid_rejected = False
    except ValueError:
        invalid_rejected = True
    return {
        "tamil_ladder_all_valid": all_valid,
        "tamil_ladder_matches_real_model_exactly": matches_exactly,
        "invalid_rank_rejected": invalid_rejected,
    }


def select_model_and_escalate_cost_tier() -> dict[str, Any]:
    """The real, synchronous, 0-arg `llm` capability: selects a real model from Madras's real,
    offline, zero-cost free-fleet catalog (`model_catalog.ModelCatalog.with_free_fleet()`, the
    same seed the task router/leaderboard consume) matching a real criterion (tool-calling
    support), and exercises the REAL cost-tier escalation cascade (`cost.escalate`) -- proving
    D49's "llm≈capability-call→model" mapping without any live network call or real spend,
    unlike a genuine model completion (which needs both)."""
    catalog = ModelCatalog.with_free_fleet()
    tool_calling_models = catalog.filter(tool_call=True)
    selected = tool_calling_models[0] if tool_calling_models else None
    escalated_from_free = escalate(CostTier.FREE)
    escalated_from_premium = escalate(CostTier.PREMIUM)  # fixed point
    return {
        "selected_model_id": selected.id if selected else None,
        "selected_model_supports_tools": bool(selected and selected.tool_call),
        "tool_calling_model_count": len(tool_calling_models),
        "escalated_from_free": escalated_from_free.value,
        "escalated_from_premium_is_fixed_point": escalated_from_premium == CostTier.PREMIUM,
    }


__all__ = [
    "append_and_verify_audit_entry",
    "check_tool_permissions",
    "emit_and_verify_trace_span",
    "remember_and_recall_quick_add",
    "select_model_and_escalate_cost_tier",
    "validate_rank_against_real_model",
]
