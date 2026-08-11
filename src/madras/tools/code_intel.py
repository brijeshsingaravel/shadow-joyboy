"""Tree-sitter code-intelligence engine — symbols / definitions / references.

The 2025-26 agent-standard for code navigation is tree-sitter structural parsing
(aider's repo-map, CodeGraph, Kiro, …) — multi-language, deterministic, and far
lighter than per-language LSP servers, with an optional LSP overlay possible
later for type-level precision. Engine here; governed tools in builtin/intel.py.

The bundled binding (tree-sitter-language-pack) exposes a PyO3/Rust surface where
node accessors are methods, not properties (``kind()``, ``start_byte()``,
``start_position().row``). All of that quirk is isolated in this module behind a
small normalizing helper, so the rest of the codebase sees plain dataclasses.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# file extension -> tree-sitter language name
_LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "c_sharp",
    ".php": "php",
}

# per-language: node-kind -> friendly definition label
_DEF_KINDS: dict[str, dict[str, str]] = {
    "python": {"function_definition": "function", "class_definition": "class"},
    "javascript": {
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    },
    "typescript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
    },
    "tsx": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "interface_declaration": "interface",
    },
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_spec": "type",
    },
    "rust": {
        "function_item": "function",
        "struct_item": "struct",
        "enum_item": "enum",
        "trait_item": "trait",
        "mod_item": "module",
    },
    "java": {
        "class_declaration": "class",
        "method_declaration": "method",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
    },
    "ruby": {"method": "method", "class": "class", "module": "module"},
    "c": {"function_definition": "function", "struct_specifier": "struct"},
    "cpp": {
        "function_definition": "function",
        "class_specifier": "class",
        "struct_specifier": "struct",
    },
    "c_sharp": {
        "class_declaration": "class",
        "method_declaration": "method",
        "interface_declaration": "interface",
    },
    "php": {
        "function_definition": "function",
        "method_declaration": "method",
        "class_declaration": "class",
    },
}

SUPPORTED_LANGUAGES = frozenset(_DEF_KINDS)


@dataclass
class Symbol:
    kind: str  # function | class | method | ...
    name: str
    line: int  # 1-based


@dataclass
class Ref:
    line: int  # 1-based
    text: str


def lang_for(path: str) -> str | None:
    """tree-sitter language name for a file path, or None if unsupported."""
    lower = path.lower()
    for ext, lang in _LANG_BY_EXT.items():
        if lower.endswith(ext):
            return lang if lang in _DEF_KINDS else None
    return None


def _call(v: Any) -> Any:
    """Normalize the PyO3 binding: some accessors are methods, some attributes."""
    return v() if callable(v) else v


def _kind(node: Any) -> str:
    return _call(node.kind)


def _row(node: Any) -> int:
    return _call(_call(node.start_position).row) + 1


def _text(node: Any, src: bytes) -> str:
    a = _call(node.start_byte)
    b = _call(node.end_byte)
    return src[a:b].decode("utf-8", "replace")


def _named_children(node: Any) -> list[Any]:
    count = _call(node.named_child_count)
    return [node.named_child(i) for i in range(count)]


def _parse_root(source: str, lang: str) -> tuple[Any, bytes] | None:
    try:
        from tree_sitter_language_pack import get_parser

        parser = get_parser(lang)  # type: ignore[arg-type]
        tree = parser.parse(source)
        if tree is None:
            return None
        root = _call(tree.root_node)
        return root, source.encode("utf-8")
    except Exception:
        return None


def definitions_in(source: str, lang: str) -> list[Symbol]:
    """All top-level + nested definitions (functions/classes/methods/…)."""
    if lang not in _DEF_KINDS:
        return []
    parsed = _parse_root(source, lang)
    if parsed is None:
        return []
    root, _src = parsed
    src = source.encode("utf-8")
    labels = _DEF_KINDS[lang]
    out: list[Symbol] = []

    def walk(node: Any) -> None:
        label = labels.get(_kind(node))
        if label is not None:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                out.append(Symbol(kind=label, name=_text(name_node, src), line=_row(name_node)))
        for child in _named_children(node):
            walk(child)

    walk(root)
    return out


# --- parallel multi-file scanning -------------------------------------------
#
# tree-sitter parsing is the actual bottleneck for a repo-wide scan (confirmed
# by direct measurement: file discovery ~0.6s/3.5k files, I/O ~0.5s, parsing
# 15-50s). Threading barely helps (tree-sitter's C parser does not release the
# GIL for the duration of a parse), so real OS-process parallelism is used
# instead once the file count makes the process-pool overhead worth paying.

_PARALLEL_THRESHOLD = 64  # below this, pool startup cost exceeds any savings


def _default_worker_count() -> int:
    cpu = os.cpu_count() or 4
    return max(1, min(16, cpu - 2))


def _read_source_file(path_str: str) -> str | None:
    try:
        data = Path(path_str).read_bytes()
        if b"\x00" in data[:4096]:
            return None
        return data.decode("utf-8", "replace")
    except OSError:
        return None


def _definitions_worker(item: tuple[str, str]) -> tuple[str, list[Symbol]]:
    path_str, lang = item
    src = _read_source_file(path_str)
    if src is None:
        return path_str, []
    return path_str, definitions_in(src, lang)


def _references_worker(item: tuple[str, str, str]) -> tuple[str, list[Ref]]:
    path_str, lang, target = item
    src = _read_source_file(path_str)
    if src is None:
        return path_str, []
    return path_str, references_in(src, lang, target)


def scan_definitions(files: list[tuple[str, str]]) -> dict[str, list[Symbol]]:
    """Definitions for every (path, lang) pair. Parallel across processes above the threshold."""
    if not files:
        return {}
    if len(files) < _PARALLEL_THRESHOLD:
        return {p: _definitions_worker((p, lang))[1] for p, lang in files}
    workers = _default_worker_count()
    chunksize = max(1, len(files) // (workers * 4))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(_definitions_worker, files, chunksize=chunksize))


def scan_references(files: list[tuple[str, str]], target: str) -> dict[str, list[Ref]]:
    """References to ``target`` for every (path, lang) pair. Parallel above the threshold."""
    if not files:
        return {}
    items = [(p, lang, target) for p, lang in files]
    if len(files) < _PARALLEL_THRESHOLD:
        return {p: _references_worker((p, lang, tgt))[1] for p, lang, tgt in items}
    workers = _default_worker_count()
    chunksize = max(1, len(items) // (workers * 4))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(_references_worker, items, chunksize=chunksize))


def references_in(source: str, lang: str, target: str) -> list[Ref]:
    """Every identifier occurrence of ``target`` with its source line."""
    if lang not in _DEF_KINDS:
        return []
    parsed = _parse_root(source, lang)
    if parsed is None:
        return []
    root, _src = parsed
    src = source.encode("utf-8")
    lines = source.splitlines()
    seen_lines: set[int] = set()
    out: list[Ref] = []

    def walk(node: Any) -> None:
        if _kind(node) in ("identifier", "type_identifier", "field_identifier") and (
            _text(node, src) == target
        ):
            ln = _row(node)
            if ln not in seen_lines:
                seen_lines.add(ln)
                text = lines[ln - 1].strip()[:300] if 0 < ln <= len(lines) else ""
                out.append(Ref(line=ln, text=text))
        for child in _named_children(node):
            walk(child)

    walk(root)
    return out
