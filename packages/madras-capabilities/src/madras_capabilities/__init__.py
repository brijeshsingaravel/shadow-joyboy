"""The Capability Catalog — runtime-importable interface (E1 Phase A).

Parses Framework/Capabilities/*.md into typed Capability objects the Compiler composes
from. CI-side correctness (well-formed / grounded / woven / registry-synced) is guaranteed
separately by tests/test_lighthouse/test_capability_catalog.py; this package assumes that
guarantee and focuses on runtime use.
"""

from __future__ import annotations

from madras_capabilities.catalog import Catalog, load_catalog
from madras_capabilities.model import Capability
from madras_capabilities.resolve import CapabilityNotBuilt, UnknownCapability, resolve_toolsets
from madras_capabilities.tiers import TIERS, capability_tier, plan_entitlement_policy

__all__ = [
    "TIERS",
    "Capability",
    "CapabilityNotBuilt",
    "Catalog",
    "UnknownCapability",
    "capability_tier",
    "load_catalog",
    "plan_entitlement_policy",
    "resolve_toolsets",
]
