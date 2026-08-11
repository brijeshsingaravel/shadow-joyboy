"""Held-out partition helpers (W0·A4b) — the eval-rigor firewall, encoded in data.

Every case carries ``setup["split"]`` (``"heldout"`` | ``"dev"``). We **tune on dev, gate + publish
on held-out**: the go-public gate (W3) and the CI gate score only held-out cases so builders can't
overfit the test set. The s24 outlier suites are whole-suite held-out gate sets. Pure functions.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, cast


def split_of(case_or_row: Any) -> str:
    """Return the split for a Case or a scenario-result row (default ``"dev"`` if untagged)."""
    setup: Any = getattr(case_or_row, "setup", None)
    if setup is None and isinstance(case_or_row, dict):
        setup = cast("dict[str, Any]", case_or_row).get("setup")
    if isinstance(setup, dict):
        return str(cast("dict[str, Any]", setup).get("split", "dev"))
    return "dev"


def is_heldout(case_or_row: Any) -> bool:
    return split_of(case_or_row) == "heldout"


def heldout_cases(cases: list[Any]) -> list[Any]:
    """Filter to held-out cases (gate/publish set)."""
    return [c for c in cases if is_heldout(c)]


def dev_cases(cases: list[Any]) -> list[Any]:
    """Filter to dev-split cases (tune-on set; G4: what the Dataset Compiler mines)."""
    return [c for c in cases if not is_heldout(c)]


def heldout_scores(scenario_results: list[dict[str, Any]]) -> dict[str, float]:
    """Per-suite mean ``pass_rate`` over HELD-OUT scenario results only (gate input).

    Rows carry ``suite_id`` + ``pass_rate`` and either ``setup.split`` or a top-level ``split``.
    Suites with no held-out rows are omitted (they can't be gated/published).
    """
    by_suite: dict[str, list[float]] = defaultdict(list)
    for row in scenario_results:
        split = row.get("split") or split_of(row)
        if split != "heldout":
            continue
        suite = row.get("suite_id")
        if suite is not None:
            by_suite[str(suite)].append(float(row.get("pass_rate", 0.0)))
    return {suite: sum(v) / len(v) for suite, v in by_suite.items() if v}
