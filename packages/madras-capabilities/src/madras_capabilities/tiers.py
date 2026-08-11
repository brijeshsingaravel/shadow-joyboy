"""Subscription tiering — plan -> entitled capability ids (E1 Task A4).

Tiers are ordered by compute/data COST, not feature-completeness (founder call, s38):
free/light capabilities (chat, light memory, basic research) are cheap to serve and still
generate hardening/Proving-Ground data even from Tourist-tier usage; capabilities with real
compute cost (code execution, browser/computer-use, multi-agent orchestration, media
generation) gate to paid tiers.

CATEGORY_TIER below is the single source of truth (a pure function of category, cumulative:
a capability is entitled at its assigned tier and every tier above it). It is NOT read from
capability-note frontmatter -- the note-level `tier:` field (written by
scripts/tag_capability_tiers.py, same idempotent-mirror pattern as benchmark_tier) is a
generated MIRROR of this mapping for visibility/audit, never the source (avoids a
chicken-and-egg: this policy must work before any note has been tagged).

"always-on" (kind == "substrate") capabilities are the governance/safety floor -- never
gated, excluded from tiering entirely; every plan gets them for free by construction.

RESEARCH NOTE (s38, corrected after founder review): "Safety & Governance" functional/
structural capabilities are NOT tiered features -- every one of the 22 built ones inspected
(approval-doctrine, fail-closed-route-auth, docs-discipline, network-egress-policy,
credential-brokering, multi-rail-guard-scanners, dependency-vuln-scan, test-doctrine,
commitments-tracking, plan-mode, ...) implements a specific ASI0x item or an ENFORCE-list
mechanism that Framework/Governance.md declares "cannot be bypassed by any agent" -- e.g.
approval-doctrine implements the approval!=authorization boundary, fail-closed-route-auth
implements ASI03, network-egress-policy implements ASI05 egress/SSRF blocking. None are
enterprise compliance/reporting add-ons (audit-log retention, SSO/DLP dashboards) -- the
category industry practice DOES legitimately gate (GitHub/Slack/Vercel-style "enterprise
security" tiers add reporting/governance UX on TOP of baseline safety, they never remove
baseline safety from lower tiers). Paywalling these would mean cheap agents are less safe,
directly undermining the "governed by construction" moat (Framework/Agent OS.md). So this
category is set to unlock at "tourist" (every plan, including free) -- the code-level
expression of the ENFORCE-list doctrine, not an oversight.
"""

from __future__ import annotations

from madras_capabilities.catalog import Catalog
from madras.factory.dynamic import AuthContext, RuleEntitlementPolicy

TIERS: list[str] = ["tourist", "resident", "professional", "creator", "enterprise"]

# category -> the tier at which it FIRST becomes entitled (cumulative upward).
# "Frontier" (frontier-model-only capabilities) is enterprise-only by definition (D41: the
# frontier plug-in is disclosed, not free-tier default).
CATEGORY_TIER: dict[str, str] = {
    "Interaction": "tourist",
    "Knowledge & Research": "tourist",
    "Memory": "tourist",
    "Reasoning & Planning": "tourist",
    "Communication & Reach": "resident",
    "Identity & Lifecycle": "resident",
    "Coding": "professional",
    "Action & Environment": "professional",
    "Integration & Apps": "professional",
    "Reasoning & Orchestration": "creator",
    "Creative & Media": "creator",
    "Primitives & Compiler": "creator",
    "Safety & Governance": "tourist",  # ENFORCE-list mechanisms -- never paywalled, see note above
    "Frontier": "enterprise",  # no-op today (all "Frontier" caps are build_state=frontier, unbuilt)
}


def capability_tier(category: str) -> str:
    """The tier a category first unlocks at. Unknown categories default to the top tier
    (fail closed -- never silently free)."""
    return CATEGORY_TIER.get(category, "enterprise")


def plan_entitlement_policy(catalog: Catalog) -> RuleEntitlementPolicy:
    """Build a RuleEntitlementPolicy (factory/dynamic.py's existing policy shape) whose
    plan_caps are cumulative: a plan grants every built, non-substrate capability whose
    category tier is at or below that plan's position in TIERS."""
    tier_index = {tier: i for i, tier in enumerate(TIERS)}
    plan_caps: dict[str, set[str]] = {tier: set() for tier in TIERS}

    for cap in catalog.capabilities:
        if cap.build_state != "built" or cap.kind == "substrate":
            continue
        unlock_tier = capability_tier(cap.category)
        unlock_index = tier_index.get(unlock_tier, len(TIERS) - 1)
        for tier in TIERS[unlock_index:]:
            plan_caps[tier].add(cap.id)

    return RuleEntitlementPolicy(plan_caps=plan_caps)


__all__ = ["CATEGORY_TIER", "TIERS", "AuthContext", "capability_tier", "plan_entitlement_policy"]
