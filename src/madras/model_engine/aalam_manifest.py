"""The `.aalam` manifest (D48 "Aalam models SemVer + model cards + registry"): a JSON
sidecar next to a merged HOPE checkpoint's safetensors files, carrying the provenance a
plain `model_card.json` (unsloth_train.py's per-adapter version) doesn't capture on its
own once multiple adapters/teachers get merged or distilled together -- SemVer, full
lineage (base model + every adapter/teacher that contributed), and a checksum per weight
file so a corrupted/truncated download or copy is caught before it's loaded.

Deliberately NOT a new binary tensor format -- per the 2026-07-17 research pass, nobody
hand-rolls tensor serialization anymore (safetensors became the PyTorch Foundation's
default in 2026 specifically to close the pickle-RCE hole). `.aalam` = this manifest +
a pointer to standard `.safetensors` files sitting next to it, mirroring how GGUF and the
newer CryptoTensors format both layer their own metadata on top of a safetensors-
compatible tensor core rather than reinventing it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

AALAM_SCHEMA_VERSION = 1

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

ProducerKind = Literal["merge", "distillation", "finetune"]


@dataclass
class SourceAdapter:
    """One LoRA adapter that contributed to this checkpoint (merged in via
    `merge_and_unload`, TIES/DARE, or similar)."""

    path: str
    base_model: str
    training_run_id: str | None = None


@dataclass
class WeightFile:
    """One `.safetensors` shard belonging to this checkpoint, with a checksum so a
    corrupted/truncated copy is caught before it's ever loaded into a model."""

    filename: str
    sha256: str
    size_bytes: int


@dataclass
class AalamManifest:
    """The full `.aalam` manifest. `version` must be SemVer (`X.Y.Z`) per D48 -- Aalam
    models are explicitly required to be SemVer-versioned, not just timestamped."""

    name: str
    version: str
    producer: ProducerKind
    base_model: str
    created_at: str
    weight_files: list[WeightFile]
    source_adapters: list[SourceAdapter] = field(default_factory=list[SourceAdapter])
    teacher_models: list[str] = field(default_factory=list[str])
    training_summary: dict[str, Any] = field(default_factory=dict[str, Any])
    schema_version: int = AALAM_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not _SEMVER_RE.match(self.version):
            raise ValueError(
                f"Aalam manifest version must be SemVer (X.Y.Z), got {self.version!r} "
                "-- per D48 'Aalam models SemVer + model cards + registry'"
            )
        if self.producer == "merge" and not self.source_adapters:
            raise ValueError(
                "producer='merge' requires at least one source_adapters entry "
                "-- a merge with nothing merged in is not a merge"
            )
        if self.producer == "distillation" and not self.teacher_models:
            raise ValueError(
                "producer='distillation' requires at least one teacher_models entry "
                "-- distillation with no teachers is not distillation"
            )


def compute_file_checksum(path: Path) -> tuple[str, int]:
    """Stream a file through SHA-256 in fixed-size chunks (never loads a multi-GB
    safetensors shard fully into memory) and return `(hex_digest, size_bytes)`."""
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def build_weight_files(checkpoint_dir: Path) -> list[WeightFile]:
    """Checksum every `.safetensors` file directly inside `checkpoint_dir` (not
    recursive -- a checkpoint's shards live flat in one directory, per the standard HF
    layout)."""
    files: list[WeightFile] = []
    for path in sorted(checkpoint_dir.glob("*.safetensors")):
        digest, size = compute_file_checksum(path)
        files.append(WeightFile(filename=path.name, sha256=digest, size_bytes=size))
    return files


def write_manifest(path: Path, manifest: AalamManifest) -> None:
    """Write the manifest as pretty-printed JSON to `path` (conventionally
    `<checkpoint_dir>/model.aalam`)."""
    path.write_text(json.dumps(asdict(manifest), indent=2), encoding="utf-8")


def read_manifest(path: Path) -> AalamManifest:
    """Read and validate a `.aalam` manifest from disk. Raises `ValueError` (via
    `AalamManifest.__post_init__`) if the file is structurally invalid -- e.g. a
    non-SemVer version or a merge with no listed source adapters."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return AalamManifest(
        name=raw["name"],
        version=raw["version"],
        producer=raw["producer"],
        base_model=raw["base_model"],
        created_at=raw["created_at"],
        weight_files=[WeightFile(**w) for w in raw.get("weight_files", [])],
        source_adapters=[SourceAdapter(**a) for a in raw.get("source_adapters", [])],
        teacher_models=raw.get("teacher_models", []),
        training_summary=raw.get("training_summary", {}),
        schema_version=raw.get("schema_version", AALAM_SCHEMA_VERSION),
    )


def verify_checksums(checkpoint_dir: Path, manifest: AalamManifest) -> list[str]:
    """Re-checksum every file the manifest lists and return the filenames that don't
    match (empty list = all verified). Does not raise -- callers decide how to react to
    a corrupted checkpoint."""
    mismatches: list[str] = []
    for wf in manifest.weight_files:
        file_path = checkpoint_dir / wf.filename
        if not file_path.exists():
            mismatches.append(wf.filename)
            continue
        digest, _size = compute_file_checksum(file_path)
        if digest != wf.sha256:
            mismatches.append(wf.filename)
    return mismatches
