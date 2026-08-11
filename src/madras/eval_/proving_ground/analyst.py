from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from madras.eval_.proving_ground.coverage import detect_regressions
from madras.eval_.proving_ground.strategist import FEATURE_TRACK

# Thresholds (deterministic): a feature is "healthy" at >=0.67 and "failing" at <0.5.
_HEALTHY = 0.67
_FAILING = 0.5

# Longitudinal recurring-fail floor: a (feature|benchmark) whose mean across the
# recent window is below this is a persistent weak spot.
_RECURRING_FLOOR = 0.5


@dataclass
class BacklogItem:
    severity: str
    pattern: str
    evidence_run_ids: list[str]
    root_cause: str
    suggested_fix: str
    track: str
    scope_flag: str


def _track(feature: str) -> str:
    return FEATURE_TRACK.get(feature, "unmapped")


def analyze(runs: list[dict[str, Any]]) -> list[BacklogItem]:
    """Detect cross-run patterns from newest-first run dicts.

    Each run dict carries ``run_id`` and ``per_feature`` (feature -> score).
    Deterministic only: ``root_cause`` is templated here; LLM enrichment is Plan 3.
    """
    if not runs:
        return []
    newest = runs[0]
    items: list[BacklogItem] = []
    features: set[str] = set()
    for r in runs:
        features.update(r.get("per_feature", {}).keys())

    for feature in sorted(features):
        new_score = newest.get("per_feature", {}).get(feature)

        # (a) regression: healthy in an older run, now failing in the newest run.
        if new_score is not None and new_score < _FAILING:
            for older in runs[1:]:
                old_score = older.get("per_feature", {}).get(feature)
                if old_score is not None and old_score >= _HEALTHY:
                    items.append(
                        BacklogItem(
                            severity="high",
                            pattern="regression",
                            evidence_run_ids=[older["run_id"], newest["run_id"]],
                            root_cause=(
                                f"[deterministic] {feature} regressed from "
                                f"{old_score:.2f} ({older['run_id']}) to "
                                f"{new_score:.2f} ({newest['run_id']}); "
                                f"LLM root-cause enrichment pending (Plan 3)."
                            ),
                            suggested_fix=(
                                f"Bisect changes between {older['run_id']} and "
                                f"{newest['run_id']} for {feature}; route via {_track(feature)}."
                            ),
                            track=_track(feature),
                            scope_flag="in_scope",
                        )
                    )
                    break  # one regression item per feature (most recent healthy anchor)

        # (b) recurring-fail: failing in the >=2 most-recent runs.
        if len(runs) >= 2:
            recent = runs[:2]
            scores = [r.get("per_feature", {}).get(feature) for r in recent]
            if all(s is not None and s < _FAILING for s in scores):
                items.append(
                    BacklogItem(
                        severity="med",
                        pattern="recurring-fail",
                        evidence_run_ids=[r["run_id"] for r in recent],
                        root_cause=(
                            f"[deterministic] {feature} failed (<{_FAILING}) in the "
                            f"two most-recent runs; LLM root-cause enrichment pending (Plan 3)."
                        ),
                        suggested_fix=(
                            f"Persistent {feature} failure; prioritize {_track(feature)}."
                        ),
                        track=_track(feature),
                        scope_flag="in_scope",
                    )
                )

    return items


def _recurring_item(
    *, model: str, axis: str, name: str, mean: float, evidence_run_ids: list[str]
) -> dict[str, Any]:
    """Recurring-fail backlog item in the v1 ``madras_pg_backlog`` row shape.

    Mirrors ``coverage._regression_item`` (same keys), but for a persistent
    weak spot rather than a single-run drop. ``track`` routes via
    ``FEATURE_TRACK`` for feature axes (benchmark axes fall to ``unmapped``).
    """
    return {
        "severity": "med",
        "pattern": "recurring-fail",
        "evidence_run_ids": evidence_run_ids,
        "root_cause": (
            f"[deterministic] {model}: {axis} {name!r} persistently low "
            f"(mean {mean:.3f} < {_RECURRING_FLOOR}) across the recent window."
        ),
        "suggested_fix": (
            f"Persistent {axis} {name!r} failure for {model}; prioritize {_track(name)}."
        ),
        "track": _track(name),
        "scope_flag": "in_scope",
    }


async def analyze_store(
    store: Any, *, limit: int = 10, regression_threshold: float = 0.05
) -> list[dict[str, Any]]:
    """Longitudinal mining over the normalized store (v2-E).

    Reads the last ``limit`` runs (newest-first) via ``store.recent_runs`` and,
    for every model appearing across them, assembles its per-feature /
    per-benchmark score history via ``store.model_run`` per run. Two detections:

    * **regression** — a per-feature or per-benchmark score dropping by more than
      ``regression_threshold`` vs that model's PREVIOUS run (the most recent
      such drop in the window; reuses ``coverage.detect_regressions`` exactly).
    * **recurring-fail** — a (feature|benchmark) whose mean across the window is
      below ``_RECURRING_FLOOR`` for a model — a persistent weak spot.

    Returns backlog-item dicts in the existing ``madras_pg_backlog`` shape
    (``coverage._regression_item`` / v1 ``analyze`` keys). If the store exposes
    ``write_backlog`` it is also persisted; the items are returned regardless.
    The store is injected — no live DB is required by this logic.
    """
    runs = await store.recent_runs(limit=limit)
    if not runs:
        return []

    # Models in newest-first run order, de-duplicated (preserve first-seen order).
    models: list[str] = []
    for r in runs:
        for m in r.get("models", []):
            if m not in models:
                models.append(m)

    items: list[dict[str, Any]] = []
    for model in models:
        # Per-model history newest-first: (run_id, model_run-or-None).
        history: list[tuple[str, dict[str, Any] | None]] = []
        for r in runs:
            mr = await store.model_run(r["run_id"], model)
            history.append((r["run_id"], mr))

        # (1) regression — latest drop vs the immediately preceding run that has a
        #     model_run for this model. Reuse coverage.detect_regressions verbatim.
        present = [(rid, mr) for rid, mr in history if mr is not None]
        if len(present) >= 2:
            (cur_rid, cur_mr), (prev_rid, prev_mr) = present[0], present[1]
            regs = detect_regressions(
                model=model,
                current_model_run=cur_mr,
                previous_model_run=prev_mr,
                threshold=regression_threshold,
            )
            for reg in regs:
                reg["evidence_run_ids"] = [prev_rid, cur_rid]
                # Route via FEATURE_TRACK (v1 analyst semantics), not the axis name.
                name = reg["root_cause"].split("'")[1]
                reg["track"] = _track(name)
            items.extend(regs)

        # (2) recurring-fail — per-axis mean across the window below the floor.
        for axis in ("per_feature", "per_benchmark"):
            sums: dict[str, list[tuple[str, float]]] = {}
            for rid, mr in present:
                axis_scores: dict[str, Any] = mr.get(axis) or {}
                for name, score in axis_scores.items():
                    if score is None:
                        continue
                    sums.setdefault(name, []).append((rid, float(score)))
            for name in sorted(sums):
                samples = sums[name]
                if len(samples) < 2:
                    continue
                mean = sum(s for _, s in samples) / len(samples)
                if mean < _RECURRING_FLOOR:
                    items.append(
                        _recurring_item(
                            model=model,
                            axis=axis,
                            name=name,
                            mean=mean,
                            evidence_run_ids=[rid for rid, _ in samples],
                        )
                    )

    if hasattr(store, "write_backlog"):
        await store.write_backlog(items)
    return items
