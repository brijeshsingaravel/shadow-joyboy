"""Filesystem-as-externalized-context (W4·B4) — recoverable compression.

Manus's pattern: when content exceeds a budget, **offload it to a workspace file** and keep a
compact ``{path, summary}`` reference in context (the agent reads it back on demand via the
file tools) — recoverable compression instead of irreversible truncation. Pure: the writer is
injected (default = workspace file write), so it's hermetically testable.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

DEFAULT_BUDGET = 4000  # chars; above this, spill to a file


def _default_writer(name: str, content: str) -> str:
    """Write under the agent workspace; return the relative path. Lazy import to stay pure."""
    from madras.tools.builtin._workspace import workspace_root

    root = workspace_root()
    out = root / "context"
    out.mkdir(parents=True, exist_ok=True)
    path = out / name
    path.write_text(content, encoding="utf-8")
    return str(path.relative_to(root))


def offload_if_large(
    content: str,
    *,
    label: str = "blob",
    budget: int = DEFAULT_BUDGET,
    writer: Callable[[str, str], str] | None = None,
) -> str:
    """Return ``content`` unchanged if within budget; else spill it to a file and return a
    compact reference (path + a head summary) the agent can read back."""
    if len(content) <= budget:
        return content
    digest = hashlib.blake2b(content.encode(), digest_size=6).hexdigest()
    name = f"{label}-{digest}.txt"
    path = (writer or _default_writer)(name, content)
    head = content[:280].rstrip()
    return (
        f"[OFFLOADED {len(content)} chars to {path} — recoverable, read it back with the "
        f"file tools]\nPreview:\n{head}…"
    )
