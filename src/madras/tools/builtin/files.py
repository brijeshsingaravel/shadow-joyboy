"""file_read — workspace-confined text file reader with strict path security."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from madras.config import settings
from madras.models.agent_config import Rank
from madras.tools.file_access_context import mark_read
from madras.tools.registry import ToolResult, tool

_MAX_BYTES = 1_000_000  # 1 MB cap
_MAX_CHARS = 8000  # truncate returned content

_REPO_ROOT = Path(__file__).resolve().parents[4]


def workspace_root() -> Path:
    root = (
        Path(settings.madras_workspace) if settings.madras_workspace else _REPO_ROOT / "workspace"
    )
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def safe_resolve(requested: str) -> Path | None:
    """Resolve `requested` under the workspace root; return None if it escapes."""
    root = workspace_root()
    # Treat requested as relative to root; reject absolute paths that aren't under root.
    candidate = (root / requested).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


@tool(
    name="file_read",
    toolset="file",
    rank_required=Rank.INTERN,
    description=(
        "Read a UTF-8 text file from the agent workspace. Path is relative to the workspace "
        "root. Returns content with 1-based line numbers (cat -n style) so you can edit "
        "against current content."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "file path relative to workspace root"},
        },
        "required": ["path"],
    },
)
async def file_read(args: dict[str, Any]) -> ToolResult:
    requested = str(args.get("path", "")).strip()
    if not requested:
        return ToolResult(ok=False, error="path is required")
    target = safe_resolve(requested)
    if target is None:
        return ToolResult(ok=False, error="path escapes the workspace boundary")
    if not target.exists() or not target.is_file():
        return ToolResult(ok=False, error=f"not a file: {requested}")
    try:
        size = target.stat().st_size
        if size > _MAX_BYTES:
            return ToolResult(ok=False, error=f"file too large ({size} bytes > {_MAX_BYTES})")
        data = target.read_bytes()
        if b"\x00" in data[:4096]:
            return ToolResult(ok=False, error="binary file not supported")
        text = data.decode("utf-8", errors="replace")
        truncated = text[:_MAX_CHARS]
        numbered = "\n".join(
            f"{i:>6}\t{line}" for i, line in enumerate(truncated.splitlines(), start=1)
        )
        mark_read(requested)
        return ToolResult(
            ok=True,
            content=numbered,
            extras={"path": requested, "bytes": size, "truncated": len(text) > _MAX_CHARS},
        )
    except Exception as exc:
        return ToolResult(ok=False, error=f"read failed: {type(exc).__name__}: {exc}")
