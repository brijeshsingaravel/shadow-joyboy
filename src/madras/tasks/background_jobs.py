"""Durable store for dispatched background sandbox jobs (cloud-async-execution's
dispatch-and-return UX). Distinct from ProcessRegistry (tools/process_context.py):
that one is local, in-memory, torn down when the run ends; this one persists to
Postgres so a job survives the dispatching session ending — a later session (or a
cockpit restart) can still reconnect via the stored sandbox_id/pid/job_id.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

_UPSERT = """
INSERT INTO madras_background_jobs
  (job_id, agent_name, session_id, sandbox_id, pid, cmd, status, exit_code, stdout, stderr,
   created_at, updated_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
ON CONFLICT (job_id) DO UPDATE SET
  status=EXCLUDED.status, exit_code=EXCLUDED.exit_code, stdout=EXCLUDED.stdout,
  stderr=EXCLUDED.stderr, updated_at=EXCLUDED.updated_at
"""
_GET = "SELECT * FROM madras_background_jobs WHERE job_id=$1 AND agent_name=$2"
_LIST = "SELECT * FROM madras_background_jobs WHERE agent_name=$1 ORDER BY created_at DESC LIMIT $2"


@dataclass
class BackgroundJob:
    job_id: str
    agent_name: str
    session_id: str
    sandbox_id: str
    pid: int
    cmd: str
    status: str
    exit_code: int | None
    stdout: str | None
    stderr: str | None
    created_at: float
    updated_at: float


def _row_to_job(r: asyncpg.Record) -> BackgroundJob:
    return BackgroundJob(
        job_id=r["job_id"],
        agent_name=r["agent_name"],
        session_id=r["session_id"],
        sandbox_id=r["sandbox_id"],
        pid=int(r["pid"]),
        cmd=r["cmd"],
        status=r["status"],
        exit_code=(int(r["exit_code"]) if r["exit_code"] is not None else None),
        stdout=r["stdout"],
        stderr=r["stderr"],
        created_at=float(r["created_at"]),
        updated_at=float(r["updated_at"]),
    )


class BackgroundJobStore:
    def __init__(self, *, postgres_url: str, agent_name: str = "shadow") -> None:
        self._url = postgres_url
        self._agent = agent_name
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=4)
        return self._pool

    async def save(self, job: BackgroundJob) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                _UPSERT,
                job.job_id,
                job.agent_name,
                job.session_id,
                job.sandbox_id,
                job.pid,
                job.cmd,
                job.status,
                job.exit_code,
                job.stdout,
                job.stderr,
                job.created_at,
                job.updated_at,
            )

    async def get(self, job_id: str) -> BackgroundJob | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_GET, job_id, self._agent)
        return _row_to_job(row) if row is not None else None

    async def list(self, limit: int = 20) -> list[BackgroundJob]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_LIST, self._agent, limit)
        return [_row_to_job(r) for r in rows]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
