"""Toolathlon external suite adapter (real multi-tool MCP agent tasks).

Toolathlon evaluates tool-using agents on realistic, multi-step tasks that drive **real tool
servers** (filesystem, github, sheets, calendars, …) with execution-based evaluation. Like
AgentDojo/MCP-Universe it installs into an isolated venv under ``.benchmarks/toolathlon`` and is
driven by subprocess, never imported here.

The lightest run path is the **public eval service** (no local Docker/kind): the in-venv
``eval_client.py run --mode public`` submits tasks to the hosted server and downloads per-task
pass/fail. Its agent LLM is OpenAI-compatible (``--base-url`` + ``--model-name`` + ``--api-key``,
also ``TOOLATHLON_OPENAI_BASE_URL``/``TOOLATHLON_OPENAI_API_KEY``), so the run routes through our
LiteLLM gateway; ``scripts/toolathlon_runner.py`` repoints the model and collects per-task scores.

⚠️ The public service is **rate-limited to 3 eval requests / 180 min / IP / day** — keep
``max_tasks`` small (the default ``task_list_file`` is ``debug_tasks.txt``) and avoid re-running.
Full local mode (Docker + kind) lifts the limit but needs the heavy substrate.

BUILT + registered (discoverable in the proving ground). A live run is gated on the isolated venv;
``run`` raises a clear, actionable error until the venv is present, and surfaces an actionable
RuntimeError if the public service is unreachable. The hermetic test pins metadata + parsing + gate.
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
_TOOLATHLON_DIR = _PROJECT_ROOT / ".benchmarks" / "toolathlon"
_RUNNER = _PROJECT_ROOT / "scripts" / "toolathlon_runner.py"
_SENTINEL = "__TOOLATHLON__"

_FEATURES = ["mcp_tool_use", "tool_selection", "tool_args", "multi_step_reasoning"]
_TOOLS = ["mcp"]
_DEFAULT_TASK_LIST = "./debug_tasks.txt"  # small list — public service is heavily rate-limited.
_TIMEOUT_SECS = float(os.environ.get("MADRAS_TOOLATHLON_TIMEOUT", "7200"))


def _python_exe() -> Path:
    win = _TOOLATHLON_DIR / ".venv" / "Scripts" / "python.exe"
    posix = _TOOLATHLON_DIR / ".venv" / "bin" / "python"
    return win if win.exists() else posix


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    base = settings.litellm_base_url
    key = settings.litellm_master_key or "sk-noauth"
    env["OPENAI_BASE_URL"] = base
    env["OPENAI_API_KEY"] = key
    env["TOOLATHLON_OPENAI_BASE_URL"] = base
    env["TOOLATHLON_OPENAI_API_KEY"] = key
    env["PYTHONUTF8"] = "1"
    return env


def parse_results(by_task: dict[str, float], k: int) -> list[dict[str, Any]]:
    """Normalize Toolathlon ``{task_id: score}`` (execution-based, 0/1) into pg rows."""
    rows: list[dict[str, Any]] = []
    eff_k = max(1, k)
    for task_id, score in by_task.items():
        passed = float(score) >= (1.0 - 1e-6)
        rows.append(
            {
                "scenario_id": f"toolathlon-{task_id}",
                "suite_id": "toolathlon",
                "benchmark_family": "toolathlon",
                "features": list(_FEATURES),
                "k": eff_k,
                "passes": eff_k if passed else 0,
                "pass_rate": 1.0 if passed else 0.0,
            }
        )
    return rows


class ToolathlonSuite(Suite):
    """External Toolathlon suite — multi-tool agent tasks over real MCP servers (exec-scored)."""

    id: str = "toolathlon"
    name: str = "Toolathlon"
    version: str = "0.1.0"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "Toolathlon. Multi-step tool-using agent tasks over real tool/MCP servers, "
        "execution-scored; runs in an isolated venv via subprocess against the public eval "
        "service. The public service is rate-limited (3 eval requests / 180 min / IP / day), so "
        "keep max_tasks small. Live run is venv- + service-gated."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))
    mode: str = "public"  # public hosted eval service (no local Docker); "local" needs kind.
    server_host: str = "47.253.6.47"  # public Toolathlon eval service.
    task_list_file: str = _DEFAULT_TASK_LIST  # relative to the toolathlon dir.
    max_tasks: int = 1  # bound a live run; the public service is heavily rate-limited.

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id="toolathlon",
                suite_id=self.id,
                benchmark_family="toolathlon",
                features=list(_FEATURES),
                tools=list(_TOOLS),
                prompt=(
                    "Toolathlon multi-tool agent tasks over real MCP servers (external, venv-gated)"
                ),
            )
        ]

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        """Run Toolathlon via the isolated venv against the public service.

        Raises FileNotFoundError if the venv is absent and RuntimeError if the public eval
        service is unreachable. The public service is rate-limited (3 req / 180 min / IP / day).
        """
        py = _python_exe()
        if not py.exists():
            raise FileNotFoundError(
                f"Toolathlon venv missing at {py}. Create .benchmarks/toolathlon/.venv and "
                "`uv sync` (or `pip install -e .`), then retry."
            )
        eff_k = k if k is not None else 1
        try:
            proc = subprocess.run(
                [
                    str(py),
                    str(_RUNNER),
                    "--mode",
                    self.mode,
                    "--model",
                    model,
                    "--server-host",
                    self.server_host,
                    "--task-list-file",
                    self.task_list_file,
                    "--max-tasks",
                    str(self.max_tasks),
                ],
                cwd=str(_TOOLATHLON_DIR),
                env=_subprocess_env(),
                check=True,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Toolathlon run timed out after {_TIMEOUT_SECS:.0f}s (lower max_tasks)."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "Toolathlon public eval service unreachable or the run failed "
                f"(exit {exc.returncode}); check connectivity and the daily rate limit "
                f"(3 requests / 180 min / IP / day). Output:\n{exc.stderr or exc.stdout}"
            ) from exc
        _, sep, payload = proc.stdout.partition(_SENTINEL)
        if not sep:
            raise RuntimeError("Toolathlon runner produced no results; check the run output.")
        return parse_results(cast("dict[str, float]", json.loads(payload)), eff_k)
