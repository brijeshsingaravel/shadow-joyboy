"""METR external suite adapter (Inspect task-bridge; venv- + registry-gated).

METR (METR/inspect-metr-task-bridge) runs METR's autonomy task families as Inspect tasks:
each task is a self-contained Docker image (built + pushed to an OCI registry) that the bridge
loads via ``mtb/bridge``. Because the bridge ships its own pinned deps it installs into an isolated
venv under ``.benchmarks/metr/bridge`` (``uv sync``) and is driven by subprocess, never imported
here. Its ``openai-api`` provider reads ``LITELLM_BASE_URL``/``LITELLM_API_KEY``, so the run routes
through our LiteLLM gateway; ``INSPECT_METR_TASK_BRIDGE_REPOSITORY`` points the bridge at the local
docker registry (``localhost:5000``) where task images live.

BUILT + registered (discoverable in the proving ground). A live run is gated on the bridge venv
(``uv sync`` under ``.benchmarks/metr/bridge``) and a reachable registry with the task image built
+ pushed (``mtb-build --push -r localhost:5000 ./tests/examples/count_odds``) — ``run`` raises a
clear, actionable error until both are present. Scoring mirrors AgentDojo: Inspect ``EvalLog``
samples carry "C"/"I" (or 1/0) scores. The hermetic test pins metadata + parsing + gating.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field

from madras.config import settings
from madras.eval_.proving_ground.suite import Case, Suite

_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_METR_DIR = _PROJECT_ROOT / ".benchmarks" / "metr"
_BRIDGE_DIR = _METR_DIR / "bridge"
_RUNNER = _PROJECT_ROOT / "scripts" / "metr_runner.py"
_SENTINEL = "__METR__"

_FEATURES = ["autonomy", "multi_step_reasoning", "tool_selection", "code_execution"]
_TOOLS = ["shell", "file"]
_TIMEOUT_SECS = float(os.environ.get("MADRAS_METR_TIMEOUT", "7200"))


def _python_exe() -> Path:
    win = _BRIDGE_DIR / ".venv" / "Scripts" / "python.exe"
    posix = _BRIDGE_DIR / ".venv" / "bin" / "python"
    return win if win.exists() else posix


def _subprocess_env(registry: str) -> dict[str, str]:
    env = dict(os.environ)
    env["LITELLM_BASE_URL"] = settings.litellm_base_url
    env["LITELLM_API_KEY"] = settings.litellm_master_key or "sk-noauth"
    env["INSPECT_METR_TASK_BRIDGE_REPOSITORY"] = registry
    env["PYTHONUTF8"] = "1"
    return env


def parse_results(by_task: dict[str, float], k: int) -> list[dict[str, Any]]:
    """Normalize METR ``{sample_id: score}`` (Inspect "C"/"I" -> 0/1) into pg rows."""
    rows: list[dict[str, Any]] = []
    eff_k = max(1, k)
    for sample_id, score in by_task.items():
        passed = float(score) >= (1.0 - 1e-6)
        rows.append(
            {
                "scenario_id": f"metr-{sample_id}",
                "suite_id": "metr",
                "benchmark_family": "metr",
                "features": list(_FEATURES),
                "k": eff_k,
                "passes": eff_k if passed else 0,
                "pass_rate": 1.0 if passed else 0.0,
            }
        )
    return rows


class MetrSuite(Suite):
    """External METR suite — autonomy task families via the Inspect task-bridge, image-scored."""

    id: str = "metr"
    name: str = "METR"
    version: str = "0.1.0"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "METR/inspect-metr-task-bridge. METR autonomy task families as Inspect tasks; each task "
        "is a self-contained Docker image scored via mtb/bridge. Runs in an isolated bridge venv "
        "via subprocess. Live run is venv- + registry-gated."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))
    image_tag: str = "count_odds-0.0.1"  # task image (built + pushed to the registry)
    sample_id: str = "hard"  # which sample within the task family
    registry: str = "localhost:5000"  # OCI registry holding the task image

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id="metr",
                suite_id=self.id,
                benchmark_family="metr",
                features=list(_FEATURES),
                tools=list(_TOOLS),
                prompt="METR autonomy tasks via the Inspect task-bridge (external, venv-/registry-gated)",  # noqa: E501
            )
        ]

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        """Run METR via the isolated bridge venv. Raises a clear error if the venv is absent."""
        py = _python_exe()
        if not py.exists():
            raise FileNotFoundError(
                f"METR bridge venv missing at {py}. Clone METR/inspect-metr-task-bridge into "
                f"{_BRIDGE_DIR} and `uv sync`, then retry."
            )
        eff_k = k if k is not None else 1
        try:
            proc = subprocess.run(
                [
                    str(py),
                    str(_RUNNER),
                    "--image-tag",
                    self.image_tag,
                    "--sample-id",
                    self.sample_id,
                    "--model",
                    model,
                ],
                cwd=str(_BRIDGE_DIR),
                env=_subprocess_env(self.registry),
                check=True,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"METR run timed out after {_TIMEOUT_SECS:.0f}s.") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"METR run failed (registry/image unavailable?). Ensure {self.registry} is "
                f"reachable and `{self.image_tag}` is built + pushed "
                f"(`mtb-build --push -r {self.registry} ./tests/examples/count_odds`).\n"
                f"{(exc.stderr or exc.stdout or '').strip()[-2000:]}"
            ) from exc
        _, sep, payload = proc.stdout.partition(_SENTINEL)
        if not sep:
            raise RuntimeError("METR runner produced no results; check the run output.")
        return parse_results(cast("dict[str, float]", json.loads(payload)), eff_k)
