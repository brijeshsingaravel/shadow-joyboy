"""compiler/marketplace.py — § B9 Sell/Publish: the real Proving-Ground gate before a
built agent can be listed. Nobody's listing gets ambient trust (Product/Marketplace.md):
an agent must clear the compile->verify->GEPA-optimize residency loop (D38/E1, already
built) before this module will even attempt a marketplace submission.

The actual seller/product/offer creation happens in the real, separately-running
Mercur/Medusa marketplace backend (Engineering/marketplace) -- this module shells out to
its own `medusa exec` script (create-creator-listing.ts) rather than reimplementing
Mercur's commerce workflows in Python. Payouts are NOT wired (parked platform-wide,
Knowledge/Ideas.md) -- a successful listing is real and browsable, priced at 0, marked
payouts_pending in its metadata.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from madras_capabilities.catalog import Catalog

from madras.compiler.optimize import ResidencyResult, compile_to_residency
from madras.factory.dynamic import AuthContext
from madras.llm.gateway import LLMGateway

_MARKETPLACE_API_DIR = Path(__file__).resolve().parents[3] / "marketplace" / "packages" / "api"


@dataclass
class MarketplaceResult:
    verified: bool
    listed: bool
    rounds: int = 0
    lift: float = 0.0
    agent_name: str | None = None
    seller_id: str | None = None
    product_id: str | None = None
    offer_id: str | None = None
    reason: str = ""


async def _run_listing_script(
    *,
    creator_email: str,
    creator_name: str,
    agent_name: str,
    outcome: str,
    capabilities: list[str],
    marketplace_api_dir: Path,
) -> dict[str, str]:
    env = {
        "CREATOR_EMAIL": creator_email,
        "CREATOR_NAME": creator_name,
        "AGENT_NAME": agent_name,
        "AGENT_OUTCOME": outcome,
        "AGENT_CAPABILITIES": ",".join(capabilities),
    }
    proc = await asyncio.create_subprocess_exec(
        "bunx",
        "medusa",
        "exec",
        "./src/scripts/create-creator-listing.ts",
        cwd=str(marketplace_api_dir),
        env={**__import__("os").environ, **env},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(
            f"create-creator-listing.ts failed: {stderr.decode(errors='replace')[-2000:]}"
        )

    # The script prints exactly one JSON line among its log output -- find it.
    for line in reversed(stdout.decode(errors="replace").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    raise RuntimeError("create-creator-listing.ts produced no JSON result line")


async def sell_agent(
    *,
    outcome: str,
    creator_email: str,
    creator_name: str,
    gateway: LLMGateway,
    model: str,
    agents_dir: Path,
    catalog: Catalog,
    auth: AuthContext,
    marketplace_api_dir: Path = _MARKETPLACE_API_DIR,
) -> MarketplaceResult:
    """Runs the real verification gate first; only calls out to the marketplace backend
    if the agent genuinely passes."""
    residency: ResidencyResult = await compile_to_residency(
        outcome=outcome,
        gateway=gateway,
        model=model,
        agents_dir=agents_dir,
        catalog=catalog,
        auth=auth,
    )
    if not residency.verified or residency.record is None:
        return MarketplaceResult(
            verified=False,
            listed=False,
            rounds=residency.rounds,
            lift=residency.lift,
            reason="didn't clear the Proving Ground gate — not listable yet",
        )

    agent_name = residency.record.config.name
    try:
        listing = await _run_listing_script(
            creator_email=creator_email,
            creator_name=creator_name,
            agent_name=agent_name,
            outcome=outcome,
            capabilities=list(residency.record.config.capabilities),
            marketplace_api_dir=marketplace_api_dir,
        )
    except Exception as exc:
        return MarketplaceResult(
            verified=True,
            listed=False,
            rounds=residency.rounds,
            lift=residency.lift,
            agent_name=agent_name,
            reason=f"verified, but listing failed: {exc}",
        )

    return MarketplaceResult(
        verified=True,
        listed=True,
        rounds=residency.rounds,
        lift=residency.lift,
        agent_name=agent_name,
        seller_id=listing.get("seller_id"),
        product_id=listing.get("product_id"),
        offer_id=listing.get("offer_id"),
        reason="verified and listed — payouts not live yet",
    )
