"""External-agent submission contract (P3) — bring any agent.

The Proving Ground scores the internal governed loop by default
(`runner.run_scenario`). To benchmark an EXTERNAL agent — Madras or third-party —
the agent only needs to speak the de-facto-standard HAL contract: a callable

    run(task: dict) -> {"history": [<openai-style messages>], "cost": float}

(the same shape Inspect's agent-bridge emits). This module adapts that output
into the trajectory dict the deterministic + judge scorers already consume —

    {"answer": str, "tools": [{"name", "args", "ok"}], "refused": bool, "cost": float}

— so any HAL/Inspect-compatible agent slots into the same harness, scoring,
pass^k and Index without touching the scorer. No base class, no SDK lock-in.

`SubmissionRun` mirrors `runner.ScenarioRun`, so the result feeds the same
aggregation (det_pass + pass_rate per scenario). Kept dependency-light (no graph
imports) so external runs don't drag the internal governed loop.
"""

from __future__ import annotations

import inspect
import ipaddress
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast, runtime_checkable
from urllib.parse import urlparse

import httpx

from madras.eval_.proving_ground.scenario import Scenario
from madras.eval_.proving_ground.scoring import looks_refusal, score_deterministic

_BLOCKED_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})


def validate_submission_url(url: str, *, require_https: bool = False) -> None:
    """SSRF guard for untrusted submission endpoints (ASI04). Rejects non-http(s)
    schemes and IP-literal hosts in loopback/private/link-local/reserved ranges —
    so a submitted agent URL can't point at our own infra (e.g. localhost:3050,
    the cloud metadata 169.254.169.254, or a 10.x service). Hostname-based DNS
    rebinding is a runtime concern handled at request time by the network policy.
    """
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError(f"submission url must be http(s): {url!r}")
    if require_https and p.scheme != "https":
        raise ValueError("submission url must use https")
    host = (p.hostname or "").lower()
    if not host:
        raise ValueError("submission url has no host")
    if host in _BLOCKED_HOSTNAMES:
        raise ValueError("submission url may not target localhost")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None  # a hostname, not an IP literal
    if ip is not None and (
        ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast
    ):
        raise ValueError(f"submission url may not target a non-public address: {host}")


