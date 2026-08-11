"""Sleep-time compute (W4·B3) — built ON TOP of the nightly Memory Manager agent.

Generalizes the nightly agent into a sleep-time pass that distills recent *raw* memories into
a single **learned-context** block (Letta's raw→learned-context), flagged **shareable** and
**exportable** (reuses E-X4b portability + per-tenant isolation). No separate always-on agent —
the existing nightly job IS the sleep agent.
"""

from madras.sleeptime.distill import distill_learned_context

__all__ = ["distill_learned_context"]
