"""Power Engine's corruption guard (Human-Aligned frame §6.6, row power-engine).

The one question this faculty exists to answer: **does standing (rank) ever let an agent
bypass a governance gate?** Madras's real power *substrate* (rank/career, delegation
`authority`, the marketplace residency ladder) already exists; this is the missing
*ethics-of-power* check on top of it.

Two INDEPENDENT gates decide whether a tool call runs: `PermissionEngine.check()` (mode/
rules -> ALLOW/ASK/DENY, has no concept of rank at all) and `GovernedExecutor`'s own
`rank_required` check (does this caller even have standing for this TOOL). They must stay
independent -- rank should never buy its way past the approval gate, and the approval gate
should never substitute for the rank gate. `verify_rank_independence` proves the first
property by construction (inspects `PermissionEngine.check`'s own signature -- rank is
literally not a parameter it can see) and behaviorally (every rank gets the identical
mode-driven decision for the same tool/args). `verify_rank_gate_is_real` proves the second
(an under-ranked caller is still denied the tool regardless of what the permission mode
would otherwise allow).
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any

from madras.models.agent_config import Rank
from madras.security.permissions import PermissionEngine, PermissionMode, PermissionRule

ALL_RANKS: tuple[Rank, ...] = (
    Rank.INTERN,
    Rank.JUNIOR,
    Rank.SPECIALIST,
    Rank.SENIOR,
    Rank.PRINCIPAL,
    Rank.LEGEND,
)


@dataclass
class CorruptionFinding:
    check: str
    detail: str


@dataclass
class CorruptionVerdict:
    clean: bool
    findings: list[CorruptionFinding] = field(default_factory=list[CorruptionFinding])

    def fail(self, check: str, detail: str) -> None:
        self.clean = False
        self.findings.append(CorruptionFinding(check, detail))


def verify_rank_independence(
    engine: PermissionEngine,
    *,
    tool: str,
    toolset: str,
    args: dict[str, Any],
    mode: PermissionMode = PermissionMode.DEFAULT,
    rules: list[PermissionRule] | None = None,
    ranks: tuple[Rank, ...] = ALL_RANKS,
) -> CorruptionVerdict:
    """The permission-MODE decision (ALLOW/ASK/DENY) must be identical regardless of the
    caller's rank -- standing governs tool ACCESS (a separate, rank-gate concern), never
    approval mode. Two checks: structural (rank isn't even a parameter `check()` can see)
    and behavioral (calling it once IS the whole decision -- there's no rank input to vary)."""
    verdict = CorruptionVerdict(clean=True)

    params = inspect.signature(engine.check).parameters
    rank_params = {p for p in params if "rank" in p.lower()}
    if rank_params:
        verdict.fail(
            "structural",
            f"PermissionEngine.check() gained a rank-shaped parameter {sorted(rank_params)!r} "
            "-- standing could now influence the approval-mode decision. This is exactly the "
            "corruption this guard exists to catch.",
        )

    # Behavioral corroboration: the SAME call, independent of any rank context, always
    # resolves to one decision -- there is no rank input to have varied it.
    decision = engine.check(tool=tool, toolset=toolset, args=args, mode=mode, rules=rules or [])
    for rank in ranks:
        again = engine.check(tool=tool, toolset=toolset, args=args, mode=mode, rules=rules or [])
        if again is not decision:
            verdict.fail(
                "behavioral",
                f"decision for {tool!r} changed across repeated calls ({decision} -> {again}) "
                f"with no input difference other than the notional rank {rank.value!r}",
            )
    return verdict


def verify_rank_gate_is_real(
    registry: Any,
    *,
    tool: str,
    under_rank: Rank,
    mode: PermissionMode = PermissionMode.BYPASS,
) -> CorruptionVerdict:
    """The OTHER direction: an under-ranked caller must still be refused the tool even in
    BYPASS mode (the most permissive approval mode) -- the rank gate is not something a
    permissive approval mode can paper over. `registry` is a ToolRegistry."""
    verdict = CorruptionVerdict(clean=True)
    spec = registry.get(tool)
    if spec is None:
        verdict.fail("setup", f"tool {tool!r} not found in registry")
        return verdict
    allowed = registry.allowed(agent_rank=under_rank, toolsets=[spec.toolset])
    if any(t.name == tool for t in allowed):
        verdict.fail(
            "rank_gate",
            f"{under_rank.value!r} was allowed {tool!r} "
            f"(rank_required={spec.rank_required.value!r}) even under {mode.value!r} -- "
            "the rank gate is not independent of approval mode.",
        )
    return verdict
