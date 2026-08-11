"""The Interrupt (E2) — autonomous push notifications to the user.

Agent-initiated messages delivered OUTSIDE the normal request-response loop.
Use cases: reminders, alerts, proactive suggestions, scheduled check-ins.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class InterruptConfig:
    """Configuration for Interrupt behavior."""

    enabled: bool = True
    max_per_day: int = 3
    min_interval_hours: int = 4
    quiet_start: int = 22  # 10 PM
    quiet_end: int = 8  # 8 AM


class InterruptContext:
    """Context for a single interrupt event."""

    def __init__(
        self,
        trigger_type: str,
        trigger_payload: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        now: datetime | None = None,
        subject: str = "",
    ):
        # Allow `payload` as alias for `trigger_payload`
        self.trigger_type = trigger_type
        self.trigger_payload = payload if trigger_payload is None else trigger_payload
        self.now = now
        self.subject = subject

    # For backward compat with first test style
    @property
    def payload(self) -> dict[str, Any]:
        return self.trigger_payload or {}

    @payload.setter
    def payload(self, value: dict[str, Any]) -> None:
        self.trigger_payload = value


def build_interrupt_prompt(
    *,
    persona: str,
    context: str,
    instruction: str,
    memory_snippet: str | None = None,
) -> str:
    """Build the LLM prompt for generating an interrupt message."""
    parts = [
        f"Persona: {persona}",
        f"Context: {context}",
        f"Instruction: {instruction}",
    ]
    if memory_snippet:
        parts.append(f"Relevant memory: {memory_snippet}")
    return "\n\n".join(parts)


def _in_quiet_hours(cfg: InterruptConfig, hour: int) -> bool:
    """Check if hour falls in quiet hours."""
    if cfg.quiet_start <= cfg.quiet_end:
        # Normal range (e.g., 9-17)
        return cfg.quiet_start <= hour < cfg.quiet_end
    else:
        # Overnight range (e.g., 22-8)
        return hour >= cfg.quiet_start or hour < cfg.quiet_end


def should_trigger_interrupt(
    config: InterruptConfig,
    state_or_ctx: Any,
    *,
    now: float | None = None,
    sent_today: int | None = None,
    last_interrupt_ts: float | None = None,
) -> bool:
    """Check if an interrupt should fire.

    Supports two calling conventions:
    1. With _FakeState: should_trigger_interrupt(cfg, state, now=1000.0)
       - state has: last_interrupt_ts, interrupt_count_today
    2. With InterruptContext: should_trigger_interrupt(cfg, ctx, sent_today=0)
       - ctx has: now (datetime), config has quiet_start/quiet_end
    """
    if not config.enabled:
        return False

    # Convention 1: _FakeState with epoch timestamps
    if hasattr(state_or_ctx, "last_interrupt_ts") or hasattr(state_or_ctx, "interrupt_count_today"):
        state = state_or_ctx
        if now is None:
            raise ValueError("now= required for state-based call")

        count = getattr(state, "interrupt_count_today", 0)
        if count >= config.max_per_day:
            return False

        # Use state's last_interrupt_ts if last_interrupt_ts param not provided
        last_ts = (
            last_interrupt_ts
            if last_interrupt_ts is not None
            else getattr(state, "last_interrupt_ts", 0)
        )
        if last_ts > 0 and now > 0:
            elapsed_hours = (now - last_ts) / 3600.0
            if elapsed_hours < config.min_interval_hours:
                return False

        return True

    # Convention 2: InterruptContext with datetime
    ctx = state_or_ctx
    if sent_today is not None and sent_today >= config.max_per_day:
        return False

    # Check quiet hours
    if ctx.now is not None:
        if _in_quiet_hours(config, ctx.now.hour):
            return False

    return True
