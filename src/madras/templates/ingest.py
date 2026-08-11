"""Template ingestion — pushes validated templates to Mercur marketplace."""

from __future__ import annotations

from typing import Any

from .manifest import MercurClientProtocol, TemplateManifest, render_template_payload


async def ingest_template(
    *,
    manifest: TemplateManifest,
    agent_md_content: str,
    mercur_client: MercurClientProtocol,
    base_url: str = "http://localhost:9000",
) -> dict[str, Any]:
    """Ingest a template into the Mercur marketplace.

    Args:
        manifest: Validated TemplateManifest
        agent_md_content: Full content of the agent.md file
        mercur_client: Client with async create_product(payload) -> {"id", "handle"}
        base_url: Mercur admin base URL (unused in mock, kept for real impl)

    Returns:
        Dict with product id and handle.
    """
    from .manifest import validate_template_manifest

    validate_template_manifest(manifest)
    payload = render_template_payload(manifest, agent_md_content)
    result = await mercur_client.create_product(payload)
    return {
        "id": result["id"],
        "handle": result["handle"],
        "manifest": manifest.to_dict(),
    }


__all__ = ["ingest_template", "render_template_payload"]
