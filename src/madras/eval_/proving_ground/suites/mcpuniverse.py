"""MCP-Universe external suite adapter (real MCP-server agent tasks).

MCP-Universe (SalesforceAIResearch/MCP-Universe) evaluates tool-using agents on realistic tasks
that drive **real MCP servers** (yfinance, calculator, google-maps, github, notion, browser, 3D
design, …) with execution-based evaluation. Like AgentDojo/SWE-bench it installs into an isolated
venv under ``.benchmarks/mcp-universe`` and is driven by subprocess, never imported here. Its
``openai``-type LLM reads ``OPENAI_BASE_URL``/``OPENAI_API_KEY``, so the run routes through our
LiteLLM gateway; ``scripts/mcpuniverse_runner.py`` repoints the model and collects per-task scores.

BUILT + registered (discoverable in the proving ground). A live run is gated on the isolated venv
(``pip install -r requirements.txt``) and the per-server credentials — ``run`` raises a clear,
actionable error until the venv is present. The hermetic test pins metadata + parsing + gating.
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
_MCPU_DIR = _PROJECT_ROOT / ".benchmarks" / "mcp-universe"
_RUNNER = _PROJECT_ROOT / "scripts" / "mcpuniverse_runner.py"
_SENTINEL = "__MCPUNIVERSE__"

_FEATURES = ["mcp_tool_use", "tool_selection", "tool_args", "multi_step_reasoning"]
_TOOLS = ["mcp"]
_DEFAULT_CONFIG = "mcpuniverse/financial_analysis.yaml"  # yfinance + calculator (no extra keys)
_TIMEOUT_SECS = float(os.environ.get("MADRAS_MCPUNIVERSE_TIMEOUT", "7200"))


def _python_exe() -> Path:
    win = _MCPU_DIR / ".venv" / "Scripts" / "python.exe"
    posix = _MCPU_DIR / ".venv" / "bin" / "python"
    return win if win.exists() else posix


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["OPENAI_BASE_URL"] = settings.litellm_base_url
    env["OPENAI_API_KEY"] = settings.litellm_master_key or "sk-noauth"
    env["PYTHONUTF8"] = "1"
    return env


def parse_results(by_task: dict[str, float], k: int) -> list[dict[str, Any]]:
    """Normalize MCP-Universe ``{task_path: score}`` (execution-based, 0/1) into pg rows."""
    rows: list[dict[str, Any]] = []
    eff_k = max(1, k)
    for task_path, score in by_task.items():
        passed = float(score) >= (1.0 - 1e-6)
        rows.append(
            {
                "scenario_id": f"mcpuniverse-{Path(task_path).stem}",
                "suite_id": "mcpuniverse",
                "benchmark_family": "mcpuniverse",
                "features": list(_FEATURES),
                "k": eff_k,
                "passes": eff_k if passed else 0,
                "pass_rate": 1.0 if passed else 0.0,
            }
        )
    return rows


class McpUniverseSuite(Suite):
    """External MCP-Universe suite — agent tasks over real MCP servers, execution-scored."""

    id: str = "mcpuniverse"
    name: str = "MCP-Universe"
    version: str = "0.1.0"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "SalesforceAIResearch/MCP-Universe. Tool-using agent tasks over real MCP servers "
        "(yfinance, maps, github, notion, browser, 3D), execution-scored; runs in an isolated "
        "venv via subprocess. Live run is venv- + credential-gated."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))
    config: str = _DEFAULT_CONFIG  # benchmark config (relative to mcp-universe configs/)
    max_tasks: int = 1  # bound a live run; 0 = all tasks in the config.

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id="mcpuniverse",
                suite_id=self.id,
                benchmark_family="mcpuniverse",
                features=list(_FEATURES),
                tools=list(_TOOLS),
                prompt="MCP-Universe agent tasks over real MCP servers (external, venv-gated)",
            )
        ]

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        """Run MCP-Universe via the isolated venv. Raises a clear error if the venv is absent."""
        py = _python_exe()
        if not py.exists():
            raise FileNotFoundError(
                f"MCP-Universe venv missing at {py}. Create .benchmarks/mcp-universe/.venv and "
                "`pip install -r requirements.txt`, then retry."
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
                    "--max-tasks",
                    str(self.max_tasks),
                ],
                cwd=str(_MCPU_DIR),
                env=_subprocess_env(),
                check=True,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"MCP-Universe run timed out after {_TIMEOUT_SECS:.0f}s (lower max_tasks)."
            ) from exc
        _, sep, payload = proc.stdout.partition(_SENTINEL)
        if not sep:
            raise RuntimeError("MCP-Universe runner produced no results; check the run output.")
        return parse_results(cast("dict[str, float]", json.loads(payload)), eff_k)
