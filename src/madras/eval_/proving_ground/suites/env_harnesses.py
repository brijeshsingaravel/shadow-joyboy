"""Environment-harness external suites (the 8 outlier benchmarks that need a live substrate).

WebVoyager, AndroidWorld, RCAEval, OpenRCA, MARBLE, Collab-Overcooked, ASB, WebWalkerQA - each needs
a live environment (browser / Android emulator / telemetry / multi-agent sim) like swebench/osworld.
They are **built + registered here as external suites** (discoverable, scored through the same
pipeline); the clone+venv install and live runs **provision in W2**. Until then ``run`` raises a
clear error and ``load_cases`` returns one descriptor case. License-clean (MIT/Apache). W0-A3.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.config import settings
from madras.eval_.proving_ground.suite import Case, Suite

_PROJECT_ROOT = Path(__file__).resolve().parents[5]  # actually the Engineering root, not repo root
_ENGINE_ROOT = Path(__file__).resolve().parents[5]  # .../Engineering (same as _PROJECT_ROOT above)
_REPO_ROOT = Path(__file__).resolve().parents[6]  # the real repo root, one level above Engineering

# RCAEval runs inside WSL2 (RE1's exact Python-3.12 / old-pinned-dep venv doesn't fit the host
# env). Same WSL-bridge pattern as osworld/agentbench.
_WSL_DISTRO = "OutkastUbuntu2"
_WSL_VENV = "~/benchmarks/rcaeval-venv/bin/python"
_WSL_RCAEVAL_DIR = "/mnt/o/Madras AI/.benchmarks/rcaeval"
_WSL_RUNNER = "/mnt/o/Madras AI/Engineering/scripts/rcaeval_runner.py"
_RCAEVAL_SENTINEL = "__RCAEVAL__"
_RCAEVAL_TIMEOUT_SECS = float(os.environ.get("MADRAS_RCAEVAL_TIMEOUT", "3600"))


def _quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def parse_rcaeval_results(by_case: dict[str, dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """Normalize the runner's ``{case_id: {true_service, predicted_service, correct}}``
    into pg rows."""
    eff_k = max(1, k)
    rows: list[dict[str, Any]] = []
    for case_id, result in by_case.items():
        correct = bool(result.get("correct", False))
        rows.append(
            {
                "scenario_id": f"rcaeval-{case_id}",
                "suite_id": "rcaeval",
                "benchmark_family": "rcaeval",
                "features": ["root_cause_analysis"],
                "k": eff_k,
                "passes": eff_k if correct else 0,
                "pass_rate": 1.0 if correct else 0.0,
            }
        )
    return rows


class _EnvHarness(Suite):
    """Base for substrate-gated env-harness suites (live install/runs ride W2)."""

    kind: Literal["external", "native", "dataset"] = "external"
    substrate: str = ""  # substrate requirement (browser / emulator / telemetry / sim)
    repo: str = ""  # upstream repo (cloned under .benchmarks/ in W2)

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id=self.id,
                suite_id=self.id,
                benchmark_family=self.id,
                features=list(self.features),
                tools=list(self.tools),
                prompt=f"{self.name} tasks (external; needs {self.substrate}; W2)",
            )
        ]

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        venv = _PROJECT_ROOT / ".benchmarks" / self.id
        raise RuntimeError(
            f"{self.id} live run needs its substrate ({self.substrate}); provision it in W2 "
            f"(clone {self.repo} -> {venv}, stand up the environment), then retry."
        )


_WEBVOYAGER_DIR = _REPO_ROOT / ".benchmarks" / "webvoyager"
_WEBVOYAGER_RUNNER = _ENGINE_ROOT / "scripts" / "webvoyager_runner.py"
_WEBVOYAGER_TIMEOUT_SECS = float(os.environ.get("MADRAS_WEBVOYAGER_TIMEOUT", "1900"))


def _webvoyager_python() -> Path:
    win = _WEBVOYAGER_DIR / ".venv" / "Scripts" / "python.exe"
    posix = _WEBVOYAGER_DIR / ".venv" / "bin" / "python"
    return win if win.exists() else posix


def parse_webvoyager_results(by_task: dict[str, int | None], k: int) -> list[dict[str, Any]]:
    """Normalize the runner's ``{task_id: 1|0|None}`` (WebVoyager's own auto-eval verdict;
    None = judge couldn't decide, treated as a fail) into pg rows."""
    eff_k = max(1, k)
    rows: list[dict[str, Any]] = []
    for task_id, verdict in by_task.items():
        passed = verdict == 1
        rows.append(
            {
                "scenario_id": f"webvoyager-{task_id}",
                "suite_id": "webvoyager",
                "benchmark_family": "webvoyager",
                "features": ["web_browsing", "computer_use", "multi_step_reasoning"],
                "k": eff_k,
                "passes": eff_k if passed else 0,
                "pass_rate": 1.0 if passed else 0.0,
            }
        )
    return rows