# A HAL-style agent: a sync or async callable task->{"history", "cost"}.
AgentCallable = Callable[[dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]


@runtime_checkable
class AgentRunner(Protocol):
    async def run_once(self, task: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class SubmissionRun:
    scenario_id: str
    k: int
    trajectories: list[dict[str, Any]]
    passes: int
    pass_rate: float
    total_cost: float
    det_per_run: list[list[dict[str, Any]]] = field(default_factory=list[list[dict[str, Any]]])


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return cast("dict[str, Any]", raw)
    if isinstance(raw, str):
        try:
            val = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {}
        return cast("dict[str, Any]", val) if isinstance(val, dict) else {}
    return {}


def task_from_scenario(scenario: Scenario) -> dict[str, Any]:
    """The HAL task input: seeded memory + the user task + declared tool names."""
    messages: list[dict[str, Any]] = [dict(m) for m in scenario.setup.get("memory", [])]
    messages.append({"role": "user", "content": scenario.task})
    return {
        "id": scenario.id,
        "prompt": scenario.task,
        "messages": messages,
        "tools": list(scenario.setup.get("tools", [])),
        "k": scenario.k,
    }


def trajectory_from_history(
    history: list[dict[str, Any]], *, cost: float = 0.0, tokens: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Adapt a HAL `history` (OpenAI-style messages) into the scorer trajectory.

    - tools: each assistant `tool_calls[].function` → {name, args, ok}
    - a `role:"tool"` message with `error` or an "ERROR…" body marks the matching
      tool call not-ok
    - answer: the last non-empty assistant `content`
    - refused: no tool action AND the answer reads as a refusal
    """
    tools: list[dict[str, Any]] = []
    answer = ""
    for msg in history:
        role = msg.get("role")
        if role == "assistant":
            tool_calls: list[dict[str, Any]] = msg.get("tool_calls") or []
            for tc in tool_calls:
                fn: dict[str, Any] = tc.get("function", tc)
                name = fn.get("name")
                if name:
                    tools.append(
                        {"name": name, "args": _parse_args(fn.get("arguments")), "ok": True}
                    )
            content = msg.get("content")
            if content:
                answer = content if isinstance(content, str) else str(content)
        elif role == "tool":
            failed = bool(msg.get("error")) or str(
                msg.get("content", "")
            ).lstrip().upper().startswith("ERROR")
            if failed:
                name = msg.get("name")
                for t in reversed(tools):
                    if name is None or t["name"] == name:
                        t["ok"] = False
                        break
    traj: dict[str, Any] = {
        "answer": answer,
        "tools": tools,
        "refused": (not tools) and looks_refusal(answer),
        "cost": float(cost),
    }
    if tokens is not None:
        traj["tokens"] = tokens
    return traj


class ExternalAgentRunner:
    """Wraps a HAL-style agent callable (sync or async) into an `AgentRunner`."""

    def __init__(self, agent: AgentCallable) -> None:
        self._agent = agent

    async def run_once(self, task: dict[str, Any]) -> dict[str, Any]:
        res: Any = self._agent(task)
        if inspect.isawaitable(res):
            res = await res
        if not isinstance(res, dict):
            raise TypeError("submission agent must return a dict {'history', 'cost'}")
        res = cast("dict[str, Any]", res)
        return trajectory_from_history(
            res.get("history", []),
            cost=float(res.get("cost", 0.0) or 0.0),
            tokens=res.get("tokens"),
        )


class HttpAgentRunner:
    """Drives a REMOTE HAL-compatible agent over HTTP — the on-ramp for untrusted
    third-party agents. The agent runs on its OWN infra (isolation by process +
    network boundary); we POST the task and receive only JSON `{history, cost}`.
    Defensive: bounded timeout, status check, and a response-size cap.
    """

    def __init__(
        self,
        url: str,
        *,
        timeout: float = 60.0,
        max_bytes: int = 2_000_000,
        require_https: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        validate_submission_url(url, require_https=require_https)
        self._url = url
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._transport = transport

    async def run_once(self, task: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            resp = await client.post(self._url, json=task)
        resp.raise_for_status()
        if len(resp.content) > self._max_bytes:
            raise ValueError("submission response exceeds size cap")
        data = resp.json()
        if not isinstance(data, dict):
            raise TypeError("submission endpoint must return a JSON object")
        data = cast("dict[str, Any]", data)
        return trajectory_from_history(
            data.get("history", []),
            cost=float(data.get("cost", 0.0) or 0.0),
            tokens=data.get("tokens"),
        )


def _as_runner(agent: AgentCallable | AgentRunner) -> AgentRunner:
    return agent if isinstance(agent, AgentRunner) else ExternalAgentRunner(agent)


async def run_external_scenario(
    scenario: Scenario, agent: AgentCallable | AgentRunner, *, k: int | None = None
) -> SubmissionRun:
    """Run an external HAL-compatible agent against `scenario` k times and score.

    `agent` is either a callable (in-process) or an `AgentRunner` (e.g. the remote
    `HttpAgentRunner`). Failproof per resample: an agent crash fails only that
    trajectory (scored a fail), never aborts the run — mirrors `runner.run_scenario`.
    """
    kk = k or scenario.k
    runner = _as_runner(agent)
    task = task_from_scenario(scenario)
    trajs: list[dict[str, Any]] = []
    det: list[list[dict[str, Any]]] = []
    passes = 0
    total_cost = 0.0
    for _ in range(kk):
        try:
            traj = await runner.run_once(task)
        except Exception as exc:  # harness records the error, keeps going (failproof)
            traj: dict[str, Any] = {
                "answer": "",
                "tools": [],
                "refused": False,
                "cost": 0.0,
                "error": str(exc)[:200],
            }
        result = score_deterministic(scenario, traj)
        trajs.append(traj)
        det.append(result.per_check)
        passes += int(result.passed)
        total_cost += float(traj.get("cost", 0.0))
    return SubmissionRun(scenario.id, kk, trajs, passes, passes / kk, total_cost, det)
