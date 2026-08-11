"""Public-domain literature/philosophy SFT producer (2026-07-16 spec, Part 4): Project
Gutenberg book summaries, feeding the same `pg_sft_rows` sink as the other producers.

Sourced from Gutendex (gutendex.com), a public structured JSON API over Project
Gutenberg's catalog -- not scraping full book text. Uses each book's `summaries` field
(an existing public-domain-safe short summary shipped in the catalog metadata itself,
not full book text) as the completion, same "no LLM synthesis, no full-text copyright
surface" doctrine as the other producers here. Every result is public domain by
construction: Gutendex only indexes Project Gutenberg's US public-domain catalog.
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol
from urllib.parse import quote

PRODUCER_GUTENBERG = "gutenberg"

GUTENDEX_API_BASE = "https://gutendex.com/books/"

# Same conservative pacing as arxiv_producer.py / wikipedia_producer.py, applied
# uniformly across every network-fetching producer regardless of the specific upstream
# API's own rate-limit policy.
GUTENBERG_RATE_LIMIT_SECONDS = 3.0


class _HttpClient(Protocol):
    async def get(self, url: str, **kwargs: Any) -> Any: ...


def parse_gutendex_results(search_json: dict[str, Any]) -> list[dict[str, str]]:
    """Extract `{title, summary}` from a Gutendex `/books/` search response. Books with
    no summary (Gutendex doesn't have one for every title) are skipped -- nothing to
    train a completion on."""
    books: list[dict[str, str]] = []
    for result in search_json.get("results", []):
        title = result.get("title", "")
        summaries: list[str] = result.get("summaries") or []
        if not title or not summaries:
            continue
        books.append({"title": title, "summary": summaries[0].strip()})
    return books


async def fetch_gutenberg_books(
    topics: list[str],
    *,
    client: _HttpClient,
    sleep: Any,
) -> list[dict[str, str]]:
    """Query Gutendex once per topic search term, rate-limited between requests
    (mirrors `arxiv_producer.fetch_arxiv_papers` / `wikipedia_producer.fetch_wikipedia_
    articles`)."""
    books: list[dict[str, str]] = []
    for i, topic in enumerate(topics):
        if i > 0:
            await sleep(GUTENBERG_RATE_LIMIT_SECONDS)
        url = f"{GUTENDEX_API_BASE}?search={quote(topic)}"
        response = await client.get(url)
        books.extend(parse_gutendex_results(response.json()))
    return books


def gutenberg_books_to_sft_rows(
    books: list[dict[str, str]],
    *,
    tenant: str = "default",
    consent: bool = True,
    mining_run_id: str,
) -> list[dict[str, Any]]:
    """Pure shaping function (no network call): book title becomes the prompt topic,
    catalog summary becomes the completion."""
    rows: list[dict[str, Any]] = []
    for book in books:
        title = book["title"]
        row_key = hashlib.sha256(
            f"{PRODUCER_GUTENBERG}|{mining_run_id}|{title}".encode()
        ).hexdigest()[:16]
        rows.append(
            {
                "id": f"sft-{row_key}",
                "tenant": tenant,
                "consent": consent,
                "producer": PRODUCER_GUTENBERG,
                "source_id": title,
                "prompt": f'What is the book "{title}" about?',
                "completion": book["summary"],
                "score": None,
                "provenance": {
                    "mining_run_id": mining_run_id,
                    "producer": PRODUCER_GUTENBERG,
                },
            }
        )
    return rows
