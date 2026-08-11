"""JSONL ledger for benchmark runs — append-only, diffable, git-friendly.

Each run appends one line. `build_record` shapes the row; `append_run` writes
it; `read_runs` reads them all back. Timestamp is passed in (not read from the
clock) so records stay deterministic and testable.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def head_sha() -> str:
    """Short git SHA of HEAD, or 'unknown' if git is unavailable."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip() or "unknown"
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def build_record(
    *,
    model: str,
    pass_rate: float,
    n: int,
    ts: int,
    slice_: str = "v1",
    benchmark: str = "bfcl_subset",
    **extra: Any,
) -> dict[str, Any]:
    """Build one ledger record. `ts` is an explicit int (no datetime.now here).

    `benchmark` defaults to "bfcl_subset" (v1 behavior). Any `extra` kwargs are
    merged into the record verbatim — used by the hard slice to carry
    `pass_caret_k`, `mean_pass_rate`, and `k`.
    """
    record = {
        "benchmark": benchmark,
        "slice": slice_,
        "model": model,
        "pass1": pass_rate,
        "n": n,
        "head_sha": head_sha(),
        "ts": ts,
    }
    record.update(extra)
    return record


def append_run(ledger_path: Path, record: dict[str, Any]) -> None:
    """Append one JSON record as a line to the ledger (creating it if needed)."""
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def read_runs(ledger_path: Path) -> list[dict[str, Any]]:
    """Read all records back. Missing file -> empty list. Blank lines skipped."""
    if not ledger_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows
