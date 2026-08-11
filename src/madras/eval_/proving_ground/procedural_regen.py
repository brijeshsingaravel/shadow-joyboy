"""Procedural/parameterized scenario regeneration — benchmark-design.md §12b (BD3).

Structurally solves the problem §7 already admits to ("rotate held-out as it leaks") for
Madras's own **Ring-2 native scenarios only** — external suites (GAIA/SWE-bench-Pro/ARC-AGI/
etc.) keep their own official held-out/versioning/freshness mechanisms; this module does not
wrap or reinvent those (BD3, scope-locked).

BEYONDBENCH-style combinatorial instance generation: a scenario author declares substitutable
slots (``regen_slots: dict[str, list[str]]``) with ``{{slot_name}}`` placeholders in the task/
rubric/check text; ``regenerate()`` deterministically resolves one value per slot (seeded, so a
given seed always reproduces the same instance — reproducibility isn't sacrificed for freshness),
and ``regenerate_many()`` walks the combinatorial space up to its real size, never fabricating
more distinct variants than the declared slots can actually produce.
"""

from __future__ import annotations

import copy
import itertools
import re
from typing import Any, cast

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def _substitute(text: str, values: dict[str, str]) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def _substitute_in(obj: Any, values: dict[str, str]) -> Any:
    if isinstance(obj, str):
        return _substitute(obj, values)
    if isinstance(obj, dict):
        return {k: _substitute_in(v, values) for k, v in cast("dict[str, Any]", obj).items()}
    if isinstance(obj, list):
        return [_substitute_in(v, values) for v in cast("list[Any]", obj)]
    return obj


def _combinatorial_space(regen_slots: dict[str, list[str]]) -> list[dict[str, str]]:
    """Every distinct slot-value combination, in a stable deterministic order."""
    names = sorted(regen_slots)
    pools = [regen_slots[n] for n in names]
    return [dict(zip(names, combo, strict=True)) for combo in itertools.product(*pools)]


def regenerate(scenario: dict[str, Any], *, seed: int) -> dict[str, Any]:
    """Deterministically resolve one slot-value combination for ``seed``.

    Same ``seed`` always reproduces the identical instance (reproducibility for CI/debugging);
    different seeds walk the real combinatorial space in a stable order, not randomly, so
    ``regenerate_many`` can enumerate it exhaustively without duplicates up to its true size.
    """
    regen_slots = scenario.get("regen_slots")
    if not regen_slots:
        raise ValueError(
            f"scenario {scenario.get('id', '?')!r} has no regen_slots — cannot regenerate a "
            "scenario that declares no substitutable slots"
        )
    space = _combinatorial_space(regen_slots)
    values = space[seed % len(space)]
    variant = copy.deepcopy(scenario)
    variant.pop("regen_slots", None)
    variant = _substitute_in(variant, values)
    variant["id"] = f"{scenario['id']}-regen-{seed % len(space)}"
    return variant


def regenerate_many(scenario: dict[str, Any], *, count: int) -> list[dict[str, Any]]:
    """``count`` distinct variants, capped at the real combinatorial space size — never
    fabricates fake diversity beyond what the declared slots can actually produce."""
    regen_slots = scenario.get("regen_slots")
    if not regen_slots:
        raise ValueError(
            f"scenario {scenario.get('id', '?')!r} has no regen_slots — cannot regenerate a "
            "scenario that declares no substitutable slots"
        )
    space_size = len(_combinatorial_space(regen_slots))
    n = min(count, space_size)
    return [regenerate(scenario, seed=i) for i in range(n)]
