"""SealQA suite (Seal-0 — search-robust hard fact-finding).

SealQA / Seal-0 (vtllms/sealqa, Apache-2.0) — the 2025 hard tier: questions where web search returns
**noisy/conflicting** results, so the agent must reconcile evidence rather than trust the top hit.
111 test items. Full set loads live (cached); a vendored slice under ``sealqa/data/`` is the
offline fallback. Each row → a v2 ``Case`` with the ``web``/``browser`` toolset + an
``answer_regex`` check. Test-only set → every case is **held-out** (canary-guarded). W0·A2.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "sealqa" / "data"
_SLICE = DATA_DIR / "sealqa_slice.json"
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
        benchmark_family="sealqa",
        features=list(_FEATURES),
        tools=list(_TOOLS),
        prompt=str(row.get("question", "")).strip(),
        setup={"gold_answer": answer, "split": "heldout"},
        checks=checks,
    )


def _load_full() -> list[dict[str, Any]] | None:
    try:
        from datasets import load_dataset  # type: ignore[reportMissingTypeStubs]

        ds: Any = load_dataset("vtllms/sealqa", "seal_0", split="test")
        return [
            {"id": f"sealqa_{i}", "question": r["question"], "answer": str(r["answer"]).strip()}
            for i, r in enumerate(ds)
        ]
    except Exception:
        return None


class SealQaSuite(Suite):
    id: str = "sealqa"
    name: str = "SealQA / Seal-0 (search-robust fact-finding)"
    version: str = "seal_0"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "vtllms/sealqa, Seal-0 config (Apache-2.0); hard fact-finding under noisy/"
        "conflicting web search — reconcile evidence. Held-out gate set."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))

    def load_cases(self) -> list[Case]:
        rows = _load_full()
        if rows is None and _SLICE.exists():
            rows = json.loads(_SLICE.read_text(encoding="utf-8"))
        return [_case(r, self.id) for r in (rows or [])]
