"""compiler/markdown.py — Agent-as-Markdown authoring -> governed agent (E1 Task D1).

Composes the already-built agent_markdown.py parser with the same factory path every
other compile mode uses -- no bypass. Design correction from grounding (founder-
confirmed): the opencode/eve format has one freeform instructions body, but
AgentConfig.persona needs THREE fields -- refusal_style/north_star must be explicit
frontmatter keys (a deliberate Madras extension of the format), never derived or
defaulted (matches B4's "identity is the moat" precedent). permission_rules are
runtime-injected (security/permissions.py::PermissionEngine), never persisted on
AgentConfig -- returned alongside the AgentRecord, not baked into the role YAML.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from madras_capabilities.catalog import Catalog
from madras_capabilities.tiers import plan_entitlement_policy

from madras.agent_markdown import parse_agent_markdown
from madras.compiler.compile import target_role_path
from madras.compiler.intent import CapabilityNotEntitled
from madras.factory.dynamic import AuthContext
from madras.factory.spawn import AgentRecord, spawn_agent
from madras.security.permissions import PermissionRule


class MissingPersonaField(ValueError):
    """The markdown frontmatter is missing a required persona.{refusal_style,north_star}."""


@dataclass
class MarkdownCompileResult:
    record: AgentRecord
    permission_rules: list[PermissionRule]


def _emit_role_from_markdown(
    name: str, fields: dict[str, Any], instructions: str
) -> dict[str, Any]:
    persona: dict[str, Any] = fields.get("persona") or {}
    for key in ("refusal_style", "north_star"):
        if not str(persona.get(key, "")).strip():
            raise MissingPersonaField(f"markdown agent {name!r} is missing required persona.{key}")
    return {
        "name": name,
        "archetype": fields.get("archetype", ""),
        "neighborhood": fields.get("neighborhood", ""),
        "rank": "intern",
        "origin": "immigrant",
        "persona": {
            "voice": instructions,
            "refusal_style": persona["refusal_style"],
            "north_star": persona["north_star"],
        },
        "capability_summary": fields.get("discovery_summary", ""),
        "capabilities": list(fields.get("capabilities", [])),
        "skills": list(fields.get("skills", [])),
        "execution": {"default_pattern": fields.get("execution", "react")},
    }


def compile_markdown(
    path: Path,
    *,
    agents_dir: Path,
    catalog: Catalog,
    auth: AuthContext,
) -> MarkdownCompileResult:
    text = Path(path).read_text(encoding="utf-8")
    doc = parse_agent_markdown(text)

    capabilities = list(doc.fields.get("capabilities", []))
    entitled = plan_entitlement_policy(catalog)(auth)
    not_entitled = [c for c in capabilities if c not in entitled]
    if not_entitled:
        raise CapabilityNotEntitled(
            f"capabilities not entitled for plan {auth.plan!r}: {not_entitled}"
        )

    role = _emit_role_from_markdown(doc.name, doc.fields, doc.instructions)

    role_path, role_name = target_role_path(agents_dir, doc.name)
    role_path.write_text(yaml.safe_dump(role), encoding="utf-8")
    record = spawn_agent(agents_dir=agents_dir, role_name=role_name)
    return MarkdownCompileResult(record=record, permission_rules=doc.permission_rules)