class WebVoyagerSuite(_EnvHarness):
    id: str = "webvoyager"
    name: str = "WebVoyager"
    version: str = "v1"
    provenance: str = (
        "MinorJerry/WebVoyager (Apache-2.0); ~643 live-web agent tasks. RE1-style wiring s37: "
        "native Windows venv (Selenium + system Chrome, no WSL needed), routed at a "
        "vision-capable model via OPENAI_BASE_URL, scored by WebVoyager's own GPT-4V-style "
        "judge (called directly, not GPT-4V specifically)."
    )
    substrate: str = "a live web browser"
    repo: str = "github.com/MinorJerry/WebVoyager"
    features: list[str] = Field(
        default_factory=lambda: ["web_browsing", "computer_use", "multi_step_reasoning"]
    )
    tools: list[str] = Field(default_factory=lambda: ["web", "browser"])
    limit: int = 2  # cap total tasks per run; the full suite is ~643.

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id=self.id,
                suite_id=self.id,
                benchmark_family=self.id,
                features=list(self.features),
                tools=list(self.tools),
                prompt="WebVoyager live-web multimodal agent tasks (external, venv-gated)",
            )
        ]

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        """Run WebVoyager's own agent loop natively (Windows, Selenium + system Chrome)."""
        eff_k = k if k is not None else 1
        py = _webvoyager_python()
        if not py.exists():
            raise FileNotFoundError(
                f"WebVoyager venv missing at {py}. Create .venv under {_WEBVOYAGER_DIR} and "
                "`uv pip install -r requirements.txt`, then retry."
            )
        env = dict(os.environ)
        env["OPENAI_BASE_URL"] = settings.ollama_url.rstrip("/") + "/v1"
        env["OPENAI_API_KEY"] = "ollama"
        cmd = [str(py), str(_WEBVOYAGER_RUNNER), "--model", model, "--limit", str(self.limit)]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(_WEBVOYAGER_DIR),
                env=env,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_WEBVOYAGER_TIMEOUT_SECS,
            )
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"WebVoyager venv python missing: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"WebVoyager run timed out after {_WEBVOYAGER_TIMEOUT_SECS:.0f}s (lower `limit`)."
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr or ""
            raise RuntimeError(
                f"WebVoyager run failed (exit {exc.returncode}). stderr: {stderr.strip()[-500:]}"
            ) from exc
        _, sep, payload = proc.stdout.partition("__WEBVOYAGER__")
        if not sep:
            raise RuntimeError("WebVoyager runner produced no results; check the run output.")
        return parse_webvoyager_results(json.loads(payload), eff_k)


class AndroidWorldSuite(_EnvHarness):
    id: str = "androidworld"
    name: str = "AndroidWorld"
    version: str = "v1"
    provenance: str = "google-research/android_world (Apache-2.0); 116 live Android tasks. W2."
    substrate: str = "an Android emulator"
    repo: str = "github.com/google-research/android_world"
    features: list[str] = Field(default_factory=lambda: ["computer_use", "gui_grounding"])
    tools: list[str] = Field(default_factory=lambda: ["computer"])


