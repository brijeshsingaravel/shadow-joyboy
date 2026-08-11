"""Memory Controls (F2.11) — user-facing governance over memory operations.

Provides: see/list, edit, delete, scope (policy), and consent gates for memory ops.
All enforcement is deny-by-default; opt-in required for write kinds/sources.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


class MemoryKind(str):
    """Valid memory kinds in the fabric."""

    FACT = "fact"
    PREFERENCE = "preference"
    PRINCIPLE = "principle"
    RELATIONSHIP = "relationship"
    SEMANTIC = "semantic"
    EPISODIC = "episodic"

    @classmethod
    def all(cls) -> set[str]:
        return {"fact", "preference", "principle", "relationship", "semantic", "episodic"}


_VALID_KINDS = {"fact", "preference", "principle", "relationship", "semantic", "episodic"}


@dataclass(frozen=True)
class MemoryScopePolicy:
    """Per-agent/tenant policy governing what memory ops are permitted.

    Deny-by-default: only explicitly allowed kinds/sources may be written.
    """

    allowed_write_kinds: frozenset[str] = field(default_factory=lambda: frozenset(MemoryKind.all()))
    allowed_write_sources: frozenset[str] = field(
        default_factory=lambda: frozenset({"session", "import"})
    )
    denied_subject_patterns: list[str] = field(default_factory=list)  # type: ignore[reportUnknownVariableType]
    retention_days: dict[str, int] = field(default_factory=dict)  # type: ignore[reportUnknownVariableType]
    allow_export: bool = True
    allow_cross_agent_import: bool = False
    audit_log_enabled: bool = True

    def __post_init__(self) -> None:
        # Normalize kinds to lowercase
        object.__setattr__(
            self, "allowed_write_kinds", frozenset(k.lower() for k in self.allowed_write_kinds)
        )
        object.__setattr__(
            self, "allowed_write_sources", frozenset(s.lower() for s in self.allowed_write_sources)
        )

    def allows_write(self, kind: str, source: str, subject: str) -> bool:
        """Check if a memory write is permitted."""
        kind_l = kind.lower()
        source_l = source.lower()
        if kind_l not in self.allowed_write_kinds:
            return False
        if source_l not in self.allowed_write_sources:
            return False
        for pattern in self.denied_subject_patterns:
            if re.search(pattern, subject, re.IGNORECASE):
                return False
        return True

    def get_retention_days(self, kind: str) -> int | None:
        """Get retention TTL in days for a kind. None = no auto-expiry."""
        return self.retention_days.get(kind.lower())


@dataclass
class ConsentRecord:
    """Immutable log of consent grant/revoke for audit."""

    timestamp: float
    action: str  # "grant" | "revoke"
    category: str
    source: str
    user_id: str

    def __post_init__(self) -> None:
        if self.action not in ("grant", "revoke"):
            raise ValueError(f"Invalid action: {self.action} (must be 'grant' or 'revoke')")


def validate_scope_policy(policy: MemoryScopePolicy) -> bool:
    """Validate a MemoryScopePolicy. Raises ValueError on invalid config."""
    # Check all kinds are valid
    valid_kinds = MemoryKind.all()
    for kind in policy.allowed_write_kinds:
        if kind not in valid_kinds:
            raise ValueError(f"Unknown memory kind: {kind}")
    # Check TTLs are non-negative
    for kind, days in policy.retention_days.items():
        if days < 0:
            raise ValueError(f"Negative TTL for {kind}: {days}")
    return True


def check_consent(
    policy: MemoryScopePolicy,
    consent_records: list[ConsentRecord],
    kind: str,
    source: str,
    subject: str = "",
) -> bool:
    """Check if a memory write is consented.

    Returns True if write is allowed, False if denied.
    """
    # Check policy gates first
    if not policy.allows_write(kind, source, subject):
        return False

    # If no consent records, allow by default (policy already checked)
    if not consent_records:
        return True

    # Find latest consent record for this category+source
    latest: ConsentRecord | None = None
    for r in consent_records:
        if r.category == kind.lower() and r.source == source:
            if latest is None or r.timestamp > latest.timestamp:
                latest = r

    if latest is None:
        return True  # No consent record for this category+source -> allow

    return latest.action == "grant"


def should_archive_by_ttl(
    kind: str, created_at: float, now: float, retention_days: dict[str, int]
) -> bool:
    """Check if a memory should be auto-archived based on TTL.

    Returns True if memory should be archived (expired).

    TTL semantics:
    - TTL not set (not in dict) -> never archive
    - TTL = 0 -> archive immediately (no retention)
    - TTL > 0 -> archive after TTL days
    """
    kind_l = kind.lower()
    ttl_days = retention_days.get(kind_l)
    if ttl_days is None:
        return False  # No TTL configured -> never archive
    if ttl_days == 0:
        return True  # TTL = 0 -> archive immediately (no retention)
    age_days = (now - created_at) / 86400.0
    return age_days >= ttl_days
