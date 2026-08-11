"""Track 3.3: deterministic tool-arg repair for the FREE/local tier.

Weak local models (llama-70b, deepseek) frequently emit tool-call ``arguments``
that aren't clean JSON: wrapped in Markdown fences, surrounded by prose, using
single quotes, trailing commas, Python literals (True/False/None), or smart
quotes. Before the governed loop gives up and burns a turn on an
``[INVALID_ARGUMENTS]`` teaching message, ``repair_tool_args`` runs a sequence
of deterministic, stdlib-only repairs and recovers a valid dict when possible.

Pure and dependency-free. No ``eval``/``exec``/``ast.literal_eval`` on the raw
string — only regex/string transforms followed by ``json.loads``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, cast

__all__ = ["RepairResult", "repair_tool_args"]


@dataclass(frozen=True)
class RepairResult:
    ok: bool
    args: dict[str, Any] = field(default_factory=dict[str, Any])
    repaired: bool = False
    method: str = ""


# Markdown code fence: ```json ... ``` or ``` ... ```
_FENCE_RE = re.compile(
    r"```(?:json|JSON)?\s*(?P<body>.*?)\s*```",
    re.DOTALL,
)

# Bare Python literal tokens (word-boundary). Applied only OUTSIDE string spans.
_PY_LITERALS = {"True": "true", "False": "false", "None": "null"}
_PY_LITERAL_RE = re.compile(r"\b(True|False|None)\b")

# Trailing comma before a closing } or ].
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")

# Smart/curly quotes → ASCII. The single-quote keys (U+2018/9) are flagged by
# RUF001 as ambiguous; they are intentional, so suppress per-line.
_SMART_QUOTES = {
    "“": '"',
    "”": '"',
    "‘": "'",  # noqa: RUF001
    "’": "'",  # noqa: RUF001
}


def _try_load_dict(text: str) -> dict[str, Any] | None:
    try:
        parsed: Any = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else None


def strip_fences(text: str) -> str:
    m = _FENCE_RE.search(text)
    return m.group("body") if m else text


def balanced_object_span(text: str) -> str | None:
    """Return the first balanced ``{ ... }`` span, ignoring braces inside strings."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    quote = ""
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _normalize_smart_quotes(text: str) -> str:
    for bad, good in _SMART_QUOTES.items():
        text = text.replace(bad, good)
    return text


def _replace_py_literals_outside_strings(text: str) -> str:
    """Replace bare True/False/None with JSON equivalents, skipping string spans."""
    out: list[str] = []
    in_str = False
    quote = ""
    esc = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            i += 1
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
            out.append(ch)
            i += 1
            continue
        m = _PY_LITERAL_RE.match(text, i)
        if m:
            out.append(_PY_LITERALS[m.group(1)])
            i = m.end()
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _single_to_double_quotes(text: str) -> str:
    """Convert a single-quoted object to double-quoted. Best-effort, span-aware.

    Swaps the quote char for any single-quoted span (preserving any double quotes
    inside it via escaping) and escapes bare double quotes that were unquoted.
    Only meaningful when the result then parses as JSON.
    """
    out: list[str] = []
    in_str = False
    quote = ""
    esc = False
    for ch in text:
        if in_str:
            if esc:
                out.append(ch)
                esc = False
                continue
            if ch == "\\":
                out.append(ch)
                esc = True
                continue
            if ch == quote:
                out.append('"')
                in_str = False
                continue
            if ch == '"' and quote == "'":
                out.append('\\"')
                continue
            out.append(ch)
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
            out.append('"')
            continue
        out.append(ch)
    return "".join(out)


def coerce_scalars(args: dict[str, Any], schema: dict[str, Any] | None) -> dict[str, Any]:
    """Light type coercion of scalar fields per schema. Never invents.

    Coerces EVERY declared scalar field the model actually emitted (T3-3: weak models
    send ``"k": "5"`` for optional params too, not just required ones). Fields the model
    did not emit are never added — the "never invents" guarantee is preserved.
    """
    if not isinstance(schema, dict):
        return args
    props = schema.get("properties")
    if not isinstance(props, dict):
        return args
    props = cast("dict[str, Any]", props)
    for key in props:
        if key not in args or not isinstance(props.get(key), dict):
            continue
        ptype = props[key].get("type")
        val = args[key]
        if ptype == "integer" and isinstance(val, str):
            try:
                args[key] = int(val)
            except ValueError:
                pass
        elif ptype == "number" and isinstance(val, str):
            try:
                args[key] = float(val)
            except ValueError:
                pass
        elif ptype == "boolean" and isinstance(val, str):
            low = val.strip().lower()
            if low == "true":
                args[key] = True
            elif low == "false":
                args[key] = False
    return args


def repair_tool_args(
    raw: str | dict[str, Any], schema: dict[str, Any] | None = None
) -> RepairResult:
    """Recover a valid tool-args dict from a (possibly malformed) model emission.

    Tries, in order: passthrough-dict, clean parse, then deterministic repairs
    (fence-strip + balanced-span, smart-quote normalize, Python literals,
    single→double quotes, trailing-comma removal), re-parsing after each and
    stopping at the first that yields a dict. Light schema-guided scalar coercion
    is applied to the recovered dict. Returns ``unrepairable`` if nothing parses.
    """
    if isinstance(raw, dict):
        return RepairResult(ok=True, args=raw, repaired=False, method="passthrough-dict")

    text = raw

    clean = _try_load_dict(text)
    if clean is not None:
        return RepairResult(
            ok=True, args=coerce_scalars(clean, schema), repaired=False, method="clean"
        )

    # Step 1: strip fences + extract first balanced { ... } span.
    candidate = strip_fences(text)
    span = balanced_object_span(candidate)
    base = span if span is not None else candidate

    steps: list[tuple[str, str]] = []
    cur = base
    steps.append(("fence-span", cur))

    cur = _normalize_smart_quotes(cur)
    steps.append(("smart-quotes", cur))

    cur = _replace_py_literals_outside_strings(cur)
    steps.append(("python-literals", cur))

    single = _single_to_double_quotes(cur)
    steps.append(("single-to-double-quotes", single))

    # Trailing-comma removal applied to the best textual form so far.
    steps.append(("trailing-comma", _TRAILING_COMMA_RE.sub(r"\1", single)))

    for method, text_form in steps:
        parsed = _try_load_dict(text_form)
        if parsed is not None:
            return RepairResult(
                ok=True,
                args=coerce_scalars(parsed, schema),
                repaired=True,
                method=method,
            )

    return RepairResult(ok=False, args={}, repaired=False, method="unrepairable")
