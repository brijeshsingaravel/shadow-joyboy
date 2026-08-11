"""Gitleaks secret scanning — wraps the gitleaks CLI for git and directory scanning.

Detects hardcoded secrets (API keys, tokens, passwords, private keys) in git
repositories and directories. Complements the regex-based secret_scanner in
rails.py (which scans live text content) by scanning git history and file trees.

Requires the gitleaks binary on PATH (https://github.com/gitleaks/gitleaks, MIT).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any

from madras.models.agent_config import Rank
from madras.tools.registry import ToolResult, tool

_TIMEOUT_SECONDS = 120


def _find_binary() -> str | None:
    """Locate the gitleaks binary on PATH."""
    return shutil.which("gitleaks")


async def _run_gitleaks(
    binary: str, path: str, mode: str, verbose: bool = False
) -> tuple[int, str, str]:
    """Run gitleaks asynchronously. Returns (returncode, stdout, stderr)."""
    cmd = [binary, mode, path, "--report-format", "json"]
    if verbose:
        cmd.append("--verbose")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT_SECONDS)
        return proc.returncode or 0, stdout.decode(), stderr.decode()
    except TimeoutError:
        return -1, "", f"gitleaks timed out after {_TIMEOUT_SECONDS}s"
    except FileNotFoundError:
        return -2, "", f"gitleaks binary not found: {binary}"
    except Exception as exc:
        return -3, "", f"gitleaks execution error: {exc}"


def _parse_findings(raw: str) -> list[dict[str, Any]]:
    """Parse gitleaks JSON output into a list of finding dicts."""
    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    findings: list[dict[str, Any]] = []
    for item in items:
        findings.append(
            {
                "rule": item.get("RuleID", ""),
                "file": item.get("File", ""),
                "start_line": item.get("StartLine", 0),
                "end_line": item.get("EndLine", 0),
                "match": item.get("Match", "")[:80],
                "secret": "[REDACTED]",
                "tags": item.get("Tags", []),
            }
        )
    return findings


@tool(
    name="gitleaks_scan",
    toolset="security",
    rank_required=Rank.INTERN,
    description=(
        "Scan a git repository or directory for hardcoded secrets "
        "(API keys, tokens, passwords, private keys). "
        "Mode 'git' scans commit history; mode 'dir' scans files on disk."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Repository or directory path to scan",
            },
            "mode": {
                "type": "string",
                "enum": ["git", "dir"],
                "description": "Scan mode: 'git' for commit history, 'dir' for files",
            },
            "verbose": {
                "type": "boolean",
                "description": "Include rule IDs and detailed output",
            },
        },
        "required": ["path"],
    },
)
async def gitleaks_scan(args: dict[str, Any]) -> ToolResult:
    """Run gitleaks on a path. Never raises — errors return in ToolResult."""
    path = str(args.get("path", "")).strip()
    if not path:
        return ToolResult(ok=False, error="path is required")

    mode = str(args.get("mode", "git")).strip()
    if mode not in ("git", "dir"):
        return ToolResult(ok=False, error=f"invalid mode: {mode} (use 'git' or 'dir')")

    verbose = bool(args.get("verbose", False))

    binary = _find_binary()
    if not binary:
        return ToolResult(
            ok=False,
            error="gitleaks not found on PATH — install via 'brew install gitleaks' or see https://github.com/gitleaks/gitleaks",
        )

    returncode, stdout, stderr = await _run_gitleaks(binary, path, mode, verbose)

    if returncode == -1:
        # Timeout
        return ToolResult(ok=False, error=stderr)

    if returncode == -2:
        # Binary not found (race condition with shutil.which)
        return ToolResult(ok=False, error=stderr)

    if returncode == -3:
        # Execution error
        return ToolResult(ok=False, error=stderr)

    if returncode == 0:
        # No findings
        return ToolResult(
            ok=True,
            content=json.dumps(
                {
                    "findings": [],
                    "summary": "No secrets detected",
                    "mode": mode,
                    "path": path,
                }
            ),
        )

    if returncode == 1:
        # Findings detected
        findings = _parse_findings(stdout)
        return ToolResult(
            ok=True,
            content=json.dumps(
                {
                    "findings": findings,
                    "summary": f"{len(findings)} secret(s) detected",
                    "mode": mode,
                    "path": path,
                }
            ),
        )

    # Other error codes (118 = scan error, etc.)
    error_msg = stderr.strip()[:500] if stderr else f"gitleaks exited with code {returncode}"
    return ToolResult(ok=False, error=error_msg)
