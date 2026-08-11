"""ScreenSpot-v2 suite (GUI grounding - computer-use dimension).

ScreenSpot-v2 (OS-Copilot/ScreenSpot-v2, Apache-2.0) - given a screenshot + instruction, the agent
must locate the target UI element (a pixel bbox). Vendored metadata (instruction + bbox + image
reference + element type); scored deterministically by the ``bbox_hit`` check (W0-A4c) - a click
coordinate in the answer must fall in the gold bbox. The screenshot is delivered via the vision
toolset. Vision; held-out gate set. W0-A3/A4.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "screenspot" / "data"
_SLICE = DATA_DIR / "screenspot_slice.json"
_FEATURES = ["gui_grounding", "computer_use", "tool_selection"]
_TOOLS = ["vision", "computer"]


def _bbox(raw: Any) -> list[float]:
    if isinstance(raw, list):
        return [float(v) for v in cast("list[Any]", raw)]
    try:
        parsed = ast.literal_eval(str(raw))
        return (
            [float(v) for v in cast("list[Any] | tuple[Any, ...]", parsed)]
            if isinstance(parsed, (list, tuple))
            else []
        )
    except Exception:
        return []


def _case(row: dict[str, Any], suite_id: str) -> Case:
    instr = str(row.get("instruction", "")).strip()
    bbox = _bbox(row.get("bbox"))
    dtype = str(row.get("data_type", ""))
    img = str(row.get("img_filename", ""))
    prompt = (
        f"Screenshot: {img}. Using vision, locate the UI element for: '{instr}'. "
        "Reply with the click coordinates as (x, y) in pixels."
    )
    checks = [{"type": "bbox_hit", "bbox": bbox}] if len(bbox) == 4 else []
    return Case(
        id=str(row.get("id")),
        suite_id=suite_id,
        benchmark_family="screenspot",
        features=list(_FEATURES),
        tools=list(_TOOLS),
        prompt=prompt,
        setup={"bbox": bbox, "img_filename": img, "data_type": dtype, "split": "heldout"},
        checks=checks,
    )


class ScreenSpotSuite(Suite):
    id: str = "screenspot"
    name: str = "ScreenSpot-v2 (GUI grounding)"
    version: str = "v2"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "OS-Copilot/ScreenSpot-v2 (Apache-2.0); screenshot + instruction -> locate "
        "the UI element. Vision; deterministic bbox_hit scoring. Held-out gate set."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))

    def load_cases(self) -> list[Case]:
        if not _SLICE.exists():
            return []
        return [_case(r, self.id) for r in json.loads(_SLICE.read_text(encoding="utf-8"))]
