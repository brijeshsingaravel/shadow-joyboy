"""MCPToolBench++ external suite adapter (real MCP-marketplace tool-call tasks).

MCPToolBench++ (aiagenta2z / MCPToolBench++) evaluates tool-using agents on tool-call tasks across
real MCP-marketplace servers grouped by category (browser, search, map, pay, finance, file_system),
scored on tool/parameter correctness. Like AgentDojo/MCP-Universe it installs into an isolated venv
under ``.benchmarks/mcptoolbench`` and is driven by subprocess, never imported here. Its built-in
``CustomOpenAIAPIProvider`` reads ``CUSTOM_OPENAI_BASE_URL``/``CUSTOM_OPENAI_API_KEY``, so any
``--model`` routes through our LiteLLM gateway; ``scripts/mcptoolbench_runner.py`` drives ``run.py``
and collects per-task pass scores.

BUILT + registered (discoverable in the proving ground). A live run is gated on the isolated venv
and the MCP marketplace server (``uvicorn src.app:app --port 5000``) — ``run`` raises a clear,
actionable error until the venv is present and the server is reachable. The hermetic test pins
metadata + parsing + gating.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field

from madras.config import settings
from madras.eval_.proving_ground.suite import Case, Suite

_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_MCPTB_DIR = _PROJECT_ROOT / ".benchmarks" / "mcptoolbench"
_RUNNER = _PROJECT_ROOT / "scripts" / "mcptoolbench_runner.py"
_SENTINEL = "__MCPTOOLBENCH__"

_FEATURES = ["mcp_tool_use", "tool_selection", "tool_args"]
_TOOLS = ["mcp"]
_DEFAULT_CATEGORY = "browser"
_DEFAULT_INPUT_FILE = "data/browser/browser_single_demo.json"  # browser demo (no extra keys)
_MARKETPLACE_HOST = "127.0.0.1"
_MARKETPLACE_PORT = 5000
_TIMEOUT_SECS = float(os.environ.get("MADRAS_MCPTOOLBENCH_TIMEOUT", "7200"))


def _python_exe() -> Path:
    win = _MCPTB_DIR / ".venv" / "Scripts" / "python.exe"
    posix = _MCPTB_DIR / ".venv" / "bin" / "python"
    return win if win.exists() else posix


def _marketplace_up(host: str = _MARKETPLACE_HOST, port: int = _MARKETPLACE_PORT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return True
    except OSError:
        return False


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["CUSTOM_OPENAI_BASE_URL"] = settings.litellm_base_url
    env["CUSTOM_OPENAI_API_KEY"] = settings.litellm_master_key or "sk-noauth"
    env["PYTHONUTF8"] = "1"
    return env


def parse_results(by_task: dict[str, float], k: int) -> list[dict[str, Any]]:
    """Normalize MCPToolBench++ ``{task_id: score}`` (tool-call pass, 0/1) into pg rows."""
    rows: list[dict[str, Any]] = []
    eff_k = max(1, k)
    for task_id, score in by_task.items():
        passed = float(score) >= (1.0 - 1e-6)
        rows.append(
            {
                "scenario_id": f"mcptoolbench-{task_id}",
                "suite_id": "mcptoolbench",
                "benchmark_family": "mcptoolbench",
                "features": list(_FEATURES),
                "k": eff_k,
                "passes": eff_k if passed else 0,
                "pass_rate": 1.0 if passed else 0.0,
            }
        )
    return rows


class McpToolBenchSuite(Suite):
    """External MCPToolBench++ suite — tool-call tasks over real MCP-marketplace servers."""

    id: str = "mcptoolbench"
    name: str = "MCPToolBench++"
    version: str = "0.1.0"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "aiagenta2z/MCPToolBench++. Tool-call tasks over real MCP-marketplace servers "
        "(browser, search, map, pay, finance, file_system), scored on tool/parameter "
        "correctness; runs in an isolated venv via subprocess. Live run is venv- + "
        "marketplace-server-gated."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))
    category: str = _DEFAULT_CATEGORY  # browser/search/map/pay/finance/file_system
    input_file: str = _DEFAULT_INPUT_FILE  # data file (relative to the mcptoolbench repo)
    max_tasks: int = 1  # bound a live run; 0 = all tasks in the input file.

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id="mcptoolbench",
                suite_id=self.id,
                benchmark_family="mcptoolbench",
                features=list(_FEATURES),
                tools=list(_TOOLS),
                prompt=(
                    "MCPToolBench++ tool-call tasks over real MCP servers (external, venv-gated)"
                ),
            )
        ]

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        """Run MCPToolBench++ via the isolated venv. Raises a clear error if the venv is absent."""
        py = _python_exe()
        if not py.exists():
            raise FileNotFoundError(
                f"MCPToolBench++ venv missing at {py}. Create .benchmarks/mcptoolbench/.venv and "
                "install its requirements, then retry."
            )
        if not _marketplace_up():
            raise RuntimeError(
                f"MCPToolBench++ marketplace server unreachable at "
                f"{_MARKETPLACE_HOST}:{_MARKETPLACE_PORT}. Start it with `cd "
                ".benchmarks/mcptoolbench/mcp/mcp-marketplace/app/mcp_tool_use && "
                "uvicorn src.app:app --port 5000`, then retry."
            )
        eff_k = k if k is not None else 1
        try:
            proc = subprocess.run(
                [
                    str(py),
                    str(_RUNNER),
                    "--category",
                    self.category,
                    "--input-file",
                    self.input_file,
                    "--model",
                    model,
                    "--max-tasks",
                    str(self.max_tasks),
                ],
                cwd=str(_MCPTB_DIR),
                env=_subprocess_env(),
                check=True,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"MCPToolBench++ run timed out after {_TIMEOUT_SECS:.0f}s (lower max_tasks)."
            ) from exc
        _, sep, payload = proc.stdout.partition(_SENTINEL)
        if not sep:
            raise RuntimeError("MCPToolBench++ runner produced no results; check the run output.")
        return parse_results(cast("dict[str, float]", json.loads(payload)), eff_k)
