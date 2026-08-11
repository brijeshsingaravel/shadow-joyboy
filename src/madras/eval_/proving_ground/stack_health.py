"""Stack Health KPIs for the founder cockpit -- Stage 1 (`hardening-eval-lab-handoff.md`,
the "Founder Cockpit -- Stack Health KPIs" row): read-only aggregates over infra that
already has a client somewhere in this codebase, no new external dependencies.

Every function here is a pure aggregation over an INJECTED client/store -- hermetically
testable, and never catches its own exceptions (the FastAPI endpoint degrades per-metric,
same "try/except -> partial payload" posture as every other /proving-ground/* handler in
server/app.py -- see `_pg_store()`'s docstring there).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from madras.eval_.proving_ground.dataset_compiler import PRODUCER_SYNTH_KIT

# Real bug (live-verified): teacher_council.py imports distilabel.pipeline.Pipeline at
# module level -- distilabel is an `eval`-extras dependency, not installed in the
# cockpit container's slimmer image. Importing PRODUCER_TEACHER_COUNCIL from that module
# would drag the whole distilabel chain into a lightweight KPI-reading module and crash
# every cockpit page load wherever distilabel is absent. Duplicated as a literal instead
# (matches teacher_council.py's own PRODUCER_TEACHER_COUNCIL exactly; update both together
# if it ever changes -- a two-line, extremely-low-churn constant, not worth a shared-
# constants module for).
PRODUCER_TEACHER_COUNCIL = "distilabel-teacher-council"


async def engine_health(store: Any) -> dict[str, Any]:
    """The internal Proving Ground engine's own KPIs (T4.1's real mined corpus + run count)
    -- distinct from the public marketing /proving-ground page, per the two-Proving-Grounds
    split this feature was scoped around."""
    synth_rows = await store.sft_rows_by_producer(PRODUCER_SYNTH_KIT)
    council_rows = await store.sft_rows_by_producer(PRODUCER_TEACHER_COUNCIL)
    run_count = await store.count_runs()
    return {
        "sft_rows_synth_kit": len(synth_rows),
        "sft_rows_teacher_council": len(council_rows),
        "runs_recorded": run_count,
    }


async def audit_health(writer: Any, *, agent_names: list[str]) -> dict[str, Any]:
    """Governed spend + action count (audit/writer.py::usage_by_agent). Deliberately does
    NOT include chain-integrity verification here -- verify_agent_chains() recomputes the
    hash chain over an agent's ENTIRE audit history, too expensive to run on every cockpit
    page load; that stays an on-demand check via the existing /workspace/agents/{agent}/audit
    endpoints, not a live KPI."""
    rows = await writer.usage_by_agent(agent_names=agent_names)
    return {
        "total_cost_usd": sum(r["total_cost_usd"] for r in rows),
        "total_actions": sum(r["action_count"] for r in rows),
    }


async def scheduler_health(store: Any) -> dict[str, Any]:
    """Active/dead job counts (scheduler/store.py::list_all()). Scoped to whatever agent
    the injected SchedulerStore was built for -- today that's effectively just Shadow, the
    only live agent (see SchedulerStore's own agent_name default)."""
    rows = await store.list_all()
    return {
        "active_jobs": sum(1 for r in rows if r["status"] == "active"),
        "dead_jobs": sum(1 for r in rows if r["status"] == "dead"),
        "jobs_with_failures": sum(1 for r in rows if r.get("fail_count", 0) > 0),
    }


def mlflow_run_count(client: Any, *, experiment_name: str) -> int | None:
    """Total logged runs under ``experiment_name`` via an injected MlflowClient (same
    client type experiment_log.py already uses). None if the experiment doesn't exist yet
    (a fresh install that's never run a non-smoke sweep)."""
    experiment = client.get_experiment_by_name(experiment_name)
    if experiment is None:
        return None
    return len(client.search_runs([experiment.experiment_id], max_results=1000))


def langfuse_counts(client: Any, *, host: str, public_key: str, secret_key: str) -> dict[str, Any]:
    """Trace + score counts via Langfuse's public REST API (confirmed live this session:
    GET {host}/api/public/{traces,scores}?limit=1, Basic Auth (public_key, secret_key),
    reading meta.totalItems -- fetching 1 record is enough for the pagination total, no
    need to page through the real data)."""
    traces = client.get(
        f"{host}/api/public/traces", params={"limit": 1}, auth=(public_key, secret_key)
    )
    traces.raise_for_status()
    scores = client.get(
        f"{host}/api/public/scores", params={"limit": 1}, auth=(public_key, secret_key)
    )
    scores.raise_for_status()
    return {
        "trace_count": traces.json()["meta"]["totalItems"],
        "score_count": scores.json()["meta"]["totalItems"],
    }


def latest_checkpoints(checkpoints_dir: Path) -> list[dict[str, Any]]:
    """The newest trained checkpoint per soul (HOPE today; RESOLVE/GRACE/etc. once T4.7
    distills them) -- reads unsloth_train.py's real model_card.json files directly off
    disk, since training is on-demand WSL work, not a standing service with its own API.
    Version sort is lexicographic ("v10" would sort before "v2") -- acceptable for the
    current v1/v2/... single-digit range; revisit if double-digit versions arrive."""
    if not checkpoints_dir.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for soul_dir in sorted(p for p in checkpoints_dir.iterdir() if p.is_dir()):
        versions = sorted(
            (p for p in soul_dir.iterdir() if p.is_dir() and (p / "model_card.json").exists()),
            key=lambda p: p.name,
        )
        if not versions:
            continue
        latest = versions[-1]
        card = json.loads((latest / "model_card.json").read_text(encoding="utf-8"))
        result.append(
            {
                "soul": soul_dir.name,
                "version": latest.name,
                "base_model": card.get("base_model"),
                "row_count": card.get("row_count"),
                "final_loss": card.get("final_loss"),
                "trained_at": card.get("trained_at"),
                "path": str(latest),
            }
        )
    return result
