"""SWE-bench Verified external suite adapter (Docker eval via WSL; smoke patch-gen).

SWE-bench is an EVALUATION harness, not an agent. It takes a *predictions* file
(one model-generated patch per task instance) and runs each repo's tests inside a
per-instance Docker image to decide whether the patch RESOLVES the issue. A real
model score therefore needs two stages:

  Stage A — GENERATE a unified-diff patch per instance with our model. This adapter
            keeps it MINIMAL: a single LiteLLM call (via the shared proxy) that is
            handed the ``problem_statement`` (+ a little repo context) and asked for
            a patch. This is a *smoke-grade* generator, NOT a SOTA coding agent — it
            has no repo checkout, no tool loop, no test feedback. It exists to drive
            the pipeline end-to-end; expect low resolve rates from it.

  Stage B — EVALUATE those patches with the swebench harness in Docker. This is the
            real, expensive step: ``python -m swebench.harness.run_evaluation`` builds
            (or pulls) a per-instance image, applies the predicted patch + the gold
            ``test_patch``, runs ``FAIL_TO_PASS`` / ``PASS_TO_PASS``, and writes a
            report JSON marking each instance resolved/unresolved.

The harness builds and runs Linux Docker images, so — like terminal-bench — it must
execute inside WSL against the shared Docker daemon, never as a Windows binary and
never imported into the main Python 3.11 Madras env. It lives in its own venv under
``~/.madras-benchmarks/swebench`` in the WSL distro (see ``scripts/setup_swebench.sh``)
and is driven here through ``wsl.exe`` subprocess.

Stage A runs in THIS (Windows) process via httpx to the LiteLLM proxy
(``settings.litellm_base_url`` already carries ``/v1``); the predictions file is
written to a shared temp dir, then stage B is shelled into WSL. The proxy master key
is read from ``settings`` and used only for the Windows-side httpx call — it is never
interpolated into the WSL script text, never logged, never written into result rows.
"""

from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import Field

from madras.config import settings
from madras.eval_.proving_ground.suite import Case, Suite

# Vendored slice (committed) — instance metadata only; the harness fetches/builds
# the heavy Docker artifacts itself at run time.
DATA_DIR = Path(__file__).resolve().parent / "swebench" / "data"
_SLICE = DATA_DIR / "swebench_verified_slice.json"

# WSL layout produced by scripts/setup_swebench.sh. Overridable via env so a
# different distro / checkout location does not require a code change.
_WSL_DISTRO = os.environ.get("MADRAS_SWEBENCH_WSL_DISTRO", "OutkastUbuntu2")
_WSL_DIR = os.environ.get("MADRAS_SWEBENCH_WSL_DIR", "/home/brijesh/.madras-benchmarks/swebench")
# swebench image namespace. "none" => build images locally instead of pulling the
# prebuilt ``swebench/*`` registry images (the spike built locally). Override to
# "swebench" once the operator wants the fast prebuilt-image path.
_NAMESPACE = os.environ.get("MADRAS_SWEBENCH_NAMESPACE", "none")
# Per-test timeout the harness passes to each instance's pytest run.
_INSTANCE_TIMEOUT = int(os.environ.get("MADRAS_SWEBENCH_INSTANCE_TIMEOUT", "1800"))
# Wall-clock cap for the whole WSL subprocess. Image builds are slow (the astropy
# env image took multiple minutes in the spike), so this defaults large.
_TIMEOUT_SECS = float(os.environ.get("MADRAS_SWEBENCH_TIMEOUT", "3600"))
# Cap how many slice instances a single run touches (each is a multi-GB image).
_MAX_INSTANCES = int(os.environ.get("MADRAS_SWEBENCH_MAX_INSTANCES", "1"))
# Stage-A generation budget (one bounded LLM call per instance).
_GEN_MAX_TOKENS = int(os.environ.get("MADRAS_SWEBENCH_GEN_MAX_TOKENS", "1500"))
_GEN_TIMEOUT_SECS = float(os.environ.get("MADRAS_SWEBENCH_GEN_TIMEOUT", "120"))

