"""search toolset — glob / grep / list: the core code-navigation tools every
competitor agent (Claude Code, Codex, OpenCode, Hermes) exposes.

Pure-Python + workspace-confined + governed. No subprocess (deterministic and
cross-platform — what weak/free models need). Each registers via @tool so it
inherits the rank-gate + 8-dim eval + immutable audit executor.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from madras.models.agent_config import Rank
from madras.tools.builtin._workspace import rel, safe_resolve, workspace_root
from madras.tools.registry import ToolResult, tool

_MAX_RESULTS = 500  # cap returned paths / match lines
_MAX_FILE_BYTES = 1_000_000  # skip files larger than this when grepping
_SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    ".venvs",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".nx",
    ".benchmarks",  # vendored benchmark harnesses (SWE-bench/AgentBench/etc.) — noise, not code
    ".hf-cache",  # HuggingFace model/dataset cache — large binary data, not code
    ".mem0-chroma",  # Chroma vector-store data files
    ".models",
    "mlruns",  # MLflow run artifacts
    "graphify-out",  # generated code-graph output, regenerable
}
# Skipped ONLY at the workspace root — "models" collides with the real src/madras/models/
# source module, so it can't be a blanket by-name skip like the entries above.
_SKIP_DIRS_TOPLEVEL = {"models"}


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:4096]


def _translate_glob(pattern: str) -> re.Pattern[str]:
    """Compile a glob pattern to a regex matching a POSIX-relative path string.

    fnmatch.fnmatch() is NOT a substitute for glob's ``**`` — it has no concept of
    path segments, so ``**/*.py`` translates to a regex that requires a literal
    "/" in the matched string, meaning a top-level file like "a.py" never matches
    even though real glob() semantics say "**/" matches ZERO directories too.
    This translates segment-by-segment instead: "**" matches zero-or-more whole
    path segments, a bare "*"/"?" matches within a single segment only (never
    crosses "/"), same as real glob/shell semantics.
    """
    segments = pattern.split("/")
    regex = "^"
    for i, seg in enumerate(segments):
        is_last = i == len(segments) - 1
        if seg == "**":
            regex += ".*" if is_last else "(?:.*/)?"
            continue
        regex += "".join("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c) for c in seg)
        if not is_last and segments[i + 1] != "**":
            regex += "/"
    regex += "$"
    return re.compile(regex)


def iter_files(base: Path, pattern: str):
    """Glob-equivalent to `base.glob(pattern)` that PRUNES `_SKIP_DIRS` before descending,
    instead of filtering after. `Path.glob()` walks into every directory (including
    `.venv`/`node_modules`) before a caller can filter by name, so a single broken symlink
    inside one (e.g. a vendored venv's Linux-style `lib64` -> `lib` link, unreadable on
    Windows) aborts the whole traversal with an OSError. `os.walk` lets us drop skip-dirs
    from `dirnames` in place, so it never descends into them at all — immune to anything
    broken inside, not just this one symlink.
    """
    rx = _translate_glob(pattern)
    for dirpath, dirnames, filenames in os.walk(base, onerror=lambda _: None):
        skip: set[str] = _SKIP_DIRS_TOPLEVEL if Path(dirpath) == base else set()
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and d not in skip]
        for name in filenames:
            p = Path(dirpath) / name
            rel_posix = p.relative_to(base).as_posix()
            if rx.match(rel_posix):
                yield p


@tool(
    name="glob",
    toolset="search",
    rank_required=Rank.INTERN,
    description=(
        "Find files by glob pattern within the workspace (e.g. '**/*.py', 'src/*.ts'). "
        "Optional 'path' scopes the search to a subdirectory. Returns matching file "
        "paths relative to the workspace root, one per line, sorted."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "glob pattern, e.g. '**/*.py'"},
            "path": {"type": "string", "description": "optional subdirectory to scope to"},
        },
        "required": ["pattern"],
    },
)
async def file_glob(args: dict[str, Any]) -> ToolResult:
    pattern = str(args.get("pattern", "")).strip()
    if not pattern:
        return ToolResult(ok=False, error="pattern is required")
    base_arg = str(args.get("path", "")).strip()
    base = safe_resolve(base_arg) if base_arg else workspace_root()
    if base is None:
        return ToolResult(ok=False, error="path escapes the workspace boundary")
    if not base.is_dir():
        return ToolResult(ok=False, error=f"not a directory: {base_arg}")
    try:
        matches: list[str] = []
        for p in iter_files(base, pattern):
            if p.is_file():
                matches.append(rel(p))
        matches = sorted(set(matches))
        truncated = len(matches) > _MAX_RESULTS
        shown = matches[:_MAX_RESULTS]
        return ToolResult(
            ok=True,
            content="\n".join(shown),
            extras={"count": len(matches), "truncated": truncated},
        )
    except Exception as exc:
        return ToolResult(ok=False, error=f"glob failed: {type(exc).__name__}: {exc}")


_OUTPUT_MODES = ("content", "files", "count")


@tool(
    name="grep",
    toolset="search",
    rank_required=Rank.INTERN,
    description=(
        "Search file contents for a regular expression within the workspace. "
        "'glob' filters which files to scan (e.g. '**/*.py'); 'ignore_case' for "
        "case-insensitive. 'output_mode': 'content' (default, 'path:line:text' per "
        "match), 'files' (distinct matching file paths), or 'count' ('path:count' per "
        "file). 'context' adds N lines around each match (content mode). 'max_results' "
        "caps returned rows (default 200)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "regular expression to search for"},
            "glob": {"type": "string", "description": "optional file glob filter, e.g. '**/*.py'"},
            "ignore_case": {"type": "boolean", "description": "case-insensitive match"},
            "output_mode": {
                "type": "string",
                "enum": list(_OUTPUT_MODES),
                "description": "content | files | count (default content)",
            },
            "context": {
                "type": "integer",
                "description": "lines of context around each match (content mode)",
            },
            "max_results": {"type": "integer", "description": "cap on returned rows"},
        },
        "required": ["pattern"],
    },
)
async def file_grep(args: dict[str, Any]) -> ToolResult:
    pattern = str(args.get("pattern", "")).strip()
    if not pattern:
        return ToolResult(ok=False, error="pattern is required")
    output_mode = str(args.get("output_mode") or "content").strip()
    if output_mode not in _OUTPUT_MODES:
        return ToolResult(
            ok=False, error=f"invalid output_mode {output_mode!r}; expected one of {_OUTPUT_MODES}"
        )
    try:
        context = max(0, int(args.get("context") or 0))
    except (TypeError, ValueError):
        context = 0
    try:
        cap = int(args.get("max_results") or 200)
    except (TypeError, ValueError):
        cap = 200
    cap = max(1, min(cap, _MAX_RESULTS))
    flags = re.IGNORECASE if bool(args.get("ignore_case")) else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error as exc:
        return ToolResult(ok=False, error=f"invalid regex: {exc}")

    root = workspace_root()
    file_glob_pat = str(args.get("glob", "")).strip() or "**/*"
    try:
        candidates = (p for p in iter_files(root, file_glob_pat) if p.is_file())
        rows: list[str] = []  # content/count rows (respect cap)
        matched_files: list[str] = []  # files mode
        total = 0  # total matching lines across all files
        for p in sorted(candidates, key=lambda x: x.as_posix()):
            try:
                if p.stat().st_size > _MAX_FILE_BYTES:
                    continue
                data = p.read_bytes()
                if _is_binary(data):
                    continue
                text = data.decode("utf-8", errors="replace")
            except OSError:
                continue
            rpath = rel(p)
            file_lines = text.splitlines()
            hit_idxs = [i for i, ln in enumerate(file_lines) if rx.search(ln)]
            if not hit_idxs:
                continue
            total += len(hit_idxs)
            if output_mode == "files":
                matched_files.append(rpath)
                continue
            if output_mode == "count":
                if len(rows) < cap:
                    rows.append(f"{rpath}:{len(hit_idxs)}")
                continue
            # content mode (optionally with context)
            for i in hit_idxs:
                if len(rows) >= cap:
                    break
                lo, hi = max(0, i - context), min(len(file_lines), i + context + 1)
                for j in range(lo, hi):
                    sep = ":" if j == i else "-"  # match vs context (rg style)
                    rows.append(f"{rpath}{sep}{j + 1}{sep}{file_lines[j].rstrip()[:300]}")
                if context:
                    rows.append("--")

        if output_mode == "files":
            shown = sorted(matched_files)[:cap]
            return ToolResult(
                ok=True,
                content="\n".join(shown),
                extras={"count": len(matched_files), "truncated": len(matched_files) > len(shown)},
            )
        return ToolResult(
            ok=True,
            content="\n".join(rows),
            extras={"count": total, "truncated": total > cap},
        )
    except Exception as exc:
        return ToolResult(ok=False, error=f"grep failed: {type(exc).__name__}: {exc}")


@tool(
    name="list",
    toolset="search",
    rank_required=Rank.INTERN,
    description=(
        "List the entries of a workspace directory (default: workspace root). Each line is "
        "'type\\tsize\\tname'; directories are marked with a trailing slash. Non-recursive."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "directory relative to workspace root"},
        },
    },
)
async def file_list(args: dict[str, Any]) -> ToolResult:
    requested = str(args.get("path", "")).strip()
    target = safe_resolve(requested) if requested else workspace_root()
    if target is None:
        return ToolResult(ok=False, error="path escapes the workspace boundary")
    if not target.exists():
        return ToolResult(ok=False, error=f"no such path: {requested}")
    if not target.is_dir():
        return ToolResult(ok=False, error=f"not a directory: {requested}")
    try:
        rows: list[str] = []
        for entry in sorted(os.scandir(target), key=lambda e: (not e.is_dir(), e.name)):
            if entry.is_dir():
                rows.append(f"dir\t-\t{entry.name}/")
            else:
                size = entry.stat().st_size
                rows.append(f"file\t{size}\t{entry.name}")
        return ToolResult(ok=True, content="\n".join(rows), extras={"count": len(rows)})
    except Exception as exc:
        return ToolResult(ok=False, error=f"list failed: {type(exc).__name__}: {exc}")
