"""Proving Ground v2-C task C4 — coverage matrix + regression gate.

Two proofs ride on top of a sweep:

* **Coverage matrix** (`build_coverage`) — for every benchmark FEATURE and every
  TOOL the selected suites declare, PLUS every tool registered in the global
  ``REGISTRY``, produce one ``pg_coverage`` row asserting whether the run actually
  exercised it. A feature cell is "covered" when ≥1 scenario carried that feature;
  a tool cell when ≥1 tool-call used that tool. UN-covered cells are red gaps
  (`red_cells`) — e.g. a registered tool no scenario reached, or a declared
  feature the suite never sampled.

* **Regression gate** (`detect_regressions`) — compares this run's per-feature /
  per-benchmark scores for one model to the SAME model's scores in the previous
  run. A drop beyond ``threshold`` becomes a high-severity backlog item reusing
  the v1 ``madras_pg_backlog`` row shape (severity/pattern/evidence_run_ids/
  root_cause/suggested_fix/track/scope_flag), so it lands in the existing backlog
  table via the existing writer — no new table, no new column.

pg_coverage row convention (matches infra/migrations/0007 exactly):
  ``{run_id, feature, tool, benchmark, covered, n_scenarios, evidence}``.
  A FEATURE cell sets ``feature`` and leaves ``tool`` None; a TOOL cell sets
  ``tool`` and leaves ``feature`` None. ``benchmark`` is left None here (it is a
  per-benchmark coverage axis reserved for future use). ``evidence`` is a JSONB
  object ``{"scenario_ids": [...]}`` (the migration defaults it to ``{}``).
"""

from __future__ import annotations

from typing import Any

from madras.eval_.proving_ground.agents import DEFAULT_AGENT
from madras.eval_.proving_ground.suite import Suite

# Drop beyond which a per-feature/per-benchmark score is treated as a regression.
_DEFAULT_THRESHOLD = 0.05


def _feature_cell(
    run_id: str, feature: str, scenario_ids: list[str], *, agent: str, model: str | None
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "agent": agent,
        "model": model,
        "feature": feature,
        "tool": None,
        "benchmark": None,
        "covered": len(scenario_ids) > 0,
        "n_scenarios": len(scenario_ids),
        "evidence": {"scenario_ids": sorted(scenario_ids)},
    }


def _tool_cell(
    run_id: str, tool: str, scenario_ids: list[str], *, agent: str, model: str | None
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "agent": agent,
        "model": model,
        "feature": None,
        "tool": tool,
        "benchmark": None,
        "covered": len(scenario_ids) > 0,
        "n_scenarios": len(scenario_ids),
        "evidence": {"scenario_ids": sorted(scenario_ids)},
    }


