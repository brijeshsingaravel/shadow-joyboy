"""Sandboxed law (RFC-0002 §5/D60's four runtime laws) -- fuzzy/untrusted work runs isolated.

Reuses Madras's existing, already-built `tools/sandbox.py` (`Sandbox` ABC + `build_sandbox()`
factory: local -> docker -> e2b) rather than building new isolation infrastructure -- this is
what RFC-0002's own reference-implementation column names for this law. The genuinely new
piece is the *policy*: deciding when a `.tamil` goal needs isolation at all.

Trust model (a 2026 outlier-sandbox survey confirms the honest posture): a capability is
trusted to run unsandboxed only if (1) the Capability Catalog confirms `build_state: built`
AND (2) it doesn't itself declare a fuzzy/untrusted-work scope (`exec`/`exec.sandbox`/
`computer.control` -- arbitrary code execution or computer control, RFC-0002's own "fuzzy/
untrusted work" language). Rule (1) alone would be dead code in practice: `resolve_toolsets()`
already raises `CapabilityNotBuilt` for anything not `built`, so an interpreted goal never
reaches this module with a not-yet-built capability. Rule (2) is what actually matters --
e.g. `code-execution` IS `build_state: built` (it passes `resolve_toolsets()` cleanly) but its
own `scopes: [exec]` names exactly the kind of work this law exists for. This mirrors the
industry lesson from 2026's agent-sandbox escapes (Bubblewrap/Claude Code, Ona's write-up): a
denylist that tries to enumerate *badness* misses paths; an allowlist keyed off the catalog's
own declared scopes is the fail-closed direction, same posture as `elastic_box`'s
`WorkingSetTooLarge` for the Bounded law. "local" (no real isolation) is never an acceptable
backend for untrusted work here, even though it's `tools/sandbox.py`'s own configured default
for trusted dev use elsewhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from madras_capabilities.catalog import Catalog
from tamil_lang import Bind, Branch, Call, Goal, Loop, Statement

if TYPE_CHECKING:  # `Sandbox` is only ever an annotation here, and annotations are strings.
    from madras.tools.sandbox import Sandbox

_TRUSTED_BUILD_STATE = "built"
_NO_ISOLATION_BACKEND = "local"

# A capability that declares any of these is "fuzzy/untrusted work" per RFC-0002's own law
# language, regardless of build_state -- arbitrary code execution or direct computer control.
_FUZZY_SCOPES = {"exec", "exec.sandbox", "computer.control"}


class UntrustedCapabilityUnsandboxed(ValueError):
    """Raised when a goal calls an untrusted capability and the only backend available/chosen
    provides no real isolation (fail-closed, never silently run untrusted work unsandboxed)."""


def _call_names(statements: list[Statement]) -> list[str]:
    names: list[str] = []
    for stmt in statements:
        if isinstance(stmt, Call):
            names.append(stmt.name)
        elif isinstance(stmt, Bind):
            if isinstance(stmt.call, Call):
                names.append(stmt.call.name)
        elif isinstance(stmt, Branch):
            names.extend(_call_names(stmt.then))
            names.extend(_call_names(stmt.otherwise))
        elif isinstance(stmt, Loop):
            names.extend(_call_names(stmt.body))
    return names


def goal_call_names(goal: Goal) -> list[str]:
    """Every capability name a goal calls, walking the same child-slot shape as
    `mugavari`/`elastic_box` (Goal.body / Branch.then+otherwise / Loop.body / Bind.call)."""
    return _call_names(goal.body)


def is_trusted(capability_name: str, catalog: Catalog) -> bool:
    """Trusted only if the catalog lists this exact name with `build_state: built` AND it
    declares no fuzzy/untrusted-work scope (`_FUZZY_SCOPES`). An unknown name (not in the
    catalog at all) is untrusted -- fail-closed, never assume trust for something the catalog
    doesn't even list."""
    for cap in catalog.capabilities:
        if cap.id == capability_name:
            if cap.build_state != _TRUSTED_BUILD_STATE:
                return False
            declared = set(cap.scopes) | set(cap.implements)
            return not (declared & _FUZZY_SCOPES)
    return False


