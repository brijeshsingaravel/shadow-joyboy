"""T2.12 -- per-run experiment record (Track 2c-ii recording architecture).

MLflow is the *experiment-comparison* layer, not a second source of truth: it points at
``pg_runs.run_id`` (the `ProvingGroundStoreV2` row is authoritative) and logs the same aggregate
leaderboard the CLI already prints -- never raw per-scenario data. A run whose tracking server is
unreachable must still complete cleanly; Postgres already has the real record regardless.
"""

from __future__ import annotations

import logging
from typing import Any

import mlflow as _mlflow

from madras.eval_.proving_ground.store_v2 import ProvingGroundStoreV2

mlflow: Any = _mlflow

logger = logging.getLogger(__name__)

EXPERIMENT_NAME = "madras-eval-lab"


def _sanitize(model: str) -> str:
    """MLflow metric/param keys reject '/' and some other separators."""
    return model.replace("/", "_")


async def log_experiment(
    store: ProvingGroundStoreV2,
    run_id: str,
    *,
    profile: str,
    subsystem: str | None,
    models: list[str],
    seed: int,
    certification: dict[str, Any] | None = None,
    dsl_metrics: dict[str, float] | None = None,
    tracking_uri: str | None = None,
) -> str | None:
    """Log a comparison-layer MLflow run for ``run_id``. Returns the MLflow run id, or None if
    the tracking server is unreachable (never raises -- Postgres stays the ledger of record)."""
    try:
        client = mlflow.MlflowClient(tracking_uri=tracking_uri)
        experiment = client.get_experiment_by_name(EXPERIMENT_NAME)
        experiment_id = (
            experiment.experiment_id
            if experiment is not None
            else client.create_experiment(EXPERIMENT_NAME)
        )
        mlflow_run = client.create_run(experiment_id, run_name=run_id)
        mlflow_run_id = mlflow_run.info.run_id

        client.log_param(mlflow_run_id, "pg_run_id", run_id)
        client.log_param(mlflow_run_id, "profile", profile)
        client.log_param(mlflow_run_id, "subsystem", subsystem)
        client.log_param(mlflow_run_id, "models", ",".join(models))
        client.log_param(mlflow_run_id, "seed", seed)

        leaderboard = await store.leaderboard(run_id)
        for row in leaderboard:
            model = _sanitize(str(row.get("model", "")))
            for metric in ("composite", "overall", "pass_k", "cost_usd", "latency_ms"):
                value = row.get(metric)
                if value is not None:
                    client.log_metric(mlflow_run_id, f"{model}.{metric}", float(value))
        client.log_dict(mlflow_run_id, {"leaderboard": leaderboard}, "leaderboard.json")

        if certification is not None:
            client.log_metric(mlflow_run_id, "certified", float(certification["certified"]))
            client.log_metric(mlflow_run_id, "gaming_flags", float(certification["gaming"]))
            client.log_metric(
                mlflow_run_id, "eval_awareness_flags", float(certification["eval_awareness"])
            )
            client.log_param(mlflow_run_id, "gate_summary", certification["gate_summary"])

        if dsl_metrics is not None:
            # RFC-0001's own decision-gate metrics (T5.3): grammar-conformance rate + round-trip
            # fidelity, held-out across >=3 seeds -- the concrete evidence needed to graduate
            # RFC-0001 from `status: draft` to an accepted ADR.
            for name, value in dsl_metrics.items():
                client.log_metric(mlflow_run_id, f"dsl.{name}", float(value))

        try:
            client.set_terminated(mlflow_run_id)
        except UnicodeEncodeError:
            # MlflowClient.set_terminated() writes an emoji run-URL banner straight to
            # sys.stdout before it marks the run FINISHED; a non-UTF8 console (Windows
            # cp1252) raises here even though every param/metric/artifact already landed.
            # Update the run status directly, skipping the decorative console write.
            client.update_run(mlflow_run_id, status="FINISHED")
        return mlflow_run_id
    except Exception:
        logger.warning("log_experiment: MLflow tracking unavailable for run_id=%s", run_id)
        return None
