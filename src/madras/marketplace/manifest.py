"""Plugin + marketplace manifest spec, install/auth policy, and validator — the 85/15 contract.

Every marketplace item ships a `plugin.json` (a `PluginManifest`); a `marketplace.json` lists them
(`MarketplaceManifest`). `validate_plugin` enforces the contract BEFORE listing/installing:
- license is OSI-permissive (the no-AGPL/GPL doctrine — reuses the skills license gate),
- the creator keeps >= 85% (platform fee <= 15% — `MARKETPLACE_CREATOR_SHARE`),
- version is semver, kind is known, permissions are declared from the allowed scope vocabulary
  (ASI03 — least privilege), and paid items actually have a price.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from madras.eval_.economics.tiers import MARKETPLACE_CREATOR_SHARE
from madras.skills.ingest import PERMISSIVE

_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")

PLUGIN_KINDS = frozenset({"skill", "agent", "tool", "connector"})
PRICING_MODELS = frozenset({"free", "one_time", "subscription"})
# Declarable permission scopes (least-privilege; mirrors the tool scope vocabulary).
ALLOWED_SCOPES = frozenset(
    {
        "web.read",
        "web.write",
        "fs.read",
        "fs.write",
        "memory.read",
        "memory.write",
        "exec",
        "network",
        "messaging",
        "schedule",
        "mcp",
    }
)


class Pricing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = "free"
    price_usd: float = 0.0
    creator_share: float = MARKETPLACE_CREATOR_SHARE


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str
    author: str = Field(min_length=1)
    license: str = Field(min_length=1)
    kind: str
    entrypoint: str = Field(min_length=1)
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    pricing: Pricing = Field(default_factory=Pricing)


class MarketplaceListing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    version: str
    source: str = Field(min_length=1)  # repo / path / url the installer resolves


class MarketplaceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = "1"
    plugins: list[MarketplaceListing] = Field(default_factory=list[MarketplaceListing])


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list[str])
    warnings: list[str] = field(default_factory=list[str])
    manifest: PluginManifest | None = None


def _pydantic_errors(exc: ValidationError) -> list[str]:
    return [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]


def validate_plugin(data: dict) -> ValidationResult:  # type: ignore[type-arg]
    """Validate a plugin manifest against the schema + the marketplace policy."""
    try:
        m = PluginManifest.model_validate(data)
    except ValidationError as exc:
        return ValidationResult(ok=False, errors=_pydantic_errors(exc))

    errors: list[str] = []
    warnings: list[str] = []

    if not _SEMVER.match(m.version):
        errors.append(f"version '{m.version}' is not semver (MAJOR.MINOR.PATCH)")
    if m.kind not in PLUGIN_KINDS:
        errors.append(f"kind '{m.kind}' unknown (one of {sorted(PLUGIN_KINDS)})")
    if m.license.strip().lower() not in PERMISSIVE:
        errors.append(f"license '{m.license}' not OSI-permissive — cannot be listed")

    p = m.pricing
    if p.model not in PRICING_MODELS:
        errors.append(f"pricing.model '{p.model}' unknown (one of {sorted(PRICING_MODELS)})")
    if not (0.0 <= p.creator_share <= 1.0):
        errors.append("pricing.creator_share must be within [0, 1]")
    elif p.creator_share < MARKETPLACE_CREATOR_SHARE:
        errors.append(
            f"pricing.creator_share {p.creator_share} < {MARKETPLACE_CREATOR_SHARE} "
            f"(platform fee may not exceed {1 - MARKETPLACE_CREATOR_SHARE:.0%})"
        )
    if p.model in {"one_time", "subscription"} and p.price_usd <= 0:
        errors.append(f"pricing.model '{p.model}' requires price_usd > 0")
    if p.model == "free" and p.price_usd > 0:
        warnings.append("pricing.model is 'free' but price_usd > 0 — price ignored")

    unknown = sorted(set(m.permissions) - ALLOWED_SCOPES)
    if unknown:
        errors.append(
            f"undeclared permission scopes: {unknown} (allowed: {sorted(ALLOWED_SCOPES)})"
        )

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings, manifest=m)


def validate_marketplace(data: dict) -> ValidationResult:  # type: ignore[type-arg]
    """Validate a marketplace.json — schema + unique plugin ids + semver listing versions."""
    try:
        mk = MarketplaceManifest.model_validate(data)
    except ValidationError as exc:
        return ValidationResult(ok=False, errors=_pydantic_errors(exc))

    errors: list[str] = []
    seen: set[str] = set()
    for listing in mk.plugins:
        if listing.id in seen:
            errors.append(f"duplicate plugin id '{listing.id}'")
        seen.add(listing.id)
        if not _SEMVER.match(listing.version):
            errors.append(f"{listing.id}: version '{listing.version}' is not semver")
    return ValidationResult(ok=not errors, errors=errors)
