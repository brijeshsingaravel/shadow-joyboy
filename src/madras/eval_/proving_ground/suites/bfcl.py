"""BFCL (Berkeley Function-Calling Leaderboard) dataset suite.

Real BFCL v4 cases are vendored as a small committed slice under ``bfcl/data/``,
snapshotted from the ``bfcl-eval`` package's bundled data (``BFCL_v4_*`` +
``possible_answer/*``). The slice spans the canonical BFCL categories:
simple · parallel · multiple · multi-turn · (ir)relevance.

``load_cases()`` maps each real case → a v2 ``Case``. Cases with a ground-truth
call list get ``tool_called`` + ``tool_args_subset`` checks; (ir)relevance cases
(where the correct behaviour is to call NOTHING) get a ``no_relevant_tool`` check.
Loading is hermetic — it reads only the committed slice, no network.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "bfcl" / "data"
_SLICE = DATA_DIR / "bfcl_slice.json"

_FEATURES = ["tool_selection", "tool_args", "multi_step_reasoning"]
# String-call rows (multi-turn ground_truth) look like: func(arg='x', y=3)
_CALL_RE = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*\((.*)\)\s*$", re.DOTALL)


def _first_user_prompt(question: Any) -> str:
    """BFCL ``question`` is list[turn][message]; return the first user content."""
    if isinstance(question, list):
        for turn in cast("list[Any]", question):
            msgs: list[Any] = cast("list[Any]", turn) if isinstance(turn, list) else [turn]
            for m in msgs:
                if isinstance(m, dict):
                    m = cast("dict[str, Any]", m)
                    if m.get("role") == "user":
                        return str(m.get("content", ""))
    return str(cast("Any", question))


def _tool_names(row: dict[str, Any]) -> list[str]:
    funcs: list[Any] = row.get("function") or []
    names: list[str] = []
    for f in funcs:
        if isinstance(f, dict) and "name" in f:
            f = cast("dict[str, Any]", f)
            names.append(f["name"])
    if not names:
        # Multi-turn rows describe tools by involved API classes.
        involved: list[Any] = row.get("involved_classes") or []
        names = [str(c) for c in involved]
    return names


def _parse_string_call(call: str) -> tuple[str, dict[str, Any]] | None:
    """Parse a BFCL multi-turn call string like ``mv(source='a', destination='b')``."""
    m = _CALL_RE.match(call)
    if not m:
        return None
    name, arg_src = m.group(1), m.group(2).strip()
    args: dict[str, Any] = {}
    if arg_src:
        try:
            tree = ast.parse(f"_f({arg_src})", mode="eval")
            call_node = tree.body
            if isinstance(call_node, ast.Call):
                for kw in call_node.keywords:
                    if kw.arg is not None:
                        try:
                            args[kw.arg] = ast.literal_eval(kw.value)
                        except (ValueError, SyntaxError):
                            args[kw.arg] = ast.dump(kw.value)
        except SyntaxError:
            return name, {}
    return name, args


def _expected_calls(ground_truth: Any) -> list[tuple[str, dict[str, Any]]]:
    """Normalize either ground-truth shape into [(tool, args_subset), ...].

    Single-turn: ``[{func: {arg: [candidate_values]}}]`` — args map to the first
    candidate value of each parameter.
    Multi-turn: ``[[call_str, ...], ...]`` — flatten turns, parse each call string.
    """
    calls: list[tuple[str, dict[str, Any]]] = []
    if not isinstance(ground_truth, list):
        return calls
    for entry in cast("list[Any]", ground_truth):
        if isinstance(entry, dict):
            entry = cast("dict[str, Any]", entry)
            for func, params in entry.items():
                args: dict[str, Any] = {}
                if isinstance(params, dict):
                    params = cast("dict[str, Any]", params)
                    for k, v in params.items():
                        cand: Any = v
                        if isinstance(v, list) and v:
                            cand = cast("list[Any]", v)[0]
                        if cand not in ("", None):
                            args[k] = cand
                calls.append((str(func), args))
        elif isinstance(entry, list):
            for call_str in cast("list[Any]", entry):
                parsed = _parse_string_call(str(call_str))
                if parsed:
                    calls.append(parsed)
        elif isinstance(entry, str):
            parsed = _parse_string_call(entry)
            if parsed:
                calls.append(parsed)
    return calls


class BfclSuite(Suite):
    id: str = "bfcl"
    name: str = "Berkeley Function-Calling Leaderboard (v4 slice)"
    version: str = "v4"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "bfcl-eval bundled BFCL_v4_* data (gorilla-llm), vendored slice; "
        "categories: simple · parallel · multiple · multi-turn · (ir)relevance"
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        rows = json.loads(_SLICE.read_text(encoding="utf-8"))
        cases: list[Case] = []
        all_tools: list[str] = []
        for row in rows:
            tools = _tool_names(row)
            for t in tools:
                if t not in all_tools:
                    all_tools.append(t)
            prompt = _first_user_prompt(row.get("question"))
            calls = _expected_calls(row.get("ground_truth"))
            checks: list[dict[str, Any]] = []
            if calls:
                for tool, args in calls:
                    checks.append({"type": "tool_called", "tool": tool})
                    if args:
                        checks.append({"type": "tool_args_subset", "tool": tool, "args": args})
            else:
                # (ir)relevance: correct behaviour is to call no offered tool.
                checks.append({"type": "no_relevant_tool", "tools": tools})
            cases.append(
                Case(
                    id=str(row["id"]),
                    suite_id=self.id,
                    benchmark_family="bfcl",
                    features=list(_FEATURES),
                    tools=tools,
                    prompt=prompt,
                    setup={"functions": row.get("function", [])},
                    checks=checks,
                )
            )
        self.tools = all_tools
        return cases
