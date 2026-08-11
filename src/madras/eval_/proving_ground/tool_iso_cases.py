"""Isolation cases for critical tools with deterministic real output.

Each case invokes a governed tool directly and asserts on the actual result — this
catches a tool backend regressing even when the agent selects it correctly. Tools
needing live network / a provisioned sandbox / per-run context (web_*, terminal,
recall, mcp_find) are covered by scenario selection + live runs, not here.
"""

from __future__ import annotations

from madras.eval_.proving_ground.tooliso import ToolCase

# Read-only, workspace-confined, deterministic — safe to run in CI with the
# workspace pointed at the repo (see test fixture).
CRITICAL_CASES: list[ToolCase] = [
    ToolCase("glob", {"pattern": "**/*.py"}, "nonempty", label="glob finds python files"),
    ToolCase(
        "glob", {"pattern": "*.toml"}, "contains", expect="pyproject", label="glob finds pyproject"
    ),
    ToolCase(
        "grep",
        {"pattern": "GovernedExecutor"},
        "contains",
        expect="registry",
        label="grep finds symbol",
    ),
    ToolCase("list", {"path": "."}, "nonempty", label="list directory"),
    ToolCase(
        "definition",
        {"name": "run_tool_isolation"},
        "nonempty",
        label="code-intel finds a definition",
    ),
    ToolCase("does_not_exist", {}, "error", label="unknown tool errors cleanly"),
]
