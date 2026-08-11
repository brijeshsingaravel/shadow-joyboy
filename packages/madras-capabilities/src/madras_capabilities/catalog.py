"""load_catalog() — parse Framework/Capabilities/*.md into typed Capability objects.

Reuses the regex-per-field frontmatter convention already established in
lighthouse_/capability_matrix.py and tests/test_lighthouse/test_capability_catalog.py
(no YAML dependency; the notes' frontmatter is a flat block, list fields are
``key: [a, b, c]`` on one line).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from madras_capabilities.model import Capability

_STR_FIELDS = ("id", "human_label", "category", "kind", "build_state", "rank_required", "tier")
_LIST_FIELDS = ("implements", "scopes", "evaluates")


def _frontmatter_block(text: str) -> str | None:
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    return parts[1]


def _parse_str(fm: str, key: str) -> str | None:
    m = re.search(rf'^{key}:\s*"?([^"\n]*)"?\s*$', fm, re.M)
    if not m:
        return None
    return m.group(1).strip().strip('"')


def _parse_list(fm: str, key: str) -> list[str]:
    # inline array: key: [a, b, c]
    m = re.search(rf"^{key}:\s*\[([^\]]*)\]", fm, re.M)
    if m:
        if not m.group(1).strip():
            return []
        return [v.strip().strip("\"'") for v in m.group(1).split(",") if v.strip()]
    # H7c (tamil-and-backend-spatial): YAML block-list style, also valid and in real
    # use (e.g. Gitleaks.md) -- was silently returning [] for this style before.
    m2 = re.search(rf"^{key}:\s*\n((?:[ \t]+-\s*.+\n?)+)", fm, re.M)
    if not m2:
        return []
    return [
        line.split("-", 1)[1].strip().strip("\"'")
        for line in m2.group(1).splitlines()
        if line.strip()
    ]


def parse_note(path: Path) -> Capability | None:
    """Parse one Capabilities/*.md note into a typed Capability, or None if it has no
    frontmatter / no id. H7c (tamil-and-backend-spatial): the single canonical per-note
    parser -- lighthouse_/conformance.py and lighthouse_/capability_matrix.py both call
    this instead of their own hand-rolled, truncated (600/1200-byte) regex scans."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    fm = _frontmatter_block(text)
    if fm is None:
        return None
    cap_id = _parse_str(fm, "id")
    if not cap_id:
        return None
    kwargs: dict[str, object] = {"id": cap_id}
    for key in _STR_FIELDS:
        if key == "id":
            continue
        val = _parse_str(fm, key)
        if val is not None:
            kwargs[key] = val
    for key in _LIST_FIELDS:
        kwargs[key] = _parse_list(fm, key)
    return Capability(**kwargs)


@dataclass
class Catalog:
    capabilities: list[Capability] = field(default_factory=list)
    by_id: dict[str, Capability] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)


def load_catalog(capabilities_dir: Path) -> Catalog:
    """Parse every *.md note in capabilities_dir into a Catalog. Malformed notes
    (no frontmatter, or no id) are recorded in .skipped, never raised."""
    catalog = Catalog()
    for path in sorted(Path(capabilities_dir).glob("*.md")):
        try:
            cap = parse_note(path)
        except Exception as exc:  # a bad note must not crash catalog load
            catalog.skipped.append(f"{path.name}: {exc}")
            continue
        if cap is None:
            catalog.skipped.append(f"{path.name}: no frontmatter or missing id")
            continue
        catalog.capabilities.append(cap)
        catalog.by_id[cap.id] = cap
    return catalog
