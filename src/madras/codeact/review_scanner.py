"""Deterministic security/correctness code-review scanner (row open-code-review).

`marketplace/review.py::ReviewFinding` is a well-designed data shape with ZERO
producers anywhere in Madras -- the review GATE exists, nothing generates findings.
OSS-radar review (alibaba/open-code-review, Apache-2.0, ~10k stars): a real gap
(line-level security findings), but the fork target is a Go CLI/npm package -- wrong
shape for this Python stack (CLAUDE.md dependency discipline). Built native: a small,
high-precision Python-focused ruleset (same regex-pattern-scanning idiom as
security/rails.py's secret scanner) rather than a general multi-language SAST engine
-- reusing the OSS project's rule CATEGORIES (SQLi/shell-injection/silent-exception-
swallowing) as a reference, not vendoring its code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from madras.marketplace.review import ReviewFinding

_SEV = "severity"


@dataclass(frozen=True)
class _Rule:
    id: str
    category: str
    severity: str
    pattern: re.Pattern[str]
    detail: str


_RULES: tuple[_Rule, ...] = (
    _Rule(
        "sql-injection",
        "security",
        "critical",
        re.compile(
            r"""\.execute\w*\(\s*f["']|\.execute\w*\([^)]*%\s*\(|"""
            r"""\.execute\w*\([^)]*\+\s*\w"""
        ),
        "SQL built via string interpolation/concatenation passed to execute() -- "
        "use parameterized queries instead.",
    ),
    _Rule(
        "shell-injection",
        "security",
        "critical",
        re.compile(r"shell\s*=\s*True"),
        "subprocess call with shell=True -- if any part of the command is "
        "attacker-influenced, this is command injection. Prefer shell=False with "
        "an argument list.",
    ),
    _Rule(
        "dynamic-eval",
        "security",
        "high",
        re.compile(r"\b(?:eval|exec)\(\s*\w"),
        "eval()/exec() on a non-literal value -- arbitrary code execution risk if "
        "the value is influenced by external input.",
    ),
    _Rule(
        "bare-except",
        "correctness",
        "medium",
        re.compile(r"except\s*:\s*(?:#.*)?$", re.MULTILINE),
        "bare except: silently swallows ALL exceptions (including KeyboardInterrupt/"
        "SystemExit) -- catch a specific exception type.",
    ),
    _Rule(
        "assert-for-validation",
        "correctness",
        "low",
        re.compile(r"^\s*assert\s+\w+.*,\s*[\"']"),
        "assert used for input validation -- assertions are stripped when Python "
        "runs with -O, silently disabling the check in production.",
    ),
)


def scan_source(text: str, *, filename: str = "") -> list[ReviewFinding]:
    """Pure, deterministic. Scans line-by-line for high-precision, low-false-positive
    patterns (a small curated ruleset, not exhaustive SAST coverage) and returns
    line-numbered findings ready for marketplace/review.py::build_review."""
    findings: list[ReviewFinding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule in _RULES:
            if rule.pattern.search(line):
                findings.append(
                    ReviewFinding(
                        category=rule.category,
                        severity=rule.severity,
                        detail=f"[{rule.id}] {rule.detail}",
                        file=filename,
                        line=lineno,
                    )
                )
    return findings
