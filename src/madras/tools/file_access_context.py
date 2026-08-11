"""Per-run read-tracking context for the read-before-write + stale-read guard.

Mirrors memory_context.py. Tracks which workspace paths the agent has read so file_edit / file_write
can refuse to blind-overwrite a file that was never read (read-before-write) AND can detect a STALE
overwrite — the on-disk content changed since the read (lifts eve's ReadFileStamp: a sha256 + byte
fingerprint stamped at read time, re-checked at write time). Stamps clear on compaction so a write
afterward must re-read.
"""

from __future__ import annotations

import hashlib
from contextvars import ContextVar
from dataclasses import dataclass

# Default MUST be None (a shared mutable default would leak across runs/tests).
_reads: ContextVar[set[str] | None] = ContextVar("madras_file_reads", default=None)
# path -> (sha256 hex, byte length) recorded at read time (the stale-read fingerprint).
_stamps: ContextVar[dict[str, tuple[str, int]] | None] = ContextVar(
    "madras_file_stamps", default=None
)

OK, NO_READ, STALE = "ok", "no_read", "stale"


@dataclass
class WriteVerdict:
    decision: str  # ok | no_read | stale
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision == OK


def _normalize(path: str) -> str:
    """Normalize a path so file_read and file_edit agree on identity."""
    return path.strip().replace("\\", "/")


def _hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def _get_stamps() -> dict[str, tuple[str, int]]:
    cur = _stamps.get()
    if cur is None:
        cur = dict[str, tuple[str, int]]()
        _stamps.set(cur)
    return cur


def mark_read(path: str) -> None:
    """Record that `path` has been read in the active context."""
    current = _reads.get()
    if current is None:
        current = set[str]()
        _reads.set(current)
    current.add(_normalize(path))


def was_read(path: str) -> bool:
    """Return True if `path` has been read in the active context."""
    current = _reads.get()
    if current is None:
        return False
    return _normalize(path) in current


def stamp_read(path: str, content: str) -> None:
    """Record a content fingerprint (sha256 + byte length) at read time — the basis for stale-read
    detection. Also marks the path read (read-before-write)."""
    mark_read(path)
    _get_stamps()[_normalize(path)] = (_hash(content), len(content or ""))


def check_write(path: str, on_disk_content: str | None) -> WriteVerdict:
    """Gate a write. `on_disk_content` is the file's CURRENT content (or None if it doesn't exist).

    * never read + file exists  → ``no_read`` (read it first; blind overwrite blocked)
    * never read + new file      → ``ok`` (a create)
    * read, but on-disk content changed since the read → ``stale`` (blocked)
    * read + unchanged           → ``ok``
    """
    key = _normalize(path)
    stamps = _stamps.get() or {}
    if key not in stamps:
        if on_disk_content is None:
            return WriteVerdict(OK, "new file (create)")
        return WriteVerdict(NO_READ, "file exists but was never read this run — read it first")
    stamped_hash, stamped_len = stamps[key]
    if _hash(on_disk_content or "") != stamped_hash:
        now_len = len(on_disk_content or "")
        return WriteVerdict(STALE, f"changed on disk since read (bytes {stamped_len}->{now_len})")
    return WriteVerdict(OK, "read + unchanged")


def stamp_write(path: str, content: str) -> None:
    """After a successful write, refresh the stamp to the just-written content so chained edits
    don't require a re-read."""
    _get_stamps()[_normalize(path)] = (_hash(content), len(content or ""))


def clear_stamps() -> None:
    """Drop the read stamps (called on context compaction) so a subsequent write must re-read."""
    _stamps.set(None)


def reset_reads() -> None:
    """Clear all recorded reads + stamps (per-session / test isolation)."""
    _reads.set(None)
    _stamps.set(None)
