"""clarify — the agent asks the user a structured question and awaits the answer.

Mirrors Claude Code's AskUserQuestion / Hermes' clarify, and is the channel the
planning Analyst uses to propose canon restructures. Routes through an injected
ClarifyCtx.ask callback (the cockpit wires it to a real prompt; headless runs
degrade gracefully so nothing hangs). Toolset 'clarify' is auto-allowed — asking
the user is non-destructive — and still rank-gated + 8-dim-eval'd + audited.
"""

from __future__ import annotations

import inspect
from typing import Any, cast

from madras.models.agent_config import Rank
from madras.tools.clarify_context import get_clarify_ctx
from madras.tools.registry import ToolResult, tool

_MAX_OPTIONS = 8


def _normalize_options(raw: Any) -> list[dict[str, str]] | None:
    """Accept options as plain strings OR {label, description} objects → structured list."""
    if not isinstance(raw, list) or not raw:
        return None
    out: list[dict[str, str]] = []
    for o in cast("list[Any]", raw)[:_MAX_OPTIONS]:
        if isinstance(o, dict):
            o = cast("dict[str, Any]", o)
            label = str(o.get("label", "")).strip()
            if label:
                out.append({"label": label, "description": str(o.get("description", "")).strip()})
        else:
            label = str(o).strip()
            if label:
                out.append({"label": label, "description": ""})
    return out or None


def _accepts_multi(fn: Any) -> bool:
    """True if the ask callback can take a 3rd (multi_select) arg — back-compat with 2-arg asks."""
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return False
    if any(p.kind == p.VAR_POSITIONAL for p in params):
        return True
    pos = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    return len(pos) >= 3


@tool(
    name="clarify",
    toolset="clarify",
    rank_required=Rank.INTERN,
    description=(
        "Ask the user a single focused question and wait for their answer. Use ONLY "
        "when genuinely blocked on a decision the user must make (ambiguous intent, a "
        "fork with no safe default). Provide 'options' (each {label, description}) for a "
        "multiple-choice question, set 'multi_select' to allow several; omit options for "
        "free text. Returns the user's answer."
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "the question to ask the user"},
            "options": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "description": {
                            "type": "string",
                            "description": "what this option means / its trade-off",
                        },
                    },
                    "required": ["label"],
                },
                "description": "choices for a multiple-choice question; omit for free text",
            },
            "multi_select": {
                "type": "boolean",
                "default": False,
                "description": "allow the user to pick more than one option",
            },
        },
        "required": ["question"],
    },
)
async def clarify(args: dict[str, Any]) -> ToolResult:
    question = str(args.get("question", "")).strip()
    if not question:
        return ToolResult(ok=False, error="question is required")
    options = _normalize_options(args.get("options"))
    multi_select = bool(args.get("multi_select", False))

    ctx = get_clarify_ctx()
    if ctx is None or ctx.ask is None:
        # Headless / no user channel — don't hang; tell the model to proceed.
        return ToolResult(
            ok=False,
            error="[NO-USER] no interactive channel; make the best decision and proceed.",
        )
    try:
        if _accepts_multi(ctx.ask):
            answer = await ctx.ask(question, options, multi_select)
        else:
            answer = await ctx.ask(question, options)  # back-compat 2-arg channel
    except Exception as exc:
        return ToolResult(ok=False, error=f"clarify failed: {type(exc).__name__}: {exc}")

    answer = str(answer).strip()
    if not answer:
        return ToolResult(ok=False, error="no answer provided")

    # Post-clarification absorption: persist the answer as a durable preference so it's
    # recalled later and NEVER re-asked ("Clarification Is Not Enough" — using the answer
    # is the real bottleneck). Best-effort; never blocks the result.
    await _absorb(question, answer)

    return ToolResult(
        ok=True,
        content=answer,
        extras={"question": question, "options": options, "multi_select": multi_select},
    )


async def _absorb(question: str, answer: str) -> None:
    try:
        import time
        import uuid

        from madras.memory.retrieval import MemoryItem
        from madras.tools.memory_fabric_context import get_memory_fabric_ctx

        mctx = get_memory_fabric_ctx()
        if mctx is None or getattr(mctx, "fabric", None) is None:
            return
        now = time.time()
        subject = question.rstrip("?").strip()[:80] or "user clarification"
        await mctx.fabric.remember(
            MemoryItem(
                id=uuid.uuid4().hex,
                kind="preference",
                subject=subject,
                content=f"On '{subject}': {answer}",
                source="clarify",
                session_id=getattr(mctx, "session_id", ""),
                agent_name=getattr(mctx, "agent_name", "shadow"),
                created_at=now,
                valid_from=now,
            ),
            now=now,
        )
    except Exception:
        pass
