"""Dynamic per-tenant capability resolution — governed ReBAC-style entitlements (row 83).

eve's `defineDynamic` resolves config at runtime; the 2026 leaders (OpenAI/SpiceDB, Auth0 FGA, Oso,
Cedar) go further — per-caller RELATIONSHIP/policy-based, **deny-by-default**, least-privilege
resource scoping, delegation-aware, continuously enforced. This is that layer OVER the static
`AgentConfig`: capabilities resolved per-caller by an injectable `EntitlementPolicy`
(caller → tenant/plan/roles/flags → entitlements), **double-bound least-privilege**
(`effective = agent-declared ∩ caller-entitled` — never escalate past either the declared ceiling or
the caller's grants), `delegate` hands a sub-agent only a SUBSET, tenant-isolated + audited +
re-evaluable. Pure; the policy is injectable (a ruleset now, an OpenFGA/Cedar adapter later).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# stdlib-only; composes the static loader's AgentConfig (its declared set is the ceiling).


@dataclass(frozen=True)
class AuthContext:
    tenant: str = ""
    plan: str = "free"
    roles: frozenset[str] = frozenset()
    flags: frozenset[str] = frozenset()


# Policy: given the caller's auth, which capabilities are they ENTITLED to? Deny-by-default
# (returns only what's granted). Injectable — a ruleset, or an OpenFGA/Cedar/Oso adapter.
EntitlementPolicy = Callable[[AuthContext], "set[str]"]


@dataclass
class ResolvedConfig:
    capabilities: list[str]  # effective = declared ∩ entitled (least-privilege, sorted)
    instructions: list[str]
    tenant: str
    plan: str
    denied: list[str]  # declared caps the caller was NOT entitled to (transparency)


@dataclass
class RuleEntitlementPolicy:
    """A declarative deny-by-default policy (stand-in for an external FGA/Cedar engine): a caller's
    entitlements = the UNION of what their plan, roles, and flags grant. Anything not granted is
    denied."""

    plan_caps: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    role_caps: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    flag_caps: dict[str, set[str]] = field(default_factory=dict[str, set[str]])

    def __call__(self, auth: AuthContext) -> set[str]:
        granted: set[str] = set(self.plan_caps.get(auth.plan, set()))
        for role in auth.roles:
            granted |= self.role_caps.get(role, set())
        for flag in auth.flags:
            granted |= self.flag_caps.get(flag, set())
        return granted


@dataclass
class EntitlementResolver:
    policy: EntitlementPolicy
    tenant_instructions: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    audit: Callable[[dict[str, Any]], None] | None = None

    def _audit(self, event: str, **kw: Any) -> None:
        if self.audit is not None:
            self.audit({"event": f"entitlement_{event}", **kw})

    def resolve(
        self, *, declared_capabilities: list[str], base_instructions: list[str], auth: AuthContext
    ) -> ResolvedConfig:
        """Resolve the effective per-caller config. `declared_capabilities` is the agent's declared
        set (the rank-gate ceiling). effective = declared ∩ entitled (double-bound least-privilege);
        a cap the policy grants but the agent didn't declare is NOT added (no escalation)."""
        declared = set(declared_capabilities)
        entitled = set(self.policy(auth))
        effective = declared & entitled
        denied = sorted(declared - effective)
        instructions = list(base_instructions) + self.tenant_instructions.get(auth.tenant, [])
        self._audit(
            "resolve",
            tenant=auth.tenant,
            plan=auth.plan,
            effective=sorted(effective),
            denied=denied,
        )
        return ResolvedConfig(sorted(effective), instructions, auth.tenant, auth.plan, denied)

    def delegate(self, *, granted: list[str], subset: list[str]) -> list[str]:
        """Hand a sub-agent only a SUBSET of the caller's effective set (scoped authority). Anything
        requested but not held is dropped — you can't delegate authority you don't have."""
        held = set(granted)
        requested = set(subset)
        out = sorted(requested & held)
        dropped = sorted(requested - held)
        if dropped:
            self._audit("delegate_clipped", granted=out, dropped=dropped)
        return out
