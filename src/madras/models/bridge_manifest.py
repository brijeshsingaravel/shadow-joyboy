"""Phase U (D79) -- the universal bridge's manifest. One governance wrapper, reused from
tool_bundle.py's REAL, already-existing `CredentialPolicy`/`Rank` models (ASI03's
credential_policy requirement is project-wide, not a new rule invented for this manifest), and
exactly one of four transport-specific interface shapes:

  in_process    -- WIT/Component-Model-style canonical-ABI, generalizing G8/N5's already-proven
                   "resolve a real address, call it directly" mechanism.
  shared_memory -- in_process's close cousin: same node, different core (the Phase-P
                   dimensional-band vCPU fabric) -- same type vocabulary, a memory region
                   description instead of an address-resolution strategy.
  network       -- MCP-style JSON-RPC 2.0, reusing Madras's own already-committed MCP standard
                   (CLAUDE.md's tech stack), not a competing format.
  human         -- the marketplace case, governed the SAME way as the other three (the plan's
                   own explicit constraint: "this stops the marketplace from becoming a special
                   case"), described differently since there is no function signature to give.

`kind: unknown` plus the generic `extra` field is the deliberate escape hatch for "unknown
future languages" -- no speculative dedicated fields for a case nothing yet needs.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from madras.models.agent_config import Rank
from madras.models.tool_bundle import CredentialPolicy

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")  # matches aalam_manifest.py's own convention

# Kollan's ACTUAL current value types only (BumpAllocator/kollan_collections.py) -- deliberately
# narrower than WIT's full vocabulary (no records/variants/flags yet). Grows only if/when
# Kollan's own value-type system does, per the founder-confirmed scoping call.
_KOLLAN_SCALAR_TYPES = {"int64", "string", "bool", "map"}


def _is_valid_kollan_type(t: str) -> bool:
    if t in _KOLLAN_SCALAR_TYPES:
        return True
    if t.startswith("list<") and t.endswith(">"):
        return _is_valid_kollan_type(t[5:-1])
    return False


class BridgeKind(str, Enum):
    GPL_FUNCTION = "gpl_function"
    HARDWARE_CONSTRUCT = "hardware_construct"
    LLM_ENDPOINT = "llm_endpoint"
    HUMAN_TASK = "human_task"
    UNKNOWN = "unknown"


class Transport(str, Enum):
    IN_PROCESS = "in_process"
    SHARED_MEMORY = "shared_memory"
    NETWORK = "network"
    HUMAN = "human"


class Metering(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contributor_share: float | None = Field(default=None, ge=0.0, le=1.0)  # the marketplace 85/15


class Governance(BaseModel):
    """ALWAYS present, same shape regardless of transport -- every bridge call crosses the
    Contribution shell's governance-check (D78), machine or human."""

    model_config = ConfigDict(extra="forbid")

    rank_required: Rank
    # Optional: a human contributor has no JIT credential to scope (mirrors tool_bundle.py's
    # own toolset-based bundles, which also carry no credential_policy).
    credential_policy: CredentialPolicy | None = None
    metering: Metering = Field(default_factory=Metering)


