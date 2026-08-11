"""background_dispatch / background_check — cloud-async-execution's dispatch-and-return UX.

Distinct from shell_start/shell_output (ProcessRegistry): those track a LOCAL host
process, in-memory, torn down when the run ends. These two dispatch to the REMOTE
E2B sandbox and persist the reconnect handle (sandbox_id/pid/job_id) to Postgres, so
a later session (or a cockpit restart) can still check on it — the actual "cloud/
remote async execution" gap identified in shadow-rebuild D1.11's follow-up.
"""

from __future__ import annotations

import time
from typing import Any

from madras.models.agent_config import Rank
from madras.tools.background_job_context import get_background_job_ctx
from madras.tools.registry import ToolResult, tool
from madras.tools.sandbox_context import get_active_sandbox


@tool(
    name="background_dispatch",
    toolset="shell",
    rank_required=Rank.INTERN,
    description=(
        "Dispatch a long-running shell command to the remote sandbox WITHOUT "
        "waiting for it to finish. Returns a job_id immediately; use "
        "background_check(job_id) later (even in a different session) to see "
        "if it's done and get its output. Only works with the e2b sandbox "
        "backend — use `terminal` instead for anything that finishes quickly."
    ),
    parameters={
        "type": "object",
        "properties": {"cmd": {"type": "string", "description": "the shell command to dispatch"}},
        "required": ["cmd"],
    },
)
async def background_dispatch(args: dict[str, Any]) -> ToolResult:
    sb = get_active_sandbox()
    if sb is None:
        return ToolResult(ok=False, error="no sandbox active")
    ctx = get_background_job_ctx()
    if ctx is None:
        return ToolResult(ok=False, error="background job store not available in this context")
    cmd = str(args.get("cmd", "")).strip()
    if not cmd:
        return ToolResult(ok=False, error="cmd is required")

    try:
        handle = await sb.start_background(cmd)
    except NotImplementedError as exc:
        return ToolResult(ok=False, error=str(exc))

    from madras.tasks.background_jobs import BackgroundJob

    now = time.time()
    job = BackgroundJob(
        job_id=handle.job_id,
        agent_name=ctx.agent_name,
        session_id=ctx.session_id,
        sandbox_id=handle.sandbox_id,
        pid=handle.pid,
        cmd=cmd,
        status="running",
        exit_code=None,
        stdout=None,
        stderr=None,
        created_at=now,
        updated_at=now,
    )
    await ctx.store.save(job)
    return ToolResult(
        ok=True,
        content=f"dispatched job {handle.job_id} (running in background)",
        extras={"job_id": handle.job_id},
    )


@tool(
    name="background_check",
    toolset="shell",
    rank_required=Rank.INTERN,
    description=(
        "Check on a command previously dispatched with background_dispatch. "
        "Reconnects to the remote sandbox by job_id — works even from a "
        "different session than the one that dispatched it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "the job_id from background_dispatch"}
        },
        "required": ["job_id"],
    },
)
async def background_check(args: dict[str, Any]) -> ToolResult:
    ctx = get_background_job_ctx()
    if ctx is None:
        return ToolResult(ok=False, error="background job store not available in this context")
    job_id = str(args.get("job_id", "")).strip()
    if not job_id:
        return ToolResult(ok=False, error="job_id is required")

    job = await ctx.store.get(job_id)
    if job is None:
        return ToolResult(ok=False, error=f"no such job: {job_id}")

    if job.status != "running":
        # Already resolved on a prior check — return the cached terminal result.
        return ToolResult(
            ok=True,
            content=f"job {job_id}: {job.status} (exit {job.exit_code})",
            extras={
                "status": job.status,
                "exit_code": job.exit_code,
                "stdout": job.stdout,
                "stderr": job.stderr,
            },
        )

    sb = get_active_sandbox()
    if sb is None:
        return ToolResult(ok=False, error="no sandbox active")
    status = await sb.check_background(job.sandbox_id, job.pid, job.job_id)
    if status.error:
        return ToolResult(ok=False, error=status.error)
    if status.running:
        return ToolResult(
            ok=True, content=f"job {job_id}: still running", extras={"status": "running"}
        )

    job.status = "done" if status.ok else "error"
    job.exit_code = status.exit_code
    job.stdout = status.stdout
    job.stderr = status.stderr
    job.updated_at = time.time()
    await ctx.store.save(job)
    return ToolResult(
        ok=True,
        content=f"job {job_id}: {job.status} (exit {status.exit_code})\n{status.stdout}",
        extras={
            "status": job.status,
            "exit_code": status.exit_code,
            "stdout": status.stdout,
            "stderr": status.stderr,
        },
    )
