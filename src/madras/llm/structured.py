"""Schema-validated structured output — the OUTPUT-side counterpart to decode.repair_tool_args.

Force the model to emit a JSON object matching a schema, tolerantly extract it (reusing
``repair_tool_args`` for the weak free-tier models that wrap JSON in fences / prose), VALIDATE
it against the schema, and RETRY feeding the validation error back — up to ``max_retries``.
Every call goes through the LLM gateway seam. Reliable structured agent I/O as a primitive
(the Claude Workflow ``schema:`` idea, native to Madras: Pydantic-grade validation + retry).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, cast

from madras.llm.decode import repair_tool_args
from madras.llm.gateway import LLMGateway, LLMRequest

__all__ = ["StructuredResult", "structured_output", "validate_against_schema"]

# JSON-schema primitive type -> Python type(s). bool is handled specially (it subclasses int).
_PY_TYPES: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


@dataclass
class StructuredResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict[str, Any])
    attempts: int = 0
    error: str = ""


def validate_against_schema(data: Any, schema: dict[str, Any]) -> str | None:
    """Minimal, dependency-free JSON-Schema check (object · required · property types).
    Returns None if valid, else a short error string. Covers the object schemas Madras
    tools + structured output use; stdlib-only, consistent with decode.py."""
    if schema.get("type") == "object" and not isinstance(data, dict):
        return f"expected a JSON object, got {type(data).__name__}"
    if not isinstance(data, dict):
        return f"expected a JSON object, got {type(data).__name__}"
    data = cast("dict[str, Any]", data)
    required: list[Any] = schema.get("required") or []
    missing = [k for k in required if k not in data]
    if missing:
        return f"missing required field(s): {missing}"
    props: dict[str, Any] = schema.get("properties") or {}
    for key, spec in props.items():
        if key not in data or not isinstance(spec, dict):
            continue
        spec = cast("dict[str, Any]", spec)
        t = spec.get("type")
        if not t or t not in _PY_TYPES:
            continue
        val = data[key]
        # bool is a subclass of int — never accept it for integer/number.
        if t in ("integer", "number") and isinstance(val, bool):
            return f"field {key!r} expected {t}, got boolean"
        if not isinstance(val, _PY_TYPES[t]):
            return f"field {key!r} expected {t}, got {type(val).__name__}"
        if t == "string" and isinstance(val, str) and (enum := spec.get("enum")):
            if val not in enum:
                return f"field {key!r} must be one of {enum}, got {val!r}"
        if t == "array" and isinstance(val, list) and isinstance(spec.get("items"), dict):
            item_spec = cast("dict[str, Any]", spec["items"])
            item_enum = item_spec.get("enum") if item_spec.get("type") == "string" else None
            if item_enum:
                bad = [v for v in cast("list[Any]", val) if v not in item_enum]
                if bad:
                    return (
                        f"field {key!r} has invalid item(s) {bad}, must all be one of {item_enum}"
                    )
    return None


def _instruction(schema: dict[str, Any]) -> str:
    return (
        "Respond with ONLY a single JSON object that conforms to this JSON Schema. "
        "No prose, no markdown fences, no explanation — just the object.\n" + json.dumps(schema)
    )


async def structured_output(
    gateway: LLMGateway,
    model: str,
    messages: list[dict[str, Any]],
    schema: dict[str, Any],
    *,
    max_retries: int = 2,
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> StructuredResult:
    """Get schema-valid structured output from the model, retrying on invalid output.

    Returns StructuredResult(ok=True, data=...) on success, else ok=False with the last
    validation error. `schema` is a JSON-Schema object spec (the same shape tool params use).
    """
    convo: list[dict[str, Any]] = [*messages, {"role": "user", "content": _instruction(schema)}]
    last_err = "no attempt made"
    attempts = 0
    for attempt in range(max_retries + 1):  # initial try + retries
        attempts = attempt + 1
        resp = await gateway.complete(
            LLMRequest(model=model, messages=convo, max_tokens=max_tokens, temperature=temperature)
        )
        rep = repair_tool_args(resp.text, schema)
        if not rep.ok:
            last_err = "output was not valid JSON"
        else:
            err = validate_against_schema(rep.args, schema)
            if err is None:
                return StructuredResult(ok=True, data=rep.args, attempts=attempts)
            last_err = err
        # Feed the failure back so the next attempt can correct it.
        convo = [
            *convo,
            {"role": "assistant", "content": resp.text[:1000]},
            {
                "role": "user",
                "content": f"That did not satisfy the schema: {last_err}. "
                "Return ONLY a corrected JSON object.",
            },
        ]
    return StructuredResult(ok=False, attempts=attempts, error=last_err)