def requires_sandbox(goal: Goal, catalog: Catalog) -> bool:
    """True if any capability the goal calls is untrusted."""
    return requires_sandbox_ids(goal_call_names(goal), catalog)


def requires_sandbox_ids(capability_ids: list[str], catalog: Catalog) -> bool:
    """Like `requires_sandbox`, but takes already-extracted capability ids directly (the
    interpreter already has these from the IR, via `nadi.capability_names()`)."""
    return any(not is_trusted(name, catalog) for name in capability_ids)


def sandbox_for_goal(
    goal: Goal,
    *,
    catalog: Catalog,
    session_id: str,
    backend: str | None = None,
) -> Sandbox | None:
    """Build a real isolated `Sandbox` (Docker/E2B) when `goal` calls anything untrusted; `None`
    when every call is a catalog-confirmed `built` capability (no isolation overhead needed).
    Construction alone does no I/O (`tools/sandbox.py`'s classes only touch Docker/E2B on
    `.start()`) -- the caller decides when to actually start it."""
    # Imported HERE, not at module scope. `tools/sandbox.py` reaches `madras.config` to pick a
    # backend, and `madras.config` is the vault loader -- so a module-level import would drag the
    # whole secrets surface along behind the two pure predicates below it (`is_trusted`,
    # `requires_sandbox_ids`), which ask nothing but a catalog. That matters concretely: the
    # crossing receiver on base-01 needs those predicates and must not hold a vault loader.
    from madras.tools.sandbox import build_sandbox

    if not requires_sandbox(goal, catalog):
        return None
    chosen = backend or "docker"
    if chosen == _NO_ISOLATION_BACKEND:
        raise UntrustedCapabilityUnsandboxed(
            f"goal calls an untrusted capability but backend={_NO_ISOLATION_BACKEND!r} "
            "provides no real isolation"
        )
    return build_sandbox(session_id=session_id, backend=chosen)


def sandbox_for_capabilities(
    capability_ids: list[str],
    *,
    catalog: Catalog,
    session_id: str,
    backend: str | None = None,
) -> Sandbox | None:
    """`sandbox_for_goal` for the IR path: takes already-extracted capability ids instead of a
    `Goal`, the same shape `requires_sandbox_ids` takes and the same shape the interpreter and the
    crossing receiver already hold (via `nadi.capability_names()`).

    Exists because the receiver never holds a `Goal` -- it receives lowered Nadi IR. Before this,
    `requires_sandbox_ids` could tell base-01 that an arrival NEEDED isolation while there was no
    supported way to build it, so the only honest answer was to refuse (`sandbox=None`).

    Same rules as the goal path, deliberately -- including refusing ``local``. A "sandbox" with no
    real isolation is worse than none: the arrival would run unprotected while looking held.
    Construction does no I/O; the caller decides when to `.start()`, and (s63 founder call) builds
    a FRESH one per arrival so arrivals cannot leak into each other.
    """
    # Imported HERE for the same reason as `sandbox_for_goal` -- `tools/sandbox.py` reaches
    # `madras.config`, the vault loader, which must not land on base-01 behind the pure predicates.
    from madras.tools.sandbox import build_sandbox

    if not requires_sandbox_ids(capability_ids, catalog):
        return None
    chosen = backend or "docker"
    if chosen == _NO_ISOLATION_BACKEND:
        untrusted = [c for c in capability_ids if not is_trusted(c, catalog)]
        raise UntrustedCapabilityUnsandboxed(
            f"arrival calls {', '.join(untrusted)}, which is untrusted, but "
            f"backend={_NO_ISOLATION_BACKEND!r} provides no real isolation"
        )
    return build_sandbox(session_id=session_id, backend=chosen)


__all__ = [
    "UntrustedCapabilityUnsandboxed",
    "goal_call_names",
    "is_trusted",
    "requires_sandbox",
    "requires_sandbox_ids",
    "sandbox_for_capabilities",
    "sandbox_for_goal",
]
