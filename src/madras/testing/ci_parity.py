"""Test doctrine — CI-parity env + a change-detector/isolation linter (row 72).

The Hermes doctrine: tests assert INVARIANTS not snapshots; run in isolation; and pass under a
CI-PARITY env so local == CI (no env-dependent green). This module enforces two halves:

* `ci_parity_env` normalizes the env (fixed TZ / locale / hash-seed) AND strips every credential
  variable — so a unit test can NEVER accidentally hit a real/paid API (no-hammering at the test
  layer, a Madras-specific win); `env_drift` reports what would make local pass but CI fail.
* `doctrine_scan` flags the change-detector / non-isolation anti-patterns in test source —
  wall-clock, credential reads from the env, real network outside a `live` test, unseeded
  randomness — the things that make a test flaky or a snapshot rather than an invariant. Pure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# the env CI pins — anything else locally is "drift" that can hide a CI failure
_NORMALIZED = {
    "TZ": "UTC",
    "LC_ALL": "C",
    "LANG": "C",
    "PYTHONHASHSEED": "0",
    "PYTHONUTF8": "1",
}

_SECRET_RE = re.compile(r"(API_?KEY|SECRET|TOKEN|PASSWORD|_PWD|CREDENTIAL|PRIVATE_KEY)$", re.I)


def is_secret_env(name: str) -> bool:
    return bool(_SECRET_RE.search(name or ""))


def ci_parity_env(base: dict[str, str]) -> dict[str, str]:
    """A normalized env for deterministic CI-parity tests: fixed TZ/locale/hash-seed + every
    credential var stripped (so a unit test can never accidentally reach a real/paid API)."""
    out = {k: v for k, v in base.items() if not is_secret_env(k)}
    out.update(_NORMALIZED)
    return out


def env_drift(current: dict[str, str]) -> list[str]:
    """What in `current` would make tests pass locally but fail in CI (or hit a paid API)."""
    issues: list[str] = []
    for k, v in _NORMALIZED.items():
        if current.get(k) != v:
            issues.append(f"{k}={current.get(k)!r} (CI pins {v!r})")
    leaked = sorted(k for k in current if is_secret_env(k) and current.get(k))
    if leaked:
        issues.append(f"credential vars in test env (strip for parity + no-hammering): {leaked}")
    return issues


@dataclass
class DoctrineFinding:
    line: int
    kind: str  # wall_clock | env_secret | real_network | nondeterminism
    detail: str


_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"\b(?:datetime\.now|datetime\.utcnow|time\.time|time\.monotonic)\s*\("),
        "wall_clock",
        "wall-clock in a test → flaky/change-detector; inject a clock or pass `now`",
    ),
    (
        re.compile(
            r"os\.environ(?:\.get)?\s*[\(\[]\s*['\"][A-Za-z_]*"
            r"(?:KEY|SECRET|TOKEN|PASSWORD)",
            re.I,
        ),
        "env_secret",
        "test reads a credential from the env → env-dependent; use a fixture/fake",
    ),
    (
        re.compile(
            r"\b(?:requests\.(?:get|post|put|delete)|httpx\.(?:get|post|AsyncClient|Client)"
            r"|urllib\.request\.urlopen)\s*\("
        ),
        "real_network",
        "real network in a unit test → mark @pytest.mark.live or inject a fake",
    ),
    (
        re.compile(r"\brandom\.(?:random|randint|choice|shuffle|uniform)\s*\("),
        "nondeterminism",
        "unseeded randomness → nondeterministic; seed it or inject",
    ),
]


@dataclass
class DoctrineReport:
    findings: list[DoctrineFinding] = field(default_factory=list[DoctrineFinding])

    @property
    def ok(self) -> bool:
        return not self.findings

    def summary(self) -> str:
        if self.ok:
            return "test doctrine: PASS (no change-detector/isolation anti-patterns)"
        by_kind: dict[str, int] = {}
        for f in self.findings:
            by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
        head = "test doctrine: " + ", ".join(f"{n} {k}" for k, n in sorted(by_kind.items()))
        return head + "\n" + "\n".join(f"  L{f.line} [{f.kind}] {f.detail}" for f in self.findings)


def doctrine_scan(source: str, *, is_live: bool | None = None) -> DoctrineReport:
    """Flag change-detector / non-isolation anti-patterns in test source. `live` tests are allowed
    real network (auto-detected from a `@pytest.mark.live` in the file unless overridden)."""
    text = source or ""
    live = ("@pytest.mark.live" in text or "mark.live" in text) if is_live is None else is_live
    findings: list[DoctrineFinding] = []
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for rx, kind, detail in _PATTERNS:
            if rx.search(line):
                if kind == "real_network" and live:
                    continue
                findings.append(DoctrineFinding(i, kind, detail))
    return DoctrineReport(findings)
