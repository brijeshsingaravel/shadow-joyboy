"""T9's minimal agent loop -- a real `.tamil` GOAL, expressed as actual `.tamil` source, that
composes MULTIPLE of D49's 6 kernel capabilities together in one program (T9.1-T9.7's own
"honest scope" gap, closed here): six separate single-capability proofs are not the same claim
as "a `.tamil` program can re-host a piece of Madras's kernel" -- this is the first thing that
actually reads six-in-one real `.tamil` source and drives all six.

Why this ISN'T built on `compile_goal`'s native x86-64 path: T9.2 established, for a real
correctness reason (an `emit_python_call` stencil's prologue must sit at byte 0 for the
registered unwind info to describe it), that a compiled goal may contain an `ffi_bridge` `Call`
ONLY when it is the goal's one and only statement. Composing six kernel capabilities needs six
`ffi_bridge` calls in ONE goal -- generalizing the unwind-info builder to describe multiple call
sites is real, deferred work (see the founder-approved plan), not attempted here.

The actually-available, already-proven mechanism composes for free: `kollan_bridge.
call_python_object(fn)` compiles + executes + tears down ONE independent stencil per call --
there is no shared unwind-info table between separate calls, so invoking it six times in a row
for six different capabilities is exactly as safe as invoking it once (already proven by every
T9.x test). What's new here is real: a genuine parsed `.tamil` `Goal` (not hand-built Python
objects) drives WHICH capability gets called, in what order, and under what name -- a real
tree-walking dispatch over `Bind`/`Call` nodes, reusing `interpreter.py`'s own
`rank_from_govern` so the "no ungoverned goal" doctrine isn't duplicated or bypassed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tamil_lang import Bind, Call, parse

from madras.dsl.interpreter import rank_from_govern
from madras.dsl.kollan_bridge import call_python_object
from madras.dsl.t9_kernel_capabilities import (
    append_and_verify_audit_entry,
    check_tool_permissions,
    emit_and_verify_trace_span,
    remember_and_recall_quick_add,
    select_model_and_escalate_cost_tier,
    validate_rank_against_real_model,
)

# The registry-of-record for what this loop may dispatch -- D49's full 6-subsystem kernel,
# named exactly as `.tamil` source must spell them in an `ffi python <name>()` call.
T9_CAPABILITIES: dict[str, Callable[[], dict[str, Any]]] = {
    "append_and_verify_audit_entry": append_and_verify_audit_entry,
    "emit_and_verify_trace_span": emit_and_verify_trace_span,
    "remember_and_recall_quick_add": remember_and_recall_quick_add,
    "check_tool_permissions": check_tool_permissions,
    "validate_rank_against_real_model": validate_rank_against_real_model,
    "select_model_and_escalate_cost_tier": select_model_and_escalate_cost_tier,
}


class UnknownT9Capability(ValueError):
    """An `ffi_bridge` `Bind` named something outside `T9_CAPABILITIES` -- fails closed, the
    same posture as `interpreter.py`'s `UngovernedGoal`/`UnrecognizedGovernCheck`, rather than
    silently ignoring a statement this loop doesn't recognize."""


def run_t9_agent_loop(source: str) -> dict[str, object]:
    """Parse real `.tamil` SOURCE TEXT, require a real `govern rank ...` floor (fails closed via
    `rank_from_govern` if absent/malformed -- reused, not reimplemented), then walk the goal's
    body in order: every `bind <name> = ffi python <capability>()` statement dispatches the real
    Python capability through Kollan's native CPython bridge (`kollan_bridge.call_python_object`)
    and the real result is stored under `<name>`. Returns `{bind_name: real_result}` for every
    bind -- proving a single real `.tamil` program can compose multiple of D49's kernel
    capabilities, not just one at a time.

    Any `ffi_bridge` bind naming something outside `T9_CAPABILITIES` raises `UnknownT9Capability`
    -- this is a narrow, T9-scoped loop, not a general `.tamil` executor."""
    (goal,) = parse(source)
    rank_from_govern(goal.body)  # fails closed on an ungoverned/malformed goal; result unused --
    # this loop's dispatch doesn't vary by rank level, only by whether the goal is governed at all.

    results: dict[str, object] = {}
    for node in goal.body:
        if not isinstance(node, Bind) or not isinstance(node.call, Call):
            continue
        if node.call.capability_kind != "ffi_bridge":
            continue
        name = node.call.name
        fn = T9_CAPABILITIES.get(name)
        if fn is None:
            raise UnknownT9Capability(
                f"bind {node.target!r} calls {name!r}, not one of the 6 T9 kernel capabilities: "
                f"{sorted(T9_CAPABILITIES)}"
            )
        results[node.target] = call_python_object(fn)
    return results


__all__ = ["T9_CAPABILITIES", "UnknownT9Capability", "run_t9_agent_loop"]
