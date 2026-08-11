"""Hot-reload on edit (row 14e).

The Builder dev-loop: edit an agent's `.md` / config / memory file and the agent reconfigures. A
poll-based, **content-hash** change detector (reuses the row-80 sha256 fingerprint idiom; no
`watchdog` dependency — pure stdlib, deterministic, testable). Each watched file carries an injected
loader callback, so the SAME reloader reconfigures an agent-as-markdown file (via
`parse_agent_markdown`), an AGENTS.md hierarchy (`assemble_context`), or a memory file
(`parse_memory`). Content-hash (not
mtime): a no-op rewrite is ignored, a real change never missed on coarse-mtime filesystems.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


def _fingerprint(path: str) -> str | None:
    """sha256 of the file's bytes, or None if the file is gone."""
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


@dataclass
class WatchEntry:
    path: str
    loader: Callable[[str], Any]
    fingerprint: str | None = None
    value: Any = None


@dataclass
class ReloadEvent:
    path: str
    value: Any
    kind: str  # loaded | changed | deleted


@dataclass
class FileReloader:
    _entries: dict[str, WatchEntry] = field(default_factory=dict[str, WatchEntry])

    def register(self, path: str, loader: Callable[[str], Any]) -> ReloadEvent:
        """Start watching `path`; load it once now. `loader` maps a path -> reconfigured value."""
        fp = _fingerprint(path)
        value = loader(path) if fp is not None else None
        self._entries[path] = WatchEntry(path, loader, fp, value)
        return ReloadEvent(path, value, "loaded")

    def poll(self) -> list[ReloadEvent]:
        """Re-check every watched file; reload + emit an event for each change/deletion."""
        events: list[ReloadEvent] = []
        for entry in self._entries.values():
            current = _fingerprint(entry.path)
            if current == entry.fingerprint:
                continue
            if current is None:  # the file was deleted
                entry.fingerprint, entry.value = None, None
                events.append(ReloadEvent(entry.path, None, "deleted"))
                continue
            entry.fingerprint = current  # changed (or re-created) -> reload
            entry.value = entry.loader(entry.path)
            events.append(ReloadEvent(entry.path, entry.value, "changed"))
        return events

    def current(self, path: str) -> Any:
        entry = self._entries.get(path)
        return entry.value if entry is not None else None
