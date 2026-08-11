"""T4.4 -- HOPE's QLoRA training bridge (D41, Model Engine.md).

Bridges to Unsloth in its ISOLATED WSL venv (bitsandbytes/Triton need Linux; the main Madras env
stays untouched) via ``wsl.exe`` subprocess -- mirrors ``media/music.py``'s ``_AceStepBridge``
shape exactly (isolated venv, subprocess bridge, injectable runner for hermetic tests), swapping
a direct Windows exe call for a WSL-wrapped one.

``export_sft_dataset`` shapes ``pg_sft_rows`` (either producer -- ``store_v2.sft_rows_by_
producer()``) into a ``{"prompt": ..., "completion": ...}`` JSONL, the same shape the rows
already have in Postgres. Unsloth's compiled ``SFTTrainer`` natively detects prompt/completion
columns and trains with completion-only loss masking -- no chat-template ``formatting_func``
needed (confirmed live: a "messages" shape requires one and adds real complexity for no benefit
here, since these rows are plain single-turn prompt/completion pairs, not multi-turn chats).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class _Runner(Protocol):
    def __call__(self, cmd: list[str], **kwargs: Any) -> Any: ...


def _default_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    # encoding="utf-8": same class of bug already fixed for dataset_compiler.py's
    # _default_runner (T4.1) -- WSL/Linux subprocess output is UTF-8; without telling
    # subprocess.run() to decode it as such, Python falls back to the parent's locale
    # encoding (cp1252 on Windows) and raises UnicodeDecodeError on real non-ASCII output.
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", **kwargs)


def export_sft_dataset(rows: list[dict[str, Any]], out_path: Path) -> None:
    """Shapes ``pg_sft_rows`` dicts into a JSONL file of ``{"prompt": ..., "completion": ...}``
    rows for TRL's ``SFTTrainer`` (natively completion-masked, no formatting_func needed). Rows
    with an empty completion (a real case found in T4.1 live verification -- a teacher's error
    trajectory can still score low but non-exceptionally and land as a best-of-N winner) are
    skipped: training on an empty target teaches nothing and would corrupt the dataset."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            completion = row.get("completion", "")
            if not completion:
                continue
            record = {"prompt": row["prompt"], "completion": completion}
            f.write(json.dumps(record) + "\n")


@dataclass
class UnslothTrainingBridge:
    """Bridges to ``scripts/unsloth_train.py`` running INSIDE the isolated WSL venv via
    ``wsl.exe -d <distro> -- <venv_python> <train_script> ...``. ``runner`` is injectable for
    tests; defaults to a real ``subprocess.run`` wrapper."""

    wsl_distro: str
    venv_python: str
    train_script: str
    timeout_s: float = 3600.0
    runner: _Runner = field(default=_default_runner)

    def train(
        self,
        dataset_path: Path,
        *,
        base_model: str,
        out_dir: Path,
        max_steps: int,
    ) -> Path:
        cmd = [
            "wsl.exe",
            "-d",
            self.wsl_distro,
            "--",
            self.venv_python,
            self.train_script,
            "--dataset",
            str(dataset_path),
            "--base-model",
            base_model,
            "--out-dir",
            str(out_dir),
            "--max-steps",
            str(max_steps),
        ]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        result = self.runner(cmd, timeout=self.timeout_s, env=env)
        if result.returncode != 0:
            raise RuntimeError(f"unsloth_train.py failed: {result.stderr[-800:]}")
        return out_dir
