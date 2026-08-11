"""Agent-as-markdown — author an agent as one markdown file: YAML frontmatter (config +
per-tool permissions) + a markdown body (the instructions/persona).

The opencode/eve/Claude agent format, adapted to Madras: the frontmatter carries the declarative
config (model · mode · tools · rank · **permissions**), the body is the always-on instructions,
and per-tool permissions compile straight to governed `PermissionRule`s (the B17 engine). A
human-authorable, diff-friendly unit that complements the YAML role files + the Compiler (D35).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import yaml

from madras.security.permissions import Decision, PermissionRule

_DECISION = {"allow": Decision.ALLOW, "ask": Decision.ASK, "deny": Decision.DENY}


@dataclass
class AgentDoc:
    name: str = ""
    # frontmatter minus `permissions`
    fields: dict[str, Any] = field(default_factory=dict[str, Any])
    instructions: str = ""  # the markdown body
    permission_rules: list[PermissionRule] = field(default_factory=list[PermissionRule])


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    s = text.lstrip()
    if not s.startswith("---"):
        return {}, text.strip()
    end = s.find("\n---", 3)
    if end == -1:
        return {}, text.strip()
    fm: Any = yaml.safe_load(s[3:end]) or {}
    body = s[end + 4 :].lstrip("\n")
    return (cast("dict[str, Any]", fm) if isinstance(fm, dict) else {}), body.strip()


def _rules_from_permissions(perms: dict[str, Any]) -> list[PermissionRule]:
    """`{tool: allow|ask|deny}` or `{tool: {arg_glob: decision}}` → PermissionRules."""
    rules: list[PermissionRule] = []
    for tool, val in (perms or {}).items():
        if isinstance(val, dict):
            for pattern, decision in cast("dict[str, Any]", val).items():
                d = _DECISION.get(str(decision).lower())
                if d is not None:
                    rules.append(
                        PermissionRule(tool=str(tool), arg_pattern=str(pattern), decision=d)
                    )
        else:
            d = _DECISION.get(str(val).lower())
            if d is not None:
                rules.append(PermissionRule(tool=str(tool), arg_pattern="*", decision=d))
    return rules


def parse_agent_markdown(text: str) -> AgentDoc:
    """Parse an agent markdown file into config fields, instructions, and permission rules."""
    fm, body = _split_frontmatter(text)
    perms: dict[str, Any] = fm.pop("permissions", {}) or {}
    return AgentDoc(
        name=str(fm.get("name", "")),
        fields=fm,
        instructions=body,
        permission_rules=_rules_from_permissions(perms),
    )
