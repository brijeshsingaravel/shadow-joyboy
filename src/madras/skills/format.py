"""SKILL.md format — agentskills.io open standard (portable across Claude Code, Codex, etc.).

A skill = YAML frontmatter (required: name, description; optional: metadata) + a markdown
body. Madras-specific fields live under metadata.madras (e.g. allowed toolsets, category).
Progressive disclosure: L0 = name+description (~tokens), L1 = full body, L2 = references.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import yaml


@dataclass
class Skill:
    name: str
    description: str
    body: str = ""
    toolsets: list[str] = field(default_factory=list[str])  # allowed toolsets (governance)
    category: str = ""
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])  # any extra frontmatter

    def l0(self) -> str:
        """Metadata line for progressive disclosure (what loads at startup)."""
        return f"- {self.name}: {self.description}"


def parse_skill_md(text: str) -> Skill:
    """Parse a SKILL.md string. Frontmatter is between leading '---' fences."""
    fm: dict[str, Any] = {}
    body = text
    if text.lstrip().startswith("---"):
        s = text.lstrip()
        end = s.find("\n---", 3)
        if end != -1:
            fm = yaml.safe_load(s[3:end]) or {}
            body = s[end + 4 :].lstrip("\n")
    name = str(fm.get("name", "")).strip()
    description = str(fm.get("description", "")).strip()
    raw_meta: Any = fm.get("metadata", {}) or {}
    meta = cast("dict[str, Any]", raw_meta) if isinstance(raw_meta, dict) else {}
    madras: dict[str, Any] = meta.get("madras", {}) or {}
    return Skill(
        name=name,
        description=description,
        body=body,
        toolsets=list(madras.get("toolsets", []) or []),
        category=str(madras.get("category", "")),
        metadata=meta,
    )


def to_skill_md(skill: Skill) -> str:
    """Serialize a Skill back to a SKILL.md string."""
    meta = dict(skill.metadata)
    madras = dict(meta.get("madras", {}))
    if skill.toolsets:
        madras["toolsets"] = skill.toolsets
    if skill.category:
        madras["category"] = skill.category
    if madras:
        meta["madras"] = madras
    fm: dict[str, Any] = {"name": skill.name, "description": skill.description}
    if meta:
        fm["metadata"] = meta
    front = yaml.safe_dump(fm, sort_keys=False).strip()
    return f"---\n{front}\n---\n\n{skill.body}".rstrip() + "\n"
