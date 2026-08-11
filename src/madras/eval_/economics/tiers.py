"""Canonical Madras pricing tiers (Vision §15), research-validated 2026-06.

Encodes the decisions:
  (1) §15 price points are the defaults (USD / PPP ~1/3).
  (2) CREDITS are the spine of every paid tier — caps token burn (the lesson from
      Cursor's ~-30% margin: agentic tasks use ~1000x chat tokens; flat-rate is a trap).
  (3) Tiers are backed by INTERNAL Sonnet-class by DEFAULT; Opus/POWER is metered, never
      unlimited frontier.
Credits mirror Lovable's proven model; outcome-based is reserved for Enterprise/SMB-MVP.
"""

from __future__ import annotations

from typing import Any

# tier -> canonical config. price_ppp ≈ 1/3 (India/SEA/LATAM). credits = monthly allowance.
MADRAS_TIERS: dict[str, dict[str, Any]] = {
    "tourist": {
        "price_usd": 0,
        "price_ppp": 0,
        "model_class": "FREE",
        "credits": 150,
        "note": "5 sessions/day · OSS models · no memory persistence (the memory wall)",
    },
    "resident": {
        "price_usd": 19,
        "price_ppp": 7,
        "model_class": "INTERNAL",
        "credits": 2000,
        "note": "memory persistence · Drona Core (Sonnet+Madras) · 15 agents",
    },
    "professional": {
        "price_usd": 49,
        "price_ppp": 17,
        "model_class": "INTERNAL+POWER",
        "credits": 6000,
        "note": "all sandboxes · POWER (Opus) metered 20 sessions · BYOK · 35 agents",
    },
    "creator": {
        "price_usd": 99,
        "price_ppp": 35,
        "model_class": "POWER",
        "credits": 15000,
        "note": "marketplace SELL (85/15) · agent builder · POWER 100 sessions",
    },
    "enterprise": {
        "price_usd": None,
        "price_ppp": None,
        "model_class": "custom",
        "credits": None,
        "note": "~$500+/mo · outcome-based option · private instance · white-label",
    },
}

# model class -> the default model alias used for cost-to-serve. INTERNAL+POWER costs
# at the INTERNAL default; POWER usage is a metered overage, not the baseline.
MODEL_CLASS_DEFAULT: dict[str, str] = {
    "FREE": "llama-70b",
    "INTERNAL": "claude-sonnet",
    "INTERNAL+POWER": "claude-sonnet",
    "POWER": "claude-opus",
    "custom": "claude-opus",
}

# Credit model — credits charged per task by complexity (mirrors Lovable's credits).
CREDITS_PER_TASK: dict[str, int] = {"light": 1, "standard": 3, "heavy": 10}
# Top-up pack price: USD per 1000 credits (consumption layer on top of the sub).
CREDIT_PACK_USD_PER_1000 = 8.0
# Marketplace creator split (creator keeps 85%).
MARKETPLACE_CREATOR_SHARE = 0.85

# Per-tier monthly TASK CAP — grounded in competitor usage (researched 2026-06):
# Lovable Pro $25 = 100 credits/mo (~3-5 actions/day); Replit Pro $95 = $100 credits;
# free tiers ~5 actions/day; Cursor 1M DAU / 7M MAU. BYOK is UNCAPPED (usage = revenue).
TIER_TASK_CAP: dict[str, int | None] = {
    "tourist": 30,  # ~5/day x ~6 active days — the conversion wall
    "resident": 120,  # ~Lovable Pro band (100-150 actions/mo)
    "professional": 300,  # ~Replit Pro / heavy active user
    "creator": 600,  # power user / builder
    "byok": None,  # uncapped — we earn on usage, not a seat
    "enterprise": None,  # custom / committed volume
}


def tier_default_model(tier: str) -> str:
    cls = MADRAS_TIERS.get(tier, {}).get("model_class", "FREE")
    return MODEL_CLASS_DEFAULT.get(cls, "llama-70b")


def credits_for_tasks(n_light: int = 0, n_standard: int = 0, n_heavy: int = 0) -> int:
    """Total credits a usage mix consumes."""
    c = CREDITS_PER_TASK
    return n_light * c["light"] + n_standard * c["standard"] + n_heavy * c["heavy"]


def credit_overage_usd(credits_used: int, allowance: int | None) -> float:
    """USD owed for credits consumed beyond the tier's monthly allowance (top-up)."""
    if allowance is None:
        return 0.0
    over = max(0, credits_used - allowance)
    return round(over / 1000.0 * CREDIT_PACK_USD_PER_1000, 4)
