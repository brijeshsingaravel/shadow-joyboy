"""T4.1 -- the Dataset Compiler (hardening-eval-lab-handoff.md Track 4).

Two producer paths, both feeding the same `pg_sft_rows` sink (store_v2.write_sft_rows):

- **Synthetic-Data-Kit** (this module's 1a half): Meta's MIT-licensed 4-stage CLI
  (ingest -> create -> curate -> save-as), run over the conformance-stable Capability
  Catalog. The fastest path to real training data -- no live multi-teacher cost, just
  ingest+curate what's already built. Runs in its own isolated venv (subprocess, mirroring
  `media/music.py`'s `_AceStepBridge`), pointed at Madras's own LiteLLM gateway rather than
  a separate vLLM server.
- **Distilabel Teacher Council** (1b, sibling module): multi-teacher generation over
  dev-split Proving Ground cases, PG-scored best-of-N. Built separately (T4.1b).

G3 (D41): every row carries tenant/consent/provenance. G4: Synthetic-Data-Kit's input is the
Capability Catalog, never the held-out benchmark firewall -- there is no held-out concept here
at all, only the Teacher Council path (1b) touches Proving Ground cases and must respect it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml
from madras_capabilities.model import Capability

PRODUCER_SYNTH_KIT = "synthetic-data-kit"
PRODUCER_LOCAL_CORPUS = "local-corpus"
PRODUCER_LOCAL_CORPUS_HEURISTIC = "local-corpus-heuristic"

_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_MIN_SECTION_CHARS = 40  # skip near-empty sections (e.g. a header immediately followed
# by another header) -- nothing useful to train on

_STABLE_BUILD_STATES = frozenset({"built", "always-on"})

# Text-based only (per design spec) -- no PDF/DOCX extraction, no code/binary ingestion.
_LOCAL_CORPUS_ALLOWED_EXTENSIONS = frozenset({".md", ".txt", ".json", ".jsonl"})

# Path-fragment denylist (case-insensitive substring match against the full path) --
# defense in depth alongside the extension allowlist: secrets/credentials directories
# can contain .json/.md files (e.g. a vault README) that would otherwise pass the
# extension filter.
_LOCAL_CORPUS_DENYLIST_FRAGMENTS = (
    "secret",
    "credential",
    ".env",
    "vault",
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    # Real bug (live-verified): machine-generated cache/build artifacts (graphify's AST
    # cache, mlflow run dirs, benchmark caches) matched the .json extension allowlist and
    # got mined as training rows -- non-linguistic, often huge single blobs with no
    # markdown headers to split on, which stalled tokenization via memory thrashing on a
    # 9GiB-RAM WSL box (one row alone was large enough to make num_proc=1 tokenization
    # hang for minutes). These directories hold generated artifacts, never authored prose.
    "graphify-out",
    "mlruns",
    ".benchmarks",
    "unsloth_compiled_cache",
    "dist",
    "build",
    ".next",
    ".cache",
)

# Hard ceiling on a single section's completion length, applied regardless of source
# path -- defense in depth alongside the directory denylist above: an unknown future
# generated-artifact directory could still slip through, but no single legitimate prose
# section should ever be this long. Chosen well above any real markdown section but far
# below the multi-hundred-KB blobs that caused the tokenization stall.
_MAX_SECTION_CHARS = 8000


def render_synth_kit_config(
    default_config_text: str, *, api_base: str, api_key: str, model: str
) -> str:
    """Patch the tool's OWN shipped default config (paths/generation/curate/format/prompts
    preserved verbatim) rather than hand-duplicating those ~100 lines: synthetic-data-kit's
    `-c` flag REPLACES the whole config wholesale, it doesn't merge with its own defaults, so a
    config containing only an LLM override fails ("Prompt 'summary' not found in
    configuration"). Points the `api-endpoint` provider at Madras's own LiteLLM gateway instead
    of a separate vLLM server -- reuses existing routing/free-tier infra rather than adding a
    new one. `vllm:` is dropped entirely (not just shadowed) since it's not selected."""
    config = yaml.safe_load(default_config_text)
    config["llm"] = {"provider": "api-endpoint"}
    config["api-endpoint"] = {
        "api_base": api_base,
        "api_key": api_key,
        "model": model,
        "max_retries": 3,
        "retry_delay": 1.0,
    }
    config.pop("vllm", None)
    return yaml.safe_dump(config, sort_keys=False)


def select_capability_notes(capabilities_dir: Path) -> list[Capability]:
    """Only conformance-stable capabilities (`built`/`always-on`) feed synthetic generation --
    `planned`/`partial`/`frontier` capabilities don't work fully yet; generating training data
    about them would teach the wrong thing."""
    from madras_capabilities.catalog import load_catalog

    catalog = load_catalog(capabilities_dir)
    return [c for c in catalog.capabilities if c.build_state in _STABLE_BUILD_STATES]


def _capability_notes_to_txt(  # pyright: ignore[reportUnusedFunction]
    # Used by scripts/mine_evaluation_corpus.py, outside pyright's src/madras scope.
    capabilities_dir: Path,
    capabilities: list[Capability],
    input_dir: Path,
) -> None:
    """Copy each selected capability's full note body (not just frontmatter -- the free-text
    body is where the rich instructional detail lives) as .txt -- Synthetic-Data-Kit's ingest
    stage doesn't recognize .md."""
    input_dir.mkdir(parents=True, exist_ok=True)
    by_id = {c.id: c for c in capabilities}
    for path in Path(capabilities_dir).glob("*.md"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        # cheap id-presence check avoids re-parsing frontmatter here; select_capability_notes
        # already did the real filtering
        if not any(f"id: {cid}" in text for cid in by_id):
            continue
        (input_dir / f"{path.stem}.txt").write_text(text, encoding="utf-8")


def parse_chatml_export(path: Path) -> list[dict[str, str]]:
    """Parse Synthetic-Data-Kit's chatml save-as output -- either a JSON array of
    {"messages": [...]} objects, or JSONL (one such object per line). Each object's first
    user/assistant pair becomes one (prompt, completion); any system message is ignored."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        parsed: Any = json.loads(text)
        records: list[dict[str, Any]] = [parsed] if isinstance(parsed, dict) else parsed
    except json.JSONDecodeError:
        records = [json.loads(line) for line in text.splitlines() if line.strip()]

    pairs: list[dict[str, str]] = []
    for record in records:
        messages: list[dict[str, Any]] = record.get("messages", [])
        user = next((m["content"] for m in messages if m.get("role") == "user"), None)
        assistant = next((m["content"] for m in messages if m.get("role") == "assistant"), None)
        if user is not None and assistant is not None:
            pairs.append({"prompt": user, "completion": assistant})
    return pairs


class _Runner(Protocol):
    def __call__(self, cmd: list[str], **kwargs: Any) -> Any: ...


def _default_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    # encoding="utf-8" matches the child's own PYTHONIOENCODING=utf-8 (set by _run() below) --
    # without it, Python decodes captured stdout/stderr using the parent's locale encoding
    # (cp1252 on Windows), which raises UnicodeDecodeError on real non-ASCII output and silently
    # drops that call's result (live-verified: a Teacher-Council mining pass lost one file's
    # generated pairs this way before this fix).
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", **kwargs)


@dataclass
class SyntheticDataKitBridge:
    """Bridges to Synthetic-Data-Kit in its ISOLATED venv via subprocess -- mirrors
    `media/music.py`'s `_AceStepBridge`. `runner` is injectable for tests; defaults to a real
    `subprocess.run` wrapper."""

    exe_path: str
    work_dir: str
    config_path: str
    timeout_s: float = 1800.0
    runner: _Runner = field(default=_default_runner)

    def run_pipeline(self, *, n_pairs: int, threshold: float) -> list[Path]:
        work = Path(self.work_dir)
        input_dir, parsed_dir = work / "data" / "input", work / "data" / "parsed"
        generated_dir, curated_dir = work / "data" / "generated", work / "data" / "curated"
        final_dir = work / "data" / "final"

        self._run(["ingest", str(input_dir), "-o", str(parsed_dir)])
        self._run(
            [
                "create",
                str(parsed_dir),
                "--type",
                "qa",
                "-n",
                str(n_pairs),
                "-o",
                str(generated_dir),
            ]
        )
        self._run(["curate", str(generated_dir), "-t", str(threshold), "-o", str(curated_dir)])
        self._run(["save-as", str(curated_dir), "-f", "chatml", "-o", str(final_dir)])

        # Real bug (live-verified): save-as writes ONE export file PER curated input file, not
        # one merged file -- returning only the first alphabetically silently dropped every
        # other capability's real, generated, curated QA pairs.
        exports = sorted(final_dir.glob("*.json")) if final_dir.is_dir() else []
        if not exports:
            raise RuntimeError(f"synthetic-data-kit save-as produced no export in {final_dir}")
        return exports

    def _run(self, args: list[str]) -> None:
        # --config is a TOP-LEVEL synthetic-data-kit option and must precede the
        # subcommand -- confirmed against the real installed CLI's --help output.
        cmd = [self.exe_path, "-c", self.config_path, *args]
        # Real bug (live-verified): the tool prints unicode status symbols straight to
        # stdout, which raises UnicodeEncodeError on a cp1252 (Windows) console -- the same
        # class of bug already fixed for MLflow's set_terminated() in T2.12. Force UTF-8,
        # mirroring _AceStepBridge's established PYTHONIOENCODING practice.
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        result = self.runner(cmd, timeout=self.timeout_s, env=env)
        if result.returncode != 0:
            raise RuntimeError(f"synthetic-data-kit {args[0]} failed: {result.stderr[-400:]}")


def collect_local_corpus_files(source_dirs: list[Path]) -> list[Path]:
    """Walk `source_dirs` recursively and return every file passing BOTH the extension
    allowlist and the path denylist (design spec: "Extension allowlist + path denylist").
    Denylist is checked against the full path so it catches denylisted *directories*
    (e.g. anything under a `secrets/` folder) as well as denylisted filenames.

    Real bug (live-verified over a full O:\\ drive walk): a broken symlink left by some
    project's venv (e.g. a `lib64 -> lib` symlink pointing nowhere real) raises OSError
    from INSIDE `os.scandir()` while pathlib's `rglob()` is walking that directory -- not
    from a leaf-file `stat()` call, and not recoverable by wrapping the loop body in
    try/except, since the generator itself dies mid-walk. `os.walk(..., onerror=...)` is
    the one API in the stdlib that keeps walking past a directory it can't listen -- an
    unbounded drive-wide walk WILL hit filesystem oddities a single-project walk never
    would, and the mine must not abort because of one bad venv three projects over."""
    matches: list[Path] = []
    for source_dir in source_dirs:
        for dirpath, _dirnames, filenames in os.walk(source_dir, onerror=lambda _e: None):
            for filename in filenames:
                path = Path(dirpath) / filename
                if path.suffix.lower() not in _LOCAL_CORPUS_ALLOWED_EXTENSIONS:
                    continue
                lower_path = str(path).lower()
                if any(fragment in lower_path for fragment in _LOCAL_CORPUS_DENYLIST_FRAGMENTS):
                    continue
                try:
                    if not path.is_file():
                        continue
                except OSError:
                    continue
                matches.append(path)
    return matches


def stage_local_corpus_files(files: list[Path], input_dir: Path) -> None:
    """Copy each collected file into `input_dir` as `.txt` (Synthetic-Data-Kit's ingest
    stage only recognizes `.txt`, matching `_capability_notes_to_txt`'s convention).
    Files are renamed with an index prefix to avoid stem collisions across source dirs
    (e.g. two different projects each having a `README.md`)."""
    input_dir.mkdir(parents=True, exist_ok=True)
    for i, path in enumerate(files):
        text = path.read_text(encoding="utf-8", errors="ignore")
        (input_dir / f"{i:05d}_{path.stem}.txt").write_text(text, encoding="utf-8")


def split_into_qa_sections(text: str, *, source_label: str) -> list[dict[str, str]]:
    """Purely local (no LLM, no network) heuristic extraction: split a document on its
    own markdown headers into (prompt, completion) pairs -- `prompt` asks about the
    header's topic, `completion` is that section's body text. No external call is made,
    so this is the only local-corpus path safe to run over files whose content must never
    leave the machine (per the 2026-07-16 spec amendment: cross-project O:\\ content may
    be read locally, but must not be sent to Synthetic-Data-Kit's external LLM backend).

    Files with no headers at all yield one section using `source_label` as the topic."""
    matches = list(_HEADER_RE.finditer(text))
    if not matches:
        body = text.strip()
        if len(body) < _MIN_SECTION_CHARS or len(body) > _MAX_SECTION_CHARS:
            return []
        return [{"prompt": f"What does {source_label} say?", "completion": body}]

    sections: list[dict[str, str]] = []
    for i, match in enumerate(matches):
        heading = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if len(body) < _MIN_SECTION_CHARS or len(body) > _MAX_SECTION_CHARS:
            continue
        sections.append(
            {
                "prompt": f'In {source_label}, what does the section "{heading}" say?',
                "completion": body,
            }
        )
    return sections


def local_heuristic_producer(
    files: list[Path],
    *,
    source_root: Path,
    tenant: str = "default",
    consent: bool = True,
    mining_run_id: str,
) -> list[dict[str, Any]]:
    """Local-only counterpart to `local_corpus_producer`: no subprocess, no LLM call,
    no network egress -- reads each file and heuristically splits it via
    `split_into_qa_sections`. `source_label` for each file is its path relative to
    `source_root` (readable provenance without leaking the full absolute filesystem
    path)."""
    rows: list[dict[str, Any]] = []
    for path in files:
        # Real bug (live-verified over a full O:\ drive mine): some .json/.md files
        # legitimately decode as UTF-8 but still contain embedded NUL bytes (e.g. a
        # binary-ish generated artifact with a .json extension) -- valid UTF-8 does not
        # exclude \x00, but Postgres's text columns reject it outright
        # (CharacterNotInRepertoireError), aborting the whole batch write. Strip it here,
        # once, rather than let one bad file poison every row already collected.
        text = path.read_text(encoding="utf-8", errors="ignore").replace("\x00", "")
        try:
            source_label = str(path.relative_to(source_root))
        except ValueError:
            source_label = path.name
        for pair in split_into_qa_sections(text, source_label=source_label):
            row_key = hashlib.sha256(
                f"{PRODUCER_LOCAL_CORPUS_HEURISTIC}|{mining_run_id}|{source_label}|"
                f"{pair['prompt']}".encode()
            ).hexdigest()[:16]
            rows.append(
                {
                    "id": f"sft-{row_key}",
                    "tenant": tenant,
                    "consent": consent,
                    "producer": PRODUCER_LOCAL_CORPUS_HEURISTIC,
                    "source_id": source_label,
                    "prompt": pair["prompt"],
                    "completion": pair["completion"],
                    "score": None,
                    "provenance": {
                        "mining_run_id": mining_run_id,
                        "producer": PRODUCER_LOCAL_CORPUS_HEURISTIC,
                    },
                }
            )
    return rows


async def local_corpus_producer(
    input_dir: Path,
    *,
    bridge: SyntheticDataKitBridge | Any,
    tenant: str = "default",
    consent: bool = True,
    n_pairs: int = 20,
    threshold: float = 7.5,
    mining_run_id: str,
) -> list[dict[str, Any]]:
    """Run the Synthetic-Data-Kit pipeline over arbitrary user-uploaded files (already
    staged as .txt in `input_dir` by `stage_local_corpus_files`) and shape the result
    into `pg_sft_rows`-ready dicts. Mirrors `synth_kit_producer` exactly except for the
    input source and producer tag."""
    final_exports = bridge.run_pipeline(n_pairs=n_pairs, threshold=threshold)
    pairs = [pair for export in final_exports for pair in parse_chatml_export(export)]

    rows: list[dict[str, Any]] = []
    for pair in pairs:
        row_key = hashlib.sha256(
            f"{PRODUCER_LOCAL_CORPUS}|{mining_run_id}|{pair['prompt']}".encode()
        ).hexdigest()[:16]
        rows.append(
            {
                "id": f"sft-{row_key}",
                "tenant": tenant,
                "consent": consent,
                "producer": PRODUCER_LOCAL_CORPUS,
                "source_id": None,
                "prompt": pair["prompt"],
                "completion": pair["completion"],
                "score": None,
                "provenance": {"mining_run_id": mining_run_id, "producer": PRODUCER_LOCAL_CORPUS},
            }
        )
    return rows


async def synth_kit_producer(
    capabilities_dir: Path,
    *,
    bridge: SyntheticDataKitBridge | Any,
    tenant: str = "default",
    consent: bool = True,
    n_pairs: int = 20,
    threshold: float = 7.5,
    mining_run_id: str,
) -> list[dict[str, Any]]:
    """Run the Synthetic-Data-Kit pipeline over the Capability Catalog and shape the result
    into `pg_sft_rows`-ready dicts. `bridge` already has its input dir populated (the CLI entry
    point wires `_capability_notes_to_txt` before calling this) -- kept as an injected object so
    the orchestration logic here is testable without a real subprocess."""
    final_exports = bridge.run_pipeline(n_pairs=n_pairs, threshold=threshold)
    pairs = [pair for export in final_exports for pair in parse_chatml_export(export)]

    rows: list[dict[str, Any]] = []
    for pair in pairs:
        row_key = hashlib.sha256(
            f"{PRODUCER_SYNTH_KIT}|{mining_run_id}|{pair['prompt']}".encode()
        ).hexdigest()[:16]
        rows.append(
            {
                "id": f"sft-{row_key}",
                "tenant": tenant,
                "consent": consent,
                "producer": PRODUCER_SYNTH_KIT,
                "source_id": None,
                "prompt": pair["prompt"],
                "completion": pair["completion"],
                "score": None,
                "provenance": {"mining_run_id": mining_run_id, "producer": PRODUCER_SYNTH_KIT},
            }
        )
    return rows
