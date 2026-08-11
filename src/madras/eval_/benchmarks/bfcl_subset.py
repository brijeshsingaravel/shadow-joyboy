"""BFCL-subset runner — scores tool-call selection on a vendored case set.

This is the first piece of the eval-harness regression line. It drives a small,
hand-written ("Berkeley Function-Calling"-style) subset through the LLM gateway
and scores, per case:

  - tool_match: did the model call the expected tool (or correctly call nothing)?
  - arg_match:  does the called tool's JSON arguments contain every required
                expected key with the expected value (subset / AST-ish match)?
  - passed:     tool_match and arg_match.

Runs against any `LLMBackend` via the gateway — `FakeBackend` / a scripted
backend for tests, `LiteLLMBackend` for a real local baseline. No network here.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from madras.llm.decode import repair_tool_args
from madras.llm.gateway import LLMGateway, LLMRequest

_DEFAULT_CASES = Path(__file__).parent / "cases" / "bfcl_subset.json"


@dataclass(frozen=True)
class BfclCase:
    id: str
    query: str  # the user turn
    tools: list[dict[str, Any]]  # OpenAI tool schemas available for this case
    expected_tool: str | None  # the correct tool to call (None = answer w/o a tool)
    expected_args: dict[str, Any]  # required arg key->value the call must contain (subset)
    # Multi-call cases: every name here must appear in the model's tool_calls
    # (set-subset, multiplicity-aware). None => single-call scoring (v1 behavior).
    expected_tools: list[str] | None = None


@dataclass(frozen=True)
class BfclCaseResult:
    case_id: str
    tool_match: bool
    arg_match: bool
    passed: bool
    called_tool: str | None
    error: str | None = None


@dataclass
class BfclRunResult:
    model: str
    n: int
    n_passed: int
    pass_rate: float
    per_case: list[BfclCaseResult] = field(default_factory=list[BfclCaseResult])


def load_cases(path: Path | None = None) -> list[BfclCase]:
    """Read the vendored case set. Defaults to cases/bfcl_subset.json."""
    src = path or _DEFAULT_CASES
    raw = json.loads(src.read_text(encoding="utf-8"))
    return [
        BfclCase(
            id=c["id"],
            query=c["query"],
            tools=c["tools"],
            expected_tool=c["expected_tool"],
            expected_args=c["expected_args"],
            expected_tools=c.get("expected_tools"),
        )
        for c in raw
    ]


def args_subset_match(arguments: str, expected_args: dict[str, Any]) -> bool:
    """True if the called args contain every expected key with equal value.

    Uses the SAME repair the governed loop applies at execution time
    (`repair_tool_args`), so the metric reflects the agent's *effective* behavior:
    a weak model that emits fenced/single-quoted/python-literal JSON is repaired and
    the tool is actually called correctly (Track 3.3). Unrepairable -> False (never
    raises). Empty expected_args -> True.
    """
    if not expected_args:
        return True
    result = repair_tool_args(arguments)
    if not result.ok:
        return False
    return all(result.args.get(k) == v for k, v in expected_args.items())


def _multiset_subset(expected: list[str], actual: list[str]) -> bool:
    """True if every name in `expected` is present in `actual`, counting
    multiplicity (e.g. ['a', 'a'] needs 'a' to appear at least twice in actual).
    """
    want = Counter(expected)
    have = Counter(actual)
    return all(have[name] >= cnt for name, cnt in want.items())


def score_case(case: BfclCase, tool_calls: list[Any]) -> BfclCaseResult:
    """Score one case's response. `tool_calls` is resp.tool_calls (list[ToolCall]).

    Parallel-call rule (`expected_tools` set): the case passes when the model's
    tool_calls include ALL expected tool names (multiplicity-aware subset). Arg
    matching is additionally applied to the FIRST call whose name == the primary
    `expected_tool`, when `expected_args` is non-empty; otherwise args are not
    re-checked for the multi-call case.
    """
    called_tool = tool_calls[0].name if tool_calls else None

    if case.expected_tools is not None:
        called_names = [tc.name for tc in tool_calls]
        tool_match = _multiset_subset(case.expected_tools, called_names)
        if not tool_match:
            return BfclCaseResult(
                case_id=case.id,
                tool_match=False,
                arg_match=False,
                passed=False,
                called_tool=called_tool,
            )
        arg_match = True
        if case.expected_args:
            primary = next((tc for tc in tool_calls if tc.name == case.expected_tool), None)
            arg_match = primary is not None and args_subset_match(
                primary.arguments, case.expected_args
            )
        return BfclCaseResult(
            case_id=case.id,
            tool_match=True,
            arg_match=arg_match,
            passed=arg_match,
            called_tool=called_tool,
        )

    if case.expected_tool is None:
        # Correct behavior is to NOT call a tool.
        tool_match = not tool_calls
        arg_match = True
        return BfclCaseResult(
            case_id=case.id,
            tool_match=tool_match,
            arg_match=arg_match,
            passed=tool_match,
            called_tool=called_tool,
        )

    tool_match = called_tool == case.expected_tool
    if not tool_match:
        return BfclCaseResult(
            case_id=case.id,
            tool_match=False,
            arg_match=False,
            passed=False,
            called_tool=called_tool,
        )

    arg_match = args_subset_match(tool_calls[0].arguments, case.expected_args)
    return BfclCaseResult(
        case_id=case.id,
        tool_match=True,
        arg_match=arg_match,
        passed=arg_match,
        called_tool=called_tool,
    )


async def run_bfcl(gateway: LLMGateway, model: str, cases: list[BfclCase]) -> BfclRunResult:
    """Run every case through the gateway and aggregate pass_rate."""
    per_case: list[BfclCaseResult] = []
    for case in cases:
        try:
            resp = await gateway.complete(
                LLMRequest(
                    model=model,
                    messages=[{"role": "user", "content": case.query}],
                    tools=case.tools,
                )
            )
            per_case.append(score_case(case, resp.tool_calls))
        except Exception as exc:  # a backend failure scores the case as a miss
            per_case.append(
                BfclCaseResult(
                    case_id=case.id,
                    tool_match=False,
                    arg_match=False,
                    passed=False,
                    called_tool=None,
                    error=str(exc),
                )
            )

    n = len(cases)
    n_passed = sum(1 for r in per_case if r.passed)
    pass_rate = (n_passed / n) if n else 0.0
    return BfclRunResult(
        model=model, n=n, n_passed=n_passed, pass_rate=pass_rate, per_case=per_case
    )


@dataclass(frozen=True)
class BfclPassKCaseResult:
    case_id: str
    passes: int  # how many of the k attempts passed
    k: int


@dataclass
class BfclPassKResult:
    model: str
    n: int
    k: int
    pass_caret_k: float  # fraction of cases that passed ALL k attempts
    mean_pass_rate: float  # mean over cases of (attempts_passed / k)
    per_case: list[BfclPassKCaseResult] = field(default_factory=list[BfclPassKCaseResult])


async def run_bfcl_passk(
    gateway: LLMGateway, model: str, cases: list[BfclCase], *, k: int = 4
) -> BfclPassKResult:
    """Run EACH case `k` times and aggregate consistency under resampling.

    - pass^k        = fraction of cases that passed ALL k attempts (strict).
    - mean_pass_rate = mean over cases of (attempts_passed / k).

    A backend failure on an attempt scores that attempt as a miss (never raises).
    """
    if k < 1:
        raise ValueError("k must be >= 1")

    per_case: list[BfclPassKCaseResult] = []
    for case in cases:
        passes = 0
        for _ in range(k):
            try:
                resp = await gateway.complete(
                    LLMRequest(
                        model=model,
                        messages=[{"role": "user", "content": case.query}],
                        tools=case.tools,
                    )
                )
                if score_case(case, resp.tool_calls).passed:
                    passes += 1
            except Exception:  # a backend failure scores the attempt as a miss
                pass
        per_case.append(BfclPassKCaseResult(case_id=case.id, passes=passes, k=k))

    n = len(cases)
    pass_caret_k = (sum(1 for c in per_case if c.passes == k) / n) if n else 0.0
    mean_pass_rate = (sum(c.passes / k for c in per_case) / n) if n else 0.0
    return BfclPassKResult(
        model=model,
        n=n,
        k=k,
        pass_caret_k=pass_caret_k,
        mean_pass_rate=mean_pass_rate,
        per_case=per_case,
    )
