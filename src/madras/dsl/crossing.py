"""The crossing decision (Phase P) -- what happens when a goal breaches `V_max`.

RFC-0002 §5.8/§5.9 and D78 define the path: **Core breaches `V_max` -> edge -> cloud**. Today that
breach has exactly one outcome -- `interpreter.interpret()` raises `WorkingSetTooLarge` and
refuses. Phase P turns it into a routing decision, and this module is the judgment half of that,
deliberately separated from the machinery that moves anything.

**Why the judgment is its own module.** Failing closed is a GOVERNANCE property; crossing is a
CAPACITY response. Collapsing them would be a real mistake:

- refusing because a program is too large for this machine is a fact about the machine;
- shipping someone's work to another machine is a governed act that crosses the Contribution
  boundary, which is exactly the shell D78 puts `governance-check` at.

So "too big" must never quietly mean "send it elsewhere". With no destination configured the answer
stays REFUSE -- deny-by-default (Aram) applies to capacity precisely as it applies to capability.
A user's work does not leave their machine because it grew.

**Three outcomes, not a boolean**, because a boolean cannot express refusal.

The decision is a pure function of `(module, v_max, destination)`: no I/O, no live node, no side
effects. That is what makes the governance-critical part testable on its own, before any of the
transport exists.

**The permission gate.** Capacity warrants a crossing; it does not AUTHORISE one. Crossing to a
destination is a governed act, so the decision routes through the existing
`security.PermissionEngine` rather than growing a second permission model -- the same engine, the
same `Decision` vocabulary, the same rules a capability call is checked against.

An **absent engine means REFUSE**, not "cross anyway". That is deny-by-default applied honestly: a
crossing nobody authorised is exactly what Aram forbids, and `interpreter.py` already holds this
line for the language itself ("v0 requires an explicit rank floor").

`ASK` also refuses, for now. It means a human should confirm, and there is no interactive path
here -- so the honest answer is no, with a reason saying why, rather than a silent yes.

---

**On the name (s61).** This module was called `spill` until a grep showed the word already meant
*three different things* in this repo:

1. **register spilling** -- `kollan/x86_64.py`, extensively ("spill an incoming ABI", "stack-spilled
   param", `test_a_call_with_five_args_spills_the_fifth_to_the_stack`). The compiler sense, in a
   repo that contains a compiler.
2. **context offload to a file** -- `context/offload.py` ("above this, spill to a file").
3. this module -- moving work to another machine.

Three meanings one directory apart, all shaped like "doesn't fit, move it": close enough to be
confused, far enough apart to matter. `crossing` was not invented for the rename -- the prose above
had already reached for it repeatedly ("the crossing is ungoverned", "a crossing nobody
authorised") while the module kept the wrong name. **The writing knew first.**

It also names the meaning rather than the mechanism: what this IS (a governed boundary being
crossed) rather than what it looks like (stuff overflowing). Mechanisms get refactored; meanings
survive. `offload` was the other candidate and is already taken by `context/offload.py`.

`LOCAL` became `HERE` because `sandboxed.py` uses "local" to mean *no real isolation* -- close to
the opposite safety connotation. `target` became `destination` because in a repo with a code
generator, a "target" is what you compile FOR.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from tamil_lang.nadi import NadiModule, working_set_size

from madras.security.permissions import Decision, PermissionEngine, PermissionRule


class CrossingVerdict(str, Enum):
    """What to do with a goal, once its working set has been measured against the ceiling."""

    HERE = "here"
    """Runs on this machine. Either it fits, or there is no ceiling to breach."""

    CROSS = "cross"
    """Breaches the ceiling and a destination exists -- route outward, return to origin."""

    REFUSE = "refuse"
    """Breaches the ceiling with nowhere to send it. Fails closed, as `interpret()` does today."""


@dataclass(frozen=True)
class CrossingDecision:
    """A decision plus the measurement behind it.

    `reason` carries the actual numbers deliberately: a routing decision that cannot say WHY it
    routed is unauditable, and every outward crossing is meant to be auditable (D78). This is the
    string an audit record quotes."""

    verdict: CrossingVerdict
    reason: str
    destination: str | None = None


CROSSING_TOOL = "crossing"
"""The tool name a crossing is checked under, so existing `PermissionRule`s can match it the same
way they match any other governed act."""


def decide_crossing(
    module: NadiModule,
    *,
    v_max: int | None,
    destination: str | None,
    permissions: PermissionEngine | None = None,
    rules: list[PermissionRule] | None = None,
) -> CrossingDecision:
    """Decide whether a lowered goal runs here, crosses outward, or is refused.

    `v_max=None` means "unbounded here" -- the same meaning `interpret()` already gives it. An
    absent ceiling cannot be breached, so there is nothing to route.

    Measured with `nadi.working_set_size`, the SAME measurement the elastic box and the compiled
    path use (s59 row 3). Deliberately not a second size calculation: two implementations of one
    measurement drifting apart is the defect shape s59 found six times over, and here the two
    would disagree about whether a program is allowed to leave the machine.
    """
    size = working_set_size(module)

    if v_max is None:
        return CrossingDecision(CrossingVerdict.HERE, f"no ceiling set (size={size})")

    if size <= v_max:
        return CrossingDecision(CrossingVerdict.HERE, f"fits (size={size}, v_max={v_max})")

    if destination is None:
        # Fails closed. This is the behaviour `interpret()` has today and it is preserved on
        # purpose: having nowhere to send work is not a licence to run it anyway, and "too big"
        # is not consent to move it.
        return CrossingDecision(
            CrossingVerdict.REFUSE,
            f"exceeds the box and no crossing destination is configured "
            f"(size={size}, v_max={v_max})",
        )

    # Capacity warrants the crossing; it does not authorise it. An unauthorised crossing refuses.
    if permissions is None:
        return CrossingDecision(
            CrossingVerdict.REFUSE,
            f"exceeds the box and the crossing is ungoverned -- no PermissionEngine was supplied "
            f"(size={size}, v_max={v_max}, destination={destination!r})",
        )

    verdict = permissions.check(
        tool=CROSSING_TOOL, toolset="runtime", args={"destination": destination}, rules=rules
    )
    if verdict is Decision.DENY:
        return CrossingDecision(
            CrossingVerdict.REFUSE,
            f"exceeds the box but the crossing to {destination!r} was denied "
            f"(size={size}, v_max={v_max})",
        )
    if verdict is Decision.ASK:
        return CrossingDecision(
            CrossingVerdict.REFUSE,
            f"exceeds the box and the crossing to {destination!r} needs confirmation, which this "
            f"path cannot obtain -- refusing rather than assuming yes (size={size}, v_max={v_max})",
        )

    return CrossingDecision(
        CrossingVerdict.CROSS,
        f"exceeds the box, routing outward (size={size}, v_max={v_max})",
        destination=destination,
    )


__all__ = ["CROSSING_TOOL", "CrossingDecision", "CrossingVerdict", "decide_crossing"]
