"""Tool Bundle and Skill references inside role.yaml.

Bundles and skills themselves are separate files in agents/bundles/ and
agents/skills/. Role.yaml references them by name.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, field_validator

_BUNDLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ToolBundleRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        if not _BUNDLE_NAME_RE.match(v):
            raise ValueError(f"bundle name must be dotted snake_case, got {v!r}")
        return v


class SkillRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        if not _SKILL_NAME_RE.match(v):
            raise ValueError(f"skill name must be snake_case, got {v!r}")
        return v
