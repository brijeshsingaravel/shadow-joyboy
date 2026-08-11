"""Git worktree isolation for parallel file-mutating agents.

Each parallel worker gets its OWN linked git worktree (an isolated checkout on its own
branch), so concurrent edits never conflict; on exit the worktree is removed (auto-clean).
Lifts the Claude Code `isolation:'worktree'` pattern — host-side via git, distinct from the
E2B sandbox isolation the SWE loop uses for remote runs. Pairs with delegate_parallel /
the kanban dispatcher so each worker mutates its own tree.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


async def _git(cwd: str | Path, *args: str, timeout: float = 60.0) -> str:
    """Run a git command off the event loop. Uses subprocess.run in a thread (not asyncio
    subprocess) so it works on Windows' SelectorEventLoop too (psycopg forces Selector,
    which can't spawn asyncio subprocesses)."""

    def _run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    try:
        proc = await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired:
        raise GitError(f"git {' '.join(args)} timed out") from None
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    return proc.stdout


@dataclass
class Worktree:
    path: Path
    branch: str
    repo: Path


class WorktreeManager:
    """Create / remove isolated git worktrees for parallel workers off one repo."""

    def __init__(self, repo: str | Path, *, base: str | Path | None = None) -> None:
        self._repo = Path(repo).resolve()
        self._base = Path(base) if base else Path(tempfile.gettempdir()) / "madras_worktrees"

    async def create(self, *, name: str = "") -> Worktree:
        self._base.mkdir(parents=True, exist_ok=True)
        wid = (name or uuid.uuid4().hex[:8]).replace("/", "-")
        branch = f"madras/wt-{wid}"
        path = self._base / f"wt-{wid}-{uuid.uuid4().hex[:4]}"
        await _git(self._repo, "worktree", "add", "-q", "-b", branch, str(path), "HEAD")
        return Worktree(path=path, branch=branch, repo=self._repo)

    async def remove(self, wt: Worktree, *, force: bool = True) -> None:
        args = ["worktree", "remove", *(["--force"] if force else []), str(wt.path)]
        try:
            await _git(self._repo, *args)
        except GitError:
            shutil.rmtree(wt.path, ignore_errors=True)  # dirty/detached → hard clean
            await _git(self._repo, "worktree", "prune")
        try:
            await _git(self._repo, "branch", "-D", wt.branch)  # best-effort branch cleanup
        except GitError:
            pass

    async def list_paths(self) -> list[str]:
        out = await _git(self._repo, "worktree", "list", "--porcelain")
        return [ln.split(" ", 1)[1] for ln in out.splitlines() if ln.startswith("worktree ")]

    async def diff(self, wt: Worktree) -> str:
        """The worker's changes in its worktree (staged + unstaged vs HEAD)."""
        await _git(wt.path, "add", "-A")
        return await _git(wt.path, "diff", "--staged")

    @asynccontextmanager
    async def worktree(self, *, name: str = ""):
        wt = await self.create(name=name)
        try:
            yield wt
        finally:
            await self.remove(wt)


@asynccontextmanager
async def isolated_worktree(repo: str | Path, *, name: str = ""):
    """One-off isolated worktree context: `async with isolated_worktree(repo) as wt: ...`."""
    async with WorktreeManager(repo).worktree(name=name) as wt:
        yield wt
