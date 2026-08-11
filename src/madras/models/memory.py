"""Memory configuration — 6 layers, MVP flags per BASE_AGENT_SCHEMA.md §3."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Store(str, Enum):
    REDIS_TTL = "redis_ttl"
    REDIS_PERMANENT = "redis_permanent"
    GRAPHITI = "graphiti"
    QDRANT = "qdrant"
    COGNEE = "cognee"


class LayerKind(str, Enum):
    WORKING = "working"
    EPISODIC = "episodic"
    REFLEX = "reflex"
    SEMANTIC = "semantic"
    PRINCIPLE = "principle"
    RELATIONSHIP = "relationship"


_LAYER_STORE_COMPAT: dict[LayerKind, set[Store]] = {
    LayerKind.WORKING: {Store.REDIS_TTL},
    LayerKind.EPISODIC: {Store.GRAPHITI},
    LayerKind.REFLEX: {Store.REDIS_PERMANENT},
    LayerKind.SEMANTIC: {Store.COGNEE},
    LayerKind.PRINCIPLE: {Store.QDRANT},
    LayerKind.RELATIONSHIP: {Store.GRAPHITI},
}


class MemoryLayer(BaseModel):
    """Generic memory layer — disabled layers can omit `store`."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    store: Optional[Store] = None
    layer: Optional[LayerKind] = Field(default=None, description="For test/cross-check only")

    @model_validator(mode="after")
    def _store_compatible_with_layer(self) -> MemoryLayer:
        if self.layer is not None and self.store is not None:
            allowed = _LAYER_STORE_COMPAT[self.layer]
            if self.store not in allowed:
                raise ValueError(
                    f"store {self.store!r} not valid for layer {self.layer!r}; "
                    f"expected one of {[s.value for s in allowed]}"
                )
        return self


class WorkingMemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    store: Store = Store.REDIS_TTL
    ttl_seconds: int = Field(default=7200, ge=60, le=86400)


class EpisodicMemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    store: Store = Store.GRAPHITI
    retention_days: int = Field(default=365, ge=1)


class ReflexFormation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold_successes: int = Field(..., ge=1, description="N successes before reflex written")
    min_success_rate: float = Field(..., ge=0.0, le=1.0)


class ReflexDecay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    half_life_days: int = Field(..., ge=1)
    floor_score: float = Field(..., ge=0.0, le=1.0)


class ReflexConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    store: Store = Store.REDIS_PERMANENT
    formation: ReflexFormation
    decay: ReflexDecay


class SemanticMemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    store: Optional[Store] = None


class PrincipleMemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    store: Optional[Store] = None
    max_loaded_tokens: int = Field(default=5000, ge=0, le=200_000)


class RelationshipMemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    store: Optional[Store] = None


class MemoryConfig(BaseModel):
    """All 6 layers declared; MVP enables working/episodic/reflex."""

    model_config = ConfigDict(extra="forbid")

    working: WorkingMemoryConfig
    episodic: EpisodicMemoryConfig
    reflex: ReflexConfig
    semantic: SemanticMemoryConfig
    principle: PrincipleMemoryConfig
    relationship: RelationshipMemoryConfig
