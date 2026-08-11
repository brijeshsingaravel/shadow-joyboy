"""AgentBench external suite adapter (isolated subprocess; infra-gated).

AgentBench (THUDM/AgentBench) evaluates an LLM-as-agent across 8 distinct
environments (OS/bash, DBBench/SQL, Knowledge-Graph, Card-game, Lateral-thinking,
House-holding/ALFWorld, Web-shopping/WebShop, Web-browsing). Each environment
runs as a Docker-backed task server reached through a controller; the harness
``src.assigner`` drives an HTTP agent against them and writes a per-task
``overall.json``. Its deps + the Docker task servers conflict with the main env,
so it lives in its own clone + venv under ``.benchmarks/agentbench`` and is
driven via subprocess — never imported.

Routing: we write a ``madras-routed`` HTTPAgent config (imports the repo's
``openai-chat.yaml`` template, overriding ``url``/``headers``/``body.model``) that
posts to our LiteLLM proxy, plus a self-contained assignment config whose
``output`` is the fixed ``outputs/madras`` dir. After the assigner finishes we
read ``outputs/madras/madras-routed/<task>/overall.json`` and normalize each
environment's headline score (``overall.acc`` / ``overall.success_rate`` /
``reward``) into one pg row per task.

Live-gated: ``run`` raises a clear error unless the cloned repo, the isolated
venv, and a reachable Docker daemon are present — the operator must stand up the
task controller + servers first (``python -m src.server.task_controller`` +
``task_worker``). Like the SWE-bench / WebArena adapters, the hermetic test pins
the parse + the config writers; a real run is a separate, infra-dependent live
smoke.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import Field

from madras.config import settings
from madras.eval_.proving_ground.suite import Case, Suite

_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_AGENTBENCH_DIR = _PROJECT_ROOT / ".benchmarks" / "agentbench"
_AGENT_NAME = "madras-routed"
_OUTPUT_SUBDIR = "outputs/madras"

_FEATURES = ["tool_selection", "tool_args", "multi_step_reasoning"]
_TOOLS = ["terminal", "browser"]
# Default environments to evaluate (override via MADRAS_AGENTBENCH_TASKS, comma-sep).
_DEFAULT_TASKS = ("dbbench-std", "os-std")
_TIMEOUT_SECS = float(os.environ.get("MADRAS_AGENTBENCH_TIMEOUT", "10800"))


def _python_exe() -> Path:
    win = _AGENTBENCH_DIR / ".venv" / "Scripts" / "python.exe"
    posix = _AGENTBENCH_DIR / ".venv" / "bin" / "python"
    return win if win.exists() else posix


def _tasks() -> list[str]:
    raw = os.environ.get("MADRAS_AGENTBENCH_TASKS", "")
    tasks = [t.strip() for t in raw.split(",") if t.strip()]
    return tasks or list(_DEFAULT_TASKS)


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _task_score(overall: dict[str, Any]) -> float | None:
    """Extract a single 0..1 success score from one AgentBench ``overall.json``.

    AgentBench environments report different headline keys:
    - OS / DBBench / KG: ``{"overall": {"acc": x, "pass": n, "total": m}}``
    - ALFWorld (house-holding): ``{"overall": {"success_rate": x}}``
    - WebShop: ``{"reward": x}``
    """
    sub = (
        cast("dict[str, Any]", overall.get("overall"))
        if isinstance(overall.get("overall"), dict)
        else None
    )
    if sub is not None:
        for key in ("acc", "success_rate", "main", "score"):
            val = sub.get(key)
            if isinstance(val, (int, float)):
                return float(val)
        passed, total = sub.get("pass"), sub.get("total")
        if isinstance(passed, (int, float)) and isinstance(total, (int, float)) and total:
            return float(passed) / float(total)
    for key in ("reward", "acc", "success_rate", "main", "score"):
        val = overall.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return None


def parse_overall(overall_by_task: dict[str, dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """Normalize AgentBench per-task ``overall.json`` dicts into pg rows.

    One row per environment; ``pass_rate`` is the environment's headline score
    (percent scores >1 are rescaled to 0..1).
    """
    rows: list[dict[str, Any]] = []
    eff_k = max(1, k)
    for task, overall in overall_by_task.items():
        score = _task_score(overall)
        if score is None:
            continue
        if score > 1.0:
            score = score / 100.0
        score = max(0.0, min(1.0, score))
        rows.append(
            {
                "scenario_id": f"agentbench-{task}",
                "suite_id": "agentbench",
                "benchmark_family": "agentbench",
                "features": list(_FEATURES),
                "k": eff_k,
                "passes": round(score * eff_k),
                "pass_rate": score,
            }
        )
    return rows


def write_routed_agent_config(agents_dir: Path, model: str) -> Path:
    """Write the ``madras-routed`` HTTPAgent config pointing at our LiteLLM proxy.

    Imports the repo's ``openai-chat.yaml`` template (which supplies the prompter
    + ``return_format``) and overrides ``url`` / ``headers`` / ``body.model`` /
    ``name`` so the agent's calls route to the proxy. Returns the written path.
    """
    base = settings.litellm_base_url.rstrip("/")
    key = settings.litellm_master_key or "sk-noauth"
    cfg = {
        "import": "./openai-chat.yaml",
        "parameters": {
            "name": _AGENT_NAME,
            "url": f"{base}/chat/completions",
            "headers": {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            "body": {"model": model, "temperature": 0, "max_tokens": 512},
        },
    }
    path = agents_dir / "madras_routed.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


def write_assignment_config(assignments_dir: Path, tasks: list[str], concurrency: int) -> Path:
    """Write a self-contained assignment config routing tasks to ``madras-routed``.

    Inlines the definition (so the routed agent file is on the import list) and
    pins ``output`` to the fixed ``outputs/madras`` dir for a predictable parse.
    """
    cfg = {
        "definition": {
            "task": {
                "overwrite": {
                    "module": "src.client.TaskClient",
                    "parameters": {"controller_address": "http://localhost:5000/api"},
                },
                "import": "../tasks/task_assembly.yaml",
            },
            "agent": {
                "import": [
                    "../agents/api_agents.yaml",
                    "../agents/fs_agent.yaml",
                    "../agents/madras_routed.yaml",
                ]
            },
        },
        "concurrency": {
            "task": {t: concurrency for t in tasks},
            "agent": {_AGENT_NAME: concurrency},
        },
        "assignments": [{"agent": [_AGENT_NAME], "task": list(tasks)}],
        "output": _OUTPUT_SUBDIR,
    }
    path = assignments_dir / "madras.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


def _read_overall_dir(output_dir: Path) -> dict[str, dict[str, Any]]:
    """Read ``<output_dir>/<agent>/<task>/overall.json`` into ``{task: overall}``."""
    out: dict[str, dict[str, Any]] = {}
    agent_dir = output_dir / _AGENT_NAME
    if not agent_dir.is_dir():
        return out
    for task_dir in sorted(agent_dir.iterdir()):
        overall_file = task_dir / "overall.json"
        if overall_file.is_file():
            try:
                out[task_dir.name] = json.loads(overall_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
    return out


class AgentBenchSuite(Suite):
    """External AgentBench suite — LLM-as-agent across 8 Docker environments."""

    id: str = "agentbench"
    name: str = "AgentBench"
    version: str = "0.2"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "THUDM/AgentBench — 8 environments (OS/DB/KG/card/lateral/ALFWorld/WebShop/"
        "browse) as Docker task servers. Needs the cloned repo + venv + a running "
        "task controller; operator-provisioned. Live run is heavy."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id="agentbench",
                suite_id=self.id,
                benchmark_family="agentbench",
                features=list(_FEATURES),
                tools=list(_TOOLS),
                prompt="AgentBench multi-environment agent tasks (external, infra-gated)",
            )
        ]

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        """Run the AgentBench assigner on ``model`` and normalize per-task scores.

        Raises a clear, actionable error if the cloned repo, the isolated venv, or
        a reachable Docker daemon is missing — the operator must stand up the task
        controller + servers first.
        """
        py = _python_exe()
        if not py.exists():
            raise FileNotFoundError(
                f"AgentBench venv missing at {py}. Clone THUDM/AgentBench into "
                ".benchmarks/agentbench, create .venv, and `uv pip install -r requirements.txt`."
            )
        if shutil.which("docker") is None:
            raise RuntimeError(
                "Docker not found. AgentBench's task environments are Docker-backed; "
                "start the daemon + `python -m src.server.task_controller` and the task "
                "workers before running."
            )
        eff_k = k if k is not None else 1
        tasks = _tasks()
        write_routed_agent_config(_AGENTBENCH_DIR / "configs" / "agents", model)
        write_assignment_config(_AGENTBENCH_DIR / "configs" / "assignments", tasks, concurrency)
        try:
            subprocess.run(
                [str(py), "-m", "src.assigner", "--config", "configs/assignments/madras.yaml"],
                cwd=str(_AGENTBENCH_DIR),
                env=_subprocess_env(),
                check=True,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"AgentBench run timed out after {_TIMEOUT_SECS:.0f}s "
                "(raise MADRAS_AGENTBENCH_TIMEOUT / fewer tasks)."
            ) from exc
        except subprocess.CalledProcessError as exc:
            # docker CLI on PATH != the task controller + task workers actually running —
            # a reachable daemon binary passes the earlier shutil.which() gate but the
            # assigner subprocess itself still fails if the server side isn't up. Wrap it
            # in the same clean, actionable error the docstring promises instead of
            # leaking the raw CalledProcessError.
            raise RuntimeError(
                "AgentBench assigner failed (exit "
                f"{exc.returncode}): {exc.stderr.strip() if exc.stderr else exc}. "
                "Start `python -m src.server.task_controller` and the task workers first."
            ) from exc
        overall_by_task = _read_overall_dir(_AGENTBENCH_DIR / _OUTPUT_SUBDIR)
        if not overall_by_task:
            raise RuntimeError(
                "AgentBench produced no overall.json; check the task controller + run output."
            )
        return parse_overall(overall_by_task, eff_k)
