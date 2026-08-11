"""Per-message workspace snapshots + instant revert (Reversible Actions / Rollback).

A git-style "snapshot the workspace per message, revert in one move" without touching the user's
git history. Each snapshot is a content-addressed manifest (relpath → sha256) over a blob store
that dedups identical content across snapshots, so repeated snapshots are cheap. `revert` makes
the workspace match a snapshot exactly: restore modified, recreate deleted, remove files added
since. Cross-platform + deterministic. Pair with the agent loop: snapshot before each message,
revert to undo.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_IGNORE = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".workflow-data",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".snapshots",
    }
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Snapshot:
    checkpoint: str
    files: dict[str, str] = field(default_factory=dict[str, str])  # relpath -> sha256
    created: float = 0.0


@dataclass
class RevertSummary:
    restored: int = 0
    deleted: int = 0


class SnapshotManager:
    """In-memory snapshot store over a workspace root (content-addressed blobs)."""

    def __init__(self, root: str | Path, *, ignore: frozenset[str] = DEFAULT_IGNORE) -> None:
        self.root = Path(root).resolve()
        self.ignore = ignore
        self._blobs: dict[str, bytes] = {}
        self._snaps: dict[str, Snapshot] = {}
        self._order: list[str] = []

    def _iter_files(self) -> Iterator[str]:
        for p in self.root.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(self.root)
            if any(part in self.ignore for part in rel.parts):
                continue
            yield rel.as_posix()

    def _manifest(self) -> dict[str, str]:
        return {rel: _sha((self.root / rel).read_bytes()) for rel in self._iter_files()}

    def snapshot(self, checkpoint: str, *, now: float = 0.0) -> Snapshot:
        """Capture the current workspace under `checkpoint` (re-snapshotting an id overwrites)."""
        manifest: dict[str, str] = {}
        for rel in self._iter_files():
            data = (self.root / rel).read_bytes()
            sha = _sha(data)
            self._blobs.setdefault(sha, data)
            manifest[rel] = sha
        if checkpoint not in self._snaps:
            self._order.append(checkpoint)
        self._snaps[checkpoint] = Snapshot(checkpoint=checkpoint, files=manifest, created=now)
        return self._snaps[checkpoint]

    def diff(self, checkpoint: str) -> dict[str, list[str]]:
        """Compare the current workspace against a snapshot: added / modified / deleted."""
        snap = self._snaps[checkpoint]
        current = self._manifest()
        cur_keys, snap_keys = set(current), set(snap.files)
        return {
            "added": sorted(cur_keys - snap_keys),
            "deleted": sorted(snap_keys - cur_keys),
            "modified": sorted(k for k in cur_keys & snap_keys if current[k] != snap.files[k]),
        }

    def revert(self, checkpoint: str) -> RevertSummary:
        """Make the workspace match the snapshot exactly. Returns counts."""
        snap = self._snaps[checkpoint]
        summary = RevertSummary()
        current = set(self._iter_files())
        target = set(snap.files)
        for rel in current - target:  # remove files added since the snapshot
            (self.root / rel).unlink()
            summary.deleted += 1
        for rel, sha in snap.files.items():  # restore modified + recreate deleted
            p = self.root / rel
            if not p.exists() or _sha(p.read_bytes()) != sha:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(self._blobs[sha])
                summary.restored += 1
        return summary

    def checkpoints(self) -> list[str]:
        return list(self._order)

    @property
    def blob_count(self) -> int:
        return len(self._blobs)
