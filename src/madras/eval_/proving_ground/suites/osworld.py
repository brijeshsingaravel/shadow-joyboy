"""OSWorld external suite adapter (computer-use; runs in WSL2 via Docker+KVM).

OSWorld (xlangai/OSWorld) is the execution-based benchmark for multimodal computer-use agents on a
real Ubuntu desktop: the agent drives GUI apps (LibreOffice, Chrome, GIMP, VS Code, …) via
screenshots + pyautogui, scored by per-task verifiers. The repo lives at ``.benchmarks/osworld``
(Windows path) but **runs inside WSL2** (distro ``OutkastUbuntu2``) because the Docker provider
needs ``/dev/kvm`` — only reachable from the Linux side. A WSL venv lives at
``~/benchmarks/osworld-venv``; the source is read from ``/mnt/o/Madras AI/.benchmarks/osworld``.

Like the other external suites it is never imported here — ``run`` drives
``scripts/osworld_runner.py`` through ``wsl.exe -d OutkastUbuntu2``, which executes the runner under
the osworld-venv python. The runner repoints OSWorld's ``openai`` LLM at our LiteLLM gateway via
``OPENAI_BASE_URL`` / ``OPENAI_API_KEY``; OSWorld only takes the OpenAI code path when the model
name starts with ``gpt``, so the adapter aliases the chosen model to a ``gpt*`` name. ``run``
raises a clear error until the WSL venv + docker/kvm provider are present. The hermetic test pins
metadata + parsing + the WSL/venv gate.
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

_PROJECT_ROOT = Path(__file__).resolve().parents[5]  # the engine root: <vault>/Engineering/
# .benchmarks is the big gitignored cache, at the repo root (= Engineering's parent).
_OSWORLD_DIR = _PROJECT_ROOT.parent / ".benchmarks" / "osworld"
_RUNNER = _PROJECT_ROOT / "scripts" / "osworld_runner.py"
_SENTINEL = "__OSWORLD__"

# OSWorld runs inside WSL2 (Docker+KVM provider). The source is read from /mnt/o/...; the runner
# executes under the osworld-venv python on the Linux side.
_WSL_DISTRO = "OutkastUbuntu2"
_WSL_VENV = "~/benchmarks/osworld-venv/bin/python"
_WSL_OSWORLD_DIR = "/mnt/o/Madras AI/.benchmarks/osworld"
_WSL_RUNNER = "/mnt/o/Madras AI/Engineering/scripts/osworld_runner.py"

_FEATURES = ["computer_use", "gui_grounding", "multi_step_reasoning", "tool_selection"]
_TOOLS = ["computer", "screenshot"]
_TIMEOUT_SECS = float(os.environ.get("MADRAS_OSWORLD_TIMEOUT", "14400"))


def _alias_model(model: str) -> str:
    """OSWorld only takes the OpenAI code path when the model name starts with ``gpt``.

    Real model selection happens at the LiteLLM gateway (OPENAI_BASE_URL); the name only routes
    OSWorld's provider branch, so we alias any non-``gpt`` model to a ``gpt*`` placeholder.
    """
    return model if model.startswith("gpt") else f"gpt-osworld-{model}"


def parse_results(by_task: dict[str, float], k: int) -> list[dict[str, Any]]:
    """Normalize OSWorld ``{task_id: score}`` (execution-based, 0/1) into pg rows."""
    rows: list[dict[str, Any]] = []
    eff_k = max(1, k)
    for task_id, score in by_task.items():
        passed = float(score) >= (1.0 - 1e-6)
        rows.append(
            {
                "scenario_id": f"osworld-{task_id}",
                "suite_id": "osworld",
                "benchmark_family": "osworld",
                "features": list(_FEATURES),
                "k": eff_k,
                "passes": eff_k if passed else 0,
                "pass_rate": 1.0 if passed else 0.0,
            }
        )
    return rows


class OsWorldSuite(Suite):
    """External OSWorld suite — computer-use tasks on a real Ubuntu desktop, execution-scored."""

    id: str = "osworld"
    name: str = "OSWorld"
    version: str = "0.1.0"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "xlangai/OSWorld. Multimodal computer-use agent tasks on a real Ubuntu desktop "
        "(LibreOffice, Chrome, GIMP, VS Code), execution-scored; runs in WSL2 via the "
        "Docker+KVM provider. Live run is WSL-venv- + docker/kvm-gated."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))
    domain: str = "libreoffice_calc"  # OSWorld task domain to run.
    observation_type: str = "screenshot"
    max_steps: int = 15
    max_tasks: int = 1  # bound a live run; 0 = all tasks in the domain.

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id="osworld",
                suite_id=self.id,
                benchmark_family="osworld",
                features=list(_FEATURES),
                tools=list(_TOOLS),
                prompt="OSWorld computer-use tasks on a real Ubuntu desktop (external, WSL-gated)",
            )
        ]

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        """Run OSWorld via the WSL2 osworld-venv. Raises a clear error if WSL/venv is absent."""
        eff_k = k if k is not None else 1
        aliased = _alias_model(model)
        # Build the in-WSL command: cd into the (mounted) source, run the runner under the venv.
        inner = (
            f"cd {_quote(_WSL_OSWORLD_DIR)} && "
            f"{_WSL_VENV} {_quote(_WSL_RUNNER)} "
            f"--domain {_quote(self.domain)} --model {_quote(aliased)} "
            f"--observation-type {_quote(self.observation_type)} "
            f"--max-steps {self.max_steps} --max-tasks {self.max_tasks}"
        )
        cmd = ["wsl.exe", "-d", _WSL_DISTRO, "--", "bash", "-lc", inner]
        try:
            proc = subprocess.run(
                cmd,
                env=_subprocess_env(),
                check=True,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECS,
            )
        except FileNotFoundError as exc:
            # wsl.exe missing entirely.
            raise FileNotFoundError(
                f"WSL ({_WSL_DISTRO}) not available; OSWorld requires WSL2 + the osworld-venv at "
                f"{_WSL_VENV}. Install the distro and venv, then retry."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"OSWorld run timed out after {_TIMEOUT_SECS:.0f}s (lower max_tasks/max_steps)."
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr or ""
            low = stderr.lower()
            if "no such file" in low and "osworld-venv" in low:
                raise FileNotFoundError(
                    f"OSWorld WSL venv missing at {_WSL_VENV} in distro {_WSL_DISTRO}. "
                    "Create ~/benchmarks/osworld-venv and pip install -r requirements.txt, "
                    "then retry."
                ) from exc
            provider_hits = ("kvm", "docker", "provider", "/dev/kvm", "permission denied")
            if any(s in low for s in provider_hits):
                raise RuntimeError(
                    "OSWorld docker/kvm provider unavailable in WSL: ensure Docker is running and "
                    f"/dev/kvm is present in {_WSL_DISTRO}. stderr: {stderr.strip()[-500:]}"
                ) from exc
            raise RuntimeError(
                f"OSWorld run failed (exit {exc.returncode}). stderr: {stderr.strip()[-500:]}"
            ) from exc
        _, sep, payload = proc.stdout.partition(_SENTINEL)
        if not sep:
            raise RuntimeError("OSWorld runner produced no results; check the run output.")
        return parse_results(cast("dict[str, float]", json.loads(payload)), eff_k)


def _quote(value: str) -> str:
    """Single-quote a value for the in-WSL ``bash -lc`` command line."""
    return "'" + value.replace("'", "'\\''") + "'"


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    # WSLENV forwards these vars into the WSL environment so the runner sees them.
    env["OPENAI_BASE_URL"] = settings.litellm_base_url
    env["OPENAI_API_KEY"] = settings.litellm_master_key or "sk-noauth"
    env["WSLENV"] = (env.get("WSLENV", "") + ":OPENAI_BASE_URL:OPENAI_API_KEY").lstrip(":")
    return env
