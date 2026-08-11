"""Dangerous tools (terminal / code_exec / file_write) — sandbox-only.

These tools execute ONLY when an active sandbox is set via sandbox_context.
If no sandbox is active they return ToolResult(ok=False, error="no sandbox active").

All three are rank INTERN; the real gate is the per-session sandbox lifecycle
wired in tool_loop.py and the approval engine (M2C-T3/T4).
"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Any

from madras.models.agent_config import Rank
from madras.tools.file_access_context import mark_read, was_read
from madras.tools.process_context import get_active_processes
from madras.tools.registry import ToolResult, tool
from madras.tools.sandbox_context import get_active_sandbox

if TYPE_CHECKING:
    from madras.tools.sandbox import Sandbox

# Bound on background-process output surfaced per poll (chars) — keeps the
# SSE/chat payload small.
_PROC_OUTPUT_MAX = 6000

# Bound on the unified-diff body surfaced to the cockpit (lines). Keeps the
# SSE event + chat-render payload small and secret-free.
_DIFF_MAX_LINES = 400


async def _format_after_write(sb: Sandbox, path: str) -> None:
    """s46: FormatOnWrite (tools/format_on_write.py, row 77) had no live caller -- best-effort,
    never blocks/fails the write itself. Runs in the SAME sandbox the write happened in."""
    import shlex

    from madras.tools.format_on_write import FormatOnWrite

    async def _run(argv: list[str]) -> tuple[bool, str]:
        res = await sb.run_command(shlex.join(argv))
        return res.ok, (res.stdout or "") + (res.stderr or res.error or "")

    try:
        await FormatOnWrite(run=_run).format(path)
    except Exception:
        pass


def _unified_diff(path: str, old: str, new: str) -> tuple[str, int, int]:
    """Compute a bounded unified diff of old→new content for `path`.

    Returns (diff_text, added_lines, removed_lines). The diff body is capped at
    ~_DIFF_MAX_LINES lines (with a truncation marker). Counts reflect the full
    change, not the truncated view. Never raises.
    """
    try:
        old_lines = old.splitlines(keepends=True)
        new_lines = new.splitlines(keepends=True)
        added = 0
        removed = 0
        body: list[str] = []
        for line in difflib.unified_diff(old_lines, new_lines, fromfile=path, tofile=path, n=3):
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
            body.append(line if line.endswith("\n") else line + "\n")
        if len(body) > _DIFF_MAX_LINES:
            body = [*body[:_DIFF_MAX_LINES], f"… (diff truncated at {_DIFF_MAX_LINES} lines)\n"]
        return "".join(body), added, removed
    except Exception:
        return "", 0, 0


@tool(
    name="terminal",
    toolset="shell",
    rank_required=Rank.INTERN,
    description="Run a shell command inside the sandboxed workspace.",
    parameters={
        "type": "object",
        "properties": {
            "cmd": {"type": "string", "description": "Shell command to run"},
        },
        "required": ["cmd"],
    },
)
async def terminal(args: dict[str, Any]) -> ToolResult:
    sb = get_active_sandbox()
    if sb is None:
        return ToolResult(ok=False, error="no sandbox active")
    res = await sb.run_command(str(args.get("cmd", "")))
    if res.error:
        return ToolResult(ok=False, error=res.error)
    out = res.stdout + (("\n[stderr] " + res.stderr) if res.stderr.strip() else "")
    return ToolResult(ok=res.ok, content=out, extras={"exit_code": res.exit_code})


@tool(
    name="code_exec",
    toolset="code",
    rank_required=Rank.INTERN,
    description="Execute a Python code snippet inside the sandboxed workspace.",
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source code to execute"},
        },
        "required": ["code"],
    },
)
async def code_exec(args: dict[str, Any]) -> ToolResult:
    sb = get_active_sandbox()
    if sb is None:
        return ToolResult(ok=False, error="no sandbox active")
    code = str(args.get("code", ""))
    write_res = await sb.write_file("__madras_exec.py", code)
    if not write_res.ok:
        return ToolResult(ok=False, error=write_res.error or "failed to write code file")
    res = await sb.run_command("python __madras_exec.py")
    if res.error:
        return ToolResult(ok=False, error=res.error)
    out = res.stdout + (("\n[stderr] " + res.stderr) if res.stderr.strip() else "")
    return ToolResult(ok=res.ok, content=out, extras={"exit_code": res.exit_code})


@tool(
    name="run_tests",
    toolset="code",
    rank_required=Rank.INTERN,
    description=(
        "Run the test suite inside the sandboxed workspace and report STRUCTURED "
        "pass/fail: per-failure node ids + messages and pass/fail/error counts. "
        "Auto-detects the runner (pytest/jest/go/cargo) when no cmd is given. Set "
        "only_failed=true to re-run just the previously failed tests (fast fix loop). "
        "Call this after changing code to verify it, then fix the listed failures."
    ),
    parameters={
        "type": "object",
        "properties": {
            "cmd": {
                "type": "string",
                "description": "Test command (default: auto-detected runner)",
            },
            "only_failed": {
                "type": "boolean",
                "description": "pytest only: re-run just the last run's failures (--lf)",
            },
        },
    },
)
async def run_tests(args: dict[str, Any]) -> ToolResult:
    from madras.testing.detect import detect_runner
    from madras.testing.report import parse_report

    sb = get_active_sandbox()
    if sb is None:
        return ToolResult(ok=False, error="no sandbox active")

    cmd = str(args.get("cmd") or "").strip()
    runner = "pytest"
    if cmd:
        runner = (
            "pytest"
            if "pytest" in cmd
            else (
                "jest"
                if "jest" in cmd
                else ("go" if cmd.startswith("go ") else ("cargo" if "cargo" in cmd else "pytest"))
            )
        )
    else:
        try:
            listing = await sb.run_command("ls -a 2>/dev/null || dir /b", timeout=20)
            files = (listing.stdout or "").splitlines()
        except Exception:
            files = []
        runner, cmd = detect_runner(files)

    if args.get("only_failed") and runner == "pytest":
        cmd = cmd + " --lf"

    res = await sb.run_command(cmd, timeout=300)
    if res.error:
        return ToolResult(
            ok=False,
            error=res.error,
            extras={
                "exit_code": res.exit_code,
                "tests_passed": False,
                "ran_tests": True,
                "runner": runner,
            },
        )
    combined = res.stdout + (("\n[stderr] " + res.stderr) if res.stderr.strip() else "")
    report = parse_report(combined, res.exit_code, runner=runner)
    body = report.summary()
    if report.failures:
        body += "\n\nFailures:\n" + "\n".join(
            f"  - {f.nodeid}" + (f" — {f.message}" if f.message else "")
            for f in report.failures[:25]
        )
    body += "\n\n" + report.raw_tail
    return ToolResult(
        ok=report.passed,
        content=body,
        extras={
            "exit_code": res.exit_code,
            "tests_passed": report.passed,
            "ran_tests": True,
            "runner": runner,
            "n_passed": report.n_passed,
            "n_failed": report.n_failed,
            "n_errors": report.n_errors,
            "failed_nodeids": report.failed_nodeids(),
        },
    )


@tool(
    name="file_write",
    toolset="file_write",
    rank_required=Rank.INTERN,
    description=(
        "Write text content to a file inside the sandboxed workspace. "
        "Path is relative to the workspace root."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to workspace root"},
            "content": {"type": "string", "description": "Text content to write"},
        },
        "required": ["path", "content"],
    },
)
async def file_write(args: dict[str, Any]) -> ToolResult:
    sb = get_active_sandbox()
    if sb is None:
        return ToolResult(ok=False, error="no sandbox active")
    path = str(args.get("path", ""))
    content = str(args.get("content", ""))
    # Read-before-write guard: if the file already exists and was never read,
    # refuse to blind-overwrite it. Creating a NEW file needs no prior read.
    existing = await sb.read_file(path)
    if existing.ok and not was_read(path):
        return ToolResult(
            ok=False,
            error=(
                f"[READ-FIRST] read '{path}' with file_read before overwriting it, "
                "so you don't lose its current content."
            ),
        )
    res = await sb.write_file(path, content)
    if not res.ok:
        return ToolResult(ok=False, error=res.error or "write failed")
    mark_read(path)
    await _format_after_write(sb, path)
    # Diff capture (additive — after a confirmed success). For a brand-new file
    # this is an all-added diff; for an overwrite it's old→new.
    new_file = not existing.ok
    prior = "" if new_file else existing.stdout
    diff_text, added, removed = _unified_diff(path, prior, content)
    return ToolResult(
        ok=True,
        content=res.stdout,
        extras={
            "diff": diff_text,
            "path": path,
            "added": added,
            "removed": removed,
            "new_file": new_file,
        },
    )


@tool(
    name="file_edit",
    toolset="file_write",
    rank_required=Rank.INTERN,
    description=(
        "Surgically edit a file by replacing an exact 'old' string with 'new'. The 'old' "
        "text must appear exactly once in the file. Read the file with file_read first."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to workspace root"},
            "old": {"type": "string", "description": "Exact text to replace (must be unique)"},
            "new": {"type": "string", "description": "Replacement text"},
        },
        "required": ["path", "old", "new"],
    },
)
async def file_edit(args: dict[str, Any]) -> ToolResult:
    sb = get_active_sandbox()
    if sb is None:
        return ToolResult(ok=False, error="no sandbox active")
    path = str(args.get("path", ""))
    old = str(args.get("old", ""))
    new = str(args.get("new", ""))
    if not was_read(path):
        return ToolResult(
            ok=False,
            error=(
                f"[READ-FIRST] read '{path}' with file_read before editing it, "
                "so you edit against current content."
            ),
        )
    rr = await sb.read_file(path)
    if not rr.ok:
        return ToolResult(ok=False, error=f"cannot read {path}: {rr.error or rr.stderr}")
    content = rr.stdout
    count = content.count(old)
    if count == 0:
        return ToolResult(
            ok=False,
            error=(
                f"[NO-MATCH] the exact 'old' text was not found in {path}; "
                "re-read it and copy the exact text to replace."
            ),
        )
    if count > 1:
        return ToolResult(
            ok=False,
            error=(
                f"[NOT-UNIQUE] 'old' appears {count} times in {path}; "
                "include more surrounding context so it matches exactly once."
            ),
        )
    new_content = content.replace(old, new, 1)
    wr = await sb.write_file(path, new_content)
    if not wr.ok:
        return ToolResult(ok=False, error=wr.error or "write failed")
    mark_read(path)
    await _format_after_write(sb, path)
    # Diff capture (additive — after the confirmed unique replace).
    diff_text, added, removed = _unified_diff(path, content, new_content)
    return ToolResult(
        ok=True,
        content=f"edited {path} (1 replacement)",
        extras={
            "diff": diff_text,
            "path": path,
            "added": added,
            "removed": removed,
            "new_file": False,
        },
    )


@tool(
    name="apply_patch",
    toolset="file_write",
    rank_required=Rank.INTERN,
    description=(
        "Apply a multi-file patch ATOMICALLY (all-or-nothing). Use to add, update, "
        "delete, or rename several files in one coherent change. Format:\n"
        "*** Begin Patch\n"
        "*** Add File: path/to/new.py\n"
        "+content line\n"
        "*** Update File: path/to/existing.py\n"
        "@@\n"
        " context line\n"
        "-removed line\n"
        "+added line\n"
        "*** Delete File: path/to/old.py\n"
        "*** End Patch\n"
        "Update/Delete require reading the file first. Hunks match by surrounding "
        "context, not line numbers."
    ),
    parameters={
        "type": "object",
        "properties": {
            "patch": {"type": "string", "description": "The patch envelope (see format above)."},
        },
        "required": ["patch"],
    },
)
async def apply_patch(args: dict[str, Any]) -> ToolResult:
    sb = get_active_sandbox()
    if sb is None:
        return ToolResult(ok=False, error="no sandbox active")
    from madras.tools.patch import PatchError, apply_hunks, parse_patch

    raw = str(args.get("patch", ""))
    if not raw.strip():
        return ToolResult(ok=False, error="patch is required")
    try:
        ops = parse_patch(raw)
    except PatchError as exc:
        return ToolResult(ok=False, error=f"[PATCH-PARSE] {exc}")

    # ---- Validation / dry-run: compute every change against current content
    # BEFORE touching disk. Any failure aborts with nothing written. ----
    writes: list[tuple[str, str, str | None]] = []  # (path, new_content, old_or_None)
    deletes: list[tuple[str, str]] = []  # (path, old_content) — for rollback
    diffs: list[dict[str, Any]] = []
    for op in ops:
        if op.action == "add":
            existing = await sb.read_file(op.path)
            if existing.ok:
                return ToolResult(
                    ok=False, error=f"[EXISTS] {op.path} already exists; use Update File"
                )
            content = op.new_content or ""
            writes.append((op.path, content, None))
            dtext, added, removed = _unified_diff(op.path, "", content)
            diffs.append(
                {
                    "path": op.path,
                    "diff": dtext,
                    "added": added,
                    "removed": removed,
                    "new_file": True,
                }
            )
        elif op.action == "update":
            rr = await sb.read_file(op.path)
            if not rr.ok:
                return ToolResult(ok=False, error=f"[NO-FILE] cannot update missing file {op.path}")
            if not was_read(op.path):
                return ToolResult(
                    ok=False,
                    error=f"[READ-FIRST] read '{op.path}' with file_read before patching it.",
                )
            try:
                new_content = apply_hunks(rr.stdout, op.hunks)
            except PatchError as exc:
                return ToolResult(ok=False, error=f"{exc} (file {op.path})")
            dtext, added, removed = _unified_diff(op.path, rr.stdout, new_content)
            if op.move_to:
                # rename: write new path, remove old.
                moved = await sb.read_file(op.move_to)
                writes.append((op.move_to, new_content, moved.stdout if moved.ok else None))
                deletes.append((op.path, rr.stdout))
                diffs.append(
                    {
                        "path": op.move_to,
                        "diff": dtext,
                        "added": added,
                        "removed": removed,
                        "new_file": not moved.ok,
                    }
                )
            else:
                writes.append((op.path, new_content, rr.stdout))
                diffs.append(
                    {
                        "path": op.path,
                        "diff": dtext,
                        "added": added,
                        "removed": removed,
                        "new_file": False,
                    }
                )
        elif op.action == "delete":
            rr = await sb.read_file(op.path)
            if not rr.ok:
                return ToolResult(ok=False, error=f"[NO-FILE] cannot delete missing file {op.path}")
            if not was_read(op.path):
                return ToolResult(
                    ok=False,
                    error=f"[READ-FIRST] read '{op.path}' with file_read before deleting it.",
                )
            deletes.append((op.path, rr.stdout))

    # ---- Apply with rollback. Snapshot is captured above (old_content). ----
    done_writes: list[tuple[str, str | None]] = []  # (path, old_or_None) applied so far
    done_deletes: list[tuple[str, str]] = []  # (path, old_content) applied so far

    async def _rollback() -> None:
        for path, old in reversed(done_writes):
            if old is None:
                await sb.delete_file(path)  # was newly created → remove
            else:
                await sb.write_file(path, old)  # restore prior content
        for path, old in reversed(done_deletes):
            await sb.write_file(path, old)  # recreate deleted file

    try:
        for path, content, old in writes:
            wr = await sb.write_file(path, content)
            if not wr.ok:
                raise RuntimeError(wr.error or f"write failed: {path}")
            done_writes.append((path, old))
        for path, old in deletes:
            dr = await sb.delete_file(path)
            if not dr.ok:
                raise RuntimeError(dr.error or f"delete failed: {path}")
            done_deletes.append((path, old))
    except Exception as exc:
        await _rollback()
        return ToolResult(
            ok=False, error=f"[APPLY-FAILED, rolled back] {type(exc).__name__}: {exc}"
        )

    for path, _content, _old in writes:
        mark_read(path)
        await _format_after_write(sb, path)
    n_add = sum(1 for d in diffs if d.get("new_file"))
    n_update = len(writes) - n_add
    summary = f"applied patch: {n_add} added, {n_update} updated, {len(deletes)} deleted"
    return ToolResult(
        ok=True,
        content=summary,
        extras={
            "diffs": diffs,
            "added_files": n_add,
            "updated_files": n_update,
            "deleted_files": len(deletes),
        },
    )


# ---------------------------------------------------------------------------
# Background-process management (shell-execution capability) — shell_start /
# shell_output / shell_kill. For LONG-RUNNING commands (dev servers, watchers,
# builds) where `terminal` would block until timeout. Governed like terminal:
# rank-gate + 8-dim eval + immutable audit on every call; gated on an active
# process registry (set by the cockpit loop when the shell toolset is in play).
# ---------------------------------------------------------------------------


@tool(
    name="shell_start",
    toolset="shell",
    rank_required=Rank.INTERN,
    description=(
        "Start a LONG-RUNNING shell command in the background (e.g. a dev server, "
        "watcher, or build) and return a proc_id immediately without blocking. Use "
        "shell_output to read its output and shell_kill to stop it. For one-shot "
        "commands that finish quickly, use `terminal` instead."
    ),
    parameters={
        "type": "object",
        "properties": {
            "cmd": {"type": "string", "description": "Shell command to run in the background"},
        },
        "required": ["cmd"],
    },
)
async def shell_start(args: dict[str, Any]) -> ToolResult:
    reg = get_active_processes()
    if reg is None:
        return ToolResult(ok=False, error="no shell session active")
    cmd = str(args.get("cmd", "")).strip()
    if not cmd:
        return ToolResult(ok=False, error="cmd is required")
    try:
        proc_id = reg.start(cmd)
    except (ValueError, RuntimeError) as exc:
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(
        ok=True,
        content=f"started background process {proc_id}: {cmd}",
        extras={"proc_id": proc_id, "cmd": cmd},
    )


@tool(
    name="shell_output",
    toolset="shell",
    rank_required=Rank.INTERN,
    description=(
        "Read NEW output (since the last read) from a background process started with "
        "shell_start, plus whether it is still running and its exit code if finished."
    ),
    parameters={
        "type": "object",
        "properties": {
            "proc_id": {"type": "string", "description": "Process id from shell_start"},
        },
        "required": ["proc_id"],
    },
)
async def shell_output(args: dict[str, Any]) -> ToolResult:
    reg = get_active_processes()
    if reg is None:
        return ToolResult(ok=False, error="no shell session active")
    proc_id = str(args.get("proc_id", "")).strip()
    if not proc_id:
        return ToolResult(ok=False, error="proc_id is required")
    st = reg.read(proc_id)
    if not st.found:
        return ToolResult(ok=False, error=f"unknown proc_id: {proc_id}")
    out = st.new_output[-_PROC_OUTPUT_MAX:]
    status = "running" if st.running else f"exited (code {st.exit_code})"
    return ToolResult(
        ok=True,
        content=out or f"(no new output; {status})",
        extras={
            "proc_id": proc_id,
            "running": st.running,
            "exit_code": st.exit_code,
            "truncated": len(st.new_output) > _PROC_OUTPUT_MAX,
        },
    )


@tool(
    name="shell_kill",
    toolset="shell",
    rank_required=Rank.INTERN,
    description="Terminate a background process started with shell_start.",
    parameters={
        "type": "object",
        "properties": {
            "proc_id": {"type": "string", "description": "Process id from shell_start"},
        },
        "required": ["proc_id"],
    },
)
async def shell_kill(args: dict[str, Any]) -> ToolResult:
    reg = get_active_processes()
    if reg is None:
        return ToolResult(ok=False, error="no shell session active")
    proc_id = str(args.get("proc_id", "")).strip()
    if not proc_id:
        return ToolResult(ok=False, error="proc_id is required")
    if not reg.kill(proc_id):
        return ToolResult(ok=False, error=f"unknown proc_id: {proc_id}")
    return ToolResult(ok=True, content=f"killed {proc_id}", extras={"proc_id": proc_id})
