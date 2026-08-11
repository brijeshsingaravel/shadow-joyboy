"""The `.tamil` v0 interpreter — a lowered Nadi module -> a live AgentConfig (RFC-0002 §7.2, T5).

Interpreted, not compiled: lowers the parsed goal to **Nadi** (the `.tamil-IR` seam, §7.1) and
calls the same `factory/spawn.py` path every hand-authored or Compiler-emitted agent goes
through -- no bypass, governed by construction.

**Reads the IR, not the AST (s59).** Every decision here -- rank floor, capability set,
working-set size, intent -- comes from the lowered module. This module no longer knows what a
`Govern` or a `Call` node looks like, so re-spelling the surface syntax (`{ }`->`[ ]`,
`govern`->`governed`, `call`->`invoke`) cannot reach it. Nadi's `walk()` also recurses into
regions by construction, which is what structurally closed the s59 hole where a capability
inside an `if` escaped both validation and sandbox gating.

The deterministic goal->AgentConfig bridge (`governance-check` -> rank, `capability-call` names
-> resolved toolsets via `resolve_toolsets()`); it makes no LLM call -- that is
`compiler/compile.py`'s separate natural-language-intent flow, a different door into the same
factory.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from madras_capabilities.catalog import Catalog
from madras_capabilities.resolve import resolve_toolsets
from tamil_lang import Goal, Govern, Statement, assign_ids
from tamil_lang.nadi import (
    NadiModule,
    capability_names,
    governance_checks,
    intent_of,
    lower_to_nadi,
)

from madras.dsl.crossing import CrossingDecision, CrossingVerdict, decide_crossing
from madras.dsl.sandboxed import requires_sandbox_ids
from madras.factory.spawn import AgentRecord, spawn_agent_preview
from madras.security.permissions import PermissionEngine, PermissionRule

if TYPE_CHECKING:  # only ever tested for `is None`; see `crossing_receipt.py`'s identical note.
    # `tools/sandbox.py` does `from madras.config import settings` to choose a backend, so
    # importing it at module scope would put the VAULT LOADER behind a parameter this module never
    # calls -- and `interpret()` is what the base-01 receiver imports to execute an arrival.
    from madras.tools.sandbox import Sandbox

# Ordered low -> high, matching models.agent_spec.Rank exactly (RFC-0002 kernel has no rank
# vocabulary of its own -- `govern rank >= N` maps onto Madras's real rank ladder).
RANK_LEVELS = ["intern", "junior", "specialist", "senior", "principal", "legend"]
_GOVERN_RE = re.compile(r"^rank\s*(>=|<=|==|!=|>|<)\s*(\d+)$")


class UngovernedGoal(ValueError):
    """A `.tamil` goal with no `govern rank ...` statement.

    v0 requires an explicit rank floor: **Aram** (right action toward others) applies to the
    language itself, not only to the runtime it targets. A goal that never says who it acts for
    is refused rather than run at some assumed-safe default -- deny-by-default is how that
    principle is enforced, not what it means.
    """


class UnrecognizedGovernCheck(ValueError):
    """A `govern` check that isn't a `rank <op> <int>` comparison -- v0's only supported
    governance shape; richer checks (scope, cred_policy, ...) are a later Compiler concern."""


class WorkingSetTooLarge(ValueError):
    """A goal's whole-tree node count exceeds the `V_max` ceiling (RFC-0002 §5.1's "Bounded"
    law, §5.2's elastic box) -- fails closed, the same posture as an ungoverned goal or an
    unknown capability. The box is enforced here (the backend/interpreter side) as well as by
    `tamil_lang.fits_in_box` itself (the open front-end side) -- both language and compiler."""


class CrossingRequired(Exception):
    """The goal breaches `V_max`, a destination exists, and the crossing was AUTHORISED.

    Raised rather than performed because `interpret()` is synchronous and returns an
    `AgentRecord`, while the transport is async and returns the far end's result. Making
    `interpret()` async to accommodate a case almost no caller hits would change every caller;
    instead the split already drawn between `crossing.py` (judgment, pure) and
    `crossing_transport.py` (transport, I/O) is drawn once more at this boundary.

    Not a `ValueError`, unlike its neighbours here: this is not a refusal. Every other exception in
    this module means "no". This one means "yes, elsewhere", and typing it as a sibling of the
    fail-closed family would invite a caller's `except ValueError` to swallow an authorised
    crossing into a refusal.

    The `decision` travels with it so the caller carries out what was already authorised rather
    than re-deciding it -- a caller free to re-derive the verdict is a second governance path.
    """

    def __init__(self, decision: CrossingDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


class UntrustedCapabilityRequiresSandbox(ValueError):
    """A goal calls a capability that isn't trusted to run unsandboxed (RFC-0002 §5's
    "Sandboxed" law, `dsl/sandboxed.py`) but no `Sandbox` was supplied to interpret it with --
    fails closed, same posture as `WorkingSetTooLarge`. The caller (typically
    `escalation.route()`) must provision a real `Sandbox` via `sandboxed.sandbox_for_goal()`
    first and pass it in."""


def _rank_from_check_strings(checks: list[str]) -> str:
    """The rank-ladder derivation itself, over raw `governance-check` texts.

    Split out (s59) so the SAME derivation serves both the AST route (`rank_from_govern`, kept
    for T9's executor) and the Nadi route (`interpret`), rather than the two drifting apart --
    which is precisely how the three hand-written tree-walkers s59 audited went wrong."""
    for check in checks:
        match = _GOVERN_RE.match(check)
        if match is None:
            raise UnrecognizedGovernCheck(f"unsupported govern check: {check!r}")
        op, level_str = match.groups()
        level = int(level_str)
        if op in (">=", ">"):
            idx = level if op == ">=" else level + 1
        elif op in ("<=", "<"):
            idx = level if op == "<=" else level - 1
        else:  # == / !=
            idx = level
        idx = max(0, min(idx, len(RANK_LEVELS) - 1))
        return RANK_LEVELS[idx]
    raise UngovernedGoal("goal has no `govern rank ...` statement -- v0 requires an explicit floor")


def rank_from_govern(body: list[Statement]) -> str:
    """Public (T9's minimal agent loop): the same rank-ladder derivation `interpret()` uses,
    reused by any other executor that must honor the same "no ungoverned goal" doctrine without
    duplicating this logic. Kept on the AST because T9's executor holds a statement list, not a
    lowered module; both routes share `_rank_from_check_strings` so they cannot diverge."""
    return _rank_from_check_strings([n.check for n in body if isinstance(n, Govern)])


def interpret(
    goal: Goal,
    *,
    agents_dir: Path,
    catalog: Catalog,
    name: str,
    archetype: str,
    neighborhood: str,
    v_max: int | None = None,
    sandbox: Sandbox | None = None,
    destination: str | None = None,
    permissions: PermissionEngine | None = None,
    rules: list[PermissionRule] | None = None,
) -> AgentRecord:
    """Interpret one Kural goal into a live, governed `AgentConfig`.

    Runs via `spawn_agent_preview()` -- zero disk side effects, matching the Compiler's own
    "no execution" preview promise and RFC-0002 §7.2's "ships now, zero new infra" scope. Raises
    `UnknownCapability`/`CapabilityNotBuilt` (from `resolve_toolsets`) if a `capability-call`
    references anything not real and built -- fails closed, not silently.

    `v_max`, if given, is the elastic box's ceiling (§5.1/§5.2), now measured over the lowered
    module; `WorkingSetTooLarge` raises if the program doesn't fit -- fails closed, same posture
    as an ungoverned goal. (The IR count is verified equal to the AST count on every real stdlib
    program and every construct -- see `test_walkers_reach_every_node.py`.) `assign_ids()` still
    runs on the AST because Mugavari addresses are consumed by the COMPILED path, which has not
    migrated yet.

    `sandbox`, if given, is the real isolation the Sandboxed law provisioned for this goal
    (§5's fourth runtime law) -- if any resolved capability isn't trusted to run unsandboxed
    (`sandboxed.is_trusted`) and `sandbox` is `None`, `UntrustedCapabilityRequiresSandbox` raises
    fail-closed rather than silently interpreting untrusted work as if it were safe.
    """
    # THE SEAM (s59). Lower once; every decision below reads the IR, never the AST. The
    # interpreter no longer knows what a `Govern` or a `Call` node looks like, so re-spelling
    # the surface syntax cannot reach it -- which is the entire point of Nadi (RFC-0002 §7.1).
    module = lower_to_nadi(goal)

    # `assign_ids` still runs on the AST: Mugavari addresses are consumed by the COMPILED path
    # (`kollan_cache` keys its result cache by Mugavari ID), which still reads the AST. Addressing
    # migrates when `kollan` does, not before -- disclosed rather than dressed up as a total seam.
    # It stays on THIS door only: `interpret_module` receives no AST to address, and the receiver
    # that calls it does not run the compiled path.
    assign_ids(goal)
    return interpret_module(
        module,
        agents_dir=agents_dir,
        catalog=catalog,
        name=name,
        archetype=archetype,
        neighborhood=neighborhood,
        v_max=v_max,
        sandbox=sandbox,
        destination=destination,
        permissions=permissions,
        rules=rules,
    )


def interpret_module(
    module: NadiModule,
    *,
    agents_dir: Path,
    catalog: Catalog,
    name: str,
    archetype: str,
    neighborhood: str,
    v_max: int | None = None,
    sandbox: Sandbox | None = None,
    destination: str | None = None,
    permissions: PermissionEngine | None = None,
    rules: list[PermissionRule] | None = None,
) -> AgentRecord:
    """Interpret an already-lowered Nadi module -- **the door arriving work comes through.**

    A crossing carries a `NadiModule` (`crossing_transport`/`crossing_grpc` both send the lowered
    IR), so the base-01 receiver holds one of these and never an AST. Before s62 it had nothing to
    call: `interpret()` demanded a Kural `Goal`, and the receiver could only validate the arrival's
    shape and report it.

    This is the same body `interpret()` has always run, not a second implementation -- `interpret()`
    now lowers and delegates here. Two definitions of "what does this program mean" would drift, and
    drift between the local door and the arriving-work door is exactly how a receiver ends up
    governing differently from the sender it was built to double-check.

    Everything below reads only the IR, which is why this split costs nothing: since s59 no decision
    here has needed the AST. The one thing that did -- `assign_ids()` for Mugavari addressing -- is
    consumed by the COMPILED path and stays on `interpret()`, disclosed rather than quietly dropped.
    """
    # Phase P slice 2. This was an unconditional `raise WorkingSetTooLarge` that never consulted
    # the crossing decision -- the judgment existed and nothing called it. `decide_crossing` uses
    # `working_set_size`, which is exactly what `fits_in_box` computes, so a breach is detected
    # identically; only what HAPPENS on a breach can now differ, and only when a destination was
    # supplied. With `destination=None` (every caller before this slice) CROSS is unreachable,
    # so the old behaviour is not merely preserved -- it is the only reachable outcome.
    decision = decide_crossing(
        module, v_max=v_max, destination=destination, permissions=permissions, rules=rules
    )
    if decision.verdict is CrossingVerdict.REFUSE:
        raise WorkingSetTooLarge(
            f"goal {intent_of(module)!r} exceeds the V_max ceiling ({v_max}) -- "
            f"the elastic box: {decision.reason}"
        )
    if decision.verdict is CrossingVerdict.CROSS:
        raise CrossingRequired(decision)

    rank = _rank_from_check_strings(governance_checks(module))
    capability_ids = capability_names(module)
    if capability_ids:
        resolve_toolsets(capability_ids, catalog)  # validate now; raises on unknown/unbuilt
        if sandbox is None and requires_sandbox_ids(capability_ids, catalog):
            raise UntrustedCapabilityRequiresSandbox(
                f"goal {intent_of(module)!r} calls an untrusted capability but no sandbox was "
                "provisioned -- see dsl.sandboxed.sandbox_for_goal()"
            )

    role_data: dict[str, object] = {
        "name": name,
        "archetype": archetype,
        "neighborhood": neighborhood,
        "rank": rank,
        "origin": "immigrant",
        "capability_summary": intent_of(module),
        "capabilities": capability_ids,
        "skills": [],
        "execution": {"default_pattern": "react"},
    }
    return spawn_agent_preview(agents_dir=agents_dir, role_name=name, role_data=role_data)


__all__ = [
    "UngovernedGoal",
    "UnrecognizedGovernCheck",
    "UntrustedCapabilityRequiresSandbox",
    "WorkingSetTooLarge",
    "interpret",
    "interpret_module",
    "rank_from_govern",
]
