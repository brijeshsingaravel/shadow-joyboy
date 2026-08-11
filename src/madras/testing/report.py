"""Parse test-runner output into a structured TestReport.

The agent should fix failures from structure (nodeid + message), not by regexing a
1500-char text blob. We extract per-failure node ids + short messages and the
pass/fail/error counts from pytest (the default runner) and degrade gracefully to a
generic exit-code report for other runners.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# pytest summary line, e.g. "= 3 failed, 10 passed, 1 error in 2.05s =" (any order).
_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed)")
# "FAILED tests/test_x.py::test_y - AssertionError: nope"
_FAILED_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+?)(?:\s+-\s+(.*))?$", re.MULTILINE)


@dataclass
class TestFailure:
    nodeid: str
    message: str = ""


@dataclass
class TestReport:
    passed: bool
    runner: str = "pytest"
    n_passed: int = 0
    n_failed: int = 0
    n_errors: int = 0
    n_skipped: int = 0
    failures: list[TestFailure] = field(default_factory=list[TestFailure])
    raw_tail: str = ""

    def summary(self) -> str:
        """One-line human/agent summary of the run."""
        bits = [f"{self.n_passed} passed"]
        if self.n_failed:
            bits.append(f"{self.n_failed} failed")
        if self.n_errors:
            bits.append(f"{self.n_errors} errors")
        if self.n_skipped:
            bits.append(f"{self.n_skipped} skipped")
        head = "PASS" if self.passed else "FAIL"
        return f"[{head}] {self.runner}: " + ", ".join(bits)

    def failed_nodeids(self) -> list[str]:
        return [f.nodeid for f in self.failures]


def parse_pytest(output: str, exit_code: int) -> TestReport:
    """Extract counts + per-failure node ids from pytest -q/-v output."""
    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    for n, kind in _COUNT_RE.findall(output):
        key = "error" if kind in ("error", "errors") else kind
        if key in counts:
            counts[key] += int(n)
    failures: list[TestFailure] = []
    seen: set[str] = set()
    for nodeid, msg in _FAILED_RE.findall(output):
        if nodeid in seen:
            continue
        seen.add(nodeid)
        failures.append(TestFailure(nodeid=nodeid, message=(msg or "").strip()[:200]))
    passed = exit_code == 0
    return TestReport(
        passed=passed,
        runner="pytest",
        n_passed=counts["passed"],
        n_failed=counts["failed"] or (len(failures) if not passed else 0),
        n_errors=counts["error"],
        n_skipped=counts["skipped"],
        failures=failures,
        raw_tail=output[-1500:],
    )


def parse_generic(output: str, exit_code: int, runner: str) -> TestReport:
    """Fallback for non-pytest runners: trust the exit code, keep the tail."""
    return TestReport(
        passed=exit_code == 0,
        runner=runner,
        raw_tail=output[-1500:],
    )


def parse_report(output: str, exit_code: int, runner: str = "pytest") -> TestReport:
    if runner == "pytest":
        return parse_pytest(output, exit_code)
    return parse_generic(output, exit_code, runner)
