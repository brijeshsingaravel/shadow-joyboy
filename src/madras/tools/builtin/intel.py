"""Code-intelligence tools — symbols / definition / references (tree-sitter).

Semantic navigation across the workspace: list the symbols defined in a file,
jump to where a name is defined, or find everywhere it is used — multi-language
via tree-sitter, deterministic, workspace-confined, governed (toolset 'search',
read-only → auto-allowed). None of Claude Code / Codex / Hermes expose semantic
navigation as a first-class tool; this matches OpenCode's `lsp` and leads them.
"""

from __future__ import annotations

import asyncio
from typing import Any

from madras.models.agent_config import Rank
from madras.tools.builtin._workspace import rel, safe_resolve, workspace_root
from madras.tools.builtin.search import iter_files
from madras.tools.code_intel import lang_for, scan_definitions, scan_references
from madras.tools.registry import ToolResult, tool

_MAX_RESULTS = 400
_MAX_FILE_BYTES = 1_000_000


def _iter_source_files(scope: str = ""):
    """Yield (Path, lang) for supported source files under an optional subdir.

    Uses search.py's `iter_files` (prunes skip-dirs before descending, not after) —
    was previously `base.rglob("*")` with its own stale, drift-prone `_SKIP_DIRS`
    copy, which hit the same broken-symlink-in-a-vendored-venv crash `iter_files`
    was built to fix. One shared skip-list, one shared safe walker.
    """
    base = safe_resolve(scope) if scope else workspace_root()
    if base is None or not base.exists():
        return
    paths = [base] if base.is_file() else iter_files(base, "**/*")
    for p in paths:
        if not p.is_file():
            continue
        lang = lang_for(p.as_posix())
        if lang is None:
            continue
        try:
            if p.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        yield p, lang


@tool(
    name="symbols",
    toolset="search",
    rank_required=Rank.INTERN,
    description=(
        "List the symbols (functions, classes, methods, …) DEFINED in a file or "
        "across the workspace, via tree-sitter. 'path' scopes to one file or "
        "subdirectory (default: whole workspace). Optional 'name_contains' filters "
        "by substring. Returns 'kind name path:line' per symbol."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "file or subdir to scope to"},
            "name_contains": {"type": "string", "description": "substring filter on symbol name"},
        },
    },
)
async def symbols(args: dict[str, Any]) -> ToolResult:
    scope = str(args.get("path", "")).strip()
    needle = str(args.get("name_contains", "")).strip().lower()
    if scope and safe_resolve(scope) is None:
        return ToolResult(ok=False, error="path escapes the workspace boundary")
    files = list(_iter_source_files(scope))
    results = await asyncio.to_thread(scan_definitions, [(str(p), lang) for p, lang in files])
    rows: list[str] = []
    total = 0
    for p, _lang in files:
        rpath = rel(p)
        for s in results.get(str(p), []):
            if needle and needle not in s.name.lower():
                continue
            total += 1
            if len(rows) < _MAX_RESULTS:
                rows.append(f"{s.kind} {s.name} {rpath}:{s.line}")
    return ToolResult(
        ok=True, content="\n".join(rows), extras={"count": total, "truncated": total > len(rows)}
    )


@tool(
    name="definition",
    toolset="search",
    rank_required=Rank.INTERN,
    description=(
        "Find where a symbol NAME is defined across the workspace (tree-sitter). "
        "Returns 'kind name path:line' for each definition. Use after grep when you "
        "want the declaration, not every mention."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "exact symbol name to locate the definition of",
            },
            "path": {"type": "string", "description": "optional subdir to scope to"},
        },
        "required": ["name"],
    },
)
async def definition(args: dict[str, Any]) -> ToolResult:
    name = str(args.get("name", "")).strip()
    if not name:
        return ToolResult(ok=False, error="name is required")
    scope = str(args.get("path", "")).strip()
    if scope and safe_resolve(scope) is None:
        return ToolResult(ok=False, error="path escapes the workspace boundary")
    files = list(_iter_source_files(scope))
    results = await asyncio.to_thread(scan_definitions, [(str(p), lang) for p, lang in files])
    rows: list[str] = []
    for p, _lang in files:
        rpath = rel(p)
        for s in results.get(str(p), []):
            if s.name == name and len(rows) < _MAX_RESULTS:
                rows.append(f"{s.kind} {s.name} {rpath}:{s.line}")
    return ToolResult(
        ok=True,
        content="\n".join(rows) or f"(no definition of {name!r} found)",
        extras={"count": len(rows)},
    )


@tool(
    name="references",
    toolset="search",
    rank_required=Rank.INTERN,
    description=(
        "Find everywhere a symbol NAME is used across the workspace (tree-sitter "
        "identifier occurrences, one row per line). Returns 'path:line:text'. Use to "
        "gauge blast radius before changing a function or class."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "exact symbol name to find usages of"},
            "path": {"type": "string", "description": "optional subdir to scope to"},
        },
        "required": ["name"],
    },
)
async def references(args: dict[str, Any]) -> ToolResult:
    name = str(args.get("name", "")).strip()
    if not name:
        return ToolResult(ok=False, error="name is required")
    scope = str(args.get("path", "")).strip()
    if scope and safe_resolve(scope) is None:
        return ToolResult(ok=False, error="path escapes the workspace boundary")
    files = list(_iter_source_files(scope))
    results = await asyncio.to_thread(scan_references, [(str(p), lang) for p, lang in files], name)
    rows: list[str] = []
    total = 0
    for p, _lang in files:
        rpath = rel(p)
        for r in results.get(str(p), []):
            total += 1
            if len(rows) < _MAX_RESULTS:
                rows.append(f"{rpath}:{r.line}:{r.text}")
    return ToolResult(
        ok=True,
        content="\n".join(rows) or f"(no references to {name!r} found)",
        extras={"count": total, "truncated": total > len(rows)},
    )
