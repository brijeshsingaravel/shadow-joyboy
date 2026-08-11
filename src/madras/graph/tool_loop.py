"""Tool-execution loop node for Madras agents.

Implements an agentic tool loop with a mandatory circuit breaker (max_iters).
The LLM calls tools in a loop until it produces a final answer or the iteration
ceiling is hit. All governance flows through GovernedExecutor (ASI03 rank gate
+ 8-dim eval signals + audit log).

Sandbox lifecycle: if the requested toolsets intersect the dangerous-tool set
(shell / code / file_write), a sandbox is started before the loop and stopped
in a finally clause. Dangerous tools read the active sandbox via sandbox_context.

Human-in-the-loop (M2C): run_agentic_loop() is the resumable core. When
on_ask="pause" and a tool batch contains an ASK decision, it returns LoopPaused
without executing anything. resume_agentic_loop() re-enters the loop at exactly
the stashed batch position — nothing re-executes.
"""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.messages import AIMessage, HumanMessage

from madras.eval_.emitter import emit_action_signals
from madras.factory.spawn import AgentRecord
from madras.graph.action_preview import build_action_preview
from madras.graph.prompt_builder import assemble_system_prompt
from madras.graph.state import AgentState
from madras.llm.decode import repair_tool_args
from madras.llm.gateway import LLMGateway, LLMRequest
from madras.models.agent_config import Rank
from madras.security.guardrails import GuardrailEngine
from madras.security.permissions import (
    Decision,
    PermissionEngine,
    PermissionMode,
    PermissionRule,
    canonical_arg,
)
from madras.tools.registry import GovernedExecutor, ToolDenied, ToolRegistry

# Toolsets that require a live sandbox — tools in these sets call get_active_sandbox()
_SANDBOX_TOOLSETS: frozenset[str] = frozenset({"shell", "code", "file_write"})


def _preview(value: Any, *, limit: int = 600) -> str:
    """Bounded string preview of tool args/results for live streaming.

    Used only to populate the cockpit's Technical tool-view. Always returns a
    short string; never raises. Long values are truncated with an ellipsis.
    """
    try:
        if isinstance(value, str):
            text = value
        else:
            text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _seed_seen_ok(messages: list[dict[str, Any]]) -> set[tuple[str, int]]:
    """Reconstruct the redundant-success cache from an incoming message thread.

    s37 hardening finding: `seen_ok` is local to a single run_agentic_loop() call,
    so a call approved+executed in an EARLIER, separate invocation (a resumed
    pause, or any fresh call carrying real conversation history) was invisible to
    THIS invocation's dedup guard — a genuinely-succeeded tool call could be
    re-requested and re-executed on a later turn. Every assistant tool_calls entry
    is paired with its tool-role response (matched by tool_call_id); a response
    that doesn't start with the loop's own ERROR:/DENIED: prefixes is treated as a
    real success and seeded the same way a live execution would cache it.
    """
    results_by_id: dict[str, str] = {
        str(m["tool_call_id"]): str(m.get("content", ""))
        for m in messages
        if m.get("role") == "tool" and m.get("tool_call_id") is not None
    }
    seeded: set[tuple[str, int]] = set()
    for m in messages:
        if m.get("role") != "assistant":
            continue
        tool_calls: list[dict[str, Any]] = m.get("tool_calls") or []
        for tc in tool_calls:
            fn: dict[str, Any] = tc.get("function") or {}
            name = fn.get("name")
            call_id = tc.get("id")
            if not name or call_id is None:
                continue
            content = results_by_id.get(str(call_id))
            if content is None or content.startswith(("ERROR:", "DENIED:")):
                continue
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            seeded.add((str(name), hash(json.dumps(args, sort_keys=True, default=str))))
    return seeded


# ---------------------------------------------------------------------------
# Return types for run_agentic_loop
# ---------------------------------------------------------------------------


@dataclass
class LoopPaused:
    """Returned when on_ask='pause' and a batch contains an ASK decision.

    The loop has NOT executed any tool in the batch. The caller stashes this,
    surfaces the pending approval to the human, then calls resume_agentic_loop().
    """

    messages: list[dict[str, Any]]  # OpenAI messages, last is the assistant tool_call msg
    batch: list[dict[str, Any]]  # pending tool_calls: [{"id","name","arguments"}, ...]
    pending: dict[str, Any]  # first ASK call: {"tool","args","toolset","reason","call_id"}
    tools_used: list[str]


@dataclass
class LoopDone:
    """Returned when the loop reaches a final text (no more tool calls or circuit breaker)."""

    text: str
    tools_used: list[str]
    cost_usd: float
    latency_ms: float
    trajectory: list[str]
    tests_passed: bool | None = None


# ---------------------------------------------------------------------------
# Core resumable loop
# ---------------------------------------------------------------------------


