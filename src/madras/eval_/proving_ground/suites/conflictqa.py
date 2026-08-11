"""ConflictQA suite (belief under conflicting evidence / contradiction arbitration).

ConflictQA (osunlp/ConflictQA, Apache-2.0) - the question has a correct ground truth, but the prompt
also surfaces *counter-evidence* arguing a wrong answer; the agent must hold the correct belief, not
flip. Case-insensitive ``answer_regex`` on the ground truth. Full set loads live via
``hf_hub_download`` (JSONL); a vendored slice is the offline fallback. Held-out gate set. W0-A2.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "conflictqa" / "data"
_SLICE = DATA_DIR / "conflictqa_slice.json"
_FEATURES = ["multi_step_reasoning", "knowledge_update"]


def _gt(v: Any) -> str:
    if isinstance(v, list):
        vl = cast("list[Any]", v)
        return str(vl[0]) if vl else ""
    s = str(v)
    try:
        parsed = ast.literal_eval(s)
        if isinstance(parsed, list) and parsed:
            return str(cast("list[Any]", parsed)[0])
    except Exception:
        pass
    m = re.search(r"['\"]([^'\"]+)['\"]", s)
    return m.group(1) if m else s


def _case(row: dict[str, Any], suite_id: str) -> Case:
    answer = str(row.get("answer", "")).strip()
    q = str(row.get("question", "")).strip()
    counter = str(row.get("counter_evidence", "")).strip()
    prompt = f"{q}\n\n[Some sources claim otherwise: {counter}]" if counter else q
    checks = [{"type": "answer_regex", "pattern": rf"(?i){re.escape(answer)}"}] if answer else []
    return Case(
        id=str(row.get("id")),
        suite_id=suite_id,
        benchmark_family="conflictqa",
        features=list(_FEATURES),
        tools=[],
        prompt=prompt,
        setup={"gold_answer": answer, "split": "heldout"},
        checks=checks,
    )


def _load_full() -> list[dict[str, Any]] | None:
    try:
        from huggingface_hub import hf_hub_download  # type: ignore[reportMissingTypeStubs]

        p: str = hf_hub_download(
            "osunlp/ConflictQA", "conflictQA-popQA-gpt4.json", repo_type="dataset"
        )
        cq = [json.loads(line) for line in open(p, encoding="utf-8") if line.strip()]
        return [
            {
                "id": f"conflictqa_{i}",
                "question": r.get("question", ""),
                "answer": _gt(r.get("ground_truth", "")),
                "counter_evidence": str(r.get("counter_memory", ""))[:300],
            }
            for i, r in enumerate(cq)
        ]
    except Exception:
        return None


class ConflictQaSuite(Suite):
    id: str = "conflictqa"
    name: str = "ConflictQA (belief under conflicting evidence)"
    version: str = "popQA-gpt4"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "osunlp/ConflictQA (Apache-2.0); hold-vs-flip belief under counter-evidence "
        "(contradiction arbitration). Held-out gate set."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        rows = _load_full()
        if rows is None and _SLICE.exists():
            rows = json.loads(_SLICE.read_text(encoding="utf-8"))
        return [_case(r, self.id) for r in (rows or [])]
