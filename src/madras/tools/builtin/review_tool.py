"""code_security_scan -- registers codeact/review_scanner.py's deterministic ruleset live.

marketplace/review.py::ReviewFinding had zero producers (the submission-gate PIPELINE
itself -- build_review/submission_gate -- is also entirely dormant, no live caller;
wiring an actual marketplace-submission endpoint is separately out of scope here).
This registers the scanner as a directly agent-callable tool so it's useful today,
independent of that larger dormant pipeline.
"""

from __future__ import annotations

from typing import Any

from madras.codeact.review_scanner import scan_source
from madras.models.agent_config import Rank
from madras.tools.registry import ToolResult, tool


@tool(
    name="code_security_scan",
    toolset="code",
    rank_required=Rank.INTERN,
    description=(
        "Scan Python source for a small, high-precision ruleset of security/"
        "correctness issues (SQL injection via string interpolation, shell=True "
        "injection risk, dynamic eval/exec, bare except, assert used for "
        "validation). Line-numbered findings, not exhaustive SAST coverage."
    ),
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "the source text to scan"},
            "filename": {"type": "string", "description": "optional filename for context"},
        },
        "required": ["source"],
    },
)
async def code_security_scan(args: dict[str, Any]) -> ToolResult:
    source = str(args.get("source", ""))
    if not source.strip():
        return ToolResult(ok=False, error="source is required")
    filename = str(args.get("filename", "") or "")

    findings = scan_source(source, filename=filename)
    if not findings:
        return ToolResult(
            ok=True, content="No issues found by the ruleset.", extras={"count": 0, "findings": []}
        )

    lines = [f"- [{f.severity}] line {f.line}: {f.detail}" for f in findings]
    return ToolResult(
        ok=True,
        content="\n".join(lines),
        extras={
            "count": len(findings),
            "findings": [
                {
                    "category": f.category,
                    "severity": f.severity,
                    "detail": f.detail,
                    "file": f.file,
                    "line": f.line,
                }
                for f in findings
            ],
        },
    )
