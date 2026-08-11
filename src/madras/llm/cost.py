"""Cost-tier cascade.

Pattern from Blueprint §6: cheapest model first, escalate to next tier on
low-confidence response. The four tiers map onto OpenRouter model slugs:

  FREE     → google/gemini-2.0-flash-exp:free
  CHEAP    → anthropic/claude-haiku-4-5
  STANDARD → anthropic/claude-sonnet-4-6
  PREMIUM  → anthropic/claude-opus-4-7
"""

from __future__ import annotations

from enum import Enum


class CostTier(str, Enum):
    FREE = "free"
    CHEAP = "cheap"
    STANDARD = "standard"
    PREMIUM = "premium"


CASCADE_ORDER: list[CostTier] = [
    CostTier.FREE,
    CostTier.CHEAP,
    CostTier.STANDARD,
    CostTier.PREMIUM,
]

DEFAULT_MODEL_PER_TIER: dict[CostTier, str] = {
    CostTier.FREE: "google/gemini-2.0-flash-exp:free",
    CostTier.CHEAP: "anthropic/claude-haiku-4-5",
    CostTier.STANDARD: "anthropic/claude-sonnet-4-6",
    CostTier.PREMIUM: "anthropic/claude-opus-4-7",
}


def escalate(tier: CostTier) -> CostTier:
    """Return the next tier up (premium is fixed point)."""
    idx = CASCADE_ORDER.index(tier)
    return CASCADE_ORDER[min(idx + 1, len(CASCADE_ORDER) - 1)]
