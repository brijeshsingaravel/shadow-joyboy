"""One registry → every surface derives (row 69, the Hermes pattern).

A single source of truth — the [[ToolRegistry]] — and EVERY interface derives from it: the LLM tool
schemas (already `registry.schemas`), the gateway/MCP exposure, the CLI help, shell autocomplete,
channel command indexes, and per-tool help. Add one `@tool` and every surface updates with zero
drift — nothing is hand-maintained twice. Crucially, every surface derives through the SAME
rank-gated `registry.allowed(...)`, so a surface can never reveal a tool the agent isn't allowed to
call (no capability leak in help/autocomplete — a security property, not just DRY). Pure derivation.
"""

from __future__ import annotations

from typing import Any

from madras.tools.registry import Rank, ToolRegistry, ToolSpec


def visible(
    registry: ToolRegistry, *, rank: Rank, toolsets: list[str] | None = None
) -> list[ToolSpec]:
    """The one rank-gated source every surface derives from (stable order)."""
    specs = registry.allowed(agent_rank=rank, toolsets=toolsets)
    return sorted(specs, key=lambda t: (t.toolset, t.name))


def command_index(
    registry: ToolRegistry, *, rank: Rank, toolsets: list[str] | None = None
) -> dict[str, list[str]]:
    """{toolset: [tool names]} — the channel `/help` index + the menu every channel renders."""
    index: dict[str, list[str]] = {}
    for t in visible(registry, rank=rank, toolsets=toolsets):
        index.setdefault(t.toolset, []).append(t.name)
    return index


def completions(
    registry: ToolRegistry, prefix: str, *, rank: Rank, toolsets: list[str] | None = None
) -> list[str]:
    """Autocomplete: tool names matching a prefix (a leading '/' is tolerated), rank-gated."""
    p = prefix.lower().lstrip("/")
    return [
        t.name
        for t in visible(registry, rank=rank, toolsets=toolsets)
        if t.name.lower().startswith(p)
    ]


def tool_help(registry: ToolRegistry, name: str, *, rank: Rank) -> str | None:
    """Per-tool help. Returns None if the tool doesn't exist OR the rank can't call it (so help
    never advertises a denied capability)."""
    allowed_names = {t.name for t in registry.allowed(agent_rank=rank)}
    if name not in allowed_names:
        return None
    t = registry.get(name)
    assert t is not None  # in allowed_names ⇒ registered
    params = list((t.parameters or {}).get("properties", {}).keys())
    param_str = ", ".join(params) if params else "(none)"
    return f"{t.name}  [{t.toolset}]\n  {t.description}\n  params: {param_str}"


def cli_help(registry: ToolRegistry, *, rank: Rank, toolsets: list[str] | None = None) -> str:
    """The CLI / `--help` listing — grouped by toolset, derived from the registry."""
    lines: list[str] = []
    for toolset, names in command_index(registry, rank=rank, toolsets=toolsets).items():
        lines.append(f"{toolset}:")
        for n in names:
            t = registry.get(n)
            desc = t.description if t else ""
            lines.append(f"  {n:<22} {desc}")
    return "\n".join(lines)


def schemas(
    registry: ToolRegistry, *, rank: Rank, toolsets: list[str] | None = None
) -> list[dict[str, Any]]:
    """The gateway/MCP/LLM tool-schema surface — delegates to the registry's own derivation so all
    surfaces share one source (no parallel schema list)."""
    return registry.schemas(agent_rank=rank, toolsets=toolsets)
