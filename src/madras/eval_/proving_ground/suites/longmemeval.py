"""LongMemEval suite (token-gated long-horizon memory benchmark).

LongMemEval ("LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive
Memory", Wu et al.) probes a model's ability to recall facts from a long, multi-
session chat history. We use ``xiaowu0162/longmemeval-cleaned`` (the original
authors' cleaned release), config ``default``, the ``longmemeval_oracle.json``
file. The *oracle* variant keeps only the evidence sessions for each question, so
histories stay reasonably sized (median ~2 sessions / ~23 turns) — ideal for a
committed, hermetic slice while still exercising cross-session recall.

Real columns used: ``question_id``, ``question_type``, ``question``, ``answer``,
``question_date``, ``haystack_dates``, ``haystack_sessions`` (a list of sessions,
each a list of ``{role, content}`` turns).

Fetching requires a free ``HUGGINGFACE_TOKEN`` (read from the master vault via
``settings``, never hardcoded). Behaviour mirrors the GAIA suite exactly:
- A committed slice under ``longmemeval/data/`` is used when present (hermetic).
- Otherwise **no token** → ``load_cases()`` returns ``[]`` and logs; **token
  present** → the oracle file is fetched live and mapped.

**Prompt context / long histories.** The session history is rendered as a compact
``[date] role: content`` transcript prefixed before the question. Long histories
are truncated to ``_MAX_HISTORY_CHARS`` characters (keeping the *most recent*
turns, which carry the evidence for temporal/knowledge-update questions) so a
single case never balloons the prompt. The committed slice stores raw sessions;
truncation happens at render time.

Each row → a v2 ``Case`` with ``memory_recall`` + ``multi_step_reasoning``
features and an ``answer_contains`` check against the reference answer.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.config import settings
from madras.eval_.proving_ground.suite import Case, Suite

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "longmemeval" / "data"
_SLICE = DATA_DIR / "longmemeval_slice.json"

_FEATURES = ["memory_recall", "multi_step_reasoning"]
_DATASET = "xiaowu0162/longmemeval-cleaned"
_CONFIG = "longmemeval_oracle"
_FILE = "longmemeval_oracle.json"
_MAX_HISTORY_CHARS = 8000


def _render_history(sessions: list[list[dict[str, Any]]], dates: list[str]) -> str:
    """Render sessions as a compact transcript, truncated to the most recent turns.

    Turns are flattened in order with an optional per-session date header. If the
    full render exceeds ``_MAX_HISTORY_CHARS``, the oldest turns are dropped so the
    evidence-bearing tail is preserved.
    """
    lines: list[str] = []
    for idx, session in enumerate(sessions):
        date = dates[idx].strip() if idx < len(dates) else ""
        if date:
            lines.append(f"--- session ({date}) ---")
        for turn in session:
            role = str(turn.get("role", "")).strip()
            content = str(turn.get("content", "")).strip()
            if content:
                lines.append(f"{role}: {content}")
    text = "\n".join(lines)
    if len(text) > _MAX_HISTORY_CHARS:
        text = "...(earlier history truncated)...\n" + text[-_MAX_HISTORY_CHARS:]
    return text


def _row_to_case(row: dict[str, Any], suite_id: str) -> Case:
    question = str(row.get("question", "")).strip()
    answer = str(row.get("answer", "")).strip()
    sessions: list[list[dict[str, Any]]] = row.get("haystack_sessions") or []
    raw_dates: list[Any] = row.get("haystack_dates") or []
    dates: list[str] = [str(d) for d in raw_dates]
    history = _render_history(sessions, dates)
    question_date = str(row.get("question_date", "")).strip()
    header = f"Current date: {question_date}\n\n" if question_date else ""
    prompt = (
        f"{header}Conversation history:\n{history}\n\n"
        f"Based on the conversation history above, answer the question.\n"
        f"Question: {question}"
    )
    checks: list[dict[str, Any]] = []
    if answer:
        checks.append({"type": "answer_contains", "text": answer})
    return Case(
        id=str(row.get("question_id", "")),
        suite_id=suite_id,
        benchmark_family="longmemeval",
        features=list(_FEATURES),
        tools=[],
        prompt=prompt,
        setup={"question_type": row.get("question_type", ""), "reference_answer": answer},
        checks=checks,
    )


class LongMemEvalSuite(Suite):
    id: str = "longmemeval"
    name: str = "LongMemEval (oracle long-horizon memory recall)"
    version: str = _CONFIG
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "xiaowu0162/longmemeval-cleaned (longmemeval_oracle.json); requires "
        "HUGGINGFACE_TOKEN in the vault. Skips cleanly when no token is provisioned."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def _token(self) -> str:
        return settings.huggingface_token

    def load_cases(self) -> list[Case]:
        # Hermetic fast-path: a committed slice (only present once fetched with a token).
        if _SLICE.exists():
            rows = json.loads(_SLICE.read_text(encoding="utf-8"))
            return [_row_to_case(r, self.id) for r in rows]

        token = self._token()
        if not token:
            logger.info(
                "LongMemEval gated — set HUGGINGFACE_TOKEN in the vault to enable (suite skipped)"
            )
            return []

        # local import: heavy, token-only path
        from huggingface_hub import hf_hub_download  # pyright: ignore[reportUnknownVariableType]

        path: str = hf_hub_download(
            repo_id=_DATASET, filename=_FILE, repo_type="dataset", token=token
        )
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
        return [_row_to_case(r, self.id) for r in rows]
