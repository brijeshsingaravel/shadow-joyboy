from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncpg

_MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "infra" / "migrations"
_MIGRATIONS = (
    _MIGRATIONS_DIR / "0005_proving_ground.sql",
    _MIGRATIONS_DIR / "0006_pg_suggestions.sql",
)
_SPINE = Path(__file__).resolve().parent / "scorecard.jsonl"


class MissingProvingGroundSchema(RuntimeError):
    """The proving-ground v1 tables do not exist -- migrations have not been applied."""


class ProvingGroundStore:
    def __init__(self, *, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None
        self._tables = ("pg_runs", "pg_scenario_results")
        self._verified = False

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn)
        return self._pool

    async def setup(self) -> None:
        """Verify the schema exists. Does NOT apply migrations (s61, D83 step 5).

        The v1 store carried the same pattern as `store_v2.py`: `setup()` read the migration files
        and executed them. Both were invisible to `tests/test_infra/test_no_runtime_ddl.py`, which
        inspects string LITERALS -- DDL read from a file at runtime never appears as one. This one
        was missed in the first pass and found by step 6 instead, when the app dropped to the
        DDL-less role and the v1 tests failed with `permission denied for schema public`.

        That is the detector's limitation doing its job the slow way: the gap was documented, and
        the cutover surfaced what the check could not.
        """
        if self._verified:
            return
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            missing = [
                t
                for t in self._tables
                if not await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", t)
            ]
        if missing:
            raise MissingProvingGroundSchema(
                f"{', '.join(missing)} do(es) not exist -- run "
                f"`uv run python scripts/apply_migrations.py`"
            )
        self._verified = True

    async def write_run(self, run: dict[str, Any], scenarios: list[dict[str, Any]]) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO madras_pg_runs (run_id,head_sha,agent_model,judge_set,bank_version,
                   overall_score,pass_k,per_feature,per_benchmark,n_scenarios,deltas,suggestions)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                   ON CONFLICT (run_id) DO NOTHING""",
                run["run_id"],
                run.get("head_sha"),
                run.get("agent_model"),
                json.dumps(run.get("judge_set", [])),
                run.get("bank_version"),
                run["overall_score"],
                run["pass_k"],
                json.dumps(run.get("per_feature", {})),
                json.dumps(run.get("per_benchmark", {})),
                run["n_scenarios"],
                json.dumps(run.get("deltas", {})),
                json.dumps(run.get("suggestions", [])),
            )
            for s in scenarios:
                await conn.execute(
                    """INSERT INTO madras_pg_scenario_results (run_id,scenario_id,benchmark_family,
                       features,k,passes,pass_rate,det_pass,judge_pass,deterministic,judge_votes,trajectory)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                       ON CONFLICT (run_id,scenario_id) DO NOTHING""",
                    run["run_id"],
                    s["scenario_id"],
                    s.get("benchmark_family"),
                    json.dumps(s.get("features", [])),
                    s["k"],
                    s["passes"],
                    s["pass_rate"],
                    s["det_pass"],
                    s["judge_pass"],
                    json.dumps(s.get("deterministic", [])),
                    json.dumps(s.get("judge_votes", [])),
                    json.dumps(s.get("trajectory", [])),
                )
        self._append_spine(run)

    def _append_spine(self, run: dict[str, Any]) -> None:
        line = {
            "run_id": run["run_id"],
            "overall": run["overall_score"],
            "pass_k": run["pass_k"],
            "per_feature": run.get("per_feature", {}),
            "per_benchmark": run.get("per_benchmark", {}),
            "deltas": run.get("deltas", {}),
        }
        with _SPINE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")

    async def write_backlog(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            for it in items:
                await conn.execute(
                    """INSERT INTO madras_pg_backlog
                       (severity,pattern,evidence_run_ids,root_cause,suggested_fix,
                        track,scope_flag,status)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,'open')""",
                    it.get("severity"),
                    it.get("pattern"),
                    json.dumps(it.get("evidence_run_ids", [])),
                    it.get("root_cause"),
                    it.get("suggested_fix"),
                    it.get("track"),
                    it.get("scope_flag"),
                )

    async def open_backlog(self) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM madras_pg_backlog WHERE status='open' ORDER BY id DESC"
            )
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["evidence_run_ids"] = json.loads(d["evidence_run_ids"])
            out.append(d)
        return out

    async def runs_for_analysis(self, *, limit: int = 20) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT run_id, ts, per_feature FROM madras_pg_runs ORDER BY ts DESC LIMIT $1",
                limit,
            )
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            pf = d["per_feature"]
            d["per_feature"] = json.loads(pf) if isinstance(pf, str) else pf
            out.append(d)
        return out

    async def latest_run(self) -> dict[str, Any] | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM madras_pg_runs ORDER BY ts DESC LIMIT 1")
        return dict(row) if row is not None else None

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            run_row = await conn.fetchrow("SELECT * FROM madras_pg_runs WHERE run_id=$1", run_id)
            if run_row is None:
                return None
            scen_rows = await conn.fetch(
                "SELECT * FROM madras_pg_scenario_results WHERE run_id=$1 ORDER BY scenario_id",
                run_id,
            )
        _json_cols = ("features", "deterministic", "judge_votes", "trajectory")
        scenarios: list[dict[str, Any]] = []
        for r in scen_rows:
            d = dict(r)
            for col in _json_cols:
                v = d.get(col)
                if isinstance(v, str):
                    d[col] = json.loads(v)
            scenarios.append(d)
        return {"run": dict(run_row), "scenarios": scenarios}

    async def recent_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM madras_pg_runs ORDER BY ts DESC LIMIT $1", limit)
        return [dict(r) for r in rows]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
