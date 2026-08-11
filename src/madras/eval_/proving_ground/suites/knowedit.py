"""KnowEdit suite (knowledge-update / recent facts).

KnowEdit (zjunlp/KnowEdit, MIT) — we use the **wiki_recent** split (recently-changed facts), the
slice most relevant to an agent's *knowledge-update* competency: given a prompt about a recently
changed entity, does it produce the current ground-truth answer. Full split loads live (cached); a
vendored slice under ``knowedit/data/`` is the offline fallback. Each row → a ``Case`` with a
case-insensitive ``answer_regex`` check on the ground-truth. Held-out gate set. W0·A2.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "knowedit" / "data"
_SLICE = DATA_DIR / "knowedit_slice.json"
_FEATURES = ["knowledge_update", "multi_step_reasoning"]


def _case(row: dict[str, Any], suite_id: str) -> Case:
    answer = str(row.get("answer", "")).strip()
    checks = [{"type": "answer_regex", "pattern": rf"(?i){re.escape(answer)}"}] if answer else []
    return Case(
        id=str(row.get("id")),
        suite_id=suite_id,
        benchmark_family="knowedit",
        features=list(_FEATURES),
        tools=[],
        prompt=str(row.get("question", "")).strip(),
        setup={"gold_answer": answer, "split": "heldout"},
        checks=checks,
    )


def _load_full() -> list[dict[str, Any]] | None:
    try:
        from datasets import load_dataset  # type: ignore[reportMissingTypeStubs]

        ds: Any = load_dataset(
            "zjunlp/KnowEdit", data_files="benchmark/wiki_recent/recent_test.json", split="train"
        )
        rows: list[dict[str, Any]] = []
        for i, r in enumerate(ds):
            gt: Any = r.get("ground_truth") or r.get("target_new")
            if isinstance(gt, list) and gt:
                gt = cast("list[Any]", gt)[0]
            rows.append(
                {
                    "id": f"knowedit_{i}",
                    "question": str(r.get("prompt", "")),
                    "answer": str(cast("Any", gt) or "").strip(),
                }
            )
        return rows
    except Exception:
        return None


class KnowEditSuite(Suite):
    id: str = "knowedit"
    name: str = "KnowEdit (knowledge-update / recent facts)"
    version: str = "wiki_recent"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "zjunlp/KnowEdit (MIT), wiki_recent split; recently-changed facts for the "
        "knowledge-update competency. Held-out gate set."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        rows = _load_full()
        if rows is None and _SLICE.exists():
            rows = json.loads(_SLICE.read_text(encoding="utf-8"))
        return [_case(r, self.id) for r in (rows or [])]