# Stable model_name_or_path written into the predictions file. The harness names
# its report ``<model_name_or_path>.<run_id>.json``, so we read it back by this.
_PRED_MODEL_NAME = "madras"

_FEATURES = ["code_editing", "multi_step_reasoning", "tool_args"]
_TOOLS = ["shell", "file_edit"]

_GEN_SYSTEM = (
    "You are an expert software engineer. Given a GitHub issue for a Python "
    "repository, output a single unified diff (git patch) that fixes the issue. "
    "Output ONLY the patch, starting with 'diff --git' or '--- '. No prose, no "
    "code fences, no explanation."
)


def _to_unified_diff(text: str) -> str:
    """Best-effort extraction of a unified diff from a model response.

    Strips markdown fences and any leading prose so the predictions file carries a
    clean patch. Returns "" when no diff-looking content is present (the harness
    then records the instance as unresolved, which is the honest outcome).
    """
    if not text:
        return ""
    body = text.strip()
    if "```" in body:
        # Take the contents of the first fenced block if present.
        parts = body.split("```")
        if len(parts) >= 3:
            block = parts[1]
            # Drop an optional language tag on the opening fence line.
            if "\n" in block:
                first, rest = block.split("\n", 1)
                if first.strip().lower() in {"diff", "patch", "", "git"}:
                    block = rest
            body = block.strip()
    for marker in ("diff --git", "--- "):
        idx = body.find(marker)
        if idx != -1:
            body = body[idx:]
            break
    else:
        return ""
    return body if body.endswith("\n") else body + "\n"


def _pass_hat_k(num_trials: int, success_count: int, k: int) -> float:
    """pass^k for a single instance (Sierra's definition; arxiv 2406.12045)."""
    k = min(k, num_trials)
    if k <= 0 or num_trials <= 0:
        return 0.0
    if success_count < k:
        return 0.0
    return math.comb(success_count, k) / math.comb(num_trials, k)


def _load_slice() -> list[dict[str, Any]]:
    with _SLICE.open(encoding="utf-8") as fh:
        return json.load(fh)


def parse_report(report: dict[str, Any], instance_ids: list[str], k: int) -> list[dict[str, Any]]:
    """Normalize a swebench run-report into proving-ground rows.

    The report's ``resolved_ids`` lists instances whose patch passed. Each instance
    we attempted becomes one row; ``passes`` is k when resolved else 0 (a single
    deterministic eval per attempt), and ``pass_rate`` is pass^k over that.
    """
    resolved = set(report.get("resolved_ids") or [])
    rows: list[dict[str, Any]] = []
    for inst in sorted(instance_ids):
        is_resolved = inst in resolved
        passes = k if is_resolved else 0
        rows.append(
            {
                "scenario_id": f"swebench-{inst}",
                "suite_id": "swebench",
                "benchmark_family": "swebench",
                "features": list(_FEATURES),
                "k": k,
                "passes": passes,
                "pass_rate": _pass_hat_k(k, passes, k),
                "verdict": "resolved" if is_resolved else "unresolved",
            }
        )
    return rows


