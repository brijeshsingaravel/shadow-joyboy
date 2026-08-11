"""Template package init."""

from __future__ import annotations

from .ingest import ingest_template, render_template_payload
from .manifest import TemplateCategory, TemplateManifest, validate_template_manifest

__all__ = [
    "TemplateCategory",
    "TemplateManifest",
    "ingest_template",
    "render_template_payload",
    "validate_template_manifest",
]
