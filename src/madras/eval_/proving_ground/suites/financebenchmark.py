"""Microsoft FinanceBenchmark suite (public) — benchmark-design.md §12f, the Mona
(Finance/CFO, vs ChatFin/HighRadius/BlackLine) vertical gap.

``microsoft/FinanceBenchmark`` (MIT, GitHub) is a real 251-entry finance-agent benchmark across
3 plugins: ``erp_qa`` (needs a provisioned Dynamics 365 sandbox we don't have — excluded),
``finance_qa`` (public-company financial analysis, internet-only, 126 entries), and
``business_brief`` (company profile synthesis, internet-only, 25 entries). This suite uses only
the 151 ERP-independent entries (verified live, s42) — no proprietary infra required.

Each row carries a free-text query + a nested ``tags`` structure (accuracy/clarity/groundedness/
relevance/structure), each with critical-level assertion texts. We flatten the critical
assertions into a single rubric string (matching Case.rubric's existing usage across the
codebase) rather than modeling the full nested judge structure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "financebenchmark" / "data"
_SLICE = DATA_DIR / "financebenchmark_slice.json"

_FEATURES = ["finance_reasoning"]
_DATASET_URL = "https://raw.githubusercontent.com/microsoft/FinanceBenchmark/main/data/dataset.yaml"
_ERP_INDEPENDENT_PLUGINS = ("finance_qa", "business_brief")


def _flatten_rubric(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for tag_entry in row.get("tags", []):
        for assertion in tag_entry.get("assertions", []):
            if assertion.get("level") == "critical":
                parts.append(f"[{tag_entry.get('tag', '?')}] {assertion.get('text', '').strip()}")
    return " ".join(parts)


def _row_to_case(row: dict[str, Any], suite_id: str, index: int) -> Case:
    query = str(row.get("query", "")).strip()
    plugin = str(row.get("plugin", ""))
    return Case(
        id=f"{plugin}_{index}",
        suite_id=suite_id,
        benchmark_family="financebenchmark",
        features=list(_FEATURES),
        tools=[],
        prompt=query,
        setup={"plugin": plugin, "segment": row.get("segment")},
        checks=[],
        rubric=_flatten_rubric(row),
    )


class FinanceBenchmarkSuite(Suite):
    id: str = "financebenchmark"
    name: str = "Microsoft FinanceBenchmark (ERP-independent slice)"
    version: str = "v1-curated"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "microsoft/FinanceBenchmark (public, MIT); uses only the finance_qa + "
        "business_brief plugins (151 of 251 entries) — the erp_qa plugin needs a "
        "provisioned Dynamics 365 sandbox we don't have, excluded. Fills the Mona "
        "(Finance/CFO, vs ChatFin/HighRadius/BlackLine) vertical gap confirmed in "
        "benchmark-design.md §12f."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        if _SLICE.exists():
            rows = json.loads(_SLICE.read_text(encoding="utf-8"))
            return [_row_to_case(r, self.id, i) for i, r in enumerate(rows)]

        import httpx

        resp = httpx.get(_DATASET_URL, timeout=30.0)
        resp.raise_for_status()
        data: list[dict[str, Any]] = yaml.safe_load(resp.text)
        usable = [r for r in data if r.get("plugin") in _ERP_INDEPENDENT_PLUGINS]
        return [_row_to_case(r, self.id, i) for i, r in enumerate(usable)]
