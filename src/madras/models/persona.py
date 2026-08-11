"""Persona configuration + the anchoring lifecycle (§7)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class PersonaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice: str
    refusal_style: str
    north_star: str
    # renamed from `register` (shadowed Pydantic BaseModel attr)
    speech_register: Optional[str] = None
    # s63: how the agent behaves once it is WRONG -- distinct from `refusal_style`, which
    # governs declining BEFOREHAND. Optional, so the eight parked agents stay valid without
    # edits; Shadow declares it because D85 makes accepting mistakes part of its standard.
    # Added as a real field rather than smuggled into `voice`: `extra="forbid"` refused the
    # undeclared key immediately, which is the schema doing its job -- a persona trait that
    # matters enough to write down matters enough to name.
    when_wrong: Optional[str] = None


class SessionStartAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inject_at: str = "turn_0"
    token_budget: int = Field(default=800, ge=0, le=8000)


class MidSessionAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inject_every_n_turns: int = Field(default=12, ge=1)
    token_budget: int = Field(default=200, ge=0, le=2000)


class SessionEndLint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classifier: str
    threshold: float = Field(..., ge=0.0, le=1.0)


class PersonaAnchoring(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    session_start_anchor: SessionStartAnchor = Field(default_factory=SessionStartAnchor)
    mid_session_anchor: MidSessionAnchor = Field(default_factory=MidSessionAnchor)
    session_end_lint: SessionEndLint = Field(...)