def build_coverage(
    *,
    run_id: str,
    suites: list[Suite],
    scenario_results: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    registry_tools: list[str],
) -> list[dict[str, Any]]:
    """Build the ``pg_coverage`` rows for one run, PER (agent, model).

    Coverage is now sliceable by *who* (agent) and *on what* (model): one set of
    cells is emitted for every distinct (agent, model) that actually ran. Within a
    unit, expected cells = the union of every selected suite's declared
    ``features`` (feature cells) and ``tools``, plus every name in
    ``registry_tools`` (tool cells). A feature cell is covered iff ≥1 of THAT
    unit's scenarios carried it; a tool cell iff ≥1 of THAT unit's tool-calls used
    it. ``n_scenarios``/``evidence`` count the distinct scenarios that exercised it
    for that unit. The ``benchmark`` (use-case) axis stays on the cell as before.
    """
    # Expected axes (de-duplicated, order-preserving) — shared across all units.
    expected_features: list[str] = []
    expected_tools: list[str] = []
    for suite in suites:
        for feat in suite.features:
            if feat not in expected_features:
                expected_features.append(feat)
        for tool in suite.tools:
            if tool not in expected_tools:
                expected_tools.append(tool)
    for tool in registry_tools:
        if tool not in expected_tools:
            expected_tools.append(tool)

    # The (agent, model) units that ran — preserve first-seen order. Fall back to
    # one default unit so an empty run still emits the all-red expected grid.
    units: list[tuple[str, str | None]] = []
    for s in scenario_results:
        unit = (s.get("agent", DEFAULT_AGENT), s.get("model"))
        if unit not in units:
            units.append(unit)
    if not units:
        units.append((DEFAULT_AGENT, None))

    rows: list[dict[str, Any]] = []
    for agent, model in units:
        # feature -> distinct scenario_ids carried by THIS unit.
        feat_scenarios: dict[str, set[str]] = {}
        for s in scenario_results:
            if (s.get("agent", DEFAULT_AGENT), s.get("model")) != (agent, model):
                continue
            sid = s.get("scenario_id")
            if sid is None:
                continue
            for feat in s.get("features", []):
                feat_scenarios.setdefault(feat, set()).add(sid)
        # tool -> distinct scenario_ids used by THIS unit.
        tool_scenarios: dict[str, set[str]] = {}
        for t in tool_calls:
            if (t.get("agent", DEFAULT_AGENT), t.get("model")) != (agent, model):
                continue
            tool = t.get("tool")
            sid = t.get("scenario_id")
            if tool is None or sid is None:
                continue
            tool_scenarios.setdefault(tool, set()).add(sid)

        for feat in expected_features:
            rows.append(
                _feature_cell(
                    run_id, feat, list(feat_scenarios.get(feat, set())), agent=agent, model=model
                )
            )
        for tool in expected_tools:
            rows.append(
                _tool_cell(
                    run_id, tool, list(tool_scenarios.get(tool, set())), agent=agent, model=model
                )
            )
    return rows


def red_cells(coverage_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the un-covered cells (``covered`` is False) — the gaps to flag."""
    return [r for r in coverage_rows if not r.get("covered")]


def _regression_item(*, model: str, axis: str, name: str, old: float, new: float) -> dict[str, Any]:
    """A high-severity backlog item for one dropped feature/benchmark score.

    Reuses the v1 ``madras_pg_backlog`` row shape consumed by
    ``store.write_backlog`` (severity/pattern/evidence_run_ids/root_cause/
    suggested_fix/track/scope_flag). ``evidence_run_ids`` is filled by the caller
    (it knows the current + previous run ids); here it defaults to empty.
    """
    return {
        "severity": "high",
        "pattern": "regression",
        "evidence_run_ids": [],
        "root_cause": (
            f"[deterministic] {model}: {axis} {name!r} regressed from "
            f"{old:.3f} to {new:.3f} (drop {old - new:.3f}) vs the previous run."
        ),
        "suggested_fix": (
            f"Bisect changes affecting {axis} {name!r} for {model}; "
            f"compare the two runs' scenario rows for that {axis}."
        ),
        "track": axis,
        "scope_flag": "in_scope",
    }


def detect_regressions(
    *,
    model: str,
    current_model_run: dict[str, Any],
    previous_model_run: dict[str, Any] | None,
    threshold: float = _DEFAULT_THRESHOLD,
) -> list[dict[str, Any]]:
    """Flag per-feature / per-benchmark score drops vs the previous run.

    Compares ``current_model_run``'s ``per_feature`` and ``per_benchmark`` dicts to
    the previous run's for the SAME model. For each key whose score dropped by more
    than ``threshold``, emit one high-severity backlog item. Returns ``[]`` when
    there is no previous run (first run for this model).
    """
    if previous_model_run is None:
        return []

    items: list[dict[str, Any]] = []
    for axis in ("per_feature", "per_benchmark"):
        cur: dict[str, Any] = current_model_run.get(axis) or {}
        prev: dict[str, Any] = previous_model_run.get(axis) or {}
        for name, prev_score in prev.items():
            cur_score = cur.get(name)
            if prev_score is None or cur_score is None:
                continue
            if float(prev_score) - float(cur_score) > threshold:
                items.append(
                    _regression_item(
                        model=model,
                        axis=axis,
                        name=name,
                        old=float(prev_score),
                        new=float(cur_score),
                    )
                )
    return items
