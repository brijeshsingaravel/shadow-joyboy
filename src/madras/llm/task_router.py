"""Auxiliary per-task model routing — pin each side-task to the cheapest competent FREE model.

The zero-cost mandate, operationalized: the main reasoning loop and every auxiliary task
(JSON/structured, titling, summarizing, curation, vision, embedding) route to the LiteLLM
free fleet — never a paid model. Encodes the B3 finding (JSON tasks → a JSON-competent free
model). `model_for(task, available=…)` degrades to whatever's actually up (e.g. when a model
is rate-limited). Pure; the caller passes the chosen model to the gateway.
"""

from __future__ import annotations

# The entire allowed routing surface — every one is FREE (no paid model, ever).
FREE_FLEET: frozenset[str] = frozenset(
    {
        "kimi-k2",
        "deepseek",
        "llama-70b",
        "qwen",
        "gemini-flash",
        "gemini-pro",
        "moondream:latest",
        "qwen2.5-vl",
        "nomic-embed-text",
    }
)

# Aux task → ordered candidate models (cheapest competent first). All ∈ FREE_FLEET.
AUX_MODELS: dict[str, tuple[str, ...]] = {
    "reasoning": ("kimi-k2", "deepseek", "llama-70b"),
    "json": ("gemini-flash", "gemini-pro", "kimi-k2"),  # B3: JSON → JSON-competent free
    "structured": ("gemini-flash", "gemini-pro", "kimi-k2"),
    "title": ("gemini-flash", "llama-70b"),
    "summarize": ("gemini-flash", "kimi-k2"),
    "curator": ("gemini-flash", "llama-70b"),
    "vision": ("moondream:latest", "qwen2.5-vl"),
    "embed": ("nomic-embed-text",),
}
DEFAULT_TASK = "reasoning"


def register_task(task: str, models: tuple[str, ...]) -> None:
    """Add/override a task→models route. All models must be in the free fleet (zero-cost)."""
    bad = [m for m in models if m not in FREE_FLEET]
    if bad:
        raise ValueError(f"non-free models rejected (zero-cost mandate): {bad}")
    AUX_MODELS[task] = models


def model_for(task: str, *, available: set[str] | None = None, override: str | None = None) -> str:
    """Pick the model for `task`. With `available`, return the first candidate that's up;
    `override` (must be free) wins. Falls back to the default task's candidates."""
    if override:
        if override not in FREE_FLEET:
            raise ValueError(f"override '{override}' is not in the free fleet (zero-cost)")
        return override
    candidates = AUX_MODELS.get(task) or AUX_MODELS[DEFAULT_TASK]
    if available is None:
        return candidates[0]
    for m in candidates:
        if m in available:
            return m
    # degrade: default-task candidate that's up, else the first listed candidate
    return next((m for m in AUX_MODELS[DEFAULT_TASK] if m in available), candidates[0])
