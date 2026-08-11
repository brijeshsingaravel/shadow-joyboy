"""Spawn an agent: load config, resolve bundle references, return a record."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from madras.factory.loader import LoaderError, load_agent_config, validate_role_data
from madras.models.agent_config import AgentConfig, Rank
from madras.models.tool_bundle import ToolBundleSpec


@dataclass
class AgentRecord:
    """In-memory view of a spawned agent. Phase 0 — no DB persistence yet."""

    config: AgentConfig
    bundle_specs: list[ToolBundleSpec] = field(default_factory=list[ToolBundleSpec])
    spawned_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))


def _load_bundle(agents_dir: Path, ref_name: str, agent_rank: Rank) -> ToolBundleSpec:
    """H6 (tamil-and-backend-spatial): route through BundleResolver, same rank gate
    factory/loader.py already enforces for `toolsets` -- `tool_bundles` refs are a
    separate AgentConfig field and were bypassing it via a raw yaml read.

    The import is FUNCTION-LOCAL, matching `factory/loader.py`'s own identical import: while
    `tools/resolver.py` itself is light (yaml + two models), `madras/tools/__init__.py` re-exports
    `MCPClient`, so importing it at module scope drags the whole `mcp` SDK -- ~85 modules including
    the `mcp.server.fastmcp` server framework -- onto anything that imports `spawn`. The base-01
    crossing receiver imports `spawn` (via `interpret`) and serves no MCP at all.
    """
    from madras.tools.resolver import BundleResolutionError, BundleResolver

    try:
        return BundleResolver(agents_dir=agents_dir).resolve(ref_name, agent_rank=agent_rank)
    except BundleResolutionError as exc:
        raise LoaderError(str(exc)) from exc


def spawn_agent(*, agents_dir: Path, role_name: str) -> AgentRecord:
    """Load+validate the agent, resolve its tool bundles, return a record."""
    config = load_agent_config(agents_dir=agents_dir, role_name=role_name)
    bundle_specs = [_load_bundle(agents_dir, ref.name, config.rank) for ref in config.tool_bundles]
    return AgentRecord(config=config, bundle_specs=bundle_specs)


def spawn_agent_preview(
    *, agents_dir: Path, role_name: str, role_data: dict[str, Any]
) -> AgentRecord:
    """Same as spawn_agent, but role_data is validated in memory -- no role file is
    written or read from disk. Used by the Compiler's guarded preview so a preview
    compile has zero disk side effects, matching the "no execution" UI promise."""
    config = validate_role_data(agents_dir=agents_dir, role_name=role_name, role_data=role_data)
    bundle_specs = [_load_bundle(agents_dir, ref.name, config.rank) for ref in config.tool_bundles]
    return AgentRecord(config=config, bundle_specs=bundle_specs)
