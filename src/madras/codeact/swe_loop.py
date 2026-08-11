"""SWE loop — the repo-in-sandbox iterate-until-green coding agent (W2 · shadow-rebuild).

Given a task (a repo at a base commit + a problem statement + a command that runs the target
tests), this mounts the repo into a ``Sandbox`` (E2B micro-VM in production), builds a lightweight
repo-map so the model can navigate before editing, then loops: the model proposes a unified diff,
we apply it, run the tests, and feed the failure back until the tests pass or the iteration budget
is exhausted. The final ``git diff`` is the patch (the SWE-bench prediction).

It upgrades the swebench suite's blind "Stage A" (a single LLM call with no checkout / no test
feedback) into a real agentic loop, and is substrate-agnostic via the ``Sandbox`` ABC
(``tools/sandbox.py``) — Local/Docker for dev, E2B for scale.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from madras.llm.gateway import LLMGateway, LLMRequest
from madras.tools.sandbox import CommandResult, Sandbox

_REPO = "repo"  # the repo dir inside the sandbox workspace

# The edit format is SEARCH/REPLACE blocks (Aider-style), NOT unified diffs: strong models emit
# malformed hunk headers + prose when asked for raw diffs, but copy exact lines reliably. Each block
# is applied by exact string replacement on the real file content — no hunk math, no `git apply`.
_SYSTEM = (
    "You are an expert software engineer fixing a bug in a Python repository. You are given the "
    "issue, the FULL content of the relevant files, the acceptance tests your fix must pass, and "
    "(after the first try) the failing test output.\n\n"
    "First, in 2-4 sentences, diagnose the ROOT CAUSE (trace which function the failing tests "
    "exercise and why it's wrong). Then output one or more EDIT BLOCKS in EXACTLY this format:\n\n"
    "FILE: path/to/file.py\n"
    "<<<<<<< SEARCH\n"
    "<lines copied EXACTLY from the shown file content>\n"
    "=======\n"
    "<the replacement lines>\n"
    ">>>>>>> REPLACE\n\n"
    "Rules: the SEARCH text must match the shown file content character-for-character (indentation "
    "included) and be small + unique. If a function/method/class you need already EXISTS in the "
    "shown content, MODIFY it in place — never add a duplicate definition. Edit source files, "
    "never the tests. No code fences around the blocks."
)

_EDIT_RE = re.compile(
    r"FILE:\s*(?P<path>\S+)\s*?\n<<<<<<< SEARCH\n(?P<search>.*?)\n=======\n"
    r"(?P<replace>.*?)\n>>>>>>> REPLACE",
    re.DOTALL,
)


@dataclass
class SweTask:
    """One repo-in-sandbox coding task."""

    repo_url: str  # git URL or local path clonable inside the sandbox
    base_commit: str
    problem: str
    test_cmd: str  # command run from the repo root; exit 0 == the target tests pass
    setup_cmd: str = ""  # optional install/build step (e.g. "pip install -e .")
    test_patch: str = ""  # optional gold test diff; applied + committed at mount (SWE-bench oracle)
    max_iters: int = 4


@dataclass
class SweResult:
    resolved: bool
    patch: str  # the final unified diff (empty if nothing applied)
    iterations: int
    log: list[str] = field(default_factory=list[str])


def _extract_diff(text: str) -> str:  # pyright: ignore[reportUnusedFunction]
    """Pull a clean unified diff out of a model response (strip fences/prose).

    Only exercised directly by tests/test_codeact/test_swe_loop.py (no in-src caller) --
    confirmed via repo-wide grep, same false-positive class as other test-only helpers
    flagged this session."""
    if not text:
        return ""
    body = text.strip()
    if "```" in body:
        parts = body.split("```")
        if len(parts) >= 3:
            block = parts[1]
            if "\n" in block:
                first, rest = block.split("\n", 1)
                if first.strip().lower() in {"diff", "patch", "git", ""}:
                    block = rest
            body = block.strip()
    for marker in ("diff --git", "--- "):
        idx = body.find(marker)
        if idx != -1:
            body = body[idx:]
            break
    else:
        return ""
    return body if body.endswith("\n") else body + "\n"


async def mount_repo(sandbox: Sandbox, task: SweTask) -> CommandResult:
    """Clone the repo + checkout the base commit inside the sandbox (+ optional setup)."""
    clone = await sandbox.run_command(
        f"rm -rf {_REPO} && git clone {task.repo_url} {_REPO}", timeout=600
    )
    if not clone.ok:
        return clone
    co = await sandbox.run_command(f"cd {_REPO} && git checkout -q {task.base_commit}", timeout=120)
    if not co.ok:
        return co
    if task.test_patch:
        # apply the gold test diff + COMMIT it, so the loop's per-iteration source reset
        # (`git checkout -- .`) never reverts the oracle tests, and the final diff stays the
        # agent's source patch only.
        await sandbox.write_file(f"{_REPO}/__test.patch", task.test_patch)
        applied = await sandbox.run_command(
            f"cd {_REPO} && git apply __test.patch && rm __test.patch && "
            "git -c user.email=t@t.co -c user.name=t commit -qam oracle-tests"
        )
        if not applied.ok:
            return CommandResult(ok=False, error=f"test_patch apply failed: {applied.stderr}")
    if task.setup_cmd:
        return await sandbox.run_command(f"cd {_REPO} && {task.setup_cmd}", timeout=900)
    return co


def _format_repo_map(files: str, symbols: str, *, max_files: int = 200, max_syms: int = 400) -> str:
    """Combine the tracked-file tree + a class/def symbol index into the navigation aid. Pure (text
    in → text out) so it's unit-testable without a sandbox."""
    flines = [ln for ln in files.splitlines() if ln.strip()][:max_files]
    slines = [ln for ln in symbols.splitlines() if ln.strip()][:max_syms]
    out = "FILES:\n" + "\n".join(flines)
    if slines:
        out += "\n\nSYMBOLS (file:line: definition):\n" + "\n".join(slines)
    return out


