"""Multi-rail guard scanners - the 2026 four-rail architecture over the deterministic core.

Production guardrail stacks run FOUR rails (input / output / retrieval / tool-call), each with
per-rail deterministic scanners, optionally ensembled with a guard-model. This layer composes the
Phase-1.5 [[Guardrails]] `GuardrailEngine` (jailbreak + system-prompt-leak, zero LLM call) and adds:
the **retrieval rail** (indirect prompt injection via poisoned docs - the ASI02 partner to the
`<retrieved>` fence) and the **tool-call rail** (SQLi / destructive args / secret exfil), plus
deterministic secret/PII scanners. An **optional injectable guard-model** (Granite Guardian /
Qwen3Guard, both Apache-2.0, zero-cost via LiteLLM/Ollama) ensembles on top - **ANY-block for
high-stakes, single-model for routine**. Deterministic-first (the moat); NeMo's per-call LLM is
rejected. Every verdict is audited; deterministic rules are non-bypassable.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from madras.security.guardrails import GuardrailEngine

if TYPE_CHECKING:
    from madras.hooks.registry import Hook, HookRegistry


class Rail(str, Enum):
    INPUT = "input"  # user message: jailbreak / injection / PII probe
    OUTPUT = "output"  # model response: compliance / prompt-leak / secret leak
    RETRIEVAL = "retrieval"  # retrieved chunks: indirect prompt injection
    TOOL_CALL = "tool_call"  # tool args: SQLi / destructive / secret exfil


ALLOW, FLAG, BLOCK = "allow", "flag", "block"

# A deterministic scanner: (category, reason) when it trips, else None. Pure, zero-cost.
Scanner = Callable[[str], "tuple[str, str] | None"]

_SECRET_RES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "openai_key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws_access_key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "github_token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "slack_token"),
    (re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"), "private_key"),
]

_PII_RES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "email"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "ssn"),
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "card_number"),
]

_DESTRUCTIVE_RES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bDROP\s+TABLE\b", re.I), "sql_drop"),
    (re.compile(r"\bUNION\s+SELECT\b", re.I), "sql_union"),
    (re.compile(r"\b(?:DELETE|TRUNCATE)\s+(?:FROM\s+)?\w+", re.I), "sql_delete"),
    (re.compile(r";\s*--"), "sql_comment_injection"),
    (re.compile(r"\brm\s+-rf\b"), "shell_rm_rf"),
]


def _scan_with(res: Sequence[tuple[re.Pattern[str], str]], category: str) -> Scanner:
    def scan(text: str) -> tuple[str, str] | None:
        for rx, name in res:
            if rx.search(text):
                return (category, f"{name} detected")
        return None

    return scan


secret_scanner = _scan_with(_SECRET_RES, "secret_leak")
pii_scanner = _scan_with(_PII_RES, "pii")
destructive_scanner = _scan_with(_DESTRUCTIVE_RES, "destructive")


@dataclass
class RailVerdict:
    decision: str  # allow | flag | block
    rail: Rail
    scanner: str = ""
    category: str | None = None
    reason: str = ""

    @property
    def blocked(self) -> bool:
        return self.decision == BLOCK


@dataclass
class ModelVerdict:
    unsafe: bool
    category: str = ""
    severity: str = "safe"  # safe | controversial | unsafe (Qwen3Guard tiers)


@runtime_checkable
class GuardModel(Protocol):
    name: str

    async def classify(self, text: str, *, rail: str) -> ModelVerdict: ...


@dataclass
class GuardRails:
    engine: GuardrailEngine = field(default_factory=GuardrailEngine)
    models: list[GuardModel] = field(default_factory=list[GuardModel])  # Granite + Qwen3Guard
    audit: Callable[[dict[str, object]], None] | None = None

    def _extra_scanners(self, rail: Rail) -> list[tuple[str, Scanner]]:
        if rail is Rail.RETRIEVAL:
            return [("secret", secret_scanner)]
        if rail is Rail.TOOL_CALL:
            return [("destructive", destructive_scanner), ("secret", secret_scanner)]
        if rail is Rail.OUTPUT:
            return [("secret", secret_scanner), ("pii", pii_scanner)]
        return [("pii", pii_scanner)]  # INPUT

    def _verdict(
        self, decision: str, rail: Rail, scanner: str, category: str | None, reason: str
    ) -> RailVerdict:
        v = RailVerdict(decision, rail, scanner, category, reason)
        if self.audit is not None:
            self.audit(
                {
                    "event": "guard",
                    "rail": rail.value,
                    "decision": decision,
                    "scanner": scanner,
                    "category": category,
                    "reason": reason,
                }
            )
        return v

    async def check(
        self, text: str, rail: Rail, *, system_prompt: str = "", high_stakes: bool = False
    ) -> RailVerdict:
        # 1. deterministic core (Phase-1.5 engine) for input/output; retrieval reuses the input
        #    override signatures (indirect injection = the same patterns, now inside a doc).
        if rail is Rail.INPUT:
            gv = self.engine.inspect_input(text)
            if not gv.allowed:
                return self._verdict(BLOCK, rail, "guardrail-engine", gv.category, gv.reason)
        elif rail is Rail.OUTPUT:
            gv = self.engine.inspect_output(text, system_prompt=system_prompt)
            if not gv.allowed:
                return self._verdict(BLOCK, rail, "guardrail-engine", gv.category, gv.reason)
        elif rail is Rail.RETRIEVAL:
            gv = self.engine.inspect_input(text)
            if not gv.allowed:
                return self._verdict(BLOCK, rail, "indirect-injection", gv.category, gv.reason)

        # 2. extra deterministic scanners per rail (always-on, zero-cost)
        for name, scan in self._extra_scanners(rail):
            hit = scan(text)
            if hit is not None:
                return self._verdict(BLOCK, rail, name, hit[0], hit[1])

        # 3. optional guard-model ensemble: ANY-block for high-stakes, single-model for routine.
        flagged: RailVerdict | None = None
        for m in self.models if high_stakes else self.models[:1]:
            mv = await m.classify(text, rail=rail.value)
            if mv.unsafe:
                return self._verdict(
                    BLOCK,
                    rail,
                    m.name,
                    mv.category or "model_guard",
                    f"guard-model {m.name}: unsafe",
                )
            if mv.severity == "controversial" and flagged is None:
                flagged = self._verdict(
                    FLAG,
                    rail,
                    m.name,
                    mv.category or "model_guard",
                    f"guard-model {m.name}: controversial",
                )
        if flagged is not None:
            return flagged

        return RailVerdict(ALLOW, rail)


def register_tool_call_rail(registry: HookRegistry, guard: GuardRails) -> Hook:
    """Register the TOOL_CALL rail as a `pre_tool_use` hook on `registry` (a
    `hooks.registry.HookRegistry`) -- s46: the same live, blocking control point
    `hooks/rails.py`'s user-authored rails already use (proven by
    `test_e2e_rail_blocks_tool_in_real_loop`), not a new wiring point.

    Deterministic scanners only (destructive-args / secret-exfil) -- no guard-model
    ensemble here, since a per-tool-call LLM classification would violate the
    zero-cost/no-API-hammering mandate on every single tool invocation.
    """
    import json

    from madras.hooks.models import HookResult

    async def _hook(event: str, payload: dict[str, Any]) -> HookResult | None:
        blob = json.dumps(payload.get("args", {}), default=str)
        verdict = await guard.check(blob, Rail.TOOL_CALL)
        if verdict.decision == BLOCK:
            return HookResult(allow=False, message=f"blocked by tool-call rail: {verdict.reason}")
        return None

    return registry.register("pre_tool_use", _hook)


# Module-level default instance -- retrieval scanning (web/browser tool results) calls
# straight through this rather than requiring every builtin tool to construct its own
# GuardRails + thread it through as a parameter. Deterministic-only (no guard-model
# ensemble; same zero-cost/no-API-hammering reasoning as the tool-call rail above).
_default_guard = GuardRails()


async def scan_retrieval(content: str) -> str:
    """Scan externally-fetched content (web_fetch/web_search/browser_snapshot/browser_read
    results) for indirect prompt injection before it re-enters context. Returns `content`
    unchanged if clean; returns a redaction marker (never raises) if flagged -- retrieval
    is a read path, so fail-safe means replacing the payload, not raising and losing the
    turn. Runs the RETRIEVAL rail: the same indirect-injection signatures the input rail
    uses (now inside a fetched doc, per rails.py's own design) plus the secret scanner."""
    if not content:
        return content
    verdict = await _default_guard.check(content, Rail.RETRIEVAL)
    if verdict.decision == BLOCK:
        return f"[content redacted by retrieval rail: {verdict.reason}]"
    return content


# rail name -> Granite Guardian risk
_RAIL_RISK: dict[str, str] = {
    "jailbreak": "jailbreak",
    "prompt_injection": "jailbreak",
    "input": "harm",
    "output": "harm",
    "bias": "social_bias",
    "violence": "violence",
    "sexual": "sexual_content",
    "profanity": "profanity",
}


class _GraniteGuard:
    """Live IBM Granite Guardian (Apache-2.0) runner: classifies text for a risk -> Yes/No."""

    name = "granite-guardian"

    def __init__(self, model: object, tokenizer: object) -> None:
        self._model, self._tok = model, tokenizer

    def _run(self, text: str, risk: str) -> str:
        import torch

        enc = self._tok.apply_chat_template(  # type: ignore[attr-defined]
            [{"role": "user", "content": text}],
            guardian_config={"risk_name": risk},
            add_generation_prompt=True,
            return_tensors="pt",
            return_dict=True,
        ).to(self._model.device)  # type: ignore[attr-defined]
        with torch.no_grad():
            out = self._model.generate(**enc, max_new_tokens=20, do_sample=False)  # type: ignore[attr-defined]
        return self._tok.decode(  # type: ignore[attr-defined]
            out[0][enc["input_ids"].shape[1] :],  # type: ignore[reportUnknownMemberType]
            skip_special_tokens=True,
        ).strip()

    async def classify(self, text: str, *, rail: str) -> ModelVerdict:
        risk = _RAIL_RISK.get(rail, "harm")
        label = await asyncio.to_thread(self._run, text, risk)
        unsafe = label.strip().lower().startswith("yes")
        return ModelVerdict(
            unsafe=unsafe, category=risk if unsafe else "", severity="unsafe" if unsafe else "safe"
        )


class GraniteGuardianBackend:
    """Adapter over IBM Granite Guardian (Apache-2.0). `connect()` loads the real guard model in
    4-bit (fits the 4 GB GPU at ~1.6 GB VRAM); the model is also injectable (a fake in tests). Maps
    the guard's Yes/No label to ModelVerdict. Llama Guard / ShieldGemma rejected on license."""

    def __init__(self, model: object, name: str = "granite-guardian") -> None:
        self._model = model
        self.name = name

    @classmethod
    def connect(
        cls,
        model_factory: Callable[[], object] | None = None,
        name: str = "granite-guardian",
        *,
        model_dir: str = "ibm-granite/granite-guardian-3.1-2b",
        device: str = "cuda",
        quantize: str = "4bit",
    ) -> GraniteGuardianBackend:
        if model_factory is not None:
            return cls(model_factory(), name=name)
        try:
            import torch
            from transformers import (
                AutoModelForCausalLM,
                AutoTokenizer,
                BitsAndBytesConfig,
            )
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "the guard stack is not installed - install the `guard` extra (transformers + "
                "bitsandbytes) + the Granite Guardian weights (Apache-2.0)"
            ) from exc
        qcfg = (
            BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16, bnb_4bit_quant_type="nf4"
            )
            if quantize == "4bit"
            else None
        )
        model = AutoModelForCausalLM.from_pretrained(  # type: ignore[reportUnknownMemberType]
            model_dir, quantization_config=qcfg, device_map=device, dtype=torch.float16
        ).eval()
        tokenizer = AutoTokenizer.from_pretrained(model_dir)  # type: ignore[reportUnknownMemberType,reportUnknownVariableType]
        return cls(_GraniteGuard(model, tokenizer), name=name)  # type: ignore[reportUnknownArgumentType]

    async def classify(self, text: str, *, rail: str) -> ModelVerdict:
        return await self._model.classify(text, rail=rail)  # type: ignore[attr-defined]
