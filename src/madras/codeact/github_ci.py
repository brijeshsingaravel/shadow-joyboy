"""GitHub PR/CI iterate-on-failures — the agent opens a PR and drives CI to green.

Layers the SWE loop's edit primitives onto a real GitHub workflow: clone a repo, the model proposes
SEARCH/REPLACE edits for the issue, commit + push a branch, open a PR (`gh`), wait for the CI run,
and on a CI FAILURE feed the run's logs back + iterate until CI is green (or the budget is spent).

Outward-facing by design (pushes branches + opens PRs via `gh`). Operates ONLY on the repo passed
in — never a third-party project. LLM calls go through the rate-safe `_complete` (one call each, no
retry-storm) on the zero-cost free fleet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from madras.codeact.swe_loop import (
    SweTask,
    apply_edits,
    parse_edits,
    pick_files,
    propose_edits,
    repo_map,
)
from madras.llm.gateway import LLMGateway
from madras.tools.sandbox import LocalSandbox

_REPO = "repo"
_BOT = "git -c user.email=madras-bot@madras.ai -c user.name=madras-bot"


@dataclass
class CIResult:
    green: bool
    pr_url: str
    iterations: int
    log: list[str] = field(default_factory=list[str])


def _latest_run(runs_json: str) -> dict[str, Any] | None:
    """Pick the most recent workflow run from `gh run list --json ...` output."""
    try:
        runs = json.loads(runs_json)
    except (json.JSONDecodeError, TypeError):
        return None
    return runs[0] if runs else None


async def _sh(sb: LocalSandbox, cmd: str, *, timeout: float = 120) -> str:
    r = await sb.run_command(f"cd {_REPO} && {cmd}", timeout=timeout)
    return r.stdout if r.ok else (r.stdout + r.stderr)


async def run_github_ci_loop(
    repo: str,
    problem: str,
    *,
    gateway: LLMGateway,
    model: str,
    branch: str = "madras-fix",
    max_iters: int = 4,
    workspace: Path | None = None,
) -> CIResult:
    """Open a PR on ``repo`` (owner/name) that fixes ``problem`` and iterate until CI is green."""
    sb = LocalSandbox(session_id=f"ghci-{branch}", workspace=workspace)
    await sb.start()
    log: list[str] = []
    # fresh clone + a working branch
    await sb.run_command(f"rm -rf {_REPO} && gh repo clone {repo} {_REPO}", timeout=180)
    await _sh(sb, f"git checkout -b {branch}")
    task = SweTask(repo_url=repo, base_commit="", problem=problem, test_cmd="")

    pr_url = ""
    last_fail = ""
    for i in range(max_iters):
        rmap = await repo_map(sb)
        files = await pick_files(gateway, model, sb, task, rmap)
        edits = parse_edits(await propose_edits(gateway, model, task, rmap, last_fail, files))
        if not edits:
            last_fail = "no edit blocks produced; reply with FILE/SEARCH/REPLACE blocks"
            log.append(f"iter {i}: no edits")
            continue
        ok, err = await apply_edits(sb, edits)
        if not ok:
            last_fail = f"edits did not apply: {err}"
            log.append(f"iter {i}: {err[:100]}")
            await _sh(sb, "git checkout -q -- .")
            continue
        await _sh(sb, f"{_BOT} commit -aqm 'madras: fix attempt {i + 1}'")
        # no real change → the edit was a no-op; don't push an empty branch / open an empty PR.
        if not (await _sh(sb, "git diff --stat HEAD~1 2>/dev/null")).strip():
            last_fail = "your edits produced no change to the code; make a real fix"
            log.append(f"iter {i}: no-op edit")
            continue
        await _sh(sb, f"git push -fq -u origin {branch}", timeout=120)
        if not pr_url:
            pr_url = (
                await _sh(sb, f"gh pr create --fill --head {branch} 2>&1 | tail -1", timeout=60)
            ).strip()
            log.append(f"iter {i}: PR {pr_url}")
        # wait for the CI run on this branch, then read its conclusion
        await _sh(sb, f"sleep 8 && gh run list --branch {branch} --limit 1 >/dev/null", timeout=60)
        runs = await _sh(
            sb,
            f"gh run list --branch {branch} --limit 1 --json databaseId,status,conclusion",
            timeout=60,
        )
        run = _latest_run(runs)
        if run:
            await _sh(
                sb,
                f"gh run watch {run['databaseId']} --exit-status >/dev/null 2>&1; true",
                timeout=600,
            )
            final = _latest_run(
                await _sh(
                    sb, f"gh run list --branch {branch} --limit 1 --json conclusion", timeout=60
                )
            )
            if final and final.get("conclusion") == "success":
                log.append(f"iter {i}: CI GREEN")
                return CIResult(True, pr_url, i + 1, log)
            last_fail = await _sh(
                sb, f"gh run view {run['databaseId']} --log-failed 2>&1 | tail -40", timeout=120
            )
            log.append(f"iter {i}: CI failed")
        else:
            last_fail = "no CI run found for the branch"
            log.append(f"iter {i}: no CI run")
    return CIResult(False, pr_url, max_iters, log)
