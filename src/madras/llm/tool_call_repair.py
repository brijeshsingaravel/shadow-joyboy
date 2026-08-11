"""Tool-call repair — recover a usable tool CALL from a weak free-tier model's emission.

`decode.repair_tool_args` already fixes malformed *arguments*; this is the call-level layer:
weak models often (a) describe a call in prose instead of emitting a structured tool_call
(`web_search({"q": "rust"})`), (b) misspell the tool name, and (c) malform the args. This
extracts the call from text, fuzzy-matches the name to a known tool, and repairs the args —
so the free fleet stays usable instead of burning a turn. Pure + deterministic.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import Any, cast

from madras.llm.decode import (
    balanced_object_span,
    coerce_scalars,
    repair_tool_args,
    strip_fences,
)

# A prose call: `name({...})` (name may be dotted), args is the first balanced object.
_CALL_FN = re.compile(r"([A-Za-z_][\w.]*)\s*\(\s*(\{)", re.DOTALL)


@dataclass
class ToolCallRepair:
    ok: bool
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict[str, Any])
    repaired: bool = False
    method: str = ""


def _closest_name(name: str, known: set[str]) -> tuple[str, bool]:
    if not known or name in known:
        return name, False
    match = get_close_matches(name, sorted(known), n=1, cutoff=0.6)
    return (match[0], True) if match else (name, False)


def _extract(emission: Any) -> tuple[str, Any]:
    """Pull (name, raw_args) from a structured call dict or a text emission."""
    if isinstance(emission, dict):
        emission = cast("dict[str, Any]", emission)
        return str(emission.get("name", "")), emission.get("arguments", emission.get("args", {}))
    text = str(emission)

    # (a) a structured call serialized in text: {"name": "...", "arguments": {...}}
    span = balanced_object_span(strip_fences(text))
    if span:
        try:
            obj = json.loads(span)
        except (json.JSONDecodeError, ValueError):
            obj = None
        if isinstance(obj, dict):
            obj = cast("dict[str, Any]", obj)
            if obj.get("name") and ("arguments" in obj or "args" in obj):
                return str(obj["name"]), obj.get("arguments", obj.get("args", {}))

    # (b) a prose call: name({ ... })
    m = _CALL_FN.search(text)
    if m:
        args_span = balanced_object_span(text[m.start(2) :])
        if args_span:
            return m.group(1), args_span
    return "", {}


def repair_tool_call(
    emission: Any,
    *,
    known_tools: tuple[str, ...] = (),
    schema_for: Callable[[str], dict[str, Any] | None] | None = None,
) -> ToolCallRepair:
    """Recover (name, args) from a possibly-malformed tool-call emission."""
    name, raw_args = _extract(emission)
    if not name:
        return ToolCallRepair(False, method="no-tool-call-found")
    fixed_name, name_repaired = _closest_name(name, set(known_tools))
    if known_tools and fixed_name not in known_tools:
        return ToolCallRepair(False, name=name, method="unknown-tool")
    schema = schema_for(fixed_name) if schema_for else None
    rr = repair_tool_args(raw_args, schema)
    if not rr.ok:
        return ToolCallRepair(False, name=fixed_name, method="args-unrepairable")
    # Coerce consistently — decode's passthrough-dict path skips coercion, so apply it here
    # when a schema is given (a dict emission gets the same scalar typing as a string one).
    args = coerce_scalars(rr.args, schema) if schema else rr.args
    return ToolCallRepair(
        ok=True,
        name=fixed_name,
        args=args,
        repaired=name_repaired or rr.repaired,
        method=f"name:{'fixed' if name_repaired else 'ok'}|args:{rr.method}",
    )
