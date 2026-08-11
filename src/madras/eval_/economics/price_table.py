"""Per-model token price table — the cost basis for unit economics.

Two bases (honest, per the pricing discussion):
  * "measured"  — frontier models report real metered cost; we use public list prices.
  * "imputed"   — free OSS models cost us ~$0 on the NVIDIA-NIM dev promo, which is NOT
                  durable. We impute the market rate for that weight class (OpenRouter /
                  together.ai-style) so margins are honest, flagged `provenance: assumed`.

Prices are USD per 1M tokens, (input, output). Frontier prices sourced 2026-06; update
as providers change. cost_of_tokens() is the single entry the economics layer calls.
"""

from __future__ import annotations

# alias -> (input_usd_per_mtok, output_usd_per_mtok, basis)
PRICES: dict[str, tuple[float, float, str]] = {
    # --- frontier (measured: public list price USD/Mtok, verified 2026-06 via web) ---
    # Caching gives ~90% off input + batch -50% — major levers for agent workloads
    # that re-send large context each step (see cost_of_tokens caching note).
    "claude-haiku": (1.0, 5.0, "measured"),  # Haiku 4.5
    "claude-sonnet": (3.0, 15.0, "measured"),  # Sonnet 4.6
    "claude-opus": (5.0, 25.0, "measured"),  # Opus 4.8 (NOT 15/75 — corrected)
    "gpt-4o": (2.5, 15.0, "measured"),  # GPT-5.4 class
    "gpt-5": (5.0, 30.0, "measured"),  # GPT-5.5
    "gemini-pro": (1.25, 10.0, "measured"),  # Gemini 2.5 Pro
    # --- free OSS (imputed market rate for the weight class; promo cost is ~0 today) ---
    "llama-70b": (0.30, 0.40, "imputed"),
    "nemotron-super": (0.20, 0.40, "imputed"),
    "nemotron-super-120b": (0.40, 0.80, "imputed"),
    "nemotron-omni": (0.15, 0.30, "imputed"),
    "qwen3.5": (0.40, 0.80, "imputed"),
    "glm-5.1": (0.40, 1.20, "imputed"),
    "kimi-k2": (0.40, 1.20, "imputed"),
    "step-3.7-flash": (0.15, 0.30, "imputed"),
    "gemini-flash": (0.075, 0.30, "imputed"),
    # --- local Ollama (compute-only; effectively self-host, near-zero marginal) ---
    "qwen3": (0.0, 0.0, "imputed"),
    "qwen-coder": (0.0, 0.0, "imputed"),
}

# Imputed fallback for any unrouted/unknown alias (treat as a small OSS model).
_FALLBACK = (0.20, 0.40, "imputed")


def _row(alias: str) -> tuple[float, float, str]:
    return PRICES.get(alias, _FALLBACK)


def basis_of(alias: str) -> str:
    return _row(alias)[2]


# Caching economics (verified 2026-06): cache READ = 10% of input price (90% off);
# batch = 50% off everything. Agentic loops re-send a large static prefix every step,
# so a high cache-hit fraction is the difference between viable and ~-30% margin.
CACHE_READ_MULT = 0.10  # Anthropic/Gemini-2.5 cached read = 0.1x input
BATCH_MULT = 0.50  # batch API halves all costs


def cost_of_tokens(
    alias: str, in_tok: int, out_tok: int, *, cached_frac: float = 0.0, batch: bool = False
) -> float:
    """USD cost for a call of (in_tok, out_tok) on this model.

    ``cached_frac`` = fraction of INPUT tokens served from a warm cache (charged at
    10% of input). The remainder is full-price input. ``batch`` halves the total.
    The defaults (0.0, False) keep the original behaviour for existing callers.
    """
    in_rate, out_rate, _ = _row(alias)
    cf = max(0.0, min(1.0, cached_frac))
    eff_in_rate = in_rate * ((1.0 - cf) + cf * CACHE_READ_MULT)
    cost = (in_tok / 1_000_000.0) * eff_in_rate + (out_tok / 1_000_000.0) * out_rate
    return cost * BATCH_MULT if batch else cost
