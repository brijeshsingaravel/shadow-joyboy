"""mcp-atlas external suite adapter (real MCP-server tool-use, CSV-scored).

mcp-atlas (scaleapi/mcp-atlas) evaluates tool-using agents on realistic multi-step MCP tasks
that drive real MCP servers (wikipedia, osm, pubmed, github, e2b, …) inside a containerized
agent environment. Like MCP-Universe/AgentDojo it lives in an isolated tree under
``.benchmarks/mcpatlas`` with its own uv project (``services/mcp_eval``) and is driven by
subprocess, never imported here. Its driver reads ``LLM_BASE_URL``/``LLM_API_KEY`` and takes the
model as ``openai/<model>``, so the run routes through our LiteLLM gateway;
``scripts/mcpatlas_runner.py`` repoints the model and collects per-task pass/fail scores.

BUILT + registered (discoverable in the proving ground). A live run is gated on the
``services/mcp_eval`` uv venv being synced AND the env container (``make run-docker``) serving on
``:1984`` — ``run`` raises a clear, actionable error until both are present. The hermetic test
pins metadata + parsing + gating.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field

from madras.config import settings
from madras.eval_.proving_ground.suite import Case, Suite

_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_MCPATLAS_DIR = _PROJECT_ROOT / ".benchmarks" / "mcpatlas"
_EVAL_DIR = _MCPATLAS_DIR / "services" / "mcp_eval"
_RUNNER = _PROJECT_ROOT / "scripts" / "mcpatlas_runner.py"
_SENTINEL = "__MCPATLAS__"
_ENV_URL = "http://localhost:1984"

_FEATURES = ["mcp_tool_use", "tool_selection", "tool_args", "multi_step_reasoning"]
_TOOLS = ["mcp"]
_DEFAULT_CONFIG = "sample_tasks.csv"  # ships with the eval driver; no extra dataset download
_TIMEOUT_SECS = float(os.environ.get("MADRAS_MCPATLAS_TIMEOUT", "7200"))


def _python_exe() -> Path:
    win = _EVAL_DIR / ".venv" / "Scripts" / "python.exe"
    posix = _EVAL_DIR / ".venv" / "bin" / "python"
    return win if win.exists() else posix


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["LLM_BASE_URL"] = settings.litellm_base_url
    env["LLM_API_KEY"] = settings.litellm_master_key or "sk-noauth"
    env["PYTHONUTF8"] = "1"
    return env


def _env_container_up() -> bool:
    """True iff the agent-environment container answers on :1984."""
    try:
        with urllib.request.urlopen(_ENV_URL, timeout=3):  # fixed localhost url
            return True
    except urllib.error.HTTPError:
        return True  # reachable; any HTTP status means the port is serving
    except OSError:
        return False


def parse_results(by_task: dict[str, float], k: int) -> list[dict[str, Any]]:
    """Normalize mcp-atlas ``{task_id: score}`` (per-task pass/fail, 0/1) into pg rows."""
    rows: list[dict[str, Any]] = []
    eff_k = max(1, k)
    for task_id, score in by_task.items():
        passed = float(score) >= (1.0 - 1e-6)
        rows.append(
            {
                "scenario_id": f"mcpatlas-{task_id}",
                "suite_id": "mcpatlas",
                "benchmark_family": "mcpatlas",
                "features": list(_FEATURES),
                "k": eff_k,
                "passes": eff_k if passed else 0,
                "pass_rate": 1.0 if passed else 0.0,
            }
        )
    return rows


class McpAtlasSuite(Suite):
    """External mcp-atlas suite — agent tasks over real MCP servers, CSV pass/fail-scored."""

    id: str = "mcpatlas"
    name: str = "mcp-atlas"
    version: str = "1.2.5"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "scaleapi/mcp-atlas. Tool-using agent tasks over real MCP servers (wikipedia, osm, "
        "pubmed, github, e2b), driven via a containerized env (:1984) + an isolated uv venv. "
        "Live run is venv- + container-gated."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))
    config: str = _DEFAULT_CONFIG  # input task CSV (relative to services/mcp_eval/)
    num_tasks: int = 1  # bound a live run; 0 = all tasks in the CSV.

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id="mcpatlas",
                suite_id=self.id,
                benchmark_family="mcpatlas",
                features=list(_FEATURES),
                tools=list(_TOOLS),
                prompt="mcp-atlas agent tasks over real MCP servers (external, venv-gated)",
            )
        ]

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        """Run mcp-atlas via the isolated uv venv. Raises a clear error if venv/container absent."""
        py = _python_exe()
        if not py.exists():
            raise FileNotFoundError(
                f"mcp-atlas venv missing at {py}. In .benchmarks/mcpatlas/services/mcp_eval run "
                "`uv sync`, then retry."
            )
        if not _env_container_up():
            raise RuntimeError(
                f"mcp-atlas env container not reachable at {_ENV_URL}. Run `make run-docker` in "
                ".benchmarks/mcpatlas (serves :1984), then retry."
            )
        eff_k = k if k is not None else 1
        try:
            proc = subprocess.run(
                [
                    str(py),
                    str(_RUNNER),
                    "--config",
                    self.config,
                    "--model",
                    model,
                    "--num-tasks",
                    str(self.num_tasks),
                ],
                cwd=str(_EVAL_DIR),
                env=_subprocess_env(),
                check=True,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"mcp-atlas run timed out after {_TIMEOUT_SECS:.0f}s (lower num_tasks)."
            ) from exc
        _, sep, payload = proc.stdout.partition(_SENTINEL)
        if not sep:
            raise RuntimeError("mcp-atlas runner produced no results; check the run output.")
        return parse_results(cast("dict[str, float]", json.loads(payload)), eff_k)
