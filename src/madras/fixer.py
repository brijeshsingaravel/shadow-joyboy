"""Fixer — diagnose-and-fix for the Madras runtime (the `doctor --fix` pattern, renamed).

A registry of checks (Homebrew/Flutter/React-Doctor style): each reports ok/warn/fail + whether
it's auto-fixable. `diagnose()` runs them all -> a health score (0-100) + summary; `fix()` runs the
fixers for fixable failures, then re-diagnoses. The headline check is **migrations** - pending
migration files vs the applied ledger, db-first (migrations are the source of truth; `--fix`
applies them). Checks/fixers + the migration inputs are injectable, so this is pure + deterministic
offline; the live PG apply is a thin adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

OK, WARN, FAIL = "ok", "warn", "fail"
_SCORE = {OK: 1.0, WARN: 0.5, FAIL: 0.0}


@dataclass
class CheckResult:
    name: str
    category: str
    status: str  # ok | warn | fail
    detail: str = ""
    fixable: bool = False


@dataclass
class Check:
    name: str
    category: str
    check_fn: Callable[[], CheckResult]
    fix_fn: Callable[[], bool] | None = None  # returns True if it applied a fix


@dataclass
class Diagnosis:
    results: list[CheckResult] = field(default_factory=list[CheckResult])

    @property
    def score(self) -> int:
        if not self.results:
            return 100
        return round(100 * sum(_SCORE[r.status] for r in self.results) / len(self.results))

    @property
    def ok(self) -> bool:
        return all(r.status != FAIL for r in self.results)

    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == FAIL]


@dataclass
class FixReport:
    fixed: list[str] = field(default_factory=list[str])
    unfixed: list[str] = field(default_factory=list[str])
    after: Diagnosis = field(default_factory=Diagnosis)


class Fixer:
    def __init__(self) -> None:
        self._checks: list[Check] = []

    def register(self, check: Check) -> Check:
        self._checks.append(check)
        return check

    def diagnose(self) -> Diagnosis:
        return Diagnosis([c.check_fn() for c in self._checks])

    def fix(self) -> FixReport:
        """Run fixers for fixable failures, then re-diagnose."""
        fixed: list[str] = []
        unfixed: list[str] = []
        for c in self._checks:
            r = c.check_fn()
            if r.status != FAIL:
                continue
            if r.fixable and c.fix_fn is not None:
                try:
                    applied = c.fix_fn()
                except Exception:  # a failing fixer must not abort the whole run
                    applied = False
                (fixed if applied else unfixed).append(c.name)
            else:
                unfixed.append(c.name)
        return FixReport(fixed=fixed, unfixed=unfixed, after=self.diagnose())


def migration_check(
    *,
    available: Callable[[], list[str]],
    applied: Callable[[], set[str]],
    apply_fn: Callable[[str], None],
) -> Check:
    """A Check that flags pending migrations (available minus applied) and, on fix, applies them
    in sorted order. Inputs are injectable: `available` lists migration ids, `applied` is the
    ledger set, `apply_fn(id)` runs+records one."""

    def _pending() -> list[str]:
        done = applied()
        return [m for m in sorted(available()) if m not in done]

    def _check() -> CheckResult:
        pending = _pending()
        if not pending:
            return CheckResult("migrations", "schema", OK, "all migrations applied")
        return CheckResult(
            "migrations", "schema", FAIL, f"{len(pending)} pending: {pending}", fixable=True
        )

    def _fix() -> bool:
        pending = _pending()
        for mig in pending:
            apply_fn(mig)
        return bool(pending)

    return Check("migrations", "schema", _check, _fix)
