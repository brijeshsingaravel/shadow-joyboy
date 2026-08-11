"""SWE-bench glue — turn a SWE-bench(-Lite) instance row into a runnable SWE-loop task on a
self-hosted Docker sandbox (D47: no hosted third-party sandbox service by default).

Handles the two things a raw instance needs to run on our substrate: per-instance Python pinning
(the SWE-bench spec version — pre-2020 code needs an older interpreter than the sandbox image's
default, e.g. cgi/PEP-594) and the oracle wiring (apply the gold test diff, point the test_cmd at
the FAIL_TO_PASS via the pinned venv). Used by the single-instance drive + the resolve-rate sweep.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from madras.codeact.swe_loop import SweResult, SweTask, run_swe_loop
from madras.config import settings
from madras.llm.gateway import LLMGateway
from madras.llm.litellm import LiteLLMBackend
from madras.tools.sandbox import DockerSandbox

_PYVER_MAP = (
    Path(__file__).resolve().parents[1]
    / "eval_/proving_ground/suites/swebench/data/python_versions.json"
)


def _f2p(inst: dict[str, Any]) -> list[str]:
    v = inst["FAIL_TO_PASS"]
    return json.loads(v) if isinstance(v, str) else list(v)


def python_version(inst: dict[str, Any]) -> str:
    """The SWE-bench spec Python for this instance (falls back to 3.11 if unmapped)."""
    table = json.loads(_PYVER_MAP.read_text(encoding="utf-8"))
    return table.get(inst["repo"], {}).get(str(inst.get("version", "")), "3.11")


def build_task(inst: dict[str, Any], *, max_iters: int = 8) -> SweTask:
    """Build a SweTask for a SWE-bench instance: pinned-Python uv venv + gold-test oracle."""
    pyver = python_version(inst)
    uv_setup = (
        "(curl -LsSf https://astral.sh/uv/install.sh | sh) >/dev/null 2>&1; "
        'export PATH="$HOME/.local/bin:$PATH"; '
        f"uv venv --python {pyver} .venv >/dev/null 2>&1 && "
        "uv pip install -p .venv -q -e . >/dev/null 2>&1; "
        "uv pip install -p .venv -q pytest"
    )
    # FAIL_TO_PASS entries are pytest node ids (path::name) for most repos, but bare function
    # names for some (e.g. sympy). Node ids run directly; bare names go through -k (keyword match)
    # or pytest treats them as missing file paths and silently runs nothing.
    f2p = _f2p(inst)
    node_ids = [t for t in f2p if "::" in t]
    bare = [t for t in f2p if "::" not in t]
    sel = " ".join(f"'{t}'" for t in node_ids)
    if bare:
        sel += ' -k "' + " or ".join(bare) + '"'
    problem = (
        inst["problem_statement"]
        + "\n\nYOUR FIX MUST MAKE THESE TESTS PASS (do NOT edit the tests):\n"
        + inst["test_patch"][:4000]
    )
    return SweTask(
        repo_url=f"https://github.com/{inst['repo']}",
        base_commit=inst["base_commit"],
        problem=problem,
        setup_cmd=uv_setup,
        test_patch=inst["test_patch"],
        test_cmd=f".venv/bin/python -m pytest {sel} -q",
        max_iters=max_iters,
    )


def loop_gateway() -> LLMGateway:
    """Route the SWE loop through the LiteLLM gateway (the canon's designated routing layer) — it
    fronts the zero-cost free/no-train fleet (Groq · Cerebras · Gemini · local) per the cost
    mandate, so the loop never talks to a paid provider directly. Gateway at
    ``settings.litellm_base_url`` (localhost:4000), keyed by ``litellm_master_key``."""
    # Force 127.0.0.1 over localhost: on this Windows host the IPv6 (::1) forward to the LiteLLM
    # container's :4000 silently drops (WinError 10053), while the IPv4 loopback works.
    base = settings.litellm_base_url.replace("localhost", "127.0.0.1")
    return LLMGateway(
        backend=LiteLLMBackend(
            api_key=settings.litellm_master_key or "litellm",
            base_url=base,
            timeout=300.0,  # free-fleet backends are slower; the big edit-gen prompt needs headroom
        )
    )


async def run_instance(
    inst: dict[str, Any], model: str, *, max_iters: int = 8, vm_timeout: int = 900
) -> SweResult:
    """Run one SWE-bench instance through the loop in a fresh, self-hosted Docker container
    (LiteLLM-routed). ``vm_timeout`` is kept for call-site compatibility but unused here —
    DockerSandbox has no session-idle-lifetime concept; the container is torn down explicitly
    in ``finally`` regardless of duration."""
    del vm_timeout
    task = build_task(inst, max_iters=max_iters)
    sb = DockerSandbox(session_id=f"swebench-{inst['instance_id'][:20]}")
    try:
        return await run_swe_loop(task, sandbox=sb, gateway=loop_gateway(), model=model)
    finally:
        await sb.stop()
