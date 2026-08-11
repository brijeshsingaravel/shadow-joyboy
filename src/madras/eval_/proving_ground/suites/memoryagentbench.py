"""MemoryAgentBench suite (long-context / multi-session memory).

MemoryAgentBench (ai-hyz/MemoryAgentBench, MIT) - four memory competencies LongMemEval/LoCoMo lack
as splits: Accurate_Retrieval, Test_Time_Learning, Long_Range_Understanding, Conflict_Resolution.
Each row = a long context + many questions + acceptable answers; we expand to one ``Case`` per
question (prompt = context + question), checked by case-insensitive ``answer_regex`` on the gold.
Full set loads live (cached, full contexts); a truncated vendored slice is the offline fallback.
Held-out gate set. W0-A2.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "memoryagentbench" / "data"
_SLICE = DATA_DIR / "memoryagentbench_slice.json"
_FEATURES = ["memory_recall", "multisession", "multi_step_reasoning"]


def _case(row: dict[str, Any], suite_id: str) -> Case:
    answer = str(row.get("answer", "")).strip()
    ctx = str(row.get("context", "")).strip()
    q = str(row.get("question", "")).strip()
    prompt = f"{ctx}\n\nQuestion: {q}" if ctx else q
    checks = [{"type": "answer_regex", "pattern": rf"(?i){re.escape(answer)}"}] if answer else []
    return Case(
        id=str(row.get("id")),
        suite_id=suite_id,
        benchmark_family="memoryagentbench",
        features=list(_FEATURES),
        tools=[],
        prompt=prompt,
        setup={"gold_answer": answer, "split": "heldout", "split_type": row.get("split_type", "")},
        checks=checks,
    )


def _gold(ans: Any) -> str:
    if isinstance(ans, list) and ans:
        first: Any = cast("list[Any]", ans)[0]
        if isinstance(first, list) and first:
            return str(cast("list[Any]", first)[0])
        return str(cast("Any", first))
    return str(cast("Any", ans) or "")


def _load_full() -> list[dict[str, Any]] | None:
    try:
        from datasets import load_dataset  # type: ignore[reportMissingTypeStubs]

        ds: Any = load_dataset("ai-hyz/MemoryAgentBench")
        rows: list[dict[str, Any]] = []
        for split in ds:
            for ri, r in enumerate(ds[split]):
                qs: list[Any] = r.get("questions") or []
                ans: list[Any] = r.get("answers") or []
                ctx = str(r.get("context", ""))
                for qi, q in enumerate(qs):
                    gold = _gold(ans[qi]) if qi < len(ans) else ""
                    rows.append(
                        {
                            "id": f"mab_{split}_{ri}_{qi}",
                            "split_type": split,
                            "question": str(q),
                            "answer": gold,
                            "context": ctx,
                        }
                    )
        return rows
    except Exception:
        return None


class MemoryAgentBenchSuite(Suite):
    id: str = "memoryagentbench"
    name: str = "MemoryAgentBench (long-context / multi-session memory)"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "ai-hyz/MemoryAgentBench (MIT); accurate-retrieval / test-time-learning / "
        "long-range / conflict-resolution memory splits. Held-out gate set."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        rows = _load_full()
        if rows is None and _SLICE.exists():
            rows = json.loads(_SLICE.read_text(encoding="utf-8"))
        return [_case(r, self.id) for r in (rows or [])]
