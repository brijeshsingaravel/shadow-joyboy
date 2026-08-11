"""Shared workspace-confinement helpers for governed file tools.

Every file/search tool resolves paths under the agent workspace root and refuses
anything that escapes it (path-traversal / absolute-outside). Factored here so
new tools (glob/grep/list, apply_patch, …) share one boundary check.
"""

from __future__ import annotations

from pathlib import Path

from madras.config import settings

_REPO_ROOT = Path(__file__).resolve().parents[4]


def workspace_root() -> Path:
    """The agent's workspace root (created if missing), resolved to a real path."""
    root = (
        Path(settings.madras_workspace) if settings.madras_workspace else _REPO_ROOT / "workspace"
    )
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def safe_resolve(requested: str) -> Path | None:
    """Resolve ``requested`` (relative to the workspace root). None if it escapes."""
    root = workspace_root()
    candidate = (root / requested).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def rel(path: Path) -> str:
    """Workspace-relative POSIX path string (stable across OSes)."""
    return path.relative_to(workspace_root()).as_posix()
