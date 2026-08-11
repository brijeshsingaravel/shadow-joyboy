"""Pre-exec command scanner — a static gate ABOVE the sandbox (the `tirith` pattern, MIT).

Scans a shell command string BEFORE execution for attack classes the permission rules don't
catch: pipe-to-interpreter (`curl … | sh`), terminal-injection control sequences, homograph /
non-ASCII lookalikes, obfuscated exec (`base64 -d | sh`, `eval $(…)`), and destructive
primitives (`rm -rf /`, `mkfs`, fork-bomb). Verdict: allow / warn / block. Pure + deterministic;
complements `permissions.py` (intent gate) and the sandbox (runtime isolation).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ALLOW, WARN, BLOCK = "allow", "warn", "block"
_SEV_RANK = {ALLOW: 0, WARN: 1, BLOCK: 2}


@dataclass
class ScanFinding:
    rule: str
    severity: str  # warn | block
    detail: str


@dataclass
class ScanResult:
    verdict: str  # allow | warn | block
    findings: list[ScanFinding] = field(default_factory=list[ScanFinding])

    @property
    def ok(self) -> bool:
        return self.verdict != BLOCK

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1


# download-pipe-to-interpreter: curl/wget/fetch ... | sh|bash|zsh|python|perl|ruby|node
_PIPE_INTERP = re.compile(
    r"\b(curl|wget|fetch)\b[^|]*\|\s*(sudo\s+)?(sh|bash|zsh|dash|python[0-9.]*|perl|ruby|node)\b",
    re.I,
)
# obfuscated exec: base64 -d | sh ; echo <b64> | base64 -d | bash
_OBFUSC_EXEC = re.compile(r"\bbase64\b[^|]*-d[^|]*\|\s*(sh|bash|zsh|python[0-9.]*)\b", re.I)
_EVAL_SUBST = re.compile(r"\beval\b\s+[\"']?\$\(", re.I)
# terminal-injection: raw ESC / OSC / CSI control sequences embedded in the command
_TERM_INJECT = re.compile(r"(\x1b\[|\x1b\]|\x9b|\x07|\\x1b|\\u001b|\\033|\\e\[)")
# destructive primitives
_DESTRUCTIVE = [
    (re.compile(r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+(/|/\*|~|\$HOME)", re.I), "rm -rf root/home"),
    (re.compile(r"\bmkfs(\.\w+)?\b", re.I), "mkfs (format filesystem)"),
    (re.compile(r"\bdd\b[^\n]*\bof=/dev/(sd|nvme|disk)", re.I), "dd to raw disk"),
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", re.I), "fork bomb"),
    (re.compile(r"\bchmod\s+-R?\s*777\s+/(\s|$)", re.I), "chmod 777 /"),
    (re.compile(r">\s*/dev/(sd|nvme)", re.I), "overwrite raw disk"),
]


def _has_non_ascii(cmd: str) -> list[str]:
    """Return the suspicious non-ASCII characters (homograph risk) in a shell command."""
    return sorted({ch for ch in cmd if ord(ch) > 0x7F})


def scan_command(cmd: str) -> ScanResult:
    """Statically scan a command; return a verdict + findings. Empty command = allow."""
    text = cmd or ""
    findings: list[ScanFinding] = []

    if _PIPE_INTERP.search(text):
        findings.append(
            ScanFinding(
                "pipe_to_interpreter", BLOCK, "downloads and pipes straight into an interpreter"
            )
        )
    if _OBFUSC_EXEC.search(text):
        findings.append(
            ScanFinding("obfuscated_exec", BLOCK, "base64-decodes and pipes into a shell")
        )
    if _EVAL_SUBST.search(text):
        findings.append(ScanFinding("eval_substitution", WARN, "eval of a command substitution"))
    if _TERM_INJECT.search(text):
        findings.append(
            ScanFinding("terminal_injection", BLOCK, "embeds terminal control/escape sequences")
        )
    for pattern, label in _DESTRUCTIVE:
        if pattern.search(text):
            findings.append(ScanFinding("destructive", BLOCK, label))
    nonascii = _has_non_ascii(text)
    if nonascii:
        findings.append(
            ScanFinding("homograph", WARN, f"non-ASCII characters in command: {nonascii}")
        )

    verdict = ALLOW
    for f in findings:
        if _SEV_RANK[f.severity] > _SEV_RANK[verdict]:
            verdict = f.severity
    return ScanResult(verdict=verdict, findings=findings)
