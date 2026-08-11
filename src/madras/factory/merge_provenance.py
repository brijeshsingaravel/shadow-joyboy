"""Merge provenance / explainability (row 14d).

Config merges in Madras are silent — `loader.load_agent_config` (base<-neighborhood<-role) and
`scope_config.merge_scopes` (platform<-seller<-org<-user) both produce a merged dict with no record
of WHICH layer set each value. This adds that record: `merge_with_provenance` tracks, per leaf
key, the layer that set the final value + the full override history — so the Builder can answer
"why is my agent configured this way?" ("persona came from seller, which overrode the platform
default"). Reuses `scope_config`'s flatten/set-dotted machinery; generic over any ordered, named
layer list. Pure stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from madras.factory.scope_config import flatten, set_dotted


@dataclass
class MergeProvenance:
    config: dict[str, Any] = field(default_factory=dict[str, Any])
    origin: dict[str, str] = field(default_factory=dict[str, str])  # dotted-key -> final layer
    history: dict[str, list[str]] = field(
        default_factory=dict[str, list[str]]
    )  # dotted-key -> layers in order


def merge_with_provenance(layers: list[tuple[str, dict[str, Any]]]) -> MergeProvenance:
    """Merge ordered (layer_name, config) pairs (later overrides earlier), recording per leaf key
    the final-value layer + the full override history."""
    result = MergeProvenance()
    for name, cfg in layers:
        for dotted, value in flatten(cfg).items():
            set_dotted(result.config, dotted, value)
            result.origin[dotted] = name
            result.history.setdefault(dotted, []).append(name)
    return result


def explain(prov: MergeProvenance, dotted: str) -> str:
    """Human-readable origin of a single key: who set it + the override chain."""
    if dotted not in prov.origin:
        return f"{dotted}: not set by any layer"
    chain = prov.history[dotted]
    suffix = f" (overrode: {' -> '.join(chain[:-1])})" if len(chain) > 1 else ""
    return f"{dotted} <- {prov.origin[dotted]}{suffix}"


def overridden_keys(prov: MergeProvenance) -> dict[str, list[str]]:
    """The keys more than one layer touched (where an override actually happened)."""
    return {k: v for k, v in prov.history.items() if len(v) > 1}
