"""YAML loader with three-level inheritance: base ← neighborhood ← role.

Merge rules (Kustomize-style):
- Later layers override earlier ones on scalar values
- Dicts merge recursively
- Lists are REPLACED (not concatenated) unless we add an explicit merge marker
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml
from madras_capabilities.catalog import load_catalog
from madras_capabilities.resolve import CapabilityNotBuilt, UnknownCapability, resolve_toolsets

if TYPE_CHECKING:
    from madras.factory.merge_provenance import MergeProvenance
from madras.models.agent_config import AgentConfig

# The Capability Catalog lives at a fixed location relative to this module, independent
# of agents_dir (which a caller may point at a temp/test copy) -- Engineering/src/madras/
# factory/loader.py -> Engineering -> the vault root -> MADRAS AI ECOSYSTEM/Framework/Capabilities
# (s46: nested under the MADRAS AI ECOSYSTEM restructure; was bare Framework/Capabilities).
# MADRAS_CANON_ROOT takes precedence (matches server/app.py's pg_capability_gate route):
# the parents[3] arithmetic resolves correctly on the host dev layout, but breaks inside
# the container (/app/src/... has a different mount structure) -- an E1 Task E2 live-drive
# finding invisible to every pytest run, since pytest always runs on the host.
_ENGINEERING_ROOT = Path(__file__).resolve().parents[3]


def _capabilities_dir() -> Path:
    # Resolved lazily (not a module-level constant) so MADRAS_CANON_ROOT is read fresh
    # on every call -- a constant frozen at import time would miss env changes made
    # after import (e.g. by tests, or by a container's entrypoint setting it late).
    root = (
        Path(os.environ["MADRAS_CANON_ROOT"])
        if os.environ.get("MADRAS_CANON_ROOT")
        else _ENGINEERING_ROOT.parent
    )
    return root / "MADRAS AI ECOSYSTEM" / "Framework" / "Capabilities"


class LoaderError(Exception):
    """Raised when a YAML file is missing, malformed, or fails validation."""


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise LoaderError(f"file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise LoaderError(f"top-level of {path} must be a mapping, got {type(data).__name__}")
    return cast(dict[str, Any], data)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `overlay` onto `base`. Overlay wins on scalars; lists replace."""
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(
                cast(dict[str, Any], result[key]), cast(dict[str, Any], value)
            )
        else:
            result[key] = value
    return result


