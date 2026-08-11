"""AgentDojo external suite adapter (Inspect-based; dependency-gated) — W5·F3.

AgentDojo (ukgovernmentbeis/inspect_evals · originally ethz-spylab/agentdojo) is the dynamic
prompt-injection robustness benchmark for tool-using agents: realistic tasks over UNTRUSTED
tool data, scored on utility + attack-resistance. It runs via Inspect, whose deps conflict
with the main env, so — like WebArena/SWE-bench — it installs into an isolated venv under
``.benchmarks/agentdojo`` and is driven by subprocess, never imported here.

Integration is BUILT + registered (discoverable in the proving ground); a real run is gated on
the isolated venv (`uv pip install inspect_evals[agentdojo]`) — ``run`` raises a clear,
actionable error until it's present. The hermetic test pins the metadata + the gating.
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
_AGENTDOJO_DIR = _PROJECT_ROOT / ".benchmarks" / "agentdojo"
_RUNNER = _PROJECT_ROOT / "scripts" / "agentdojo_runner.py"

_FEATURES = ["prompt_injection_robustness", "tool_selection", "tool_args", "untrusted_data"]
_TOOLS = ["web", "file", "messaging"]
_TIMEOUT_SECS = float(os.environ.get("MADRAS_AGENTDOJO_TIMEOUT", "7200"))


def _python_exe() -> Path:
    win = _AGENTDOJO_DIR / ".venv" / "Scripts" / "python.exe"
    posix = _AGENTDOJO_DIR / ".venv" / "bin" / "python"
    return win if win.exists() else posix


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["OPENAI_BASE_URL"] = settings.litellm_base_url
    env["OPENAI_API_KEY"] = settings.litellm_master_key or "sk-noauth"
    env["PYTHONUTF8"] = "1"
    return env


def parse_results(by_task: dict[str, float], k: int) -> list[dict[str, Any]]:
    """Normalize AgentDojo ``{task_id: score}`` (utility-under-attack, 0/1) into pg rows."""
    rows: list[dict[str, Any]] = []
    eff_k = max(1, k)
    for task_id, score in by_task.items():
        passed = float(score) >= (1.0 - 1e-6)
        rows.append(
            {
                "scenario_id": f"agentdojo-{task_id}",
                "suite_id": "agentdojo",
                "benchmark_family": "agentdojo",
                "features": list(_FEATURES),
                "k": eff_k,
                "passes": eff_k if passed else 0,
                "pass_rate": 1.0 if passed else 0.0,
            }
        )
    return rows


class AgentDojoSuite(Suite):
    """External AgentDojo suite — prompt-injection robustness over untrusted tool data."""

    id: str = "agentdojo"
    name: str = "AgentDojo"
    version: str = "0.1.34"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "ukgovernmentbeis/inspect_evals (agentdojo). Prompt-injection robustness for "
        "tool-using agents; runs via Inspect in an isolated venv. Live run is dep-gated."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))
    max_tasks: int = 0  # 0 = all; set to bound a live run.

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id="agentdojo",
                suite_id=self.id,
                benchmark_family="agentdojo",
                features=list(_FEATURES),
                tools=list(_TOOLS),
                prompt="AgentDojo prompt-injection robustness tasks (external, dep-gated)",
            )
        ]

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        """Run AgentDojo via the isolated Inspect venv. Raises a clear error if absent."""
        py = _python_exe()
        if not py.exists():
            raise FileNotFoundError(
                f"AgentDojo venv missing at {py}. Create .benchmarks/agentdojo/.venv and "
                "`uv pip install inspect_evals[agentdojo]`, then retry."
            )
        eff_k = k if k is not None else 1
        try:
            proc = subprocess.run(
                [str(py), str(_RUNNER), "--model", model, "--max-tasks", str(self.max_tasks)],
                cwd=str(_AGENTDOJO_DIR),
                env=_subprocess_env(),
                check=True,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"AgentDojo run timed out after {_TIMEOUT_SECS:.0f}s (lower max_tasks)."
            ) from exc
        _, sep, payload = proc.stdout.partition("__AGENTDOJO__")
        if not sep:
            raise RuntimeError("AgentDojo runner produced no results; check the run output.")
        return parse_results(cast("dict[str, float]", json.loads(payload)), eff_k)
