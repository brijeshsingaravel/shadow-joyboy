"""Template manifest — spec for agent templates in the gallery."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class TemplateCategory(str, Enum):
    """High-level categories for template browsing."""

    CODING = "coding"
    PRODUCTIVITY = "productivity"
    RESEARCH = "research"
    DATA_ANALYSIS = "data_analysis"
    CUSTOMER_SUPPORT = "customer_support"
    MARKETING = "marketing"
    SECURITY = "security"
    OPERATIONS = "operations"
    OTHER = "other"


@dataclass
class TemplateManifest:
    """Manifest for a deployable agent template."""

    name: str
    version: str
    description: str
    category: TemplateCategory
    agent_md: str
    required_capabilities: list[str]
    estimated_tokens_per_run: int
    author: str = "Madras Team"
    license: str = "MIT"
    preview_prompt: str | None = None
    sample_outputs: list[dict[str, str]] | None = None

    def __post_init__(self) -> None:
        validate_template_manifest(self)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_template_manifest(manifest: TemplateManifest) -> bool:
    """Validate a template manifest. Raises ValueError on failure."""
    if not manifest.name or not manifest.name.strip():
        raise ValueError("name is required")
    if not manifest.version or not manifest.version.strip():
        raise ValueError("version is required")
    if not manifest.description or not manifest.description.strip():
        raise ValueError("description is required")
    if not manifest.agent_md or not manifest.agent_md.strip():
        raise ValueError("agent_md path is required")
    if manifest.estimated_tokens_per_run < 0:
        raise ValueError("estimated_tokens_per_run must be >= 0")
    if manifest.license not in ("MIT", "Apache-2.0", "BSD-3-Clause", "ISC", "MPL-2.0"):
        raise ValueError(f"license {manifest.license} not in permissive set")
    return True


def render_template_payload(
    manifest: TemplateManifest,
    agent_md_content: str,
) -> dict[str, Any]:
    """Render the Mercur product payload from a manifest + agent.md content."""
    return {
        "title": manifest.name,
        "subtitle": manifest.description,
        "description": f"Madras Agent Template\n\n{manifest.description}",
        "handle": f"{manifest.name.lower().replace(' ', '-')}-v{manifest.version}",
        "is_giftcard": False,
        "status": "published",
        "metadata": {
            "madras_template": True,
            "template_version": manifest.version,
            "template_category": manifest.category.value,
            "required_capabilities": manifest.required_capabilities,
            "estimated_tokens": manifest.estimated_tokens_per_run,
            "preview_prompt": manifest.preview_prompt,
            "sample_outputs": manifest.sample_outputs,
            "deliverables": [f"{manifest.agent_md}: {agent_md_content[:200]}..."],
        },
    }


class MercurClientProtocol:
    """Protocol for Mercur client (real or mock)."""

    async def create_product(self, payload: dict[str, Any]) -> dict[str, Any]: ...


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
    validate_template_manifest(manifest)
    payload = render_template_payload(manifest, agent_md_content)
    result = await mercur_client.create_product(payload)
    return {
        "id": result["id"],
        "handle": result["handle"],
        "manifest": manifest.to_dict(),
    }
