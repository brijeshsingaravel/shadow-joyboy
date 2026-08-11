"""`#` quick-add memory (row 14f).

Inline "remember this": a `#remember <text>` (or `#mem` / `#note`) directive in a message becomes a
memory. Rides the row-14a file-memory store — quick-add writes a content-hashed `.md` file (so the
same note twice is idempotent, not duplicated); the row-14a `import_from_files` later reconciles it
into the DB through `MemoryFabric.remember` (arbitration applies). The capture half of the
user-sovereign memory loop.

The directive is `#` immediately followed by a keyword (no space) — unambiguous vs a markdown
`# Heading`, which requires the space. Pure stdlib.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from madras.memory.file_memory import FileMemoryStore
from madras.memory.retrieval import MemoryItem

# a line like `#remember acme: deploys on fridays`  (keyword glued to #, then the text)
_QUICK_RE = re.compile(r"^[ \t]*#(?:remember|mem|note)\b[ \t]+(.+?)[ \t]*$", re.M | re.I)


@dataclass
class QuickAdd:
    content: str
    subject: str = "general"
    kind: str = "note"


def parse_quick_adds(text: str) -> list[QuickAdd]:
    """Extract `#remember`/`#mem`/`#note` directives. An optional `subject: content` form sets the
    subject (drives contradiction); a plain markdown `# Heading` is never matched."""
    out: list[QuickAdd] = []
    for match in _QUICK_RE.finditer(text or ""):
        body = match.group(1).strip()
        subject, sep, rest = body.partition(":")
        if sep and rest.strip() and len(subject.split()) <= 4:
            out.append(QuickAdd(content=rest.strip(), subject=subject.strip().lower()))
        else:
            out.append(QuickAdd(content=body))
    return out


def _quick_id(content: str) -> str:
    digest = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:12]
    return f"qa-{digest}"


def quick_add(
    content: str,
    *,
    store: FileMemoryStore,
    now: float = 0.0,
    subject: str = "general",
    kind: str = "note",
    source: str = "quick-add",
) -> MemoryItem:
    """Capture one memory to the file store; content-hashed id => idempotent (no duplicates)."""
    item = MemoryItem(
        id=_quick_id(content),
        kind=kind,
        subject=subject,
        content=content.strip(),
        tags=["quick-add"],
        source=source,
        created_at=now,
        valid_from=now,
    )
    store.write(item)
    return item


def capture_quick_adds(text: str, *, store: FileMemoryStore, now: float = 0.0) -> list[MemoryItem]:
    """Parse every quick-add directive in `text` and write each to the file store."""
    return [
        quick_add(qa.content, store=store, now=now, subject=qa.subject, kind=qa.kind)
        for qa in parse_quick_adds(text)
    ]
