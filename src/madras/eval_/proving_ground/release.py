"""P3 anti-contamination — the public-release exporter.

Writes ONLY the `public`-partition scenarios to an output directory (the
open-source release artifact). The `held_out` partition — used to score the
official Madras Index — never leaves the repo. Original files are copied verbatim
so authored content is preserved exactly.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from madras.eval_.proving_ground.scenario import PUBLIC, Scenario


def export_public_scenarios(src_dir: str | Path, out_dir: str | Path) -> list[str]:
    """Copy every `public` scenario from `src_dir` into `out_dir`; skip held_out.

    Returns the sorted list of exported scenario ids. Creates `out_dir` if needed.
    """
    src = Path(src_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    exported: list[str] = []
    for p in sorted(src.glob("*.json")):
        scenario = Scenario.model_validate(json.loads(p.read_text(encoding="utf-8")))
        if scenario.partition != PUBLIC:
            continue
        shutil.copy2(p, out / p.name)
        exported.append(scenario.id)
    return sorted(exported)
