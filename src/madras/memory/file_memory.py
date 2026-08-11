"""File-memory frontmatter — portable, human-editable agent memory (row 14a).

Agent memory as markdown files with YAML frontmatter (the same shape as a `MemoryItem`): the
human-editable, git-versionable MIRROR of the DB memory — Postgres/Qdrant (the `MemoryFabric` spine)
stays the fast queryable layer, the files are the user-sovereign one. The *complement* of
`portability.py` (E-X4b), which exports one machine-portable signed JSON bundle; here each memory is
its own readable `.md` file a user can open, diff, hand-edit, and commit.

Composes the spine, never bypasses it: `export_to_files` pulls via `MemoryFabric.all_items`, and
`import_from_files` feeds every file back through `MemoryFabric.remember` (so contradiction
arbitration + provenance still apply — an edited file is reconciled, not blind-inserted). Tenant +
agent isolated by path (composes row 74). Pure stdlib for the file layer.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from typing import Any, cast

from madras.memory.retrieval import MemoryItem

_BODY_FIELD = "content"  # the atomic statement -> the markdown body
_FLOAT_FIELDS = {
    "confidence",
    "created_at",
    "valid_from",
    "valid_until",
    "strength",
    "last_accessed",
}
_INT_FIELDS = {"recall_count"}
_LIST_FIELDS = {"tags"}
_NULLABLE = {"valid_until", "supersedes"}
_NULLS = {"null", "none", ""}

_FENCE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)


def _dump_value(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, list):
        return "[" + ", ".join(str(x) for x in cast("list[Any]", value)) + "]"
    return str(value)


def dump_memory(item: MemoryItem) -> str:
    """Serialize a MemoryItem to a frontmatter .md file (content -> body)."""
    lines = ["---"]
    for f in dataclass_fields(item):
        if f.name == _BODY_FIELD:
            continue
        lines.append(f"{f.name}: {_dump_value(getattr(item, f.name))}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + (item.content or "") + "\n"


def _coerce(name: str, raw: str) -> object:
    low = raw.strip().lower()
    if name in _NULLABLE and low in _NULLS:
        return None
    if name in _LIST_FIELDS:
        inner = raw.strip()
        if inner.startswith("[") and inner.endswith("]"):
            inner = inner[1:-1]
        return [x.strip() for x in inner.split(",") if x.strip()]
    if name in _FLOAT_FIELDS:
        return None if low in _NULLS else float(raw)
    if name in _INT_FIELDS:
        return int(float(raw))
    return raw


def parse_memory(text: str) -> MemoryItem:
    """Parse a frontmatter .md file back to a MemoryItem (lossless for the known schema)."""
    if text[:1] and ord(text[0]) == 0xFEFF:  # tolerate a leading BOM
        text = text[1:]
    m = _FENCE.match(text.lstrip())
    fm: dict[str, str] = {}
    body = ""
    if m is not None:
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            key, _, raw = line.partition(":")
            fm[key.strip()] = raw.strip()
        body = m.group(2).strip("\n")
    kwargs: dict[str, object] = {_BODY_FIELD: body}
    for f in dataclass_fields(MemoryItem):
        if f.name == _BODY_FIELD or f.name not in fm:
            continue
        kwargs[f.name] = _coerce(f.name, fm[f.name])
    return MemoryItem(**kwargs)  # type: ignore[arg-type]


def _safe(part: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in part) or "_"


@dataclass
class FileMemoryStore:
    """A tenant + agent scoped store of memory files under `<root>/<agent>/<tenant>/<id>.md`."""

    root: str
    agent_name: str = "shadow"
    tenant: str = "default"

    @property
    def dir(self) -> str:
        return os.path.join(self.root, _safe(self.agent_name), _safe(self.tenant))

    def _path(self, mem_id: str) -> str:
        return os.path.join(self.dir, _safe(mem_id) + ".md")

    def write(self, item: MemoryItem) -> str:
        os.makedirs(self.dir, exist_ok=True)
        path = self._path(item.id)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(dump_memory(item))
        return path

    def read(self, mem_id: str) -> MemoryItem | None:
        path = self._path(mem_id)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return parse_memory(fh.read())

    def list(self) -> list[MemoryItem]:
        if not os.path.isdir(self.dir):
            return []
        out: list[MemoryItem] = []
        for name in sorted(os.listdir(self.dir)):
            if not name.endswith(".md"):
                continue
            with open(os.path.join(self.dir, name), encoding="utf-8") as fh:
                out.append(parse_memory(fh.read()))
        return out

    def delete(self, mem_id: str) -> bool:
        path = self._path(mem_id)
        if not os.path.exists(path):
            return False
        os.remove(path)
        return True

    def export_from(self, items: list[MemoryItem]) -> int:
        """Mirror a batch of memory items to disk. Returns the count written."""
        for item in items:
            self.write(item)
        return len(items)

    def import_all(self) -> list[MemoryItem]:
        """Read every memory file back (after the user hand-edited them)."""
        return self.list()


async def export_to_files(
    fabric: Any, store: FileMemoryStore, *, include_expired: bool = False
) -> int:
    """DB -> files: pull the agent's memory via the MemoryFabric spine and mirror it to disk."""
    items = await fabric.all_items(include_expired=include_expired)
    return store.export_from(items)


async def import_from_files(fabric: Any, store: FileMemoryStore, *, now: float) -> list[str]:
    """files -> DB: feed every (possibly hand-edited) file back through MemoryFabric.remember, so
    contradiction arbitration + provenance apply. Returns the remembered item ids."""
    remembered: list[str] = []
    for item in store.import_all():
        remembered.extend(await fabric.remember(item, now=now))
    return remembered