async def run_agentic_loop(
    *,
    gateway: LLMGateway,
    model: str,
    schemas: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    executor: GovernedExecutor,
    permission_engine: PermissionEngine,
    mode: PermissionMode,
    rules: list[PermissionRule],
    granted: dict[tuple[str, str], Decision],  # (tool_name, canonical_arg) -> Decision
    agent_name: str,
    session_id: str,
    agent_rank: Rank,
    registry: ToolRegistry,
    max_iters: int,
    guard: GuardrailEngine | None,
    system_prompt: str | None,
    on_ask: str,  # "pause" | "deny"
    resume_batch: list[dict[str, Any]] | None = None,  # when set, use as first iteration batch
    episodic: Any = None,  # EpisodicMemory or duck-typed; None -> skip compaction persist
    compact_threshold: int = 3000,  # token estimate above which compaction fires
    verify_before_done: bool = False,  # verify-then-fix reflex for coding agents
    on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    temperature: float = 0.2,  # low by default — high temp degrades tool-call emission
    tool_mask: Any = None,  # B4 action-space mask: keep schema stable, reject masked calls
    hooks: Any = None,  # B5 HookRegistry: deterministic pre_tool_use blocking + feedback
    self_critique: bool = False,  # s46: action-level Reflexion on a failed tool call
    self_critique_max_retries: int = 1,
    judgment_engine: bool = False,  # s46: bias-check a repeated_failure impasse
    seed: int | None = None,  # eval-lab T2.10: reproducible sampling when set
) -> LoopDone | LoopPaused:
    """The resumable agentic loop.

    Normal entry: resume_batch=None — calls gateway on every iteration.
    Resume entry: resume_batch=<stashed batch> — re-evaluates that batch with
    the updated `granted` dict, then continues normally. The gateway is NOT
    called for this first batch (avoids double-execution).

    on_event: optional fire-and-forget async hook for live streaming. When set,
    it is invoked at existing points (tool_call / tool_result / guardrail) WITHOUT
    affecting control flow. Default None leaves every existing caller unchanged.
    Any exception raised by on_event is swallowed so it can never break the loop.
    """

    async def _emit(evt: dict[str, Any]) -> None:
        if on_event is None:
            return
        try:
            await on_event(evt)
        except Exception:
            pass  # fire-and-forget: a streaming hook must never break the loop

    tools_used: list[str] = list([])
    total_cost: float = 0.0
    total_latency: float = 0.0
    start = time.perf_counter()

    # s46: Context Discipline (row context-discipline) -- ToolMask (graph/tool_mask.py) was
    # built with zero live callers/constructors. Plan mode already blocked mutating tools,
    # but only at EXECUTE time inside GovernedExecutor; auto-masking them here too rejects
    # at DISPATCH time (cheaper) via the mechanism Manus's pattern actually calls for
    # (mask, don't remove from the schema -- keeps the KV-cache prefix stable). A caller-
    # supplied tool_mask always wins; this only fills the gap when none was given.
    if tool_mask is None and getattr(executor, "plan_mode", False):
        from madras.graph.tool_mask import mask_mutating_tools

        tool_mask = mask_mutating_tools(registry, schemas=schemas)

    from madras.obs.langfuse_client import start_turn_trace

    langfuse_trace_id = start_turn_trace(
        session_id=session_id, agent_name=agent_name, rank=agent_rank.value
    )

    # Stuck-loop detection: track the last 10 executed calls as
    # (tool_name, arghash, ok). When the same failing (tool, arghash) recurs 3x
    # we nudge once; if it recurs again after the nudge we early-terminate.
    recent_calls: deque[tuple[str, int, bool]] = deque(maxlen=10)
    nudged_stuck: set[tuple[str, int]] = set()
    # s46: SelfMonitor/detect_impasse (metacog/detect.py, row W5-F4) had zero live callers --
    # only ONE of its four impasse kinds (repeated_failure) was ever surfaced, via a
    # hand-built Impasse bypassing detect_impasse() entirely. This SelfMonitor catches the
    # other three: no_progress (different failing calls, never repeating exactly -- the
    # existing recent_calls guard above only catches an EXACT repeat), low_confidence, and
    # choice_paralysis. Soft nudge, not a hard stop (recent_calls' exact-repeat guard above
    # keeps that harder safety net); nudges once per impasse kind so it doesn't spam.
    from madras.metacog import Outcome, SelfMonitor

    self_monitor = SelfMonitor()
    nudged_impasse_kinds: set[str] = set()
    # Redundant-success suppression: a weak model often re-calls the SAME tool with
    # identical args after it already succeeded, burning iterations to the cap. Track
    # the cached success keys so an identical repeat is short-circuited, not re-run.
    # Seeded from any prior-turn history already in `messages` (s37) so the guard
    # survives across a pause/resume boundary or any fresh call carrying real history.
    seen_ok: set[tuple[str, int]] = _seed_seen_ok(messages)

    # Verify-then-fix reflex: coding tools that mutate state require a run_tests
    # before the loop is allowed to finish. Fires the nudge at most once.
    _coding_tools = frozenset({"file_write", "file_edit", "code_exec", "terminal"})
    verify_nudged = False
    # Empty-completion resilience: nudge-and-retry at most once when the model
    # returns a blank message with tools attached (FREE-tier flakiness).
    empty_retried = False
    # Latest run_tests outcome (extras["tests_passed"]); None until any run_tests ran.
    latest_tests_passed: bool | None = None
    # Last run_tests structured failure node ids, for the fix-until-green nudge.
    latest_failed_nodeids: list[str] = []
    # Bounded fix-until-green: re-nudge with the actual failures when the model
    # tries to finish on red. Capped so a model that can't fix still terminates.
    fix_rounds = 0
    _MAX_FIX_ROUNDS = 3

    _resume_batch = resume_batch  # consumed on first iter, then None

    for _iter in range(max_iters):
        # ---- Obtain the current tool_calls batch ----
        if _resume_batch is not None:
            # Resume path: re-evaluate the stashed batch without an LLM call.
            batch_dicts = _resume_batch
            _resume_batch = None  # only used once
            # Reconstruct a minimal tool_calls list for decision logic below.
            # (messages already has the assistant tool_call msg from the pause)
        else:
            # Normal path: maybe compact before calling the LLM.
            from madras.graph.compaction import maybe_compact

            messages, _comp = await maybe_compact(
                messages,
                gateway=gateway,
                model=model,
                session_id=session_id,
                agent_name=agent_name,
                threshold_tokens=compact_threshold,
                episodic=episodic,
                langfuse_trace_id=langfuse_trace_id,
                seed=seed,
            )

            # Re-inject the live plan checklist as the last system-positioned reminder.
            # Keeps the current plan in front of the model every turn (anti-drift)
            # without a DB read — reads the in-memory Plan held on the active context.
            from madras.graph.prompt_builder import render_plan_block
            from madras.tools.plan_context import get_active_plan

            _plan_ctx = get_active_plan()
            if _plan_ctx is not None and _plan_ctx.current_plan is not None:
                _plan_block = render_plan_block(_plan_ctx.current_plan)
                if _plan_block:
                    messages = [
                        m
                        for m in messages
                        if not (
                            m.get("role") == "system"
                            and isinstance(m.get("content"), str)
                            and m["content"].lstrip().startswith("# Plan:")
                        )
                    ]
                    messages.append({"role": "system", "content": _plan_block})

            req = LLMRequest(
                model=model,
                messages=messages,
                tools=schemas or None,
                temperature=temperature,
                seed=seed,
                metadata={
                    "session_id": session_id,
                    "agent_name": agent_name,
                    "langfuse_trace_id": langfuse_trace_id,
                },
            )
            resp = await gateway.complete(req)
            total_cost += resp.cost_usd
            total_latency += resp.latency_ms

            if not resp.tool_calls:
                # Verify-then-fix reflex: if a coding tool ran but tests never did,
                # nudge once and continue the loop to force a run_tests before finish.
                if (
                    verify_before_done
                    and not verify_nudged
                    and "run_tests" not in tools_used
                    and any(t in _coding_tools for t in tools_used)
                ):
                    verify_nudged = True
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "[VERIFY] You changed code but haven't run tests. "
                                "Call run_tests, fix any failures, then finish."
                            ),
                        }
                    )
                    continue

                # Fix-until-green: tests RAN and FAILED, but the model is trying to
                # finish on red. Re-nudge with the concrete failing node ids, up to
                # _MAX_FIX_ROUNDS, then let it terminate (bounded so it can't hang).
                if (
                    verify_before_done
                    and latest_tests_passed is False
                    and fix_rounds < _MAX_FIX_ROUNDS
                ):
                    fix_rounds += 1
                    _failing = ", ".join(latest_failed_nodeids[:10]) or "(see last output)"
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                f"[FIX {fix_rounds}/{_MAX_FIX_ROUNDS}] Tests are still "
                                f"failing: {_failing}. Fix the cause, then call "
                                "run_tests (you may pass only_failed=true) before finishing."
                            ),
                        }
                    )
                    continue

                # Empty-completion resilience: a weak FREE-tier model with tool
                # schemas attached sometimes returns an empty message (no content,
                # no parseable tool_calls) — which would otherwise be returned as a
                # blank final answer and fail the scenario. Nudge once and retry
                # before giving up, instead of silently emitting "".
                if not (resp.text or "").strip() and schemas and not empty_retried:
                    empty_retried = True
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "[RETRY] Your previous response was empty. Either CALL one of "
                                "the available tools to take the next action, or give a concise "
                                "final text answer. Do not return an empty message."
                            ),
                        }
                    )
                    continue

                # Final answer.
                response_text = resp.text or ""
                output_blocked = False
                if guard is not None:
                    ov = guard.inspect_output(response_text, system_prompt=system_prompt or "")
                    if not ov.allowed:
                        response_text = ov.safe_response or ""
                        output_blocked = True
                        await _emit({"type": "guardrail", "blocked": True})

                wall_ms = (time.perf_counter() - start) * 1000.0
                trace = (
                    ["tool_agent"]
                    + [f"tool:{n}" for n in tools_used]
                    + (["guardrail_output_block"] if output_blocked else [])
                )
                return LoopDone(
                    text=response_text,
                    tools_used=tools_used,
                    cost_usd=total_cost,
                    latency_ms=round(max(wall_ms, total_latency), 3),
                    trajectory=trace,
                    tests_passed=latest_tests_passed,
                )

            # Append assistant tool_call message to thread.
            messages.append(
                {
                    "role": "assistant",
                    "content": resp.text or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": tc.arguments,
                            },
                        }
                        for tc in resp.tool_calls
                    ],
                }
            )
            batch_dicts = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in resp.tool_calls
            ]

        # ---- Decide whole batch before executing anything ----
        decisions: list[tuple[dict[str, Any], Decision]] = []
        first_ask: dict[str, Any] | None = None

        for call in batch_dicts:
            cname = call["name"]
            try:
                cargs: dict[str, Any] = json.loads(call["arguments"])
            except (json.JSONDecodeError, ValueError):
                cargs = {}

            spec = registry.get(cname)
            toolset = spec.toolset if spec is not None else "unknown"
            carg_str = canonical_arg(cname, cargs)

            # granted dict takes precedence (one-time approvals from this run)
            if (cname, carg_str) in granted:
                dec = granted[(cname, carg_str)]
            else:
                dec = permission_engine.check(
                    tool=cname,
                    toolset=toolset,
                    args=cargs,
                    mode=mode,
                    rules=rules,
                )

            if dec is Decision.ASK:
                if on_ask == "pause":
                    if first_ask is None:
                        first_ask = {
                            "tool": cname,
                            "args": cargs,
                            "toolset": toolset,
                            "reason": f"{cname} requires approval",
                            "call_id": call["id"],
                            # E-E16: render the drafted artifact, not just "requires approval"
                            "preview": build_action_preview(cname, cargs),
                        }
                else:
                    # on_ask == "deny" — treat ASK as DENY
                    dec = Decision.DENY

            decisions.append((call, dec))

        # ---- Pause if any ASK in batch and on_ask="pause" ----
        if first_ask is not None:
            return LoopPaused(
                messages=messages,
                batch=batch_dicts,
                pending=first_ask,
                tools_used=list(tools_used),
            )

        # ---- Execute batch ----
        for call, dec in decisions:
            cname = call["name"]
            # B4 action-space mask: tool stays in the schema (stable prefix), but a masked
            # call is rejected with a teach-back rather than executed.
            if tool_mask is not None and tool_mask.is_masked(cname):
                messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": tool_mask.reason(cname)}
                )
                continue
            # B5 pre_tool_use hook: a user-defined hook can deterministically BLOCK the call.
            if hooks is not None:
                _hr = await hooks.dispatch("pre_tool_use", {"tool": cname, "args": call})
                if not _hr.allow:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": f"[HOOK_BLOCKED] {_hr.message}",
                        }
                    )
                    continue
            # Validate args BEFORE executing. A weak model (llama-70b) often emits
            # malformed JSON or omits required fields; instead of silently running
            # the tool with {}, teach the model so it can self-correct next turn.
            _repaired = False
            try:
                cargs: dict[str, Any] = json.loads(call["arguments"])
            except (json.JSONDecodeError, ValueError):
                # Track 3.3: weak local models often emit fenced/single-quoted/
                # Python-literal args. Try a deterministic repair before giving up.
                _spec_repair = registry.get(cname)
                _rep = repair_tool_args(
                    call["arguments"],
                    _spec_repair.parameters if _spec_repair is not None else None,
                )
                if _rep.ok:
                    cargs = _rep.args
                    _repaired = True
                else:
                    snippet = str(call["arguments"])[:120]
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": (
                                f"[INVALID_ARGUMENTS] arguments were not valid JSON: "
                                f"{snippet}; resend the tool call with valid JSON."
                            ),
                        }
                    )
                    continue

            spec = registry.get(cname)
            if spec is not None:
                required: Any = spec.parameters.get("required")
                if isinstance(required, list):
                    required = cast("list[str]", required)
                    missing = [k for k in required if k not in cargs]
                    if missing:
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "content": (
                                    f"[INVALID_ARGUMENTS] missing required field(s): "
                                    f"{', '.join(missing)} for tool {cname!r}; "
                                    f"resend with all required fields."
                                ),
                            }
                        )
                        continue

            # ---- Redundant identical-call short-circuit ----
            # If this exact (tool, args) already succeeded this run, don't re-execute
            # (avoids wasted iterations + repeated side effects); nudge toward the answer.
            _exact_key = (cname, hash(json.dumps(cargs, sort_keys=True, default=str)))
            if dec is not Decision.DENY and _exact_key in seen_ok:
                await _emit({"type": "tool_result", "tool": cname, "ok": True, "deduped": True})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": (
                            f"[ALREADY_CALLED] You already called {cname!r} with these exact "
                            "arguments and its result is above. Do not call it again — use that "
                            "result to write your final answer now."
                        ),
                    }
                )
                continue

            if dec is Decision.DENY:
                content = f"DENIED: permission denied for tool {cname!r}"
                result_ok = False
                await _emit({"type": "tool_result", "tool": cname, "ok": False})
            else:
                # ALLOW. Include a bounded, safe preview of the args so the
                # cockpit's Technical tool-view can render raw inputs. Never
                # include credentials — cargs are model-supplied tool inputs.
                _call_evt: dict[str, Any] = {
                    "type": "tool_call",
                    "tool": cname,
                    "args": _preview(cargs),
                }
                if _repaired:
                    _call_evt["repaired"] = True
                await _emit(_call_evt)
                _result_evt: dict[str, Any] = {}
                try:
                    if self_critique:
                        # s46: on failure, ask the model to critique its own call and
                        # retry with corrected args (bounded) -- see self_critique.py.
                        from madras.graph.self_critique import run_with_self_critique

                        async def _exec(a: dict[str, Any], _c: str = cname) -> Any:
                            return await executor.execute(
                                tool_name=_c,
                                args=a,
                                agent_name=agent_name,
                                session_id=session_id,
                                agent_rank=agent_rank,
                                langfuse_trace_id=langfuse_trace_id,
                            )

                        result, _sc_attempts, _sc_reasons = await run_with_self_critique(
                            execute=_exec,
                            tool=cname,
                            args=cargs,
                            gateway=gateway,
                            model=model,
                            max_retries=self_critique_max_retries,
                        )
                    else:
                        result = await executor.execute(
                            tool_name=cname,
                            args=cargs,
                            agent_name=agent_name,
                            session_id=session_id,
                            agent_rank=agent_rank,
                            langfuse_trace_id=langfuse_trace_id,
                        )
                    content = result.content if result.ok else f"ERROR: {result.error}"
                    result_ok = result.ok
                    extras: dict[str, Any] = result.extras
                    if cname == "run_tests":
                        _tp = extras.get("tests_passed")
                        if isinstance(_tp, bool):
                            latest_tests_passed = _tp
                        _fn = extras.get("failed_nodeids")
                        if isinstance(_fn, list):
                            latest_failed_nodeids = [str(x) for x in cast("list[Any]", _fn)]
                    # File-tool diff capture (file_edit / file_write): surface the
                    # bounded, secret-free diff extras so the cockpit can render an
                    # OpenCode-style inline diff. Additive — control flow unchanged.
                    if result_ok and "diff" in extras:
                        _result_evt.update(
                            {
                                "diff": extras.get("diff", ""),
                                "path": extras.get("path", ""),
                                "added": extras.get("added", 0),
                                "removed": extras.get("removed", 0),
                                "new_file": bool(extras.get("new_file", False)),
                            }
                        )
                except ToolDenied as exc:
                    content = f"DENIED: {exc}"
                    result_ok = False
                _result_evt.update(
                    {
                        "type": "tool_result",
                        "tool": cname,
                        "ok": result_ok,
                        "result": _preview(content),
                    }
                )
                await _emit(_result_evt)

            tools_used.append(cname)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": content,
                }
            )

            # ---- Metacognitive self-monitoring (soft nudge; not a hard stop) ----
            self_monitor.record(Outcome(tool=cname, ok=result_ok, progressed=result_ok))
            _impasse = self_monitor.check()
            if _impasse is not None and _impasse.kind not in nudged_impasse_kinds:
                nudged_impasse_kinds.add(_impasse.kind)
                from madras.metacog import recommend_subgoal as _recommend_subgoal

                _nudge_text = (
                    f"[IMPASSE:{_impasse.kind}] {_impasse.detail}. {_recommend_subgoal(_impasse)}"
                )

                # s46: Judgment Engine -- a repeated_failure impasse is exactly where
                # confirmation/sunk-cost bias shows up (retrying because of prior
                # investment, not new evidence). One cheap judge call, opt-in only.
                if judgment_engine and _impasse.kind == "repeated_failure":
                    from madras.metacog import judge_decision as _judge_decision

                    _evidence = "; ".join(
                        f"{o.tool}: {'ok' if o.ok else 'failed'}"
                        for o in self_monitor.outcomes[-5:]
                    )
                    try:
                        _verdict = await _judge_decision(
                            gateway=gateway,
                            model=model,
                            decision=f"retry {cname!r} again",
                            evidence=_evidence,
                        )
                    except Exception:
                        _verdict = None
                    if _verdict is not None and _verdict.biased:
                        _nudge_text += (
                            f" [BIAS-CHECK: {_verdict.bias_kind}] {_verdict.reason} "
                            f"{_verdict.recommendation}"
                        )

                    # row overcoming-engine — the missing push-vs-quit DECISION: a
                    # sunk-cost-biased verdict used to only become nudge text, then the
                    # loop kept going regardless. Now it actually stops, distinctly from
                    # the blunter exact-repeat stuck-loop guard below.
                    from madras.metacog.overcoming import PersistenceDecision, decide_persistence

                    if decide_persistence(_verdict) is PersistenceDecision.WISE_QUIT:
                        wall_ms = (time.perf_counter() - start) * 1000.0
                        quit_text = (
                            "I recognized I was retrying "
                            f"{cname!r} from sunk cost, not new evidence, and stopped "
                            f"rather than keep pushing. Here's what I tried: "
                            f"{', '.join(tools_used)}."
                        )
                        return LoopDone(
                            text=quit_text,
                            tools_used=tools_used,
                            cost_usd=total_cost,
                            latency_ms=round(max(wall_ms, total_latency), 3),
                            trajectory=(
                                ["tool_agent"] + [f"tool:{n}" for n in tools_used] + ["wise_quit"]
                            ),
                        )

                messages.append({"role": "system", "content": _nudge_text})

            # ---- Stuck-loop detection ----
            arghash = hash(canonical_arg(cname, cargs))
            key = (cname, arghash)
            recent_calls.append((cname, arghash, result_ok))
            if result_ok:
                # cache EXACT args (not the permission-canonical form, which drops values)
                # so only a truly identical repeat is short-circuited
                seen_ok.add((cname, hash(json.dumps(cargs, sort_keys=True, default=str))))
            if not result_ok:
                fail_count = sum(
                    1 for (t, h, ok) in recent_calls if t == cname and h == arghash and not ok
                )
                if fail_count >= 3:
                    if key not in nudged_stuck:
                        # First time stuck: nudge once and let the model retry.
                        nudged_stuck.add(key)
                        from madras.metacog import Impasse, recommend_subgoal

                        _strategy = recommend_subgoal(
                            Impasse("repeated_failure", f"{cname} failed 3x")
                        )
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    f"[STUCK] You have repeated the same failing call to "
                                    f"{cname!r} 3 times (an impasse). {_strategy}"
                                ),
                            }
                        )
                    else:
                        # Recurred after the nudge: early-terminate honestly.
                        wall_ms = (time.perf_counter() - start) * 1000.0
                        stuck_text = (
                            f"I got stuck repeating the same failing action ({cname!r}) "
                            f"and stopped to avoid wasting steps. Here's what I tried: "
                            f"{', '.join(tools_used)}."
                        )
                        trace_stuck = (
                            ["tool_agent"] + [f"tool:{n}" for n in tools_used] + ["stuck_loop"]
                        )
                        return LoopDone(
                            text=stuck_text,
                            tools_used=tools_used,
                            cost_usd=total_cost,
                            latency_ms=round(max(wall_ms, total_latency), 3),
                            trajectory=trace_stuck,
                        )

        # Continue loop for next LLM call.

    # ---- Circuit breaker ----
    wall_ms = (time.perf_counter() - start) * 1000.0
    cb_text = "I've reached my step limit — let's check in."
    trace_cb = ["tool_agent"] + [f"tool:{n}" for n in tools_used] + ["circuit_breaker"]
    return LoopDone(
        text=cb_text,
        tools_used=tools_used,
        cost_usd=total_cost,
        latency_ms=round(wall_ms, 3),
        trajectory=trace_cb,
    )


