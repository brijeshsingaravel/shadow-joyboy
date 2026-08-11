"""LLM responder node — produces an AIMessage and emits 8 per-action signals.

Replaces Phase 0's _noop_responder when a real LLM gateway is provided.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from madras.eval_.emitter import emit_action_signals
from madras.factory.spawn import AgentRecord
from madras.graph.state import AgentState
from madras.llm.gateway import LLMGateway, LLMRequest
from madras.mindpalace.ledger import MindPalaceLedger, SessionRecord
from madras.persona.anchor import build_session_start_anchor
from madras.security.guardrails import GuardrailEngine


def _state_to_messages(state: AgentState, *, system_prompt: str | None) -> list[dict[str, str]]:
    """Convert AgentState messages + user_input to LLM message format."""
    msgs: list[dict[str, str]] = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    for m in state.get("messages", []) or []:
        if isinstance(m, HumanMessage):
            msgs.append({"role": "user", "content": str(m.content)})
        elif isinstance(m, AIMessage):
            msgs.append({"role": "assistant", "content": str(m.content)})
    user_input = state.get("user_input", "")
    if user_input:
        msgs.append({"role": "user", "content": user_input})
    return msgs


def build_llm_responder(
    *,
    gateway: LLMGateway,
    model: str,
    agent: AgentRecord | None = None,
    ledger: MindPalaceLedger | None = None,
    project: str = "default",
    guardrails_on: bool = True,
    guardrails: GuardrailEngine | None = None,
) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
    """Build an async LLM responder node.

    Args:
        gateway: LLMGateway instance (handles backend dispatch, tracing, retries)
        model: Model ID (e.g., "anthropic/claude-haiku-4-5")
        agent: Optional AgentRecord for persona/system prompt
        ledger: Optional MindPalaceLedger for durable per-turn session rows
        project: Project name written to the ledger row (default "default")
        guardrails_on: Whether to run the GuardrailEngine (default True)
        guardrails: Optional pre-built GuardrailEngine; a new one is created if None
    """

    system_prompt: str | None = build_session_start_anchor(agent) if agent is not None else None
    guard: GuardrailEngine | None = (guardrails or GuardrailEngine()) if guardrails_on else None

    async def _write_ledger(
        state: AgentState,
        summary: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
    ) -> None:
        """Write a SessionRecord to the ledger; swallows exceptions."""
        if ledger is None:
            return
        try:
            session_id = state.get("session_id", "")
            prior = await ledger.get(session_id=session_id)
            agent_name: str = state.get("agent_name") or (
                agent.config.name if agent is not None else "unknown"
            )
            acc_tokens_in = (prior.tokens_in if prior else 0) + tokens_in
            acc_tokens_out = (prior.tokens_out if prior else 0) + tokens_out
            acc_cost = (prior.cost_usd if prior else 0.0) + cost_usd
            started_at = prior.started_at if prior else datetime.now(UTC)
            ended_at = datetime.now(UTC)
            rec = SessionRecord(
                session_id=session_id,
                agent_name=agent_name,
                started_at=started_at,
                ended_at=ended_at,
                summary=summary[:500],
                project=project,
                tokens_in=acc_tokens_in,
                tokens_out=acc_tokens_out,
                cost_usd=acc_cost,
                tools_used=prior.tools_used if prior else [],
                decisions=prior.decisions if prior else [],
                files_touched=prior.files_touched if prior else [],
                open_items=prior.open_items if prior else [],
                tags=prior.tags if prior else [],
            )
            await ledger.write(rec)
        except Exception as exc:
            print(f"mindpalace ledger write failed: {exc}", file=sys.stderr)

    async def llm_responder(state: AgentState) -> dict[str, Any]:
        start = time.perf_counter()
        user_input = state.get("user_input", "")

        # --- Input guard (pre-LLM) ---
        if guard is not None:
            iv = guard.inspect_input(user_input)
            if not iv.allowed:
                wall_ms = (time.perf_counter() - start) * 1000.0
                safe_text = iv.safe_response or ""
                signals = emit_action_signals(
                    {
                        "task_completion": False,
                        "trajectory_trace": ["guardrail_input_block"],
                        "tool_calls": [],
                        "tool_selection": "none_required",
                        "argument_correctness": True,
                        "confidence": 0.0,
                        "latency_ms": round(wall_ms, 3),
                        "cost_usd": 0.0,
                    }
                )
                new_msgs: list[BaseMessage] = []
                if user_input:
                    new_msgs.append(HumanMessage(content=user_input))
                new_msgs.append(AIMessage(content=safe_text))
                await _write_ledger(state, safe_text, 0, 0, 0.0)
                return {"messages": new_msgs, "eval_signals": signals}

        # --- Normal LLM call ---
        messages = _state_to_messages(state, system_prompt=system_prompt)
        req = LLMRequest(model=model, messages=messages)
        resp = await gateway.complete(req)
        wall_ms = (time.perf_counter() - start) * 1000.0

        response_text = resp.text

        # --- Output guard (post-LLM) ---
        output_blocked = False
        if guard is not None:
            ov = guard.inspect_output(resp.text, system_prompt=system_prompt or "")
            if not ov.allowed:
                response_text = ov.safe_response or ""
                output_blocked = True

        trace = ["llm_responder", "guardrail_output_block"] if output_blocked else ["llm_responder"]

        # All per-action signals flow through the emitter seam (CLAUDE.md):
        # emit_action_signals validates completeness against the §5 contract.
        signals = emit_action_signals(
            {
                "task_completion": bool(resp.text.strip()),
                "trajectory_trace": trace,
                "tool_calls": [],
                "tool_selection": "none_required",
                "argument_correctness": True,
                "confidence": 0.7,
                "latency_ms": round(max(wall_ms, resp.latency_ms), 3),
                "cost_usd": resp.cost_usd,
            }
        )
        new_msgs_normal: list[BaseMessage] = []
        if user_input:
            new_msgs_normal.append(HumanMessage(content=user_input))
        new_msgs_normal.append(AIMessage(content=response_text))

        await _write_ledger(
            state, response_text, resp.input_tokens, resp.output_tokens, resp.cost_usd
        )

        return {"messages": new_msgs_normal, "eval_signals": signals}

    return llm_responder
