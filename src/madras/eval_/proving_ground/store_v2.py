from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncpg

from madras.eval_.proving_ground.agents import DEFAULT_AGENT

_MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "infra" / "migrations"
# Applied in order on setup(): base v2 schema, then the agent dimension, then the
# Dataset Compiler's SFT output (T4.1).
_MIGRATIONS = (
    _MIGRATIONS_DIR / "0007_proving_ground_v2.sql",
    _MIGRATIONS_DIR / "0009_pg_agent_dimension.sql",
    _MIGRATIONS_DIR / "0032_dataset_compiler.sql",
)

_SLICE_COLUMNS = {"feature": "feature", "tool": "tool", "model": "model", "agent": "agent"}


async def _init_conn(conn: asyncpg.Connection) -> None:
    # Decode JSONB to Python objects on read (fixes v1's str-not-dict bug).
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


class MissingProvingGroundSchema(RuntimeError):
    """The proving-ground tables do not exist -- migrations have not been applied."""


class ProvingGroundStoreV2:
    def __init__(self, *, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None
        # One table per migration above is enough to tell "schema applied" from "not": checking
        # every pg_* table would couple this to the schema's shape rather than its presence.
        self._tables = ("pg_runs", "pg_model_runs", "pg_sft_rows")
        self._verified = False

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, init=_init_conn)
        return self._pool

    async def setup(self) -> None:
        """Verify the proving-ground schema exists. Does NOT apply migrations (s61, D83 step 5).

        This method used to read the three migration files above and execute them, which is a
        different sin from the rest of the sweep: no duplicated definition and so no drift, but
        still DDL at runtime -- and `madras_app`, the DDL-less role RLS requires, is refused it.
        Applying migrations is `scripts/apply_migrations.py`'s job; a store should not also be a
        migration runner, or "which one applied the schema" becomes ambiguous.

        Note it is invisible to `tests/test_infra/test_no_runtime_ddl.py`: that check inspects
        string LITERALS, and DDL read from a file at runtime never appears as one.
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
                f"`uv run python scripts/apply_migrations.py` "
                f"({', '.join(m.name for m in _MIGRATIONS)} define this schema)"
            )
        self._verified = True

    async def write_run(
        self,
        run: dict[str, Any],
        model_runs: list[dict[str, Any]],
        scenario_results: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]],
        judge_votes: list[dict[str, Any]],
        metrics: list[dict[str, Any]],
        coverage: list[dict[str, Any]],
    ) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO pg_runs
                   (run_id,head_sha,seed,models,suites,composite_by_model,leaderboard,agents)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                   ON CONFLICT (run_id) DO NOTHING""",
                run["run_id"],
                run.get("head_sha"),
                run.get("seed"),
                run.get("models", []),
                run.get("suites", []),
                run.get("composite_by_model", {}),
                run.get("leaderboard", []),
                run.get("agents", []),
            )
            for m in model_runs:
                await conn.execute(
                    """INSERT INTO pg_model_runs
                       (run_id,agent,model,overall,pass_k,composite,per_feature,per_benchmark,
                        per_metric,cost_usd,latency_ms,safety_completion_rate)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                       ON CONFLICT (run_id,agent,model) DO NOTHING""",
                    m["run_id"],
                    m.get("agent", DEFAULT_AGENT),
                    m["model"],
                    m.get("overall"),
                    m.get("pass_k"),
                    m.get("composite"),
                    m.get("per_feature", {}),
                    m.get("per_benchmark", {}),
                    m.get("per_metric", {}),
                    m.get("cost_usd"),
                    m.get("latency_ms"),
                    m.get("safety_completion_rate"),
                )
            for s in scenario_results:
                await conn.execute(
                    """INSERT INTO pg_scenario_results
                       (run_id,agent,model,scenario_id,suite_id,benchmark_family,features,k,passes,
                        pass_rate,det,judge_pass,verdict,n_steps,tool_error_rate,latency_ms,tokens)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                       ON CONFLICT (run_id,agent,model,scenario_id) DO NOTHING""",
                    s["run_id"],
                    s.get("agent", DEFAULT_AGENT),
                    s["model"],
                    s["scenario_id"],
                    s.get("suite_id"),
                    s.get("benchmark_family"),
                    s.get("features", []),
                    s.get("k"),
                    s.get("passes"),
                    s.get("pass_rate"),
                    s.get("det", []),
                    s.get("judge_pass"),
                    s.get("verdict"),
                    s.get("n_steps"),
                    s.get("tool_error_rate"),
                    s.get("latency_ms"),
                    s.get("tokens"),
                )
            for t in tool_calls:
                await conn.execute(
                    """INSERT INTO pg_tool_calls
                       (run_id,agent,model,scenario_id,resample,seq,tool,args,ok,error,governance,latency_ms)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)""",
                    t["run_id"],
                    t.get("agent", DEFAULT_AGENT),
                    t.get("model"),
                    t.get("scenario_id"),
                    t.get("resample"),
                    t.get("seq"),
                    t.get("tool"),
                    t.get("args", {}),
                    t.get("ok"),
                    t.get("error"),
                    t.get("governance", {}),
                    t.get("latency_ms"),
                )
            for v in judge_votes:
                await conn.execute(
                    """INSERT INTO pg_judge_votes
                       (run_id,agent,model,scenario_id,judge_model,pass,score,reason)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                    v["run_id"],
                    v.get("agent", DEFAULT_AGENT),
                    v.get("model"),
                    v.get("scenario_id"),
                    v.get("judge_model"),
                    v.get("pass"),
                    v.get("score"),
                    v.get("reason"),
                )
            for mt in metrics:
                await conn.execute(
                    """INSERT INTO pg_metrics
                       (run_id,agent,model,scenario_id,suite_id,feature,tool,metric,value)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                    mt["run_id"],
                    mt.get("agent", DEFAULT_AGENT),
                    mt.get("model"),
                    mt.get("scenario_id"),
                    mt.get("suite_id"),
                    mt.get("feature"),
                    mt.get("tool"),
                    mt.get("metric"),
                    mt.get("value"),
                )
            for c in coverage:
                await conn.execute(
                    """INSERT INTO pg_coverage
                       (run_id,agent,model,feature,tool,benchmark,covered,n_scenarios,evidence)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                    c["run_id"],
                    c.get("agent", DEFAULT_AGENT),
                    c.get("model"),
                    c.get("feature"),
                    c.get("tool"),
                    c.get("benchmark"),
                    c.get("covered"),
                    c.get("n_scenarios"),
                    c.get("evidence", {}),
                )

    async def leaderboard(self, run_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM pg_model_runs WHERE run_id=$1
                   ORDER BY composite DESC NULLS LAST""",
                run_id,
            )
        return [dict(r) for r in rows]

    async def model_run(
        self, run_id: str, model: str, agent: str = DEFAULT_AGENT
    ) -> dict[str, Any] | None:
        """The single ``pg_model_runs`` row for (run_id, agent, model), or None.

        Used by the regression gate to fetch the previous run's per-feature /
        per-benchmark scores for the same agent+model. ``agent`` defaults to the
        historical implicit agent so pre-agent-dimension callers keep working.
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM pg_model_runs WHERE run_id=$1 AND agent=$2 AND model=$3",
                run_id,
                agent,
                model,
            )
        return dict(row) if row is not None else None

    async def tools_for_scenario(self, run_id: str, scenario_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM pg_tool_calls WHERE run_id=$1 AND scenario_id=$2
                   ORDER BY seq""",
                run_id,
                scenario_id,
            )
        return [dict(r) for r in rows]

    async def judge_votes_for_scenario(self, run_id: str, scenario_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM pg_judge_votes WHERE run_id=$1 AND scenario_id=$2
                   ORDER BY judge_model""",
                run_id,
                scenario_id,
            )
        return [dict(r) for r in rows]

    async def metrics_for_scenario(self, run_id: str, scenario_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM pg_metrics WHERE run_id=$1 AND scenario_id=$2
                   ORDER BY metric""",
                run_id,
                scenario_id,
            )
        return [dict(r) for r in rows]

    async def scenarios_using_tool(self, run_id: str, tool: str) -> list[str]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT DISTINCT scenario_id FROM pg_tool_calls
                   WHERE run_id=$1 AND tool=$2 ORDER BY scenario_id""",
                run_id,
                tool,
            )
        return [r["scenario_id"] for r in rows]

    async def scenarios_for_run(self, run_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT scenario_id, agent, model, suite_id, benchmark_family, pass_rate
                   FROM pg_scenario_results WHERE run_id=$1
                   ORDER BY agent, model, scenario_id""",
                run_id,
            )
        return [dict(r) for r in rows]

    async def coverage_matrix(self, run_id: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM pg_coverage WHERE run_id=$1
                   ORDER BY agent, model, feature, tool""",
                run_id,
            )
        return [dict(r) for r in rows]

    async def metric_slice(self, run_id: str, metric: str, by: str = "feature") -> dict[str, float]:
        col = _SLICE_COLUMNS.get(by)
        if col is None:
            raise ValueError(f"unsupported slice dimension: {by!r}")
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT {col} AS key, AVG(value) AS mean FROM pg_metrics
                    WHERE run_id=$1 AND metric=$2 AND {col} IS NOT NULL
                    GROUP BY {col}""",
                run_id,
                metric,
            )
        return {r["key"]: float(r["mean"]) for r in rows}

    async def metric_flag_count(self, run_id: str, metric: str) -> int:
        """Count of rows for `metric` (e.g. gaming_flagged/eval_awareness_flagged)
        with a truthy (>0) value, for surfacing already-recorded scan results."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS n FROM pg_metrics WHERE run_id=$1 AND metric=$2 AND value>0",
                run_id,
                metric,
            )
        return int(row["n"]) if row else 0

    async def cost_rows(self, run_id: str) -> list[dict[str, Any]]:
        """Per-(agent, model, scenario) cost + benchmark_family for the economics engine."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT m.agent,
                       m.model,
                       sr.benchmark_family,
                       MAX(CASE WHEN m.metric = 'cost_usd' THEN m.value END) AS cost_usd,
                       MAX(CASE WHEN m.metric = 'tokens'   THEN m.value END) AS tokens
                FROM pg_metrics m
                JOIN pg_scenario_results sr
                  ON sr.run_id = m.run_id AND sr.agent = m.agent AND sr.model = m.model
                 AND sr.scenario_id = m.scenario_id
                WHERE m.run_id = $1 AND m.scenario_id IS NOT NULL
                GROUP BY m.agent, m.model, sr.benchmark_family, m.scenario_id
                """,
                run_id,
            )
        return [dict(r) for r in rows]

    async def recent_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM pg_runs ORDER BY ts DESC LIMIT $1", limit)
        return [dict(r) for r in rows]

    async def count_runs(self) -> int:
        """True total run count -- for KPI display, where recent_runs()'s limit would
        under-report once the platform has more runs than any reasonable page size."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM pg_runs")

    async def write_backlog(self, items: list[dict[str, Any]]) -> None:
        """Persist run_sweep's regression-gate findings (detect_regressions) so
        they're queryable after the run, not silently dropped."""
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
                    # raw list, not json.dumps'd -- the pool's jsonb codec (_init_conn)
                    # already encodes on write and decodes on read (v1's store.py lacks
                    # that codec, hence its own manual json.dumps/json.loads pairing).
                    it.get("evidence_run_ids", []),
                    it.get("root_cause"),
                    it.get("suggested_fix"),
                    it.get("track"),
                    it.get("scope_flag"),
                )

    async def open_backlog(self) -> list[dict[str, Any]]:
        # evidence_run_ids is JSONB — the pool's type codec (_init_conn) already
        # decodes it to a Python list, unlike v1's store.py (no codec there).
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM madras_pg_backlog WHERE status='open' ORDER BY id DESC"
            )
        return [dict(r) for r in rows]

    async def write_sft_rows(self, rows: list[dict[str, Any]]) -> None:
        """Persist Dataset Compiler output (T4.1) -- rows from either producer
        (Synthetic-Data-Kit or the Distilabel Teacher Council). Idempotent on id:
        a re-run of the same mining pass must not duplicate rows."""
        if not rows:
            return
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            for r in rows:
                await conn.execute(
                    """INSERT INTO pg_sft_rows
                       (id,tenant,consent,producer,source_id,prompt,completion,score,provenance)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                       -- (id, tenant), not id alone: producers derive ids from CONTENT for
                       -- idempotence, so two tenants mining the same corpus produce the same id
                       -- and the bare target silently dropped the second one (0040).
                       ON CONFLICT (id, tenant) DO NOTHING""",
                    r["id"],
                    r.get("tenant", "default"),
                    r.get("consent", True),
                    r["producer"],
                    r.get("source_id"),
                    r["prompt"],
                    r["completion"],
                    r.get("score"),
                    r.get("provenance", {}),
                )

    async def sft_rows_by_producer(self, producer: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM pg_sft_rows WHERE producer=$1 ORDER BY created_at DESC", producer
            )
        return [dict(r) for r in rows]

    async def save_leaderboard(
        self,
        run_id: str,
        rows: list[dict[str, Any]],
        *,
        ts: float = 0.0,
        head_sha: str = "",
    ) -> None:
        """Persist leaderboard columns onto pg_model_runs + append pg_climb points."""
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            for r in rows:
                await conn.execute(
                    """UPDATE pg_model_runs SET tier=$4, madras_index=$5, raw_index=$6,
                          scaffold_lift=$7, cost_of_pass=$8, tokens_per_task=$9, speed_tok_s=$10
                       WHERE run_id=$1 AND agent=$2 AND model=$3""",
                    run_id,
                    r.get("agent", DEFAULT_AGENT),
                    r["model"],
                    r.get("tier"),
                    r.get("madras_index"),
                    r.get("raw_index"),
                    r.get("scaffold_lift"),
                    r.get("cost_of_pass"),
                    r.get("tokens_per_task"),
                    r.get("speed_tok_s"),
                )
                await conn.execute(
                    """INSERT INTO pg_climb
                       (run_id,ts,agent,model,tier,madras_index,scaffold_lift,cost_of_pass,head_sha)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                    run_id,
                    ts,
                    r.get("agent", DEFAULT_AGENT),
                    r["model"],
                    r.get("tier", ""),
                    r.get("madras_index"),
                    r.get("scaffold_lift"),
                    r.get("cost_of_pass"),
                    head_sha,
                )

    async def save_outlier(
        self,
        run_id: str,
        agent: str,
        model: str,
        verdict: dict[str, Any],
    ) -> None:
        """Persist the compounding signature + outlier verdict onto pg_model_runs (W0·3 spine).

        The other outlier signals are already persisted by ``save_leaderboard`` (madras_index,
        scaffold_lift, cost_of_pass, tokens_per_task, speed_tok_s) + the run write (pass_k).
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE pg_model_runs
                      SET quality_lift=$4, cost_decay=$5, compounding=$6, is_outlier=$7
                   WHERE run_id=$1 AND agent=$2 AND model=$3""",
                run_id,
                agent,
                model,
                verdict.get("quality_lift"),
                verdict.get("cost_decay"),
                verdict.get("compounding"),
                verdict.get("is_outlier"),
            )

    async def climb_series(
        self,
        agent: str,
        model: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Madras Index over run history for one (agent, model) — the climb chart."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT run_id, ts, tier, madras_index, scaffold_lift, cost_of_pass, head_sha
                   FROM pg_climb WHERE agent=$1 AND model=$2 ORDER BY ts ASC, id ASC LIMIT $3""",
                agent,
                model,
                limit,
            )
        return [dict(r) for r in rows]

    async def add_human_label(
        self,
        *,
        run_id: str,
        scenario_id: str,
        human_pass: bool,
        agent: str = DEFAULT_AGENT,
        model: str = "",
        note: str = "",
        ts: float = 0.0,
    ) -> None:
        """Record a human pass/fail label for judge meta-evaluation (upsert)."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO pg_human_labels
                   (run_id,scenario_id,agent,model,human_pass,note,ts)
                   VALUES ($1,$2,$3,$4,$5,$6,$7)
                   ON CONFLICT (run_id,scenario_id,agent,model)
                   DO UPDATE SET human_pass=EXCLUDED.human_pass, note=EXCLUDED.note""",
                run_id,
                scenario_id,
                agent,
                model,
                human_pass,
                note,
                ts,
            )

    async def judge_human_agreement(self, run_id: str) -> dict[str, Any]:
        """Compare stored human labels to the panel's per-scenario pass for a run."""
        from madras.eval_.proving_ground.agreement import judge_human_agreement

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT h.human_pass, (sr.pass_rate >= 1.0) AS panel_pass
                   FROM pg_human_labels h
                   JOIN pg_scenario_results sr
                     ON sr.run_id=h.run_id AND sr.scenario_id=h.scenario_id
                    AND sr.agent=h.agent AND (h.model='' OR sr.model=h.model)
                   WHERE h.run_id=$1""",
                run_id,
            )
        pairs = [{"human_pass": r["human_pass"], "panel_pass": r["panel_pass"]} for r in rows]
        return judge_human_agreement(pairs)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
