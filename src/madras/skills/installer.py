"""Skill hub installer — install skills from a GitHub repo, a local path, or a URL.

The Codex `skill-installer` pattern, governed: the license is auto-detected from the source's
LICENSE file and gated (only OSI-permissive sources ingest — the no-AGPL/GPL doctrine), with
provenance recorded. Builds on `ingest.py`. `git clone` runs via subprocess in a thread
(Windows SelectorEventLoop-safe, per the worktree fix).
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from madras.skills.ingest import IngestResult, ingest_dir

# LICENSE-text sniff → SPDX-ish id (first match wins; order matters — AGPL before GPL).
_LICENSE_SIGNS: list[tuple[str, str]] = [
    ("apache license", "apache-2.0"),
    ("mit license", "mit"),
    ("permission is hereby granted, free of charge", "mit"),
    ("bsd 3-clause", "bsd-3-clause"),
    ("bsd 2-clause", "bsd-2-clause"),
    ("mozilla public license", "mpl-2.0"),
    ("isc license", "isc"),
    ("gnu affero general public", "agpl"),
    ("gnu lesser general public", "lgpl"),
    ("gnu general public", "gpl"),
    ("server side public license", "sspl"),
    ("business source license", "bsl"),
]
_LICENSE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "COPYING.md")


def detect_license(root: str | Path) -> str:
    """Best-effort SPDX id from a source's LICENSE file; 'unknown' if none found."""
    base = Path(root)
    for name in _LICENSE_FILES:
        p = base / name
        if p.is_file():
            txt = p.read_text(encoding="utf-8", errors="replace").lower()
            for needle, spdx in _LICENSE_SIGNS:
                if needle in txt:
                    return spdx
    return "unknown"


def _clone_url(repo: str, token: str | None) -> str:
    """Resolve a repo ref to a clone URL. Accepts 'owner/name', a full URL, or a local path.
    A token (private repos) is embedded only for https github URLs."""
    if repo.startswith(("http://", "https://", "git@", "file://")) or Path(repo).exists():
        url = repo
    else:
        url = f"https://github.com/{repo}.git"
    if token and url.startswith("https://github.com/"):
        url = url.replace("https://", f"https://{token}@", 1)
    return url


async def _run_git(*args: str, timeout: float = 120.0) -> tuple[int, str]:
    def _call() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=timeout, check=False
        )

    proc = await asyncio.to_thread(_call)
    return proc.returncode, (proc.stdout + proc.stderr)


async def install_from_path(
    store: Any,
    path: str | Path,
    *,
    source: str | None = None,
    license: str | None = None,
    project: str = "library",
    active: bool = True,
) -> IngestResult:
    """Install every SKILL.md under a local path. License auto-detected if not given."""
    root = Path(path)
    lic = license or detect_license(root)
    src = source or f"path:{root.name}"
    return await ingest_dir(store, root, source=src, license=lic, project=project, active=active)


async def install_from_repo(
    store: Any,
    repo: str,
    *,
    ref: str | None = None,
    subdir: str | None = None,
    token: str | None = None,
    license: str | None = None,
    project: str = "library",
    active: bool = True,
) -> IngestResult:
    """Shallow-clone a repo (or local repo path) and install its skills. The license is read
    from the cloned repo root (so even a `subdir` install is gated by the repo's license)."""
    url = _clone_url(repo, token)
    tmp = Path(tempfile.mkdtemp(prefix="madras_skill_"))
    try:
        args = ["clone", "--depth", "1"]
        if ref:
            args += ["--branch", ref]
        args += [url, str(tmp)]
        code, out = await _run_git(*args)
        if code != 0:
            return IngestResult(
                source=f"repo:{repo}",
                license=license or "unknown",
                reasons=[f"git clone failed: {out.strip()[:200]}"],
            )
        lic = license or detect_license(tmp)
        root = tmp / subdir if subdir else tmp
        return await ingest_dir(
            store, root, source=f"repo:{repo}", license=lic, project=project, active=active
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
