"""Tool Bundle spec — what lives in agents/bundles/tools/*.yaml."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from madras.models.agent_config import Rank


class Scope(str, Enum):
    READ = "read"
    WRITE = "write"
    EXEC = "exec"
    EXTERNAL = "external"


_MIN_RANK_FOR_SCOPE: dict[Scope, Rank] = {
    Scope.READ: Rank.INTERN,
    Scope.WRITE: Rank.SENIOR,
    Scope.EXEC: Rank.SPECIALIST,
    Scope.EXTERNAL: Rank.PRINCIPAL,
}

_RANK_ORDER = [Rank.INTERN, Rank.JUNIOR, Rank.SPECIALIST, Rank.SENIOR, Rank.PRINCIPAL, Rank.LEGEND]


def _rank_at_least(actual: Rank, required: Rank) -> bool:
    return _RANK_ORDER.index(actual) >= _RANK_ORDER.index(required)


class CredentialPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issuance: Literal["jit_task_scoped"]
    max_ttl_seconds: int = Field(..., ge=10, le=3600)


class ToolBundleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    rank_required: Rank
    scopes: list[Scope] = Field(min_length=1)
    # s46: a bundle is EITHER an MCP-server bundle (mcp_servers + credential_policy,
    # both required together -- JIT task-scoped creds for an external integration) OR a
    # builtin-toolset bundle (toolset names a live @tool(toolset=...) registration, no
    # external credential to scope). Exactly one of the two kinds is set.
    mcp_servers: list[str] = Field(default_factory=list)
    credential_policy: CredentialPolicy | None = None
    toolset: str | None = None

    @model_validator(mode="after")
    def _rank_sufficient_for_scopes(self) -> ToolBundleSpec:
        for scope in self.scopes:
            needed = _MIN_RANK_FOR_SCOPE[scope]
            if not _rank_at_least(self.rank_required, needed):
                raise ValueError(
                    f"bundle {self.name!r} declares scope {scope.value!r} but "
                    f"rank_required={self.rank_required.value!r} is below {needed.value!r}"
                )
        return self

    @model_validator(mode="after")
    def _exactly_one_kind(self) -> ToolBundleSpec:
        is_mcp, is_builtin = bool(self.mcp_servers), self.toolset is not None
        if is_mcp == is_builtin:
            raise ValueError(f"bundle {self.name!r} must set EXACTLY ONE of mcp_servers or toolset")
        if is_mcp and self.credential_policy is None:
            raise ValueError(f"bundle {self.name!r} has mcp_servers but no credential_policy")
        if is_builtin and self.credential_policy is not None:
            raise ValueError(f"bundle {self.name!r} is toolset-based; credential_policy is N/A")
        return self
