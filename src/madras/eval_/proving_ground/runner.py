"""Task 4 — scenario runner: run a scenario k times through the governed loop.

This harness WRAPS the real governed loop read-only: it constructs the same
dependencies `build_tool_agent` uses (a ToolRegistry, a GovernedExecutor, a
PermissionEngine in BYPASS mode, on_ask="deny") and drives
`madras.graph.tool_loop.run_agentic_loop` once per resample, folding the
returned `LoopDone` + the emitted `on_event` tool_call/tool_result stream into
the trajectory dict shape Task 3's scorer consumes:

    {"answer": str, "tools": [{"name", "args", "ok"}], "refused": bool}

pass^k = fraction of the k resamples whose deterministic check passed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, cast

from madras.eval_.proving_ground.agents import AgentSpec
from madras.eval_.proving_ground.scenario import Scenario
from madras.eval_.proving_ground.scoring import looks_refusal, score_deterministic
from madras.eval_.proving_ground.seeding import derive_seed
from madras.graph.tool_loop import LoopDone, run_agentic_loop
from madras.llm.gateway import LLMGateway
from madras.models.agent_config import Rank
from madras.security.permissions import PermissionEngine, PermissionMode
from madras.tools.registry import GovernedExecutor, ToolRegistry


@dataclass
class ScenarioRun:
    scenario_id: str
    k: int
    trajectories: list[dict[str, Any]]
    passes: int
    pass_rate: float
    det_per_run: list[list[dict[str, Any]]] = field(default_factory=list[list[dict[str, Any]]])


def _messages(scenario: Scenario) -> list[dict[str, Any]]:
    """Build the OpenAI message thread: seeded memory turns + the user task."""
    msgs: list[dict[str, Any]] = [dict(m) for m in scenario.setup.get("memory", [])]
    msgs.append({"role": "user", "content": scenario.task})
    return msgs


def _parse_args(preview: Any) -> dict[str, Any]:
    """on_event delivers args as a bounded string preview. Recover a dict when it
    is JSON (best-effort); otherwise an empty dict. Tool *selection* checks only
    need the name; tool_args checks degrade to empty when the preview isn't JSON.
    """
    if isinstance(preview, dict):
        return cast("dict[str, Any]", preview)
    if isinstance(preview, str):
        try:
            val = json.loads(preview)
        except (json.JSONDecodeError, ValueError):
            return {}
        return cast("dict[str, Any]", val) if isinstance(val, dict) else {}
    return {}


async def _one_trajectory(
    scenario: Scenario,
    gateway: LLMGateway,
    *,
    registry: ToolRegistry,
    toolsets: list[str] | None,
    model: str,
    max_iters: int,
    agent_name: str = "shadow",
    agent_rank: Rank = Rank.PRINCIPAL,
    system_prompt: str | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    tools_seen: list[dict[str, Any]] = []

    async def on_event(evt: dict[str, Any]) -> None:
        et = evt.get("type")
        if et == "tool_call":
            tools_seen.append(
                {"name": evt.get("tool"), "args": _parse_args(evt.get("args")), "ok": True}
            )
        elif et == "tool_result":
            tool = evt.get("tool")
            for t in reversed(tools_seen):
                if t["name"] == tool:
                    t["ok"] = bool(evt.get("ok", True))
                    break

    executor = GovernedExecutor(registry=registry, audit=None)
    schemas = registry.schemas(agent_rank=agent_rank, toolsets=toolsets)

    result = await run_agentic_loop(
        gateway=gateway,
        model=model,
        schemas=schemas,
        messages=_messages(scenario),
        executor=executor,
        permission_engine=PermissionEngine(),
        mode=PermissionMode.BYPASS,  # harness: non-interactive; rank gate via executor
        rules=[],
        granted={},
        agent_name=agent_name,
        session_id=f"pg-{scenario.id}",
        agent_rank=agent_rank,
        registry=registry,
        max_iters=max_iters,
        guard=None,
        system_prompt=system_prompt,
        on_ask="deny",  # BYPASS means nothing reaches ASK anyway
        on_event=on_event,
        seed=seed,
    )
    # on_ask="deny" never returns LoopPaused.
    assert isinstance(result, LoopDone)
    answer = result.text or ""

    # Refusal is true when the loop took NO tool action and the final text reads
    # as a refusal. The governed loop has no explicit refusal flag at this layer;
    # an input-guardrail block surfaces as a no-tool refusal-shaped answer too.
    refused = not tools_seen and looks_refusal(answer)

    return {"answer": answer, "tools": tools_seen, "refused": refused}


async def run_scenario(
    scenario: Scenario,
    *,
    gateway: LLMGateway,
    k: int | None = None,
    registry: ToolRegistry | None = None,
    toolsets: list[str] | None = None,
    model: str | None = None,
    max_iters: int = 5,
    agent: AgentSpec | None = None,
    seed: int | None = None,
) -> ScenarioRun:
    """Run `scenario` k times through the governed loop and score each run.

    registry/toolsets default to an empty registry with no tools (the FakeBackend
    path and any tool-free scenario). The real bank wiring (Plan 2/3) passes a
    populated registry + the scenario's declared toolsets.

    `agent` binds the unit-under-test: its `rank` gates the tools, its `agent_name`
    labels the governed loop, and its `persona` overrides the system prompt. When
    None the loop keeps the historical Shadow/PRINCIPAL defaults.
    """
    kk = k or scenario.k
    use_model = model or scenario.setup.get("model", "llama-70b")
    agent_name = agent.agent_name if agent is not None else "shadow"
    agent_rank = agent.rank if agent is not None else Rank.PRINCIPAL
    system_prompt = agent.persona if agent is not None else None
    use_toolsets = toolsets if toolsets is not None else scenario.setup.get("tools")

    # When a scenario requests tools, wire the SAME real registry + governed
    # executor + sandbox the cockpit uses (`server/app.py:_prepare_tool_loop`),
    # so the agent-under-test can actually call tools. Tool-free scenarios keep
    # the empty registry (no tools exposed, nothing to provision).
    if registry is not None:
        reg = registry
    elif use_toolsets:
        import madras.tools.builtin  # noqa: F401 — side-effect: registers built-in tools  # pyright: ignore[reportUnusedImport]
        from madras.tools.registry import REGISTRY

        reg = REGISTRY
    else:
        reg = ToolRegistry()

    # Sandbox lifecycle for dangerous toolsets — mirror app.py:458-467. Provision
    # only when a dangerous toolset is requested AND tools are actually exposed,
    # so safety scenarios (refuse_malware) face REAL dangerous tools. Hermetic
    # tool-free / non-dangerous scenarios never touch Docker.
    sandbox = None
    schemas_nonempty = bool(use_toolsets) and bool(
        reg.schemas(agent_rank=Rank.PRINCIPAL, toolsets=use_toolsets)
    )
    _sandbox_toolsets = frozenset({"shell", "code", "file_write"})
    _needs_sandbox = bool(set(use_toolsets or []) & _sandbox_toolsets) and schemas_nonempty
    if _needs_sandbox:
        from madras.tools.sandbox import build_sandbox
        from madras.tools.sandbox_context import set_active_sandbox

        try:
            sandbox = build_sandbox(session_id=f"pg-{scenario.id}")
            await sandbox.start()
            set_active_sandbox(sandbox)
        except Exception:
            sandbox = None  # degrade if sandbox unavailable

    try:
        trajs: list[dict[str, Any]] = []
        det: list[list[dict[str, Any]]] = []
        passes = 0
        for i in range(kk):
            # Per-resample error isolation: a tool timeout / LLM error / loop crash
            # must fail ONLY this trajectory (scored as a fail), never abort the run.
            # A "failproof" harness keeps going and records the error honestly.
            # seed derives a distinct-but-reproducible sub-seed per resample (via
            # derive_seed) so k-repeat sampling still varies while the whole
            # scenario reproduces identically across runs at the same base seed.
            resample_seed = derive_seed(seed, scenario.id, i) if seed is not None else None
            try:
                traj = await _one_trajectory(
                    scenario,
                    gateway,
                    registry=reg,
                    toolsets=use_toolsets,
                    model=use_model,
                    max_iters=max_iters,
                    agent_name=agent_name,
                    agent_rank=agent_rank,
                    system_prompt=system_prompt,
                    seed=resample_seed,
                )
            except Exception as exc:
                traj: dict[str, Any] = {
                    "answer": "",
                    "tools": [],
                    "refused": False,
                    "error": str(exc)[:200],
                }
            r = score_deterministic(scenario, traj)
            trajs.append(traj)
            det.append(r.per_check)
            passes += int(r.passed)
    finally:
        if sandbox is not None:
            from madras.tools.sandbox_context import set_active_sandbox

            set_active_sandbox(None)
            try:
                await sandbox.stop()
            except Exception:
                pass
    return ScenarioRun(scenario.id, kk, trajs, passes, passes / kk, det)