async def resume_agentic_loop(
    *,
    paused: LoopPaused,
    decision: str,  # "allow" | "deny"
    scope: str,  # "once" | "always"
    gateway: LLMGateway,
    model: str,
    schemas: list[dict[str, Any]],
    executor: GovernedExecutor,
    permission_engine: PermissionEngine,
    mode: PermissionMode,
    rules: list[PermissionRule],
    agent_name: str,
    session_id: str,
    agent_rank: Rank,
    registry: ToolRegistry,
    max_iters: int,
    guard: GuardrailEngine | None,
    system_prompt: str | None,
    perm_store: Any,  # PermissionStore | None
    project: str,
    episodic: Any = None,
    compact_threshold: int = 3000,
    on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> LoopDone | LoopPaused:
    """Resume a paused loop after a human approval/denial.

    Builds a `granted` dict for the pending call and re-enters run_agentic_loop
    with `resume_batch=paused.batch` so the batch is re-evaluated (not re-fetched
    from the LLM), avoiding double-execution.
    """
    pending = paused.pending
    dec = Decision.ALLOW if decision == "allow" else Decision.DENY

    # Build granted mapping for the pending call.
    carg_str = canonical_arg(pending["tool"], pending["args"])
    granted: dict[tuple[str, str], Decision] = {(pending["tool"], carg_str): dec}

    # If scope=="always" and allow: persist a rule so future calls auto-allow.
    if scope == "always" and dec is Decision.ALLOW and perm_store is not None:
        try:
            from madras.security.permissions import PermissionRule as PR

            rule = PR(pending["tool"], carg_str or "*", Decision.ALLOW)
            await perm_store.add(project, rule)
        except Exception:
            pass  # degrade gracefully if DB down

    return await run_agentic_loop(
        gateway=gateway,
        model=model,
        schemas=schemas,
        messages=paused.messages,  # already includes the assistant tool_call msg
        executor=executor,
        permission_engine=permission_engine,
        mode=mode,
        rules=rules,
        granted=granted,
        agent_name=agent_name,
        session_id=session_id,
        agent_rank=agent_rank,
        registry=registry,
        max_iters=max_iters,
        guard=guard,
        system_prompt=system_prompt,
        on_ask="pause",  # subsequent ASKs in the same session still pause
        resume_batch=paused.batch,  # re-evaluate the stashed batch
        episodic=episodic,
        compact_threshold=compact_threshold,
        on_event=on_event,
    )


# ---------------------------------------------------------------------------
# Graph node (existing API — delegates to run_agentic_loop with on_ask="deny")
# ---------------------------------------------------------------------------


def build_tool_agent(
    *,
    gateway: LLMGateway,
    model: str,
    registry: ToolRegistry,
    executor: GovernedExecutor,
    agent: AgentRecord | None = None,
    toolsets: list[str] | None = None,
    max_iters: int = 5,
    guardrails_on: bool = True,
    guardrails: GuardrailEngine | None = None,
    project: str = "default",
    sandbox_toolsets: frozenset[str] | None = None,
    episodic: Any = None,
    compact_threshold: int = 3000,
    progressive: bool = False,
    hooks: Any = None,  # s46: HookRegistry -- was accepted by run_agentic_loop but never
    # forwarded from here, so pre_tool_use hooks (incl. user-authored
    # rails) were structurally unreachable from any real turn.
) -> Callable[[AgentState], Awaitable[dict[str, Any]]]:
    """Returns an async AgentState node that loops: LLM -> tool calls -> results -> LLM,
    until the model stops calling tools or max_iters is hit (circuit breaker).

    ASK decisions are treated as DENY in this graph-node path (non-interactive).
    """

    from madras.graph.orchestration import budget_for
    from madras.tools.delegation_context import DelegationCtx, TurnBudget, set_delegation_ctx

    _orch = budget_for(model)
    _deleg_guidance = _orch.delegation_guidance

    if agent is not None:
        from madras.factory.project_rules import load_project_rules
        from madras.tools.builtin._workspace import workspace_root

        _base_system_prompt: str | None = assemble_system_prompt(
            agent, project_rules=load_project_rules(workspace_root())
        )
    else:
        _base_system_prompt = None
    _delegation_active = toolsets is not None and "delegation" in toolsets

    # Inject tier-specific guidance into system prompt when delegation toolset is active.
    if _delegation_active and _base_system_prompt is not None:
        system_prompt = _base_system_prompt + "\n\n# Delegation\n" + _deleg_guidance
    elif _delegation_active and _base_system_prompt is None:
        system_prompt = "# Delegation\n" + _deleg_guidance
    else:
        system_prompt = _base_system_prompt

    guard: GuardrailEngine | None = (guardrails or GuardrailEngine()) if guardrails_on else None
    agent_rank: Rank = agent.config.rank if agent is not None else Rank.INTERN
    agent_name_str: str = agent.config.name if agent is not None else "unknown"
    _sbx_toolsets: frozenset[str] = (
        sandbox_toolsets if sandbox_toolsets is not None else _SANDBOX_TOOLSETS
    )
    _perm_engine = PermissionEngine()
    # Coding-capable agents get the verify-then-fix reflex; pure chat/research don't.
    _verify_before_done: bool = toolsets is not None and bool(
        set(toolsets) & {"shell", "code", "file_write"}
    )
    # s46: ExecutionConfig.self_critique existed with zero live callers -- run_with_self_critique
    # (graph/self_critique.py) was never invoked from any real turn. Off by default per its
    # own docstring; per-agent opt-in via role.yaml's execution.self_critique.
    _exec_cfg = agent.config.execution if agent is not None else None
    _self_critique: bool = bool(_exec_cfg is not None and _exec_cfg.self_critique)
    _self_critique_max_retries: int = _exec_cfg.self_critique_max_retries if _exec_cfg else 1
    # s46: Judgment Engine (metacog/judgment.py) -- off by default, same opt-in pattern.
    _judgment_engine: bool = bool(_exec_cfg is not None and _exec_cfg.judgment_engine)

    async def tool_agent(state: AgentState) -> dict[str, Any]:
        from madras.tools.sandbox import build_sandbox
        from madras.tools.sandbox_context import set_active_sandbox

        start = time.perf_counter()
        user_input: str = state.get("user_input", "") or ""
        session_id: str = state.get("session_id", "") or ""
        name: str = state.get("agent_name", "") or agent_name_str

        # --- Input guardrail (pre-LLM) ---
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
                new_msgs: list[Any] = []
                if user_input:
                    new_msgs.append(HumanMessage(content=user_input))
                new_msgs.append(AIMessage(content=safe_text))
                return {"messages": new_msgs, "eval_signals": signals}

        # --- Build initial messages ---
        if progressive:
            # Progressive tool disclosure: defer the long tail behind the discovery bridge
            # (tool_find/tool_describe/tool_call) routed through the same GovernedExecutor.
            from madras.tools.tool_discovery_context import (
                ToolDiscoveryCtx,
                set_discovery_ctx,
            )

            set_discovery_ctx(
                ToolDiscoveryCtx(
                    registry=registry,
                    executor=executor,
                    agent_name=name,
                    session_id=session_id or "",
                    agent_rank=agent_rank,
                    toolsets=toolsets,
                )
            )
            tool_schemas = registry.progressive_schemas(agent_rank=agent_rank, toolsets=toolsets)
        else:
            tool_schemas = registry.schemas(agent_rank=agent_rank, toolsets=toolsets)

        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        for m in state.get("messages", []) or []:
            if isinstance(m, HumanMessage):
                messages.append({"role": "user", "content": str(m.content)})
            elif isinstance(m, AIMessage):
                messages.append({"role": "assistant", "content": str(m.content)})
        if user_input:
            messages.append({"role": "user", "content": user_input})

        # --- Sandbox lifecycle ---
        _needs_sandbox = (
            toolsets is not None and bool(set(toolsets) & _sbx_toolsets) and bool(tool_schemas)
        )
        sandbox = None
        if _needs_sandbox:
            sandbox = build_sandbox(session_id=session_id or "sbx")
            await sandbox.start()
            set_active_sandbox(sandbox)

        # --- Delegation context (when delegation toolset is active) ---
        if _delegation_active:
            _deleg_ctx = DelegationCtx(
                gateway=gateway,
                registry=registry,
                executor=executor,
                permission_engine=_perm_engine,
                base_model=model,
                agent_name=name,
                session_id=session_id,
                depth=0,
                budget=TurnBudget(max_subagents=_orch.max_total),
                agent_rank=agent_rank,
            )
            set_delegation_ctx(_deleg_ctx)

        try:
            loop_result = await run_agentic_loop(
                gateway=gateway,
                model=model,
                schemas=tool_schemas,
                messages=messages,
                executor=executor,
                permission_engine=_perm_engine,
                mode=PermissionMode.BYPASS,  # graph node: non-interactive; rank gate via executor
                rules=[],
                granted={},
                agent_name=name,
                session_id=session_id,
                agent_rank=agent_rank,
                registry=registry,
                max_iters=max_iters,
                guard=guard,
                system_prompt=system_prompt,
                on_ask="deny",  # on_ask=deny as safety net; BYPASS means nothing reaches ASK anyway
                episodic=episodic,
                compact_threshold=compact_threshold,
                verify_before_done=_verify_before_done,
                hooks=hooks,
                self_critique=_self_critique,
                self_critique_max_retries=_self_critique_max_retries,
                judgment_engine=_judgment_engine,
            )
        finally:
            from madras.tools.sandbox_context import set_active_sandbox as _sas

            _sas(None)
            if _delegation_active:
                set_delegation_ctx(None)
            if sandbox is not None:
                await sandbox.stop()

        # run_agentic_loop with on_ask="deny" never returns LoopPaused.
        assert isinstance(loop_result, LoopDone)

        response_text = loop_result.text
        tools_used = loop_result.tools_used
        total_cost = loop_result.cost_usd
        total_latency = loop_result.latency_ms
        trace = loop_result.trajectory

        task_completion = (
            loop_result.tests_passed
            if loop_result.tests_passed is not None
            else bool(response_text.strip())
        )
        signals = emit_action_signals(
            {
                "task_completion": task_completion,
                "trajectory_trace": trace,
                "tool_calls": [{"name": n} for n in tools_used],
                "tool_selection": "correct" if tools_used else "none_required",
                "argument_correctness": True,
                "confidence": 0.7 if "circuit_breaker" not in trace else 0.0,
                "latency_ms": round(max((time.perf_counter() - start) * 1000.0, total_latency), 3),
                "cost_usd": total_cost,
            }
        )
        new_msgs_final: list[Any] = []
        if user_input:
            new_msgs_final.append(HumanMessage(content=user_input))
        new_msgs_final.append(AIMessage(content=response_text))
        return {"messages": new_msgs_final, "eval_signals": signals}

    return tool_agent