async def repo_map(sandbox: Sandbox, *, max_files: int = 200, max_syms: int = 400) -> str:
    """A codebase-index navigation aid: the tracked file tree + a class/def symbol map (so the model
    can locate the right definition before editing, not just guess from filenames)."""
    files = await sandbox.run_command(f"cd {_REPO} && git ls-files | head -{max_files}")
    # top-level + method class/def signatures across the Python sources (a lightweight repo-map).
    syms = await sandbox.run_command(
        f"cd {_REPO} && git grep -n -E '^(class |def |[ \\t]+def )' -- '*.py' | head -{max_syms}"
    )
    return _format_repo_map(
        files.stdout if files.ok else "",
        syms.stdout if syms.ok else "",
        max_files=max_files,
        max_syms=max_syms,
    )


async def _complete(
    gateway: LLMGateway, model: str, system: str, user: str, max_tokens: int
) -> str:
    """One model, one call. On a rate-limit (429) we STOP — never retry-storm or hop providers
    (that abuse pattern risks getting the free-tier accounts flagged/banned). The caller treats a
    raised rate-limit as "back off and stop the run", not a signal to hammer harder."""
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return (
        await gateway.complete(
            LLMRequest(model=model, messages=msgs, max_tokens=max_tokens, temperature=0.0)
        )
    ).text or ""


async def pick_files(
    gateway: LLMGateway, model: str, sandbox: Sandbox, task: SweTask, rmap: str
) -> str:
    """Two-phase: the model names the files to edit from the repo-map, we read their real content
    so the next diff has correct context (editing blind is why LLM patches fail to apply)."""
    sys_pick = (
        "You are triaging a bug. Given the issue and the repo file list, reply with ONLY the "
        "repo-relative paths most likely to need editing — one per line, at most 4, no prose."
    )
    raw = await _complete(
        gateway,
        model,
        sys_pick,
        f"ISSUE:\n{task.problem[:4000]}\n\nFILES:\n{rmap[:4000]}",
        max_tokens=200,
    )
    paths = [
        p.strip().strip("`-* ") for p in raw.splitlines() if "/" in p or p.strip().endswith(".py")
    ]
    out: list[str] = []
    # Read up to 5 files, generously (so existing defs the model must edit-in-place are visible —
    # truncating below a symbol is why the model adds duplicate definitions).
    for p in paths[:5]:
        r = await sandbox.read_file(f"{_REPO}/{p}")
        if r.ok:
            out.append(f"### FILE: {p}\n{r.stdout[:18000]}")
    return "\n\n".join(out)


