"""Dependency-pinning policy — ASI04 supply-chain gate (row 71).

After the litellm + Shai-Hulud supply-chain incidents, mutable refs are the attack surface: a
GitHub Action pinned to `@v4` or a git dep pinned to `@main` can be silently re-pointed to malicious
code. This is the deterministic policy that REJECTS unpinned dependencies — every GitHub Action and
git dependency must pin to a full 40-char commit SHA, and the lockfile must carry hashes. Pure +
CI-gate-ready (a non-zero exit on any violation); composes `osv_scan` (vulns) for the full ASI04
picture. Lifts the Hermes "SHA-pin everything" doctrine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SHA = re.compile(r"^[0-9a-f]{40}$")
_USES = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.M)  # `uses: owner/repo@ref`
_GIT_DEP = re.compile(r"git\+[^\s@'\"]+@([^\s'\"#)]+)")  # `git+https://...@ref`
_UV_REV = re.compile(r'rev\s*=\s*"([^"]+)"')  # uv git source `rev = "..."`


@dataclass
class PinViolation:
    where: str  # file/source label
    ref: str  # the offending reference
    kind: str  # action_unpinned | git_unpinned | lock_no_hashes
    detail: str


def _is_sha(ref: str) -> bool:
    return bool(_SHA.match(ref.strip()))


def scan_workflow(text: str, *, label: str = "workflow") -> list[PinViolation]:
    """Flag GitHub Action `uses:` refs not pinned to a 40-char commit SHA (local `./` + `docker://`
    actions are exempt)."""
    out: list[PinViolation] = []
    for m in _USES.finditer(text or ""):
        spec = m.group(1).strip().strip("'\"")
        if spec.startswith(("./", "docker://")):
            continue
        if "@" not in spec:
            out.append(PinViolation(label, spec, "action_unpinned", "no ref pinned"))
            continue
        _, ref = spec.rsplit("@", 1)
        if not _is_sha(ref):
            out.append(
                PinViolation(
                    label,
                    spec,
                    "action_unpinned",
                    f"mutable ref '{ref}' — pin to a 40-char commit SHA",
                )
            )
    return out


def scan_manifest(text: str, *, label: str = "pyproject.toml") -> list[PinViolation]:
    """Flag git deps (PEP 508 `git+...@ref` or uv `rev = ...`) not pinned to a commit SHA."""
    out: list[PinViolation] = []
    for m in _GIT_DEP.finditer(text or ""):
        if not _is_sha(m.group(1)):
            out.append(
                PinViolation(
                    label, m.group(0), "git_unpinned", f"git ref '{m.group(1)}' is not a commit SHA"
                )
            )
    for m in _UV_REV.finditer(text or ""):
        if not _is_sha(m.group(1)):
            out.append(
                PinViolation(
                    label,
                    f'rev="{m.group(1)}"',
                    "git_unpinned",
                    f"uv git rev '{m.group(1)}' is not a commit SHA",
                )
            )
    return out


def lock_has_hashes(text: str) -> bool:
    return "hash = " in (text or "") or "hashes = " in (text or "")


@dataclass
class PolicyReport:
    violations: list[PinViolation] = field(default_factory=list[PinViolation])

    @property
    def ok(self) -> bool:
        return not self.violations

    def summary(self) -> str:
        if self.ok:
            return "dependency-pinning policy: PASS (all deps SHA-pinned, lock hashed)"
        lines = [f"dependency-pinning policy: {len(self.violations)} violation(s)"]
        lines += [f"  [{v.kind}] {v.where}: {v.ref or '-'} — {v.detail}" for v in self.violations]
        return "\n".join(lines)


@dataclass
class DependencyPolicy:
    require_lock_hashes: bool = True

    def audit(
        self,
        *,
        workflows: dict[str, str] | None = None,
        manifests: dict[str, str] | None = None,
        lock: str | None = None,
    ) -> PolicyReport:
        v: list[PinViolation] = []
        for label, text in (workflows or {}).items():
            v.extend(scan_workflow(text, label=label))
        for label, text in (manifests or {}).items():
            v.extend(scan_manifest(text, label=label))
        if self.require_lock_hashes and lock is not None and not lock_has_hashes(lock):
            v.append(
                PinViolation(
                    "uv.lock",
                    "",
                    "lock_no_hashes",
                    "lockfile carries no hashes (uv lock keeps them)",
                )
            )
        return PolicyReport(v)

    def gate(self, report: PolicyReport) -> int:
        """CI gate exit code: 0 = pass, 1 = unpinned dependency found."""
        return 0 if report.ok else 1
