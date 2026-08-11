"""The 6 launch experiences (§12). All on by default."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class FlagBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True


class ExperiencesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    whisper: FlagBlock = FlagBlock()
    interrupt: FlagBlock = FlagBlock()
    memory_import: FlagBlock = FlagBlock()
    cross_channel: FlagBlock = FlagBlock()
    birthday: FlagBlock = FlagBlock()
    drift_flag: FlagBlock = FlagBlock()
