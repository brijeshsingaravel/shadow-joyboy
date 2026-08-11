"""Skill supply-chain integrity (ASI06) — vet a skill BEFORE it's trusted.

Three checks, composed into a guard:
- `skill_provenance` — was it sourced + permissively licensed? (reuses the ingest license set)
- `audit_skill_body` — AST-audit python code blocks (eval/exec, os.system, subprocess, risky
  imports) + scan shell blocks with the B29 command scanner
- `skills_guard` — combine into a trust decision (block on untrusted provenance OR any
  block-severity body finding)

Pure + deterministic. The trust gate on the skill pipeline ([[Skill Installer]] → here → trust).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any

from madras.security.cmd_scanner import scan_command
from madras.skills.ingest import PERMISSIVE

# python-level danger
_DANGEROUS_CALLS = {"eval", "exec", "compile", "__import__"}
_DANGEROUS_ATTRS = {
    ("os", "system"),
    ("os", "popen"),
    ("os", "remove"),
    ("os", "unlink"),
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("subprocess", "call"),
    ("subprocess", "check_output"),
    ("shutil", "rmtree"),
}
_RISKY_IMPORTS = {"subprocess", "socket", "ctypes", "pickle", "marshal"}

_CODE_BLOCK = re.compile(r"```([A-Za-z0-9_+-]*)\n(.*?)```", re.S)
_PY_LANGS = {"python", "py", "python3"}
_SH_LANGS = {"bash", "sh", "shell", "console", "zsh"}

# authored-markdown-is-data (ASI06): the opening frontmatter fence selects a parsing engine in
# gray-matter-style loaders; `---js`/`---javascript`/... EVAL the frontmatter on parse. Only plain
# YAML data is allowed — a bare `---` (or `---yaml`) fence.
_FM_OPEN_FENCE = re.compile(r"\A---([^\r\n]*)\r?\n")
_FM_ALLOWED = frozenset({"", "yaml"})
_FM_EXEC_ENGINES = frozenset(
    {"js", "javascript", "node", "coffee", "coffeescript", "ts", "typescript"}
)


@dataclass
class AuditFinding:
    rule: str
    severity: str  # warn | block
    detail: str


@dataclass
class ProvenanceVerdict:
    trusted: bool
    source: str
    issues: list[str] = field(default_factory=list[str])


@dataclass
class FrontmatterVerdict:
    ok: bool
    engine: str = ""  # the disallowed engine tag, if any
    reason: str = ""


@dataclass
class GuardResult:
    trusted: bool
    provenance: ProvenanceVerdict
    findings: list[AuditFinding] = field(default_factory=list[AuditFinding])


def audit_frontmatter(raw: str) -> FrontmatterVerdict:
    """Authored markdown frontmatter must be plain YAML DATA, never code.

    Reject an executable / non-YAML frontmatter engine declared on the opening fence
    (`---js` / `---javascript` / `---node` / ...) — in gray-matter-style loaders such a tag
    selects an engine that EVALs the frontmatter on parse. A bare `---` (or `---yaml`) fence is
    allowed; no opening fence at all is allowed (nothing to guard).
    """
    text = raw or ""
    if text and ord(text[0]) == 0xFEFF:  # tolerate a leading BOM
        text = text[1:]
    m = _FM_OPEN_FENCE.match(text)
    if m is None:
        return FrontmatterVerdict(True)  # no frontmatter fence
    tag = m.group(1).strip()
    if tag.lower() in _FM_ALLOWED:
        return FrontmatterVerdict(True)  # bare `---` or `---yaml` -> plain YAML data
    kind = "executable" if tag.lower() in _FM_EXEC_ENGINES else "non-YAML"
    return FrontmatterVerdict(
        False,
        engine=tag,
        reason=(
            f"{kind} frontmatter engine '{tag}' is disabled — frontmatter must be plain YAML data"
        ),
    )


def _attr_root(node: ast.Attribute) -> str:
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        cur = cur.value
    return cur.id if isinstance(cur, ast.Name) else ""


def audit_python(code: str) -> list[AuditFinding]:
    """AST-audit a python snippet for dynamic exec / dangerous calls / risky imports."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return [AuditFinding("parse_error", "warn", "python block did not parse")]
    out: list[AuditFinding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in _DANGEROUS_CALLS:
                out.append(AuditFinding("dynamic_exec", "block", f"{f.id}()"))
            elif isinstance(f, ast.Attribute) and (_attr_root(f), f.attr) in _DANGEROUS_ATTRS:
                out.append(AuditFinding("dangerous_call", "block", f"{_attr_root(f)}.{f.attr}()"))
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in _RISKY_IMPORTS:
                    out.append(AuditFinding("risky_import", "warn", a.name))
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in _RISKY_IMPORTS:
                out.append(AuditFinding("risky_import", "warn", node.module or ""))
    return out


def extract_code_blocks(body: str) -> list[tuple[str, str]]:
    """Return (lang, code) for every fenced block in a SKILL.md body."""
    return [(m.group(1).lower(), m.group(2)) for m in _CODE_BLOCK.finditer(body or "")]


def audit_skill_body(body: str) -> list[AuditFinding]:
    """Audit python + shell code blocks in a skill body."""
    out: list[AuditFinding] = []
    for lang, code in extract_code_blocks(body):
        if lang in _PY_LANGS:
            out.extend(audit_python(code))
        elif lang in _SH_LANGS:
            for f in scan_command(code).findings:
                out.append(AuditFinding(f"shell:{f.rule}", f.severity, f.detail))
    return out


def skill_provenance(provenance: dict[str, Any]) -> ProvenanceVerdict:
    """Trust the provenance if a source is recorded and the license is OSI-permissive."""
    source = str(provenance.get("source") or "")
    lic = str(provenance.get("license") or "").strip().lower()
    issues: list[str] = []
    if not source:
        issues.append("no source recorded")
    if lic and lic not in PERMISSIVE:
        issues.append(f"non-permissive license '{lic}'")
    return ProvenanceVerdict(trusted=not issues, source=source, issues=issues)


def skills_guard(*, body: str, provenance: dict[str, Any], raw: str | None = None) -> GuardResult:
    """The trust gate: untrusted provenance OR any block-severity finding → not trusted.

    When `raw` (the full file incl. frontmatter) is given, also reject an executable / non-YAML
    frontmatter engine (authored-markdown-is-data, ASI06).
    """
    prov = skill_provenance(provenance)
    findings = audit_skill_body(body)
    if raw is not None:
        fm = audit_frontmatter(raw)
        if not fm.ok:
            findings.append(AuditFinding("frontmatter_engine", "block", fm.reason))
    blocked = (not prov.trusted) or any(f.severity == "block" for f in findings)
    return GuardResult(trusted=not blocked, provenance=prov, findings=findings)