class SweBenchSuite(Suite):
    """External SWE-bench Verified suite (model patch-gen + Docker eval via WSL)."""

    id: str = "swebench"
    name: str = "SWE-bench Verified"
    version: str = "4.1.0"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "SWE-bench/SWE-bench_Verified (princeton-nlp), vendored metadata slice; "
        "stage-B Docker eval via swebench.harness.run_evaluation inside WSL "
        "(OutkastUbuntu2); stage-A patches are smoke-grade single-LLM-call diffs, "
        "NOT a SOTA coding agent."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))
    # Specific instance ids to run. Empty -> the first _MAX_INSTANCES of the slice.
    instance_ids: list[str] = Field(default_factory=list)
    k: int = 1
    # Eval engine: "wsl" = the official Docker harness (Verified); "e2b" = the SWE loop generates
    # AND scores end-to-end in a per-instance pinned-Python E2B micro-VM (oracle FAIL_TO_PASS).
    engine: Literal["wsl", "e2b"] = "wsl"
    dataset: str = "princeton-nlp/SWE-bench_Lite"  # source for the e2b engine (needs full rows)
    e2b_concurrency: int = 3
    e2b_max_iters: int = 6

    def _select_instances(self) -> list[dict[str, Any]]:
        rows = _load_slice()
        if self.instance_ids:
            wanted = set(self.instance_ids)
            return [r for r in rows if r["instance_id"] in wanted]
        return rows[:_MAX_INSTANCES]

    def load_cases(self) -> list[Case]:
        """Lightweight per-instance Case stubs for coverage metadata.

        swebench drives its own Docker eval loop, so these are not executed through
        our governed runner — they exist so the suite registry can aggregate
        feature/tool coverage.
        """
        rows = _load_slice()
        if self.instance_ids:
            wanted = set(self.instance_ids)
            rows = [r for r in rows if r["instance_id"] in wanted]
        return [
            Case(
                id=f"swebench-{r['instance_id']}",
                suite_id=self.id,
                benchmark_family="swebench",
                features=list(_FEATURES),
                tools=list(_TOOLS),
                prompt=f"swebench {r['instance_id']} ({r.get('repo', '')}) external Docker task",
                k=self.k,
            )
            for r in rows
        ]

    def _generate_patch(self, instance: dict[str, Any], model: str) -> str:
        """Stage A: one bounded LiteLLM call -> a unified-diff patch (best-effort).

        Routes through the shared proxy (``settings.litellm_base_url`` already
        carries ``/v1``). Returns "" on any failure or when the model emits no diff —
        the harness then records the instance unresolved, the honest outcome.
        """
        problem = (instance.get("problem_statement") or "")[:8000]
        hints = (instance.get("hints_text") or "")[:1500]
        repo = instance.get("repo", "")
        user = (
            f"Repository: {repo}\n"
            f"Base commit: {instance.get('base_commit', '')}\n\n"
            f"Issue:\n{problem}\n"
        )
        if hints:
            user += f"\nHints:\n{hints}\n"
        user += "\nProduce the unified diff patch that resolves this issue."

        url = settings.litellm_base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        key = settings.litellm_master_key
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": _GEN_SYSTEM},
                {"role": "user", "content": user},
            ],
            "max_tokens": _GEN_MAX_TOKENS,
            "temperature": 0.0,
        }
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=_GEN_TIMEOUT_SECS)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except Exception:
            return ""
        return _to_unified_diff(content or "")

    def _build_wsl_script(self, preds_wsl_path: str, instance_ids: list[str], run_id: str) -> str:
        """Bash run, executed inside WSL, that shells ``run_evaluation`` and emits the
        report JSON to stdout between sentinels so the parent can recover it.

        No secrets are referenced here — stage A (the only LLM call) already ran on
        the Windows side, so the predictions file holds plain patches only.
        """
        ids = " ".join(shlex.quote(i) for i in instance_ids)
        dataset = "SWE-bench/SWE-bench_Verified"
        report_name = f"{_PRED_MODEL_NAME}.{run_id}.json"
        return (
            f"set -euo pipefail; cd {shlex.quote(_WSL_DIR)}; "
            f"rm -f {shlex.quote(report_name)}; "
            "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 "
            "./.venv/bin/python -m swebench.harness.run_evaluation "
            f"--dataset_name {shlex.quote(dataset)} --split test "
            f"--predictions_path {shlex.quote(preds_wsl_path)} "
            f"--run_id {shlex.quote(run_id)} "
            f"--instance_ids {ids} "
            "--max_workers 1 "
            f"--namespace {shlex.quote(_NAMESPACE)} "
            f"--timeout {int(_INSTANCE_TIMEOUT)} "
            ">/dev/null 2>&1; "
            'echo "__MADRAS_SWEBENCH_REPORT__"; '
            f"cat {shlex.quote(report_name)}"
        )

    def _run_eval(self, preds: list[dict[str, Any]], instance_ids: list[str]) -> dict[str, Any]:
        """Stage B: write the predictions file, shell ``run_evaluation`` in WSL,
        return the parsed report dict."""
        run_id = f"madras_{uuid.uuid4().hex[:12]}"
        # Write the predictions file to a Windows temp dir and hand WSL its /mnt path.
        tmp = Path(tempfile.gettempdir()) / f"madras_swebench_{run_id}.json"
        tmp.write_text(json.dumps(preds), encoding="utf-8")
        try:
            preds_wsl = _to_wsl_path(tmp)
            script = self._build_wsl_script(preds_wsl, instance_ids, run_id)
            cmd = ["wsl.exe", "-d", _WSL_DISTRO, "--", "bash", "-c", script]
            try:
                proc = subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=_TIMEOUT_SECS,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"swebench eval timed out after {_TIMEOUT_SECS:.0f}s "
                    f"(an image build likely wedged). Raise MADRAS_SWEBENCH_TIMEOUT."
                ) from exc
            _, sep, payload = proc.stdout.partition("__MADRAS_SWEBENCH_REPORT__")
            if not sep or not payload.strip():
                raise RuntimeError(
                    "swebench produced no report JSON. Run scripts/setup_swebench.sh "
                    "and confirm WSL + Docker are up."
                )
            return json.loads(payload)
        finally:
            tmp.unlink(missing_ok=True)

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        """Run SWE-bench Verified for ``model``; return normalized pg rows.

        Stage A generates a patch per selected instance; stage B evaluates them in
        Docker via WSL. ``k`` controls the pass^k order (defaults to ``self.k``).
        ``concurrency`` is accepted for interface parity but the harness is driven
        single-worker (``--max_workers 1``) because each instance is a heavy image.
        """
        eff_k = k if k is not None else self.k
        if self.engine == "e2b":
            return self._run_e2b(model, eff_k)
        instances = self._select_instances()
        if not instances:
            return []
        instance_ids = [r["instance_id"] for r in instances]
        preds = [
            {
                "instance_id": r["instance_id"],
                "model_name_or_path": _PRED_MODEL_NAME,
                "model_patch": self._generate_patch(r, model),
            }
            for r in instances
        ]
        report = self._run_eval(preds, instance_ids)
        return parse_report(report, instance_ids, eff_k)

    def _full_instances(self) -> list[dict[str, Any]]:
        """Full instance rows from the HF dataset (the e2b loop needs problem/test_patch/version,
        not just the metadata slice)."""
        from datasets import load_dataset  # type: ignore[reportMissingTypeStubs]

        ds: Any = load_dataset(self.dataset, split="test")
        if self.instance_ids:
            wanted = set(self.instance_ids)
            return [dict(r) for r in ds if r["instance_id"] in wanted]
        return [dict(r) for r in list(ds)[:_MAX_INSTANCES]]

    def _run_e2b(self, model: str, k: int) -> list[dict[str, Any]]:
        """Generate + score each instance with the SWE loop on E2B (bounded concurrency), then map
        the resolved set through ``parse_report`` (same pg-row shape as the WSL path)."""
        import asyncio

        from madras.codeact import swe_bench as _swe_bench

        instances = self._full_instances()
        if not instances:
            return []
        ids = [r["instance_id"] for r in instances]

        async def _all() -> list[tuple[str, bool]]:
            sem = asyncio.Semaphore(self.e2b_concurrency)

            async def _one(inst: dict[str, Any]) -> tuple[str, bool]:
                async with sem:
                    try:
                        res = await _swe_bench.run_instance(
                            inst, model, max_iters=self.e2b_max_iters
                        )
                        return inst["instance_id"], res.resolved
                    except Exception:
                        return inst["instance_id"], False

            return list(await asyncio.gather(*[_one(r) for r in instances]))

        resolved = [iid for iid, ok in asyncio.run(_all()) if ok]
        return parse_report({"resolved_ids": resolved}, ids, k)


def _to_wsl_path(path: Path) -> str:
    """Convert a Windows path (e.g. C:\\Users\\..\\x.json) to its WSL /mnt form."""
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    rest = resolved.as_posix()[len(resolved.drive) :].lstrip("/")
    return f"/mnt/{drive}/{rest}"


if __name__ == "__main__":  # pragma: no cover - manual live smoke
    suite = SweBenchSuite(k=int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    print(json.dumps(suite.run(sys.argv[1] if len(sys.argv) > 1 else "llama-70b")))
