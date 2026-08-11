"""Overflow-to-backend escalation (RFC-0002 §5.8's "escape velocity" -- device -> edge -> cloud).

`interpret()` (T5) is the device tier: deterministic, no LLM call, fast, but bounded by the
elastic box's `V_max` (T3.4). When a `.tamil` goal's whole working set doesn't fit, escalate to
the backend tier -- the real, already-built async Compiler pipeline (`compile_agent()`) -- for
larger tasks, instead of failing closed outright. Founder's own framing (s55): "when the data
pointer overflows it can come to our backend for larger tasks." Each tier already enforces its
own governance independently (`interpret()`'s rank floor, `compile_agent()`'s
`AuthContext`/entitlement) -- this module only decides which one runs, never bypasses either.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from madras_capabilities.catalog import Catalog
from tamil_lang import Goal, assign_ids, fits_in_box

from madras.compiler.compile import CompileResult, compile_agent
from madras.dsl.interpreter import interpret
from madras.dsl.sandboxed import sandbox_for_goal
from madras.factory.dynamic import AuthContext
from madras.factory.spawn import AgentRecord
from madras.llm.gateway import LLMGateway
from madras.tools.sandbox import Sandbox


@dataclass
class RoutingResult:
    """Which tier actually handled the goal -- never silent about it."""

    escalated: bool
    agent: AgentRecord | None
    compile_result: CompileResult | None = None
    sandbox: Sandbox | None = None


async def route(
    goal: Goal,
    *,
    v_max: int,
    agents_dir: Path,
    catalog: Catalog,
    name: str,
    archetype: str,
    neighborhood: str,
    gateway: LLMGateway,
    model: str,
    auth: AuthContext,
    sandbox_backend: str | None = None,
) -> RoutingResult:
    """Device tier first: if `goal`'s whole tree fits under `v_max`, `interpret()` runs it --
    deterministic, no LLM call (§7.2). On overflow, escalate to the backend tier: the real
    Compiler pipeline (`compile_agent()`), fed `goal.intent` as the outcome. The caller always
    learns which tier ran via `RoutingResult.escalated`, never a silent fallback.

    The Sandboxed law (RFC-0002 §5/D60) is checked regardless of tier: any call to a
    not-yet-`built` capability makes `sandbox_for_goal()` return a real `Sandbox` (unstarted --
    the caller starts it before actually running untrusted work); an all-trusted goal gets
    `sandbox=None`, no isolation overhead."""
    assign_ids(goal)
    sandbox = sandbox_for_goal(goal, catalog=catalog, session_id=name, backend=sandbox_backend)
    if fits_in_box(goal, v_max):
        record = interpret(
            goal,
            agents_dir=agents_dir,
            catalog=catalog,
            name=name,
            archetype=archetype,
            neighborhood=neighborhood,
            v_max=v_max,
            sandbox=sandbox,
        )
        return RoutingResult(escalated=False, agent=record, sandbox=sandbox)

    result = await compile_agent(
        outcome=goal.intent,
        gateway=gateway,
        model=model,
        agents_dir=agents_dir,
        catalog=catalog,
        auth=auth,
    )
    return RoutingResult(escalated=True, agent=result.agent, compile_result=result, sandbox=sandbox)


__all__ = ["RoutingResult", "route"]