def _gather_layers(
    *, agents_dir: Path, role_name: str, role_data: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    """The ordered (layer_name, data) pairs `_build_agent_config` merges -- factored out so
    `explain_agent_config` (row 14d, merge provenance) can run the SAME layer resolution
    through `merge_with_provenance` instead of the silent `_deep_merge`."""
    agents_dir = Path(agents_dir)
    base_path = agents_dir / "base_agent.yaml"
    if not base_path.exists():
        raise LoaderError(f"base_agent.yaml not found at {base_path}")
    base_data = _load_yaml(base_path)

    neighborhood = role_data.get("neighborhood")
    if not neighborhood:
        raise LoaderError(f"role {role_name} missing required 'neighborhood' field")

    neighborhood_path = agents_dir / "neighborhoods" / f"{neighborhood}.yaml"
    if not neighborhood_path.exists():
        raise LoaderError(f"neighborhood file not found: {neighborhood_path}")
    neighborhood_data = _load_yaml(neighborhood_path)

    # Strip neighborhood-only metadata fields that don't belong in AgentConfig
    neighborhood_metadata_fields = {"color", "register", "description", "behaviour"}
    for field in neighborhood_metadata_fields:
        neighborhood_data.pop(field, None)

    return [("base", base_data), ("neighborhood", neighborhood_data), ("role", role_data)]


def _build_agent_config(
    *, agents_dir: Path, role_name: str, role_data: dict[str, Any]
) -> AgentConfig:
    """Merge base ← neighborhood ← role_data, validate, resolve toolsets.

    Shared by `load_agent_config` (role_data read from a written role file) and
    `validate_role_data` (role_data held in memory only -- e.g. a Compiler preview
    that must not touch disk). agents_dir is still read from for base_agent.yaml and
    neighborhoods/, both hand-authored and never written by the Compiler.
    """
    layers = _gather_layers(agents_dir=agents_dir, role_name=role_name, role_data=role_data)
    merged: dict[str, Any] = {}
    for _name, data in layers:
        merged = _deep_merge(merged, data)

    try:
        config = AgentConfig.model_validate(merged)
    except Exception as exc:
        raise LoaderError(f"validation failed for {role_name}: {exc}") from exc

    if config.capabilities:
        catalog = load_catalog(_capabilities_dir())
        try:
            resolved_toolsets = resolve_toolsets(config.capabilities, catalog)
        except (UnknownCapability, CapabilityNotBuilt) as exc:
            raise LoaderError(f"capability resolution failed for {role_name}: {exc}") from exc
        config.toolsets = sorted(set(config.toolsets) | set(resolved_toolsets))

    # s46: BundleResolver (tools/resolver.py) -- CLAUDE.md's own architecture rule ("ALL
    # tool bundle resolutions... enforces rank gate") -- had no live caller. Every declared
    # toolset with a bundle YAML now gets its rank checked here, at config-resolve time
    # (defense in depth on top of the existing per-tool rank_required checked at call time
    # by GovernedExecutor). A toolset with no bundle YAML yet is skipped, not failed --
    # bundles are opt-in coverage, not a hard requirement for every toolset.
    _bundles_dir = Path(agents_dir) / "bundles" / "tools"
    if _bundles_dir.is_dir() and config.toolsets:
        from madras.tools.resolver import BundleResolutionError, BundleResolver

        _resolver = BundleResolver(agents_dir=agents_dir)
        for _toolset in config.toolsets:
            if not (_bundles_dir / f"{_toolset}.yaml").exists():
                continue
            try:
                _resolver.resolve(_toolset, agent_rank=config.rank)
            except BundleResolutionError as exc:
                raise LoaderError(f"bundle gate failed for {role_name}: {exc}") from exc

    return config


def load_agent_config(*, agents_dir: Path, role_name: str) -> AgentConfig:
    """Load a single role: base ← neighborhood ← role, validate, return AgentConfig.

    Args:
        agents_dir: path containing base_agent.yaml, neighborhoods/, roles/.
        role_name: stem of the role YAML (e.g., "shadow" → roles/shadow.yaml).
    """
    agents_dir = Path(agents_dir)
    if not (agents_dir / "base_agent.yaml").exists():
        raise LoaderError(f"base_agent.yaml not found at {agents_dir / 'base_agent.yaml'}")
    role_path = agents_dir / "roles" / f"{role_name}.yaml"
    if not role_path.exists():
        # Compiler-written roles live in a separate writable "compiled/" overlay --
        # agents_dir/roles/ is deliberately read-only in the live container to protect
        # hand-authored files from container-drift corruption (E1 Task E2 finding).
        compiled_path = agents_dir / "compiled" / f"{role_name}.yaml"
        if compiled_path.exists():
            role_path = compiled_path
        else:
            raise LoaderError(f"role file not found: {role_path}")

    role_data = _load_yaml(role_path)
    return _build_agent_config(agents_dir=agents_dir, role_name=role_name, role_data=role_data)


def explain_agent_config(*, agents_dir: Path, role_name: str) -> MergeProvenance:
    """Row 14d -- WHY is this agent configured this way? Same base<-neighborhood<-role
    resolution as `load_agent_config`, but through `merge_with_provenance` so every leaf
    key records which layer set it (and the full override chain), not just the final
    silently-merged dict."""
    from madras.factory.merge_provenance import merge_with_provenance

    agents_dir = Path(agents_dir)
    role_path = agents_dir / "roles" / f"{role_name}.yaml"
    if not role_path.exists():
        compiled_path = agents_dir / "compiled" / f"{role_name}.yaml"
        if compiled_path.exists():
            role_path = compiled_path
        else:
            raise LoaderError(f"role file not found: {role_path}")
    role_data = _load_yaml(role_path)
    layers = _gather_layers(agents_dir=agents_dir, role_name=role_name, role_data=role_data)
    result: MergeProvenance = merge_with_provenance(layers)
    return result


def validate_role_data(
    *, agents_dir: Path, role_name: str, role_data: dict[str, Any]
) -> AgentConfig:
    """Validate a role in memory -- no role file is read or required to exist on disk.

    Used by the Compiler's preview path (E1 Task B5 / "no execution" guarded preview):
    proves the emitted spec resolves to a valid, entitled AgentConfig without writing
    anything to agents_dir/compiled/.
    """
    return _build_agent_config(agents_dir=agents_dir, role_name=role_name, role_data=role_data)
