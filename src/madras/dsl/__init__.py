"""The `.tamil` v0 bridge — interpreted, not compiled (RFC-0002 §7.2, T5).

The closed-tree side of `.tamil`: `packages/tamil-lang/` only turns source into the Kural AST
(D9 boundary); this package turns that AST into a live, governed `AgentConfig` via the existing
factory/spawn.py path. No bypass, no new infra.
"""

from __future__ import annotations

from madras.dsl.interpreter import UngovernedGoal, interpret

__all__ = ["UngovernedGoal", "interpret"]
