"""AGENTS.md tree-walk discovery + @imports (row 14b).

A Madras agent operating inside a user repo discovers `AGENTS.md` files up the directory tree
(nearest-wins, per the open AGENTS.md standard / Claude Code), and each file may pull in shared
instructions via recursive `@path` imports (bounded depth + cycle-safe + path-scoped to the repo).

The repo-context counterpart to the in-engine config loader (`factory/loader.py`, base<-neighborhood
<-role) and the `@file` JIT (`graph/jit_context.py`); the discovery side of the AGENTS.md discipline
linter (`docs_discipline.py`, row 73). Pure stdlib (pathlib).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_FILENAME = "AGENTS.md"
# a line that is exactly `@<relative-path>` (an import directive), not an inline @mention
_IMPORT_RE = re.compile(r"^[ \t]*@([^\s#@][^\r\n]*?)[ \t]*$", re.M)


def discover_context_files(
    start_dir: str | Path, *, filename: str = DEFAULT_FILENAME, stop_at: str | Path | None = None
) -> list[Path]:
    """Walk from start_dir UP to stop_at (or the fs root), collecting existing `filename` files,
    ordered SHALLOWEST -> DEEPEST so the nearest (deepest) wins when concatenated last."""
    start = Path(start_dir).resolve()
    stop = Path(stop_at).resolve() if stop_at is not None else None
    found: list[Path] = []
    cur = start
    while True:
        candidate = cur / filename
        if candidate.is_file():
            found.append(candidate)
        if stop is not None and cur == stop:
            break
        if cur.parent == cur:  # reached the fs root
            break
        cur = cur.parent
    found.reverse()  # shallowest first
    return found


@dataclass
class ImportResult:
    text: str = ""
    imported: list[str] = field(default_factory=list[str])
    skipped: list[str] = field(default_factory=list[str])  # cycle / max-depth / outside / missing


def resolve_imports(
    text: str, base_dir: str | Path, *, root: str | Path | None = None, max_depth: int = 5
) -> ImportResult:
    """Recursively expand `@path` import directives, resolved relative to the importing file's dir,
    scoped within `root` (the repo boundary; defaults to base_dir). Bounded + cycle-safe."""
    base = Path(base_dir).resolve()
    boundary = Path(root).resolve() if root is not None else base
    result = ImportResult()
    result.text = _expand(text, base, boundary, max_depth, set(), result)
    return result


def _expand(
    text: str, base: Path, boundary: Path, depth: int, seen: set[Path], result: ImportResult
) -> str:
    def repl(match: re.Match[str]) -> str:
        rel = match.group(1).strip()
        target = (base / rel).resolve()
        try:
            target.relative_to(boundary)
        except ValueError:
            result.skipped.append(f"{rel} (outside repo)")
            return match.group(0)
        if depth <= 0:
            result.skipped.append(f"{rel} (max depth)")
            return match.group(0)
        if target in seen:
            result.skipped.append(f"{rel} (cycle)")
            return match.group(0)
        if not target.is_file():
            result.skipped.append(f"{rel} (not found)")
            return match.group(0)
        result.imported.append(rel)
        content = target.read_text(encoding="utf-8")
        return _expand(content, target.parent, boundary, depth - 1, seen | {target}, result)

    return _IMPORT_RE.sub(repl, text)


@dataclass
class AssembledContext:
    text: str = ""
    files: list[str] = field(default_factory=list[str])  # discovered, shallow -> deep (precedence)
    imported: list[str] = field(default_factory=list[str])
    skipped: list[str] = field(default_factory=list[str])


def assemble_context(
    start_dir: str | Path,
    *,
    filename: str = DEFAULT_FILENAME,
    stop_at: str | Path | None = None,
    max_depth: int = 5,
) -> AssembledContext:
    """Discover the AGENTS.md hierarchy, resolve each file's @imports, and concatenate
    shallowest->deepest (nearest-wins) with a provenance header per section."""
    files = discover_context_files(start_dir, filename=filename, stop_at=stop_at)
    root = Path(stop_at).resolve() if stop_at is not None else None
    out = AssembledContext()
    sections: list[str] = []
    for f in files:
        expanded = resolve_imports(
            f.read_text(encoding="utf-8"),
            f.parent,
            root=root if root is not None else f.parent,
            max_depth=max_depth,
        )
        out.files.append(str(f))
        out.imported.extend(expanded.imported)
        out.skipped.extend(expanded.skipped)
        sections.append(f"<!-- from: {f} -->\n{expanded.text.rstrip()}")
    out.text = "\n\n".join(sections)
    return out
