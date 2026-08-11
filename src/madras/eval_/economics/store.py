# src/madras/eval_/economics/store.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncpg

_MIGRATION = Path(__file__).resolve().parents[4] / "infra" / "migrations" / "0008_pg_economics.sql"


async def _init_conn(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


class EconomicsStore:
    def __init__(self, *, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, init=_init_conn)
        return self._pool

    async def setup(self) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(_MIGRATION.read_text(encoding="utf-8"))

    async def write_economics(
        self,
        *,
        economics_id: str,
        source_run_id: str | None,
        report: dict[str, Any],
        user_mix: dict[str, Any],
        target_margins: dict[str, Any],
    ) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO pg_economics
                   (economics_id, source_run_id, report, user_mix, target_margins)
                   VALUES ($1,$2,$3,$4,$5)
                   ON CONFLICT (economics_id) DO NOTHING""",
                economics_id,
                source_run_id,
                report,
                user_mix,
                target_margins,
            )

    async def recent_economics(self, *, limit: int = 20) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM pg_economics ORDER BY ts DESC LIMIT $1", limit)
        return [dict(r) for r in rows]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
