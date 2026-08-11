"""Docs discipline — the AGENTS.md gold standard, enforced (row 73).

Research (138 repos): bloated / LLM-generated context files REDUCE agent task success and add ~20%
inference cost — agents follow unnecessary rules faithfully, broadening exploration. So the gold
standard is **minimal + precise + no-duplication**: if a constraint is already enforced by a tool in
the repo (ruff / pyright / a CI gate), it must NOT be restated; the highest-ROI block is the exact
**Commands**. This module is the linter that catches that bloat + derives the Commands block from
the repo so it can't drift (the row-69 "derive, don't duplicate" principle). Pure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class DocFinding:
    line: int
    kind: str  # missing_commands | restated_tool_rule | over_budget | filler
    detail: str


# constraints a tool already enforces — restating them in AGENTS.md is the bloat that HURTS agents
_TOOL_ENFORCED: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"line[- ]?length|max[- ]?line|\b\d{2,3}\s*(?:chars|columns|cols)\b", re.I),
        "line length (ruff enforces)",
    ),
    (
        re.compile(r"\b(?:4|two|four)\s*spaces?\b|\bindentation\b", re.I),
        "indentation (formatter enforces)",
    ),
    (
        re.compile(
            r"\b(?:always |remember to )?run\s+(?:ruff|black|isort|pyright|mypy|flake8)\b", re.I
        ),
        "lint/format/type command (config + CI enforces)",
    ),
    (
        re.compile(r"\btype[- ]?hints?\s+(?:are\s+)?required\b|annotate every", re.I),
        "type hints (pyright enforces)",
    ),
    (re.compile(r"\bsort\s+(?:your\s+)?imports\b", re.I), "import sorting (ruff I enforces)"),
    (
        re.compile(r"\b(?:no\s+)?trailing\s+whitespace\b|final\s+newline", re.I),
        "whitespace (formatter enforces)",
    ),
]

_FILLER = re.compile(
    r"\b(?:as an ai|please note that|it is important to|make sure to always|"
    r"world[- ]class|seamless|leverage|robust and|best practices)\b",
    re.I,
)

_CMD_HEADER = re.compile(
    r"^#{1,4}\s*(?:commands?|build|setup|running|tests?|quick\s*start)", re.I | re.M
)

_BUDGET_LINES = 200


@dataclass
class DocReport:
    findings: list[DocFinding] = field(default_factory=list[DocFinding])

    @property
    def ok(self) -> bool:
        return not self.findings

    def summary(self) -> str:
        if self.ok:
            return "AGENTS.md discipline: PASS (minimal, precise, no tool-rule duplication)"
        by: dict[str, int] = {}
        for f in self.findings:
            by[f.kind] = by.get(f.kind, 0) + 1
        head = "AGENTS.md discipline: " + ", ".join(f"{n} {k}" for k, n in sorted(by.items()))
        return head + "\n" + "\n".join(f"  L{f.line} [{f.kind}] {f.detail}" for f in self.findings)


def validate_agents_md(text: str, *, budget_lines: int = _BUDGET_LINES) -> DocReport:
    """Lint an AGENTS.md against the gold standard: has Commands, within budget, no restated
    tool-enforced rules, no LLM filler."""
    lines = (text or "").splitlines()
    findings: list[DocFinding] = []

    if not _CMD_HEADER.search(text or ""):
        findings.append(
            DocFinding(
                0, "missing_commands", "no Commands/Build/Setup section (the highest-ROI block)"
            )
        )
    if len(lines) > budget_lines:
        findings.append(
            DocFinding(
                len(lines),
                "over_budget",
                f"{len(lines)} lines > {budget_lines} — bloat adds ~20% cost",
            )
        )

    in_code = False
    for i, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line.strip():
            continue
        for rx, why in _TOOL_ENFORCED:
            if rx.search(line):
                findings.append(
                    DocFinding(i, "restated_tool_rule", f"{why}: {line.strip()[:50]!r}")
                )
                break
        if _FILLER.search(line):
            findings.append(DocFinding(i, "filler", f"LLM filler: {line.strip()[:40]!r}"))
    return DocReport(findings)


def derive_commands(pyproject_text: str) -> list[str]:
    """The authoritative Commands block, derived from the repo (so it can't drift)."""
    p = pyproject_text or ""
    cmds: list[str] = []
    if "[tool.uv" in p or "uv" in p:
        cmds.append("uv sync")
    if "pytest" in p:
        cmds.append("uv run pytest")
    if "[tool.ruff" in p:
        cmds.append("uv run ruff check src tests")
    if "pyright" in p:
        cmds.append("uv run pyright")
    return cmds


def render_skeleton(
    *,
    commands: list[str],
    canon: str = "CLAUDE.md + the `Madras AI/` Obsidian vault",
    subsystems: list[str] | None = None,
) -> str:
    """A minimal AGENTS.md skeleton: exact Commands + a pointer to the canon (no duplication)."""
    out = [
        "# AGENTS.md",
        "",
        f"Agent operating doc for this repo. Full rules + architecture live in **{canon}** —",
        "this file is the minimal cross-tool entry point (read the canon for everything else).",
        "",
        "## Commands",
        "```bash",
    ]
    out += [f"cd Engineering && {c}" for c in commands] or ["# (none derived)"]
    out += ["```"]
    if subsystems:
        out += [
            "",
            "## Subsystems",
            *(f"- `{s}` — see its module docstring for invariants." for s in subsystems),
        ]
    out += [
        "",
        "> Style, imports, and types are enforced by ruff + pyright — not restated here.",
        "> See CLAUDE.md for the behavioral protocol.",
    ]
    return "\n".join(out) + "\n"