class Param(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str

    @model_validator(mode="after")
    def _valid_type(self) -> Param:
        if not _is_valid_kollan_type(self.type):
            raise ValueError(f"param {self.name!r} has unsupported type {self.type!r}")
        return self


class DllExportResolve(BaseModel):
    """Generalizes N5's real llama.cpp proof: a compiled function inside a `.dll` file,
    found by (file path, export name) -- the OS resolves the real address from those two."""

    model_config = ConfigDict(extra="forbid")

    strategy: Literal["dll_export"] = "dll_export"
    dll_path: str
    export_name: str


class PythonCallableResolve(BaseModel):
    """Generalizes G8's proof: an already-loaded Python callable, found by (module, qualified
    name) -- Python resolves the real address of that already-loaded object from those two."""

    model_config = ConfigDict(extra="forbid")

    strategy: Literal["python_callable"] = "python_callable"
    module: str
    qualname: str


# `syscall_number` deliberately dropped (not just left undispatched): a raw syscall number is
# an immediate baked into an instruction, not a resolved address -- it doesn't fit "resolve an
# address" at all, unlike the two real cases above. Add it back as its OWN, differently-shaped
# thing if/when a real use case needs manifest-described raw syscalls (G2 already has its own
# mechanism for this that doesn't go through address resolution).
ResolveSpec = DllExportResolve | PythonCallableResolve


class InProcessInterface(BaseModel):
    model_config = ConfigDict(extra="forbid")

    params: list[Param] = Field(default_factory=list[Param])
    returns: str
    resolve: ResolveSpec = Field(discriminator="strategy")

    @model_validator(mode="after")
    def _valid_return_type(self) -> InProcessInterface:
        if not _is_valid_kollan_type(self.returns):
            raise ValueError(f"returns type {self.returns!r} is unsupported")
        return self


class SharedMemoryRegion(BaseModel):
    """A real Morton-banded, arena-resident radix region (K-phase's own
    `SparseRadixIndex`/`kollan_sparse_index`) -- `key_bits` must be a positive multiple of
    `RADIX_BITS` (8), the same constraint `SparseRadixIndex.__init__` itself enforces."""

    model_config = ConfigDict(extra="forbid")

    key_bits: int = Field(..., gt=0, multiple_of=8)


class SharedMemoryInterface(BaseModel):
    model_config = ConfigDict(extra="forbid")

    params: list[Param] = Field(default_factory=list[Param])
    returns: str
    region: SharedMemoryRegion

    @model_validator(mode="after")
    def _valid_return_type(self) -> SharedMemoryInterface:
        if not _is_valid_kollan_type(self.returns):
            raise ValueError(f"returns type {self.returns!r} is unsupported")
        return self


class StdioServerDescriptor(BaseModel):
    """A real, spawnable local MCP server -- same (command, args) shape
    `StdioServerParameters` (the MCP SDK itself) already takes."""

    model_config = ConfigDict(extra="forbid")

    transport: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list[str])


class HttpSseServerDescriptor(BaseModel):
    """A real, already-running MCP server reachable over streamable-HTTP."""

    model_config = ConfigDict(extra="forbid")

    transport: Literal["http_sse"] = "http_sse"
    url: str


ServerDescriptor = StdioServerDescriptor | HttpSseServerDescriptor


class NetworkInterface(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str
    params_schema: dict[str, Any]
    result_schema: dict[str, Any]
    server: ServerDescriptor = Field(discriminator="transport")


class HumanInterface(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    notify_via: Literal["queue", "ui_form", "email"]


_TRANSPORT_FIELD: dict[Transport, str] = {
    Transport.IN_PROCESS: "in_process_interface",
    Transport.SHARED_MEMORY: "shared_memory_interface",
    Transport.NETWORK: "network_interface",
    Transport.HUMAN: "human_interface",
}


class BridgeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    kind: BridgeKind
    transport: Transport
    governance: Governance

    in_process_interface: InProcessInterface | None = None
    shared_memory_interface: SharedMemoryInterface | None = None
    network_interface: NetworkInterface | None = None
    human_interface: HumanInterface | None = None
    extra: dict[str, Any] = Field(default_factory=dict)  # escape hatch for kind=unknown

    @model_validator(mode="after")
    def _valid_semver(self) -> BridgeManifest:
        if not _SEMVER_RE.match(self.version):
            raise ValueError(f"version must be semver (X.Y.Z), got {self.version!r}")
        return self

    @model_validator(mode="after")
    def _exactly_one_interface_matches_transport(self) -> BridgeManifest:
        expected_field = _TRANSPORT_FIELD[self.transport]
        for field_name in _TRANSPORT_FIELD.values():
            value = getattr(self, field_name)
            if field_name == expected_field and value is None:
                raise ValueError(
                    f"transport={self.transport.value!r} requires {field_name} to be set"
                )
            if field_name != expected_field and value is not None:
                raise ValueError(f"transport={self.transport.value!r} must not set {field_name}")
        return self


__all__ = [
    "BridgeKind",
    "BridgeManifest",
    "DllExportResolve",
    "Governance",
    "HttpSseServerDescriptor",
    "HumanInterface",
    "InProcessInterface",
    "Metering",
    "NetworkInterface",
    "Param",
    "PythonCallableResolve",
    "ServerDescriptor",
    "SharedMemoryInterface",
    "SharedMemoryRegion",
    "StdioServerDescriptor",
    "Transport",
]
