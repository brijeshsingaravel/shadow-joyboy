"""ARC-AGI-2 suite (public) — benchmark-design.md §12a, a frontier-difficulty suite genuinely
discriminating in 2026 (Gemini 3.1 Pro 77.1% vs GPT-5.2 52.9%, per s42 research — real spread,
not saturated like the older AIME/GPQA/MMLU-Pro/GSM8K suites already in the roster).

``arcprize/ARC-AGI-2`` (Apache-2.0, verified live s42) ships 120 public evaluation tasks as
JSON grid-transformation puzzles: N training input/output grid pairs demonstrating a rule, then
a test input the model must transform using the same rule. The public evaluation split
includes ground-truth test outputs (confirmed live), unlike the fully-hidden competition set.

No dedicated grid-match check type exists in this codebase's scoring.py, so the prompt asks for
a canonical compact-JSON grid string and the check is ``answer_contains`` against it — the same
pattern gpqa/gsm8k use (format instructions + a matching check), not a new check type.

Per BD8 (§12): this suite is a structural floor for the local 3-4B Ollama fleet (near-0% expected,
same as ARC-AGI-3) — include only in nightly/release-certification tiers, not smoke/regression.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "arc_agi2" / "data"
_SLICE = DATA_DIR / "arc_agi2_slice.json"

_FEATURES = ["abstract_reasoning"]
_EVAL_DIR_API = "https://api.github.com/repos/arcprize/ARC-AGI-2/contents/data/evaluation"
_RAW_BASE = "https://raw.githubusercontent.com/arcprize/ARC-AGI-2/main/data/evaluation"


def _grid_str(grid: list[list[int]]) -> str:
    return json.dumps(grid, separators=(",", ":"))


def _task_to_case(task: dict[str, Any], suite_id: str) -> Case:
    train_examples = "\n".join(
        f"Example {i + 1}:\nInput: {_grid_str(p['input'])}\nOutput: {_grid_str(p['output'])}"
        for i, p in enumerate(task["train"])
    )
    test_input = task["test"][0]["input"]
    expected = task["test"][0]["output"]
    prompt = (
        f"Each grid is a 2D array of integers 0-9 (colors). Study the input->output "
        f"transformation rule from the examples, then apply the SAME rule to the test input.\n\n"
        f"{train_examples}\n\nTest input: {_grid_str(test_input)}\n\n"
        f"Respond with ONLY the output grid as a compact JSON array, e.g. [[1,2],[3,4]]."
    )
    return Case(
        id=task["id"],
        suite_id=suite_id,
        benchmark_family="arc_agi2",
        features=list(_FEATURES),
        tools=[],
        prompt=prompt,
        setup={"expected_grid": expected},
        checks=[{"type": "answer_contains", "text": _grid_str(expected)}],
    )


class ArcAgi2Suite(Suite):
    id: str = "arc_agi2"
    name: str = "ARC-AGI-2 (public evaluation slice)"
    version: str = "v1-curated"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "arcprize/ARC-AGI-2 (public, Apache-2.0, verified live s42); curated 15-task slice of "
        "the 120-task public evaluation set. Frontier-difficulty per benchmark-design.md §12a — "
        "genuinely discriminating in 2026, unlike the more saturated existing math/knowledge "
        "suites. BD2: reported on its own internal panel, not blended into the Madras Index. "
        "BD8: nightly/release-certification tiers only, not smoke/regression (near-0% expected "
        "for the local 3-4B fleet — a structural floor, not a bug)."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        if _SLICE.exists():
            tasks = json.loads(_SLICE.read_text(encoding="utf-8"))
            return [_task_to_case(t, self.id) for t in tasks]

        import httpx

        listing = httpx.get(_EVAL_DIR_API, timeout=30.0)
        listing.raise_for_status()
        filenames = [f["name"] for f in listing.json()]
        tasks: list[dict[str, Any]] = []
        for fname in filenames:
            resp = httpx.get(f"{_RAW_BASE}/{fname}", timeout=30.0)
            resp.raise_for_status()
            task = resp.json()
            tasks.append({"id": fname.replace(".json", ""), **task})
        return [_task_to_case(t, self.id) for t in tasks]
