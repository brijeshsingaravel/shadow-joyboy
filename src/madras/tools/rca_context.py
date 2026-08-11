"""Per-run RCA context for the rca_analyze tool.

Holds the LLM gateway + model the tool routes the incident-reasoning through. Set by the cockpit
loop when the 'rca' toolset is active; mirrors vision_context / delegation_context.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class RcaCtx:
    gateway: Any  # LLMGateway or a duck-typed fake with async complete(LLMRequest)
    model: str


_active: ContextVar[RcaCtx | None] = ContextVar("madras_rca_ctx", default=None)


def set_rca_ctx(ctx: RcaCtx | None) -> None:
    _active.set(ctx)


def get_rca_ctx() -> RcaCtx | None:
    return _active.get()
