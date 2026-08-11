"""Action-space masking (W4·B4) — Manus-style stable-prefix tool masking.

Instead of REMOVING tools from the schema (which invalidates the KV-cache prefix from that
token on), a ``ToolMask`` keeps every tool resident but marks which are currently allowed; a
call to a masked tool is **rejected with a teach-back** so the model self-corrects on the next
turn. Hosted-model-compatible (no logit access required); pure + testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolMask:
    allowed: set[str] | None = None  # None = all allowed (subject to `denied`)
    denied: set[str] = field(default_factory=set[str])

    def is_masked(self, tool: str) -> bool:
        if tool in self.denied:
            return True
        return self.allowed is not None and tool not in self.allowed

    def reason(self, tool: str) -> str:
        return (
            f"[TOOL_MASKED] {tool!r} is not available at this step; "
            "choose one of the currently-available tools."
        )


def mask_mutating_tools(registry: Any, *, schemas: list[dict[str, Any]]) -> ToolMask:
    """s46: Context Discipline (row context-discipline) -- plan mode already blocks mutating
    tools, but at EXECUTE time inside GovernedExecutor (after a schema round-trip). This
    masks them at DISPATCH time instead -- cheaper, and it's ToolMask (built, never
    constructed anywhere) doing the actual masking Manus's pattern calls for, not a
    bespoke duplicate check. GovernedExecutor's own plan-mode block stays as the
    authoritative backstop (defense in depth, not replaced). `schemas` is the resident,
    UNCHANGED tool schema list -- masking never removes anything from it."""
    from madras.tools.registry import MUTATING_TOOLSETS

    denied: set[str] = set()
    for schema in schemas:
        func: dict[str, Any] = schema.get("function") or {}
        name: str | None = func.get("name")
        if not name:
            continue
        spec = registry.get(name)
        if spec is not None and spec.toolset in MUTATING_TOOLSETS:
            denied.add(name)
    return ToolMask(denied=denied)
