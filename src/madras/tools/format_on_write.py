"""Auto-format edited files on write (row 77, the opencode pattern).

After the agent writes/edits a file, run the right formatter (ruff for Python, prettier for
web/markdown/yaml, gofmt/rustfmt/shfmt/taplo for the rest) so every edit lands well-formatted —
no separate "now format it" step. The Madras edge: governed — only an **allowlist** of known
formatters (no arbitrary exec), run through an **injected governed runner**, **skipped gracefully**
when the tool isn't installed, and **audited**. Pure mapping + injectable runner → testable offline.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

# ext -> (tool, base argv). The file path is appended. Allowlist ONLY — no arbitrary commands.
_FORMATTERS: dict[str, tuple[str, list[str]]] = {
    ".py": ("ruff", ["ruff", "format"]),
    ".pyi": ("ruff", ["ruff", "format"]),
    ".js": ("prettier", ["prettier", "--write"]),
    ".jsx": ("prettier", ["prettier", "--write"]),
    ".ts": ("prettier", ["prettier", "--write"]),
    ".tsx": ("prettier", ["prettier", "--write"]),
    ".json": ("prettier", ["prettier", "--write"]),
    ".css": ("prettier", ["prettier", "--write"]),
    ".scss": ("prettier", ["prettier", "--write"]),
    ".html": ("prettier", ["prettier", "--write"]),
    ".md": ("prettier", ["prettier", "--write"]),
    ".yaml": ("prettier", ["prettier", "--write"]),
    ".yml": ("prettier", ["prettier", "--write"]),
    ".go": ("gofmt", ["gofmt", "-w"]),
    ".rs": ("rustfmt", ["rustfmt"]),
    ".sh": ("shfmt", ["shfmt", "-w"]),
    ".toml": ("taplo", ["taplo", "fmt"]),
}


@dataclass
class FormatCmd:
    tool: str
    argv: list[str]


@dataclass
class FormatResult:
    formatted: bool
    tool: str = ""
    skipped: str = ""  # reason, if skipped (unknown type / tool not installed)
    error: str | None = None


def formatter_for(path: str) -> FormatCmd | None:
    """The allowlisted formatter command for a file path, or None if the type is unhandled."""
    ext = os.path.splitext(str(path))[1].lower()
    spec = _FORMATTERS.get(ext)
    if spec is None:
        return None
    tool, base = spec
    return FormatCmd(tool, [*base, str(path)])


# run(argv) -> (ok, output); available(tool) -> bool
Runner = Callable[[list[str]], Awaitable["tuple[bool, str]"]]


@dataclass
class FormatOnWrite:
    run: Runner
    available: Callable[[str], bool] | None = None
    audit: Callable[[dict[str, Any]], None] | None = None

    def _audit(self, record: dict[str, Any]) -> None:
        if self.audit is not None:
            self.audit(record)

    async def format(self, path: str) -> FormatResult:
        cmd = formatter_for(path)
        if cmd is None:
            return FormatResult(False, skipped="no formatter for this file type")
        if self.available is not None and not self.available(cmd.tool):
            self._audit(
                {"event": "format_skip", "path": path, "tool": cmd.tool, "reason": "not_installed"}
            )
            return FormatResult(False, cmd.tool, skipped=f"{cmd.tool} not installed")
        ok, output = await self.run(cmd.argv)
        self._audit({"event": "format", "path": path, "tool": cmd.tool, "ok": ok})
        if not ok:
            return FormatResult(False, cmd.tool, error=(output or "")[:200])
        return FormatResult(True, cmd.tool)
