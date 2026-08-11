"""FRAMES suite (multi-hop web-research fact-finding).

FRAMES (google/frames-benchmark, Apache-2.0) — 824 multi-hop factual questions that require
retrieving + reasoning across several Wikipedia pages; the answer is a short verifiable fact. The
full set loads live (cached); a vendored slice under ``frames/data/`` is the offline fallback. Each
row → a v2 ``Case`` with the ``web``/``browser`` toolset + a case-insensitive ``answer_regex`` check
on the gold answer. Test-only benchmark → every case is **held-out** (gate set). W0·A2.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "frames" / "data"
_SLICE = DATA_DIR / "frames_slice.json"
_FEATURES = ["web_browsing", "fact_finding", "multi_step_reasoning"]
_TOOLS = ["web", "browser"]


def _case(row: dict[str, Any], suite_id: str) -> Case:
    answer = str(row.get("answer", "")).strip()
    checks: list[dict[str, Any]] = []
    if answer:
        checks.append({"type": "answer_regex", "pattern": rf"(?i){re.escape(answer)}"})
    return Case(
        id=str(row.get("id")),
        suite_id=suite_id,
        benchmark_family="frames",
        features=list(_FEATURES),
        tools=list(_TOOLS),
        prompt=str(row.get("question", "")).strip(),
        setup={"gold_answer": answer, "split": "heldout"},
        checks=checks,
    )


def _load_full() -> list[dict[str, Any]] | None:
    try:
        from datasets import load_dataset  # type: ignore[reportMissingTypeStubs]

        ds: Any = load_dataset("google/frames-benchmark", split="test")
        return [
            {"id": f"frames_{i}", "question": r["Prompt"], "answer": str(r["Answer"]).strip()}
            for i, r in enumerate(ds)
        ]
    except Exception:
        return None


class FramesSuite(Suite):
    id: str = "frames"
    name: str = "FRAMES (multi-hop web fact-finding)"
    version: str = "test"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "google/frames-benchmark (Apache-2.0); 824 multi-hop factual questions "
        "requiring multi-page web retrieval + reasoning. Held-out gate set."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))

    def load_cases(self) -> list[Case]:
        rows = _load_full()
        if rows is None and _SLICE.exists():
            rows = json.loads(_SLICE.read_text(encoding="utf-8"))
        return [_case(r, self.id) for r in (rows or [])]
