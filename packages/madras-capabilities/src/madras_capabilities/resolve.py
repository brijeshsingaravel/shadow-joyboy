"""resolve_toolsets() — capability ids -> the toolset/bundle names they implement.

The compose step of the Compiler pipeline: a user/spec picks capability ids from the
tier-entitled palette; this turns them into the concrete toolset names that populate
AgentConfig.toolsets (E1 Task A3 wires that expansion into factory/loader.py).
"""

from __future__ import annotations

from madras_capabilities.catalog import Catalog


class UnknownCapability(ValueError):
    """A requested capability id is not in the catalog."""


class CapabilityNotBuilt(ValueError):
    """A requested capability exists but isn't build_state == 'built' — can't compose
    an unbuilt capability into a live agent."""


def resolve_toolsets(capability_ids: list[str], catalog: Catalog) -> list[str]:
    toolsets: set[str] = set()
    for cap_id in capability_ids:
        cap = catalog.by_id.get(cap_id)
        if cap is None:
            raise UnknownCapability(f"unknown capability: {cap_id!r}")
        if cap.build_state != "built":
            raise CapabilityNotBuilt(
                f"capability {cap_id!r} is not built (build_state={cap.build_state!r})"
            )
        toolsets.update(cap.implements)
    return sorted(toolsets)