class RcaEvalSuite(_EnvHarness):
    id: str = "rcaeval"
    name: str = "RCAEval"
    version: str = "v1"
    provenance: str = (
        "phamquiluan/RCAEval (MIT); 735 multi-modal root-cause cases. RE1 (metrics-only, 375 "
        "cases, ~5GB) wired s37 via the WSL2 rcaeval-venv; RE2/RE3 (logs+traces) deferred."
    )
    substrate: str = "the RCAEval telemetry harness (RE1 wired; RE2/RE3 deferred)"
    repo: str = "github.com/phamquiluan/RCAEval"
    features: list[str] = Field(
        default_factory=lambda: ["root_cause_analysis", "multi_step_reasoning"]
    )
    tools: list[str] = Field(default_factory=lambda: ["shell"])
    limit: int = 0  # cap total RE1 cases per run; 0 = all 375.

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id=self.id,
                suite_id=self.id,
                benchmark_family=self.id,
                features=list(self.features),
                tools=list(self.tools),
                prompt="RCAEval RE1 (metrics-only) root-cause-analysis cases (external, WSL-gated)",
            )
        ]

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        """Run RCAEval's RE1 cases via the WSL2 rcaeval-venv. Raises a clear error if absent."""
        eff_k = k if k is not None else 1
        inner = (
            f"cd {_quote(_WSL_RCAEVAL_DIR)} && "
            f"{_WSL_VENV} {_quote(_WSL_RUNNER)} --model {_quote(model)} "
            f"--data-dir {_quote('data')}" + (f" --limit {self.limit}" if self.limit else "")
        )
        cmd = ["wsl.exe", "-d", _WSL_DISTRO, "--", "bash", "-lc", inner]
        env = dict(os.environ)
        env["MADRAS_LITELLM_BASE_URL"] = settings.litellm_base_url
        env["MADRAS_LITELLM_API_KEY"] = settings.litellm_master_key or "sk-noauth"
        env["WSLENV"] = (
            env.get("WSLENV", "") + ":MADRAS_LITELLM_BASE_URL:MADRAS_LITELLM_API_KEY"
        ).lstrip(":")
        try:
            proc = subprocess.run(
                cmd,
                env=env,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_RCAEVAL_TIMEOUT_SECS,
            )
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"WSL ({_WSL_DISTRO}) not available; RCAEval requires WSL2 + the rcaeval-venv "
                f"at {_WSL_VENV}. Provision it, then retry."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"RCAEval run timed out after {_RCAEVAL_TIMEOUT_SECS:.0f}s (set `limit` lower)."
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr or ""
            if "no such file" in stderr.lower() and "rcaeval-venv" in stderr.lower():
                raise FileNotFoundError(
                    f"RCAEval WSL venv missing at {_WSL_VENV} in distro {_WSL_DISTRO}. "
                    "Create it and `pip install -e '.[default]'`, then retry."
                ) from exc
            raise RuntimeError(
                f"RCAEval run failed (exit {exc.returncode}). stderr: {stderr.strip()[-500:]}"
            ) from exc
        _, sep, payload = proc.stdout.partition(_RCAEVAL_SENTINEL)
        if not sep:
            raise RuntimeError("RCAEval runner produced no results; check the run output.")
        return parse_rcaeval_results(json.loads(payload), eff_k)


class OpenRcaSuite(_EnvHarness):
    id: str = "openrca"
    name: str = "OpenRCA"
    version: str = "v1"
    provenance: str = "microsoft/OpenRCA (MIT); 335 agentic long-context RCA cases. W2."
    substrate: str = "the OpenRCA telemetry (~68 GB)"
    repo: str = "github.com/microsoft/OpenRCA"
    features: list[str] = Field(
        default_factory=lambda: ["root_cause_analysis", "multi_step_reasoning"]
    )
    tools: list[str] = Field(default_factory=lambda: ["shell"])


class MarbleSuite(_EnvHarness):
    id: str = "marble"
    name: str = "MARBLE / MultiAgentBench"
    version: str = "v1"
    provenance: str = "ulab-uiuc/MARBLE (MIT); multi-agent orchestration scoring. W2."
    substrate: str = "the MARBLE multi-agent sim"
    repo: str = "github.com/ulab-uiuc/MARBLE"
    features: list[str] = Field(default_factory=lambda: ["delegation", "planning", "messaging"])
    tools: list[str] = Field(default_factory=lambda: ["delegation"])


class CollabOvercookedSuite(_EnvHarness):
    id: str = "collab_overcooked"
    name: str = "Collab-Overcooked"
    version: str = "v1"
    provenance: str = "YusaeMeow/Collab-Overcooked (MIT); 30 cooperative process tasks. W2."
    substrate: str = "the Overcooked cooperative sim"
    repo: str = "github.com/YusaeMeow/Collab-Overcooked"
    features: list[str] = Field(default_factory=lambda: ["delegation", "planning"])
    tools: list[str] = Field(default_factory=lambda: ["delegation"])


class AsbSuite(_EnvHarness):
    id: str = "asb"
    name: str = "Agent Security Bench"
    version: str = "v1"
    provenance: str = "agiresearch/ASB (MIT); memory/tool-poisoning attack-defense. W2."
    substrate: str = "the ASB attack harness"
    repo: str = "github.com/agiresearch/ASB"
    features: list[str] = Field(default_factory=lambda: ["guardrails", "refusal_safety", "mcp"])
    tools: list[str] = Field(default_factory=lambda: ["mcp"])


