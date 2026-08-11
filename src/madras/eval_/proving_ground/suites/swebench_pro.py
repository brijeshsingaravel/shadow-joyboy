"""SWE-bench Pro suite (public) — benchmark-design.md §12a, replaces the older SWE-bench
Verified as the roster's primary coding-agent gate.

``ScaleAI/SWE-bench_Pro`` (verified live s42) is the contamination-resistant successor to
SWE-bench Verified: 1865 human-verified problems split public (731, HF-hosted)/commercial
(276, private — proprietary startup repos)/held-out (858, private — mirrors the public set on
separate repos specifically to catch overfitting, per Scale's own anti-contamination design).
This suite uses the public 731-instance split.

**Provision-later, matching the existing `_EnvHarness` pattern (`env_harnesses.py`) this
codebase already uses for the 8 heavy live-substrate suites** — deliberately, not a shortcut:
Scale ships its own eval harness (`scaleapi/SWE-bench_Pro-os`, not princeton-nlp's
`swebench.harness` package the existing `SweBenchSuite` already wires through WSL+Docker), with
per-instance prebuilt Docker images (`dockerhub_tag`, confirmed present per-row). Wiring that
harness is a genuine new infra-provisioning task, not a code tweak — registering the suite now
with real vendored metadata (not a single generic placeholder) is the honest, right-sized v1;
the live Docker-eval wiring is a scoped follow-on, not built in this pass.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "swebench_pro" / "data"
_SLICE = DATA_DIR / "swebench_pro_slice.json"

_FEATURES = ["code_editing", "multi_step_reasoning", "tool_args"]
_TOOLS = ["shell", "file_edit"]


def _load_slice() -> list[dict[str, Any]]:
    return json.loads(_SLICE.read_text(encoding="utf-8"))


class SweBenchProSuite(Suite):
    """SWE-bench Pro — registered with real per-instance metadata; live Docker eval via
    Scale's own harness is provision-later (matches the `_EnvHarness` convention)."""

    id: str = "swebench_pro"
    name: str = "SWE-bench Pro (public split)"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "ScaleAI/SWE-bench_Pro (public HF split, verified live s42) — the contamination-"
        "resistant successor to SWE-bench Verified (1865 total: 731 public/276 commercial-"
        "private/858 held-out-private). Replaces SWE-bench Verified as the primary gate per "
        "benchmark-design.md §12a. Live Docker eval via Scale's own harness "
        "(scaleapi/SWE-bench_Pro-os) is provision-later — a genuine new infra task, not yet "
        "wired; this suite registers real per-instance metadata for coverage now."
    )
    substrate: str = "Docker (Scale's own SWE-bench_Pro-os harness, per-instance prebuilt images)"
    repo: str = "github.com/scaleapi/SWE-bench_Pro-os"
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))

    def load_cases(self) -> list[Case]:
        rows = _load_slice()
        return [
            Case(
                id=f"swebench_pro-{r['instance_id']}",
                suite_id=self.id,
                benchmark_family=self.id,
                features=list(_FEATURES),
                tools=list(_TOOLS),
                prompt=(
                    f"swebench_pro {r['instance_id']} ({r['repo']}, {r['repo_language']}) "
                    f"— external Docker task (provision-later, needs {self.substrate})"
                ),
                setup={"dockerhub_tag": r["dockerhub_tag"], "repo": r["repo"]},
            )
            for r in rows
        ]

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        del model, k, concurrency
        raise RuntimeError(
            f"{self.id} live run needs its substrate ({self.substrate}); provision it "
            f"(clone {self.repo}, stand up the harness against the prebuilt per-instance "
            "Docker images), then retry."
        )
