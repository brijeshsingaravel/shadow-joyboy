"""Scope-layer precedence — platform -> seller -> org -> user (row 14c).

A marketplace-mapped override chain: each later layer overrides earlier ones (like the loader's
base<-neighborhood<-role merge), EXCEPT a higher-authority layer can LOCK keys that lower layers may
not override — the enterprise-policy-wins rule (platform safety floors can't be tuned away by a
seller template or an end-user preference). Deny-respecting + audited via the rejected list. Pure
stdlib; composes `factory/loader._deep_merge`'s philosophy with a per-key lock gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

# authority order: earlier = higher authority; later layers override earlier UNLESS locked
SCOPE_ORDER = ("platform", "seller", "org", "user")


@dataclass
class ScopeLayer:
    name: str  # one of SCOPE_ORDER
    config: dict[str, Any] = field(default_factory=dict[str, Any])
    locked: list[str] = field(default_factory=list[str])  # dotted keys frozen for lower layers


@dataclass
class ScopeMergeResult:
    config: dict[str, Any] = field(default_factory=dict[str, Any])
    rejected: list[str] = field(default_factory=list[str])  # "<layer>:<dotted>" blocked overrides


def flatten(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in d.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(flatten(cast("dict[str, Any]", value), dotted))
        else:
            out[dotted] = value  # lists are leaves -> replaced wholesale
    return out


def set_dotted(target: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = target
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = cast("dict[str, Any]", nxt)
    cur[parts[-1]] = value


def merge_scopes(layers: list[ScopeLayer]) -> ScopeMergeResult:
    """Merge in authority order; a lower layer cannot override a key locked by a higher one."""
    ordered = sorted(layers, key=lambda layer: SCOPE_ORDER.index(layer.name))
    result = ScopeMergeResult()
    locked: set[str] = set()
    for layer in ordered:
        for dotted, value in flatten(layer.config).items():
            if dotted in locked:
                result.rejected.append(f"{layer.name}:{dotted}")
                continue
            set_dotted(result.config, dotted, value)
        locked.update(layer.locked)  # this layer's locks bind only lower layers
    return result
