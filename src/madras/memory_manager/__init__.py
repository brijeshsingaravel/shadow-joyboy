"""Memory Manager — nightly batch: reflex extraction, episode consolidation, briefing."""

from madras.memory_manager.consolidator import consolidate
from madras.memory_manager.job import MemoryManagerJob, NightlyReport
from madras.memory_manager.reflex_extractor import extract_candidates, promote, task_shape_hash
from madras.memory_manager.shadow_mode import IRREVERSIBLE_ACTIONS, PlannedAction, ShadowModeGuard

__all__ = [
    "IRREVERSIBLE_ACTIONS",
    "MemoryManagerJob",
    "NightlyReport",
    "PlannedAction",
    "ShadowModeGuard",
    "consolidate",
    "extract_candidates",
    "promote",
    "task_shape_hash",
]
