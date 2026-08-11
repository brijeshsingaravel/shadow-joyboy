"""BrowseComp suite (hard live web browsing).

BrowseComp (openai/simple-evals) — hard fact-finding questions that require **browsing** the
live web; the answer is a short, verifiable fact. Slice vendored under ``browsecomp/data/``
(20, decrypted from the official XOR-encrypted CSV via each row's canary). Each → a v2 ``Case``
with the ``web``/``browser`` toolset and an ``answer_regex`` (case-insensitive) check on the
gold answer. A no-browsing run scores ~0 (by design — this measures browsing).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "browsecomp" / "data"
_SLICE = DATA_DIR / "browsecomp_slice.json"
_FEATURES = ["web_browsing", "fact_finding"]
_TOOLS = ["web", "browser"]


def _case(row: dict[str, Any], suite_id: str) -> Case:
    answer = str(row.get("answer", "")).strip()
    checks: list[dict[str, Any]] = []
    if answer:
        checks.append({"type": "answer_regex", "pattern": rf"(?i){re.escape(answer)}"})
    return Case(
        id=str(row.get("id")),
        suite_id=suite_id,
        benchmark_family="browsecomp",
        features=list(_FEATURES),
        tools=list(_TOOLS),
        prompt=str(row.get("question", "")).strip(),
        setup={"gold_answer": answer},
        checks=checks,
    )


class BrowseCompSuite(Suite):
    id: str = "browsecomp"
    name: str = "BrowseComp (hard web browsing)"
    version: str = "test"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "openai/simple-evals (public, decrypted); hard fact-finding requiring live web browsing."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))

    def load_cases(self) -> list[Case]:
        if _SLICE.exists():
            rows = json.loads(_SLICE.read_text(encoding="utf-8"))
            return [_case(r, self.id) for r in rows]
        return []
