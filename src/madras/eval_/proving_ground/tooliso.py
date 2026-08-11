"""Tool-isolation correctness harness.

Scenarios test whether the agent *selects* the right tool. This harness tests whether
a governed tool *actually works* — it invokes the tool DIRECTLY through the
GovernedExecutor (no agent loop, no LLM) and asserts on the real ToolResult. Catches
backend regressions (a tool that's chosen correctly but returns wrong/empty output).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from madras.models.agent_config import Rank
from madras.tools.registry import GovernedExecutor, ToolRegistry


@dataclass
class ToolCase:
    tool: str
    args: dict[str, Any]
    assert_kind: str  # ok | nonempty | contains | equals | regex | error
    expect: Any = None
    label: str = ""


@dataclass
class IsoResult:
    tool: str
    ok: bool  # the call completed (executor returned a result)
    passed: bool  # the assertion held
    detail: str
    content: str = ""


def _check(kind: str, result: Any, expect: Any) -> tuple[bool, str]:
    content = (result.content or "") if result.ok else (result.error or "")
    if kind == "ok":
        return result.ok, "result.ok"
    if kind == "error":
        return (not result.ok), "expected an error result"
    if kind == "nonempty":
        return (result.ok and bool(content.strip())), "non-empty content"
    if kind == "contains":
        return (result.ok and str(expect) in content), f"content contains {expect!r}"
    if kind == "equals":
        return (result.ok and content.strip() == str(expect)), f"content == {expect!r}"
    if kind == "regex":
        return (result.ok and re.search(str(expect), content) is not None), f"matches {expect!r}"
    return False, f"unknown assert_kind {kind!r}"


async def run_tool_isolation(
    case: ToolCase,
    executor: GovernedExecutor,
    *,
    agent_rank: Rank = Rank.PRINCIPAL,
    session_id: str = "tool-iso",
) -> IsoResult:
    """Invoke one tool directly and assert on its real output."""
    try:
        result = await executor.execute(
            tool_name=case.tool,
            args=case.args,
            agent_name="tool-iso",
            session_id=session_id,
            agent_rank=agent_rank,
        )
    except Exception as exc:
        return IsoResult(
            tool=case.tool,
            ok=False,
            passed=(case.assert_kind == "error"),
            detail=f"raised {type(exc).__name__}: {exc}",
        )
    passed, detail = _check(case.assert_kind, result, case.expect)
    content = (result.content or "") if result.ok else (result.error or "")
    return IsoResult(
        tool=case.tool, ok=result.ok, passed=passed, detail=detail, content=content[:200]
    )


async def run_isolation_suite(
    cases: list[ToolCase],
    *,
    registry: ToolRegistry | None = None,
) -> list[IsoResult]:
    """Run a list of ToolCases through a fresh GovernedExecutor over the real registry."""
    if registry is None:
        import madras.tools.builtin  # noqa: F401  # pyright: ignore[reportUnusedImport]
        from madras.tools.registry import REGISTRY

        registry = REGISTRY
    ex = GovernedExecutor(registry=registry, audit=None)
    return [await run_tool_isolation(c, ex) for c in cases]
