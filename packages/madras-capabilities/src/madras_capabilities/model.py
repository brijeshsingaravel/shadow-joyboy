"""Capability — the typed frontmatter contract for a Framework/Capabilities/*.md note.

Field set matches Framework/Capability Catalog.md's entry schema: id · human_label ·
category · kind · build_state · implements[bundles] · rank_required · scopes ·
evaluates[]. H7b (tamil-and-backend-spatial): kind/build_state are now real enums
(BuildState/Kind) instead of bare str — an invalid value now fails validation at
parse time instead of silently sitting in the catalog. extra="forbid" is safe here
because the parser (catalog.py::parse_note) only ever passes the fields this model
declares as kwargs — notes carry more frontmatter than the Compiler needs (source_files,
benchmark_suites, etc.), but those are never constructor arguments in the first place.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class BuildState(str, Enum):
    """Mirrors tests/test_lighthouse/test_capability_catalog.py's _VALID_STATE — the
    single source both must agree on (H7c retargets the test to import this enum)."""

    BUILT = "built"
    PARTIAL = "partial"
    PLANNED = "planned"
    HARVESTING = "harvesting"
    FRONTIER = "frontier"
    ALWAYS_ON = "always-on"
    NOT_APPLICABLE = "not_applicable"
    DEFERRED = "deferred"


class Kind(str, Enum):
    STRUCTURAL = "structural"
    FUNCTIONAL = "functional"
    SUBSTRATE = "substrate"
    FACULTY = "faculty"


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    human_label: str = ""
    category: str = ""
    kind: Kind | None = None
    build_state: BuildState | None = None
    implements: list[str] = Field(default_factory=list)
    rank_required: str | None = None
    scopes: list[str] = Field(default_factory=list)
    evaluates: list[str] = Field(default_factory=list)
    tier: str = ""
