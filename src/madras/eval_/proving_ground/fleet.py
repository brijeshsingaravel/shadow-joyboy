"""Model fleet registry for the Proving Ground.

Enumerates every model accessible to us (from O:/docker-stack/litellm-config.yaml +
vault provider keys) with its tier and routing. The sweep uses ``sweep_safe_models()``
which excludes rate-capped providers (Groq/Cerebras free tiers) so a long sweep does
not get throttled mid-run. Frontier models are routed via vault keys (added to the
LiteLLM config in plan Task A4) and used for the raw-model scaffold-lift baseline.

tier:
  free   — strong OSS, no daily cap (NVIDIA NIM) → the free-tier + sweep workhorses
  capped — free but rate-limited (Groq/Cerebras) → NOT for sweeps
  local  — tiny on-box Ollama models
  frontier — paid Claude/GPT/Gemini-pro via vault keys → premium subs + lift baseline
"""

from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel


class FleetModel(BaseModel):
    alias: str
    real_model: str
    provider: str
    tier: str  # free | capped | local | frontier
    routed_via: str  # litellm | ollama
    no_daily_cap: bool
    notes: str = ""


def _m(
    alias: str,
    real: str,
    provider: str,
    tier: str,
    *,
    cap: bool,
    via: str = "litellm",
    notes: str = "",
) -> FleetModel:
    return FleetModel(
        alias=alias,
        real_model=real,
        provider=provider,
        tier=tier,
        routed_via=via,
        no_daily_cap=cap,
        notes=notes,
    )


# Sourced from O:/docker-stack/litellm-config.yaml (NVIDIA NIM no-cap workhorses,
# Groq/Cerebras rate-capped, Gemini, Ollama-local) + vault frontier keys.
FLEET: dict[str, FleetModel] = {
    m.alias: m
    for m in [
        # --- free, no daily cap (NVIDIA NIM) — the sweep + free-tier fleet ---
        _m("llama-70b", "meta/llama-3.3-70b-instruct", "nvidia_nim", "free", cap=True),
        _m(
            "nemotron-super",
            "nvidia/llama-3.3-nemotron-super-49b-v1",
            "nvidia_nim",
            "free",
            cap=True,
        ),
        _m(
            "nemotron-super-120b",
            "nvidia/nemotron-3-super-120b-a12b",
            "nvidia_nim",
            "free",
            cap=True,
        ),
        _m(
            "nemotron-omni",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
            "nvidia_nim",
            "free",
            cap=True,
        ),
        _m(
            "qwen3.5",
            "qwen/qwen3.5-397b-a17b",
            "nvidia_nim",
            "free",
            cap=True,
            notes="397B MoE flagship",
        ),
        _m("glm-5.1", "z-ai/glm-5.1", "nvidia_nim", "free", cap=True, notes="flagship agentic"),
        _m(
            "kimi-k2",
            "moonshotai/kimi-k2.6",
            "nvidia_nim",
            "free",
            cap=True,
            notes="agentic/coding",
        ),
        _m("step-3.7-flash", "stepfun-ai/step-3.7-flash", "nvidia_nim", "free", cap=True),
        # --- free but rate-capped (Groq / Cerebras) — NOT sweep-safe ---
        _m("gpt-oss-120b", "openai/gpt-oss-120b", "groq", "capped", cap=False),
        _m("deepseek-r1", "qwen3-32b", "groq", "capped", cap=False),
        _m("qwq", "qwen3-32b", "groq", "capped", cap=False),
        _m("llama-70b-groq", "llama-3.3-70b-versatile", "groq", "capped", cap=False),
        _m("qwen3-32b", "qwen-3-32b", "cerebras", "capped", cap=False),
        _m("llama-4-scout", "llama-4-scout-17b-16e-instruct", "cerebras", "capped", cap=False),
        # --- Gemini (free tier → paid) ---
        _m(
            "gemini-flash",
            "gemini/gemini-2.5-flash",
            "google",
            "free",
            cap=False,
            notes="1500/day free",
        ),
        _m("gemini-pro", "gemini/gemini-2.5-pro", "google", "frontier", cap=False),
        # --- local Ollama (tiny, on-box, unlimited) ---
        _m("qwen3", "qwen3:4b", "ollama", "local", cap=True, via="ollama"),
        _m("qwen-coder", "qwen2.5-coder:3b", "ollama", "local", cap=True, via="ollama"),
        # --- frontier (paid, via vault keys; LiteLLM routes added in Task A4) ---
        _m("claude-haiku", "anthropic/claude-haiku", "anthropic", "frontier", cap=False),
        _m("claude-sonnet", "anthropic/claude-sonnet", "anthropic", "frontier", cap=False),
        _m("claude-opus", "anthropic/claude-opus", "anthropic", "frontier", cap=False),
        _m("gpt-4o", "openai/gpt-4o", "openai", "frontier", cap=False),
        _m("gpt-5", "openai/gpt-5", "openai", "frontier", cap=False),
    ]
}


def free_models() -> list[str]:
    return [a for a, m in FLEET.items() if m.tier == "free"]


def frontier_models() -> list[str]:
    return [a for a, m in FLEET.items() if m.tier == "frontier"]


def local_models() -> list[str]:
    return [a for a, m in FLEET.items() if m.tier == "local"]


def sweep_safe_models() -> list[str]:
    """Models safe to hammer in a long sweep — exclude rate-capped providers."""
    return [a for a, m in FLEET.items() if m.no_daily_cap and m.tier != "capped"]


async def smoke_model(alias: str, gateway: object) -> dict[str, Any]:
    """One tiny live call to confirm a model alias is reachable via the gateway.

    Returns ``{ok, latency_ms, error}``. Unknown alias or any backend error → ok=False.
    """
    from madras.llm.gateway import LLMRequest

    if alias not in FLEET:
        return {"ok": False, "latency_ms": 0.0, "error": "unknown alias"}
    req = LLMRequest(
        model=alias,
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=8,
    )
    try:
        raw_resp = await gateway.complete(req)  # type: ignore[attr-defined]
    except Exception as exc:  # unreachable / no credit / bad route
        return {"ok": False, "latency_ms": 0.0, "error": f"{type(exc).__name__}: {exc}"}
    resp = cast("Any", raw_resp)
    return {"ok": True, "latency_ms": max(resp.latency_ms, 0.001), "model": resp.model}


async def smoke_fleet(aliases: list[str], gateway: object) -> dict[str, dict[str, Any]]:
    """Smoke a list of aliases; returns ``{alias: result}``. Sequential (cheap)."""
    out: dict[str, dict[str, Any]] = {}
    for a in aliases:
        out[a] = await smoke_model(a, gateway)
    return out
