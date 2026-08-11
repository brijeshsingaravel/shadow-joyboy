"""Public-domain classical-text SFT producer (2026-07-16 spec, Part 3): the Thirukkural
(1330 Tamil couplets on virtue/wealth/love, ~2000 years old, public domain), feeding the
same `pg_sft_rows` sink as `dataset_compiler.py` and `arxiv_producer.py`.

Sourced from a well-known public GitHub JSON dataset (tk120404/thirukkural), a single
fetch (not paginated/rate-limited like arXiv -- it's one ~2.3MB file, not a query API).
No LLM synthesis -- same "purely local shaping" doctrine as the other producers: each
kural's own English explanation becomes the completion, verbatim, not a summary.
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol

PRODUCER_THIRUKKURAL = "thirukkural"
PRODUCER_THIRUKKURAL_VERIFIED = "thirukkural-verified"

KURAL_JSON_URL = "https://raw.githubusercontent.com/tk120404/thirukkural/master/thirukkural.json"
KURAL_DETAIL_URL = "https://raw.githubusercontent.com/tk120404/thirukkural/master/detail.json"


class _HttpClient(Protocol):
    async def get(self, url: str, **kwargs: Any) -> Any: ...


async def fetch_thirukkural(
    *, client: _HttpClient
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Single fetch of both source files (no rate limiting needed -- unlike arXiv's
    per-category query API, this is two static files, not a search endpoint). Returns
    `(kurals, detail)` as parsed JSON, for `parse_thirukkural_chapters` and
    `thirukkural_to_sft_rows` to shape."""
    kural_resp = await client.get(KURAL_JSON_URL)
    detail_resp = await client.get(KURAL_DETAIL_URL)
    kurals = kural_resp.json()["kural"]
    detail = detail_resp.json()
    return kurals, detail


def parse_thirukkural_chapters(detail: list[dict[str, Any]]) -> dict[int, str]:
    """Flatten the nested section -> chapterGroup -> chapters structure into a
    `{kural_number: chapter_translation_name}` lookup, using each chapter's `start`/`end`
    (inclusive) kural-number range. A kural not covered by any chapter range is simply
    absent from the returned dict -- callers fall back to a generic prompt."""
    lookup: dict[int, str] = {}
    for root in detail:
        for section in root.get("section", {}).get("detail", []):
            for group in section.get("chapterGroup", {}).get("detail", []):
                for chapter in group.get("chapters", {}).get("detail", []):
                    name = chapter.get("translation", "")
                    start = chapter.get("start")
                    end = chapter.get("end")
                    if not name or start is None or end is None:
                        continue
                    for number in range(start, end + 1):
                        lookup[number] = name
    return lookup


def thirukkural_to_sft_rows(
    kurals: list[dict[str, Any]],
    *,
    chapters: dict[int, str] | None = None,
    tenant: str = "default",
    consent: bool = True,
    mining_run_id: str,
) -> list[dict[str, Any]]:
    """Pure shaping function (no network call): each kural's own `explanation` field
    becomes the completion. When a chapter lookup is available, the prompt names the
    chapter topic (e.g. "What does the Thirukkural teach about The Blessing of Rain?");
    otherwise it falls back to referencing the kural by number. Kurals with no
    `explanation` text are skipped (nothing to train a completion on)."""
    chapters = chapters or {}
    rows: list[dict[str, Any]] = []
    for kural in kurals:
        number = kural.get("Number")
        explanation = (kural.get("explanation") or "").strip()
        if not number or not explanation:
            continue
        chapter_name = chapters.get(number)
        prompt = (
            f'What does the Thirukkural teach about "{chapter_name}"?'
            if chapter_name
            else f"What does Thirukkural {number} teach?"
        )
        row_key = hashlib.sha256(
            f"{PRODUCER_THIRUKKURAL}|{mining_run_id}|{number}".encode()
        ).hexdigest()[:16]
        rows.append(
            {
                "id": f"sft-{row_key}",
                "tenant": tenant,
                "consent": consent,
                "producer": PRODUCER_THIRUKKURAL,
                "source_id": f"kural-{number}",
                "prompt": prompt,
                "completion": explanation,
                "score": None,
                "provenance": {
                    "mining_run_id": mining_run_id,
                    "producer": PRODUCER_THIRUKKURAL,
                },
            }
        )
    return rows


def verified_thirukkural_to_sft_rows(
    couplets: list[dict[str, Any]],
    *,
    tenant: str = "default",
    consent: bool = True,
    mining_run_id: str,
) -> list[dict[str, Any]]:
    """Pure shaping function, no network, no LLM call: sources from our OWN hand-verified
    translation (Engineering/datasets/tamil/kural_aiyar_1916_en.json -- V. V. S. Aiyar, 1916,
    the first complete English Tirukkural translation by a Tamil scholar, cross-checked this
    session against two independent PD Tamil editions), not `fetch_thirukkural`'s third-party
    GitHub dataset of unknown provenance. `couplets` is that file's own `couplets` list
    (`{number, chapter, en}` per entry)."""
    rows: list[dict[str, Any]] = []
    for couplet in couplets:
        number = couplet.get("number")
        chapter = couplet.get("chapter")
        completion = (couplet.get("en") or "").strip()
        if not number or not completion:
            continue
        row_key = hashlib.sha256(
            f"{PRODUCER_THIRUKKURAL_VERIFIED}|{mining_run_id}|{number}".encode()
        ).hexdigest()[:16]
        rows.append(
            {
                "id": f"sft-{row_key}",
                "tenant": tenant,
                "consent": consent,
                "producer": PRODUCER_THIRUKKURAL_VERIFIED,
                "source_id": f"kural-{number}-aiyar1916",
                "prompt": f"What does Tirukkural {number} (chapter {chapter}) say?",
                "completion": completion,
                "score": None,
                "provenance": {
                    "mining_run_id": mining_run_id,
                    "producer": PRODUCER_THIRUKKURAL_VERIFIED,
                },
            }
        )
    return rows