_WEBWALKERQA_DIR = _REPO_ROOT / ".benchmarks" / "webwalkerqa"
_WEBWALKERQA_RUNNER = _ENGINE_ROOT / "scripts" / "webwalkerqa_runner.py"
_WEBWALKERQA_TIMEOUT_SECS = float(os.environ.get("MADRAS_WEBWALKERQA_TIMEOUT", "1200"))


def _webwalkerqa_python() -> Path:
    win = _WEBWALKERQA_DIR / ".venv" / "Scripts" / "python.exe"
    posix = _WEBWALKERQA_DIR / ".venv" / "bin" / "python"
    return win if win.exists() else posix


def parse_webwalkerqa_results(by_q: dict[str, dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """Normalize the runner's ``{q_id: {f1, passed, termination}}`` (word-overlap F1 against
    the real WebWalkerQA reference answer) into pg rows."""
    eff_k = max(1, k)
    rows: list[dict[str, Any]] = []
    for q_id, result in by_q.items():
        passed = bool(result.get("passed", False))
        rows.append(
            {
                "scenario_id": f"webwalkerqa-{q_id}",
                "suite_id": "webwalkerqa",
                "benchmark_family": "webwalkerqa",
                "features": ["web_browsing", "fact_finding"],
                "k": eff_k,
                "passes": eff_k if passed else 0,
                "pass_rate": 1.0 if passed else 0.0,
            }
        )
    return rows


class WebWalkerQaSuite(_EnvHarness):
    id: str = "webwalkerqa"
    name: str = "WebWalkerQA"
    version: str = "v1"
    provenance: str = (
        "callanwu/WebWalkerQA (Apache-2.0); 680 deep multi-page web tasks. Wired s37: native "
        "Windows venv (CPU-only — vllm/torch in the upstream requirements.txt are for "
        "self-hosting the reference model, not needed to call an external API), the real "
        "680-question dataset (HF callanwu/WebWalkerQA, not the repo's placeholder example), "
        "call_server monkey-patched (not the vendored file) to route through our own gateway "
        "instead of its hardcoded local-vllm address."
    )
    substrate: str = "a live web browser"
    repo: str = "github.com/callanwu/WebWalkerQA"
    features: list[str] = Field(default_factory=lambda: ["web_browsing", "fact_finding"])
    tools: list[str] = Field(default_factory=lambda: ["web", "browser"])
    limit: int = 2  # cap total questions per run; the full suite is 680.

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id=self.id,
                suite_id=self.id,
                benchmark_family=self.id,
                features=list(self.features),
                tools=list(self.tools),
                prompt="WebWalkerQA deep multi-page web-traversal QA (external, venv-gated)",
            )
        ]

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        """Run WebWalkerQA's own ReAct agent loop natively (CPU-only, monkey-patched routing)."""
        eff_k = k if k is not None else 1
        py = _webwalkerqa_python()
        if not py.exists():
            raise FileNotFoundError(
                f"WebWalkerQA venv missing at {py}. Create .venv under {_WEBWALKERQA_DIR} and "
                "install its (non-GPU) deps, then retry."
            )
        env = dict(os.environ)
        env["OPENAI_BASE_URL"] = settings.ollama_url.rstrip("/") + "/v1"
        env["OPENAI_API_KEY"] = "ollama"
        cmd = [str(py), str(_WEBWALKERQA_RUNNER), "--model", model, "--limit", str(self.limit)]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(_WEBWALKERQA_DIR),
                env=env,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_WEBWALKERQA_TIMEOUT_SECS,
            )
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"WebWalkerQA venv python missing: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"WebWalkerQA run timed out after {_WEBWALKERQA_TIMEOUT_SECS:.0f}s (lower `limit`)."
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr or ""
            raise RuntimeError(
                f"WebWalkerQA run failed (exit {exc.returncode}). stderr: {stderr.strip()[-500:]}"
            ) from exc
        _, sep, payload = proc.stdout.partition("__WEBWALKERQA__")
        if not sep:
            raise RuntimeError("WebWalkerQA runner produced no results; check the run output.")
        return parse_webwalkerqa_results(json.loads(payload), eff_k)


ENV_HARNESS_SUITES: dict[str, _EnvHarness] = {
    "webvoyager": WebVoyagerSuite(),
    "androidworld": AndroidWorldSuite(),
    "rcaeval": RcaEvalSuite(),
    "openrca": OpenRcaSuite(),
    "marble": MarbleSuite(),
    "collab_overcooked": CollabOvercookedSuite(),
    "asb": AsbSuite(),
    "webwalkerqa": WebWalkerQaSuite(),
}
