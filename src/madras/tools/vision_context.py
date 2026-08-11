"""Per-run vision context for the vision_analyze tool.

Holds the LLM gateway + the vision-capable model alias the tool routes images
through. Set by the cockpit loop when the 'vision' toolset is active; mirrors
delegation_context (which also carries a gateway).
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class VisionCtx:
    gateway: Any  # LLMGateway or duck-typed fake with async complete(LLMRequest)
    model: str


_active: ContextVar[VisionCtx | None] = ContextVar("madras_vision_ctx", default=None)


def set_vision_ctx(ctx: VisionCtx | None) -> None:
    _active.set(ctx)


def get_vision_ctx() -> VisionCtx | None:
    return _active.get()
