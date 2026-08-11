"""Build a LangGraph for an agent. Phase 0 = minimal (no LLM); Phase 1 swaps in real LLM."""

from __future__ import annotations

import time
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph  # type: ignore[import-untyped]

from madras.factory.spawn import AgentRecord
from madras.graph.llm_responder import build_llm_responder
from madras.graph.state import AgentState
from madras.graph.tool_loop import build_tool_agent
from madras.llm.gateway import LLMGateway


def _noop_responder(state: AgentState) -> dict[str, Any]:
    """Phase-0 node — echoes the user input as an AIMessage.

    Phase 1 replaces this with the real LLM responder in M1B.
    """
    start = time.perf_counter()
    agent_name = state.get("agent_name", "?")
    user_input = state.get("user_input", "")
    response_text = f"[{agent_name}] heard: {user_input}"
    latency_ms = (time.perf_counter() - start) * 1000.0

    signals = {
        "task_completion": True,
        "trajectory_trace": ["noop_responder"],
        "tool_calls": [],
        "tool_selection": "none_required",
        "argument_correctness": True,
        "confidence": 0.5,
        "latency_ms": round(latency_ms, 3),
        "cost_usd": 0.0,
    }
    new_messages: list[Any] = []
    if user_input:
        new_messages.append(HumanMessage(content=user_input))
    new_messages.append(AIMessage(content=response_text))
    return {"messages": new_messages, "eval_signals": signals}


def build_minimal_graph(agent: AgentRecord, *, checkpointer: Any = None):
    """A graph that takes one turn and emits per-action signals."""
    graph = StateGraph(AgentState)
    graph.add_node("respond", _noop_responder)  # type: ignore[reportUnknownMemberType]
    graph.add_edge(START, "respond")
    graph.add_edge("respond", END)
    if checkpointer is None:
        return graph.compile()  # type: ignore[reportUnknownMemberType]
    return graph.compile(checkpointer=checkpointer)  # type: ignore[reportUnknownMemberType]


def build_llm_graph(
    agent: AgentRecord,
    *,
    gateway: LLMGateway,
    model: str = "anthropic/claude-haiku-4-5",
    checkpointer: Any = None,
    ledger: Any = None,
    project: str = "default",
    guardrails_on: bool = True,
    tools_on: bool = False,
    toolsets: list[str] | None = None,
    registry: Any = None,
    executor: Any = None,
    max_iters: int = 5,
    progressive: bool = False,
    plan_mode: bool = False,
    hooks: Any = None,  # s46: HookRegistry, forwarded to build_tool_agent -- see its own
    # docstring note; was accepted at the lowest layer but never
    # exposed up through build_llm_graph at all.
):
    """Build the graph with the real LLM responder.

    Phase 1 default model = Claude Haiku 4.5 (cheap tier per cost.py).
    ledger: optional MindPalaceLedger for durable per-turn session rows.
    project: project name written to ledger rows.
    guardrails_on: whether to run the GuardrailEngine (default True).
    tools_on: when True, use the tool-execution loop instead of the plain responder.
    toolsets: optional list of toolset names to restrict tool access.
    registry: ToolRegistry to use (default: global REGISTRY with builtins).
    executor: GovernedExecutor to use (default: auto-built from registry).
    max_iters: circuit-breaker iteration ceiling for the tool loop.
    """
    if tools_on:
        from madras.tools.builtin import (
            files,  # noqa: F401  # type: ignore[reportUnusedImport]
            tool_discovery,  # noqa: F401  # type: ignore[reportUnusedImport]
            web,  # noqa: F401  # type: ignore[reportUnusedImport]
        )
        from madras.tools.registry import REGISTRY, GovernedExecutor

        _registry = registry if registry is not None else REGISTRY
        _executor = (
            executor
            if executor is not None
            else GovernedExecutor(registry=_registry, audit=None, plan_mode=plan_mode)
        )
        if executor is not None and plan_mode:
            _executor.set_plan_mode(True)  # read-only exploration gate
        responder = build_tool_agent(
            gateway=gateway,
            model=model,
            registry=_registry,
            executor=_executor,
            agent=agent,
            toolsets=toolsets,
            max_iters=max_iters,
            guardrails_on=guardrails_on,
            project=project,
            progressive=progressive,
            hooks=hooks,
        )
    else:
        responder = build_llm_responder(
            gateway=gateway,
            model=model,
            agent=agent,
            ledger=ledger,
            project=project,
            guardrails_on=guardrails_on,
        )
    graph = StateGraph(AgentState)
    graph.add_node("respond", responder)  # type: ignore[reportUnknownMemberType]
    graph.add_edge(START, "respond")
    graph.add_edge("respond", END)
    if checkpointer is None:
        return graph.compile()  # type: ignore[reportUnknownMemberType]
    return graph.compile(checkpointer=checkpointer)  # type: ignore[reportUnknownMemberType]
