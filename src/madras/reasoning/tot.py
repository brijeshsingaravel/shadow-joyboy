"""Tree-of-Thoughts — beam search reasoning scaffold.

A minimal, governed Tree-of-Thoughts implementation for Madras agents.
Forked concept from princeton-nlp/tree-of-thought-llm (MIT), rebuilt around
Madras's own LLM gateway, task protocol, and metacog integration point.

Usage:
    task = make_tot_task(query, generate_fn, evaluate_fn)
    result = await beam_search(task, ToTConfig(width=3, depth=5))
    print(result.answer, result.score)

For LLM-backed usage:
    from madras.reasoning.tot import litellm_generate, litellm_evaluate
    result = await beam_search(task, cfg, generate_fn=gen, evaluate_fn=evl)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# ── Config ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToTConfig:
    """Beam search hyperparameters."""

    width: int = 3  # thoughts expanded per state (beam width)
    depth: int = 5  # max search depth
    temperature: float = 0.7


# ── Task protocol ───────────────────────────────────────────────────────────


@runtime_checkable
class ToTTask(Protocol):
    """The interface a problem must implement for beam search.

    Implementations define how thoughts are generated, evaluated, and
    when a state is terminal (solved).
    """

    @property
    def query(self) -> str: ...

    def initial_state(self) -> str: ...

    async def generate_thoughts(self, state: str, n: int, **kwargs: Any) -> list[str]: ...

    async def evaluate(self, state: str, thought: str, **kwargs: Any) -> float: ...

    async def is_terminal(self, state: str, **kwargs: Any) -> bool: ...


# ── Result ──────────────────────────────────────────────────────────────────


@dataclass
class ToTResult:
    """Output of a beam search."""

    answer: str = ""
    path: list[str] = field(default_factory=list)  # type: ignore[reportUnknownVariableType]
    score: float = 0.0
    steps: int = 0


# ── Beam search ─────────────────────────────────────────────────────────────


async def beam_search(
    task: ToTTask,
    config: ToTConfig | None = None,
    *,
    generate_fn: Callable[..., Awaitable[list[str]]] | None = None,
    evaluate_fn: Callable[..., Awaitable[float]] | None = None,
) -> ToTResult:
    """Run beam search over a ToTTask.

    At each depth level:
    1. For each beam state, generate `width` candidate thoughts.
    2. Evaluate each (state, thought) pair.
    3. Keep the top-`width` by score.
    4. If any reach a terminal state, return the best.

    The optional generate_fn/evaluate_fn overrides allow the caller to
    inject a real LLM backend (e.g. Madras's LiteLLM gateway) without
    the task needing to know about it.
    """
    cfg = config or ToTConfig()
    gen = generate_fn or task.generate_thoughts
    evl = evaluate_fn or task.evaluate

    initial = task.initial_state()
    # Each beam entry: (state, path, score)
    beams: list[tuple[str, list[str], float]] = [(initial, [], 0.0)]

    best_answer = ""
    best_score = float("-inf")
    best_path: list[str] = []

    for _depth in range(cfg.depth):
        candidates: list[tuple[str, list[str], float]] = []

        for state, path, _score in beams:
            if await task.is_terminal(state):
                final_score = _score
                if final_score > best_score:
                    best_score = final_score
                    best_answer = state
                    best_path = path
                continue

            thoughts: list[str] = await gen(state, cfg.width, temperature=cfg.temperature)  # type: ignore[misc]
            if not thoughts:
                continue

            eval_results: list[float | BaseException] = await asyncio.gather(
                *(evl(state, t, temperature=cfg.temperature) for t in thoughts),  # type: ignore[misc]
                return_exceptions=True,
            )

            for thought, eval_result in zip(thoughts, eval_results, strict=False):  # type: ignore[arg-type]
                if isinstance(eval_result, BaseException):
                    continue
                score = float(eval_result)
                new_state = thought
                new_path = [*path, thought]
                candidates.append((new_state, new_path, score))

        if not candidates:
            break

        # Keep top-width candidates
        candidates.sort(key=lambda x: x[2], reverse=True)
        beams = candidates[: cfg.width]

        # Check terminals after expansion
        for state, path, score in beams:
            if await task.is_terminal(state) and score > best_score:
                best_score = score
                best_answer = state
                best_path = path

    # If no terminal was found, return best leaf
    if not best_answer and beams:
        best_state, best_path, best_score = max(beams, key=lambda x: x[2])
        best_answer = best_state

    return ToTResult(
        answer=best_answer,
        path=best_path,
        score=best_score if best_score != float("-inf") else 0.0,
        steps=len(best_path),
    )


# ── Helper: make a ToTTask from plain functions ─────────────────────────────


def make_tot_task(
    query: str,
    generate_thoughts: Callable[[str, int], Awaitable[list[str]]],
    evaluate: Callable[[str, str], Awaitable[float]],
    *,
    is_terminal: Callable[[str], Awaitable[bool]] | None = None,
    initial_state: Callable[[], str] | None = None,
) -> ToTTask:
    """Create a ToTTask from plain async functions (for quick prototyping)."""

    class _FnTask:
        @property
        def query(self) -> str:
            return query

        def initial_state(self) -> str:
            return initial_state() if initial_state else ""

        async def generate_thoughts(self, state: str, n: int, **kw: Any) -> list[str]:
            return await generate_thoughts(state, n)

        async def evaluate(self, state: str, thought: str, **kw: Any) -> float:
            return await evaluate(state, thought)

        async def is_terminal(self, state: str, **kw: Any) -> bool:
            if is_terminal:
                return await is_terminal(state)
            return False

    return _FnTask()  # type: ignore[return-value]


# ── LLM-backed helpers (Madras LiteLLM gateway) ─────────────────────────────


async def litellm_generate(
    state: str,
    n: int,
    *,
    model: str,
    backend: Any,
    query: str = "",
    temperature: float = 0.7,
) -> list[str]:
    """Generate N candidate thoughts via the Madras LLM gateway.

    Args:
        state: Current search state (the chain of prior thoughts).
        n: Number of thoughts to generate.
        model: Model name (e.g. 'ollama/qwen3:8b').
        backend: An LLMBackend with an async .complete() method.
        query: The original problem query.
        temperature: Sampling temperature.
    """
    from madras.llm.gateway import LLMRequest

    prompt = (
        f"Problem: {query}\n"
        f"Current reasoning state: {state or '(start)'}\n\n"
        f"Generate {n} distinct next reasoning steps. "
        f"Return a JSON array of strings, one per step. "
        f'Example: ["step A", "step B"]'
    )

    req = LLMRequest(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=512,
        temperature=temperature,
    )
    resp = await backend.complete(req)

    # Parse JSON array from response
    text = resp.text.strip()
    # Extract JSON array from possible markdown fences
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed: Any = json.loads(text)
        if isinstance(parsed, list):
            return [str(item) for item in parsed[:n]]  # type: ignore[reportUnknownArgumentType]
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: split by newlines
    stripped = "-•*1234567890. "
    lines = [line.strip().lstrip(stripped) for line in resp.text.splitlines() if line.strip()]
    return lines[:n] if lines else [resp.text[:200]]


async def litellm_evaluate(
    state: str,
    thought: str,
    *,
    model: str,
    backend: Any,
    query: str = "",
    temperature: float = 0.3,
) -> float:
    """Evaluate a thought's promise via the Madras LLM gateway.

    Returns a score 0.0-1.0 indicating how promising this thought path is.
    """
    from madras.llm.gateway import LLMRequest

    prompt = (
        f"Problem: {query}\n"
        f"Reasoning so far: {state or '(start)'}\n"
        f"Candidate next step: {thought}\n\n"
        f"Rate how promising this next step is on a scale of 0.0 to 1.0.\n"
        f"Return ONLY a JSON number, e.g. {'score': 0.7}"
    )

    req = LLMRequest(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=64,
        temperature=temperature,
    )
    resp = await backend.complete(req)

    text = resp.text.strip()
    if "```" in text:
        text = text.split("```")[1].strip()
    try:
        parsed: Any = json.loads(text)
        if isinstance(parsed, dict) and "score" in parsed:
            return float(parsed["score"])  # type: ignore[reportUnknownArgumentType]
        if isinstance(parsed, (int, float)):
            return float(parsed)  # type: ignore[reportUnknownArgumentType]
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # Fallback: try to extract a number from the text
    import re

    nums = re.findall(r"0?\.\d+|1\.0|0", text)
    if nums:
        return float(nums[0])
    return 0.5