async def propose_edits(
    gateway: LLMGateway, model: str, task: SweTask, rmap: str, last_fail: str, files: str
) -> str:
    user = f"ISSUE:\n{task.problem[:6000]}\n\nREPO MAP:\n{rmap[:2000]}\n"
    if files:
        user += f"\nCURRENT FILE CONTENT (copy SEARCH lines EXACTLY from here):\n{files[:60000]}\n"
    if last_fail:
        user += (
            f"\nYOUR LAST ATTEMPT FAILED — diagnose the root cause from this, then fix it:\n"
            f"{last_fail[:3000]}\n"
        )
    user += "\nDiagnose the root cause, then produce the SEARCH/REPLACE edit block(s) that fix it."
    return await _complete(gateway, model, _SYSTEM, user, max_tokens=3000)


def parse_edits(text: str) -> list[tuple[str, str, str]]:
    """Parse FILE/SEARCH/REPLACE blocks → (path, search, replace) triples."""
    return [(m["path"], m["search"], m["replace"]) for m in _EDIT_RE.finditer(text)]


async def apply_edits(sandbox: Sandbox, edits: list[tuple[str, str, str]]) -> tuple[bool, str]:
    """Apply each edit by exact-string replacement on the real file content. Returns (all_ok,
    error). A SEARCH that isn't found (or isn't unique enough to locate) fails that edit."""
    errs: list[str] = []
    for path, search, replace in edits:
        r = await sandbox.read_file(f"{_REPO}/{path}")
        if not r.ok:
            errs.append(f"{path}: file not found")
            continue
        content = r.stdout
        if search not in content:
            errs.append(f"{path}: SEARCH block did not match the file content")
            continue
        w = await sandbox.write_file(f"{_REPO}/{path}", content.replace(search, replace, 1))
        if not w.ok:
            errs.append(f"{path}: write failed ({w.error})")
    return (not errs, "; ".join(errs))


async def run_swe_loop(
    task: SweTask, *, sandbox: Sandbox, gateway: LLMGateway, model: str
) -> SweResult:
    """Drive the iterate-until-green loop; return the resolving patch (or the best attempt)."""
    log: list[str] = []
    await sandbox.start()
    mounted = await mount_repo(sandbox, task)
    if not mounted.ok:
        return SweResult(False, "", 0, [f"mount failed: {mounted.error or mounted.stderr}"])

    rmap = await repo_map(sandbox)
    files = await pick_files(gateway, model, sandbox, task, rmap)  # read real content once
    log.append(f"context: {files.count('### FILE:')} file(s) read")
    last_fail = ""
    best_patch = ""  # the most recent applied attempt (captured before reset, for the report)
    for i in range(task.max_iters):
        raw = await propose_edits(gateway, model, task, rmap, last_fail, files)
        edits = parse_edits(raw)
        if not edits:
            last_fail = "no edit blocks found — reply with FILE/SEARCH/REPLACE blocks only"
            log.append(f"iter {i}: no edit blocks (resp len {len(raw)})")
            continue
        ok, err = await apply_edits(sandbox, edits)
        if not ok:
            last_fail = f"some edits did not apply: {err}"
            log.append(f"iter {i}: {err[:120]}")
            await sandbox.run_command(f"cd {_REPO} && git checkout -q -- .")  # undo partial edits
            continue
        best_patch = (await sandbox.run_command(f"cd {_REPO} && git diff")).stdout
        tested = await sandbox.run_command(f"cd {_REPO} && {task.test_cmd}", timeout=600)
        if tested.ok:
            log.append(f"iter {i}: RESOLVED")
            return SweResult(True, best_patch, i + 1, log)
        last_fail = (tested.stdout + "\n" + tested.stderr)[-3000:]
        log.append(f"iter {i}: tests still failing")
        await sandbox.run_command(f"cd {_REPO} && git checkout -q -- .")  # reset for next attempt

    return SweResult(False, best_patch, task.max_iters, log)
