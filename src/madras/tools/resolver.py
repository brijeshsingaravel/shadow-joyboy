"""Bundle resolver: bundle name → ToolBundleSpec, enforcing rank gate."""

from __future__ import annotations

from pathlib import Path

import yaml

from madras.models.agent_config import Rank
from madras.models.tool_bundle import ToolBundleSpec

_RANK_ORDER = [Rank.INTERN, Rank.JUNIOR, Rank.SPECIALIST, Rank.SENIOR, Rank.PRINCIPAL, Rank.LEGEND]


def _rank_at_least(actual: Rank, required: Rank) -> bool:
    return _RANK_ORDER.index(actual) >= _RANK_ORDER.index(required)


class BundleResolutionError(Exception):
    """Raised when a bundle cannot be resolved or the rank gate fails."""


class BundleResolver:
    def __init__(self, *, agents_dir: Path) -> None:
        self._dir = Path(agents_dir) / "bundles" / "tools"

    def resolve(self, name: str, *, agent_rank: Rank) -> ToolBundleSpec:
        path = self._dir / f"{name}.yaml"
        if not path.exists():
            raise BundleResolutionError(f"bundle {name!r} not found at {path}")

        with path.open("r", encoding="utf-8") as fh:
            spec = ToolBundleSpec.model_validate(yaml.safe_load(fh))

        if not _rank_at_least(agent_rank, spec.rank_required):
            raise BundleResolutionError(
                f"rank gate: agent_rank={agent_rank.value!r} below "
                f"bundle.rank_required={spec.rank_required.value!r}"
            )
        return spec
