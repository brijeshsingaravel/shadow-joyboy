"""Ingest external SKILL.md bundles into the SkillStore — the catalog-growth lever.

Walk a directory for SKILL.md files, parse them (the agentskills.io open format Madras already
uses), and add each to the store with provenance (source · path · license). **License-gated**:
only OSI-permissive sources (MIT / Apache-2.0 / BSD / MPL / ISC) are ingested — the CLAUDE.md
no-AGPL/GPL/SSPL/BSL doctrine. Lets Madras absorb the whole ecosystem's skills (superpowers,
Hermes, OpenClaw, …) instead of re-authoring them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from madras.skills.format import parse_skill_md

# OSI-permissive licenses Madras may lift commercially.
PERMISSIVE = frozenset(
    {
        "mit",
        "apache-2.0",
        "apache2",
        "apache 2.0",
        "bsd",
        "bsd-2-clause",
        "bsd-3-clause",
        "isc",
        "mpl-2.0",
    }
)


@dataclass
class IngestResult:
    source: str
    license: str
    found: int = 0
    ingested: int = 0
    skipped: int = 0
    license_blocked: bool = False
    reasons: list[str] = field(default_factory=list[str])


def discover_skills(root: str | Path) -> list[Path]:
    """All SKILL.md files under root (recursive), sorted."""
    return sorted(Path(root).rglob("SKILL.md"))


async def ingest_dir(
    store: Any,
    root: str | Path,
    *,
    source: str,
    license: str,
    project: str | None = None,
    active: bool = False,
) -> IngestResult:
    """Ingest every SKILL.md under root into the store (if the source license is permissive).

    `store` needs `async add_candidate(skill, *, project, provenance)` and (when active)
    `async approve(name, *, project)`. With `active=True` each skill is promoted to active so
    it's immediately usable (e.g. the shared `library`); else it lands as a candidate.
    """
    res = IngestResult(source=source, license=license)
    if license.strip().lower() not in PERMISSIVE:
        res.license_blocked = True
        res.reasons.append(f"license '{license}' not OSI-permissive — NOT ingested")
        return res
    if not Path(root).exists():
        res.reasons.append(f"path missing: {root}")
        return res
    proj = project or f"harvest:{source}"
    for path in discover_skills(root):
        res.found += 1
        raw_text = path.read_text(encoding="utf-8", errors="replace")
        try:
            skill = parse_skill_md(raw_text)
        except Exception as exc:
            res.skipped += 1
            res.reasons.append(f"{path.name}: parse error {type(exc).__name__}")
            continue
        if not skill.name or not skill.description:
            res.skipped += 1
            res.reasons.append(f"{path}: missing name/description")
            continue
        # s46: skills_guard (skill supply-chain integrity, ASI06) had no live caller --
        # only the license-string check above gated ingestion. AST-audits python/shell
        # code blocks + rejects an executable/non-YAML frontmatter engine BEFORE a skill
        # ever reaches the store, permissive license or not.
        from madras.skills.integrity import skills_guard

        guard = skills_guard(
            body=skill.body, provenance={"source": source, "license": license}, raw=raw_text
        )
        if not guard.trusted:
            res.skipped += 1
            reasons = ", ".join(f.detail for f in guard.findings if f.severity == "block")
            res.reasons.append(f"{path.name}: blocked by skills_guard ({reasons})")
            continue
        await store.add_candidate(
            skill,
            project=proj,
            provenance={"source": source, "path": str(path), "license": license},
        )
        if active:
            await store.approve(skill.name, project=proj)
        res.ingested += 1
    return res
