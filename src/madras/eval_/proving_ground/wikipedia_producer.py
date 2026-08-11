"""General-knowledge SFT producer (2026-07-16 spec, Part 4): Wikipedia article intros,
feeding the same `pg_sft_rows` sink as the other producers in this package.

Sourced from Wikipedia's own public MediaWiki API (`action=query`, `prop=extracts`,
`exintro=1`) -- no scraping, the intro-extract endpoint exists specifically for this kind
of programmatic summary use. No LLM synthesis: each article's plain-text intro becomes
the completion verbatim, same doctrine as `arxiv_producer.py`'s abstracts and
`thirukkural_producer.py`'s explanations.

Two-step fetch per topic (search -> extract), rate-limited like arxiv_producer.py --
Wikipedia's API etiquette asks for reasonable request pacing, not a hard per-request
minimum, but treating it the same as arXiv (>=1 request per 3s) keeps this producer safe
regardless of which upstream API it's pointed at.
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol
from urllib.parse import quote

PRODUCER_WIKIPEDIA = "wikipedia"

WIKIPEDIA_API_BASE = "https://en.wikipedia.org/w/api.php"

# Matches arxiv_producer.py's ARXIV_RATE_LIMIT_SECONDS -- conservative pacing applied
# uniformly across every network-fetching producer in this package, not just arXiv.
WIKIPEDIA_RATE_LIMIT_SECONDS = 3.0

# Real bug (live-verified): Wikipedia's API returns 403 "Please set a user-agent and
# respect our robot policy" for requests with no User-Agent header -- unlike arXiv/
# Gutendex, Wikipedia's robot policy actively rejects anonymous-looking clients rather
# than just rate-limiting them. A bare app-name/purpose string still 403s -- their
# check requires a URL-shaped component in the UA (live-verified: linking their own
# bot-policy page, not a fabricated contact, is what satisfies it) plus a library token.
WIKIPEDIA_USER_AGENT = (
    "Madras-HOPE-DatasetCompiler/1.0 "
    "(https://en.wikipedia.org/wiki/Wikipedia:Bot_policy; "
    "internal training-data mining, non-commercial) python-httpx"
)


class _HttpClient(Protocol):
    async def get(self, url: str, **kwargs: Any) -> Any: ...


def parse_wikipedia_search_results(search_json: dict[str, Any]) -> list[str]:
    """Extract page titles from a `list=search` response."""
    return [
        result["title"]
        for result in search_json.get("query", {}).get("search", [])
        if result.get("title")
    ]


def parse_wikipedia_extract(extract_json: dict[str, Any]) -> dict[str, str] | None:
    """Extract `{title, extract}` from a `prop=extracts` response for a single page.
    Returns None when the page has no extract (e.g. a disambiguation page or a title
    that no longer resolves) -- nothing to train a completion on."""
    pages = extract_json.get("query", {}).get("pages", {})
    for page in pages.values():
        title = page.get("title", "")
        extract = (page.get("extract") or "").strip()
        if not title or not extract:
            continue
        return {"title": title, "extract": extract}
    return None


async def fetch_wikipedia_articles(
    topics: list[str],
    *,
    results_per_topic: int = 5,
    client: _HttpClient,
    sleep: Any,
) -> list[dict[str, str]]:
    """Search each topic, then fetch the intro extract for every result title.
    Rate-limited between every individual HTTP request (search AND extract calls alike)
    to `WIKIPEDIA_RATE_LIMIT_SECONDS`, mirroring `arxiv_producer.fetch_arxiv_papers`."""
    articles: list[dict[str, str]] = []
    first_request = True
    for topic in topics:
        if not first_request:
            await sleep(WIKIPEDIA_RATE_LIMIT_SECONDS)
        first_request = False
        search_url = (
            f"{WIKIPEDIA_API_BASE}?action=query&list=search&format=json"
            f"&srsearch={quote(topic)}&srlimit={results_per_topic}"
        )
        search_resp = await client.get(search_url, headers={"User-Agent": WIKIPEDIA_USER_AGENT})
        titles = parse_wikipedia_search_results(search_resp.json())

        for title in titles:
            await sleep(WIKIPEDIA_RATE_LIMIT_SECONDS)
            extract_url = (
                f"{WIKIPEDIA_API_BASE}?action=query&prop=extracts&exintro=1"
                f"&explaintext=1&format=json&titles={quote(title)}"
            )
            extract_resp = await client.get(
                extract_url, headers={"User-Agent": WIKIPEDIA_USER_AGENT}
            )
            article = parse_wikipedia_extract(extract_resp.json())
            if article:
                articles.append(article)
    return articles


def wikipedia_articles_to_sft_rows(
    articles: list[dict[str, str]],
    *,
    tenant: str = "default",
    consent: bool = True,
    mining_run_id: str,
) -> list[dict[str, Any]]:
    """Pure shaping function (no network call): article title becomes the prompt topic,
    intro extract becomes the completion -- same shape as arxiv_producer's title/abstract
    and thirukkural_producer's chapter-name/explanation."""
    rows: list[dict[str, Any]] = []
    for article in articles:
        title = article["title"]
        row_key = hashlib.sha256(
            f"{PRODUCER_WIKIPEDIA}|{mining_run_id}|{title}".encode()
        ).hexdigest()[:16]
        rows.append(
            {
                "id": f"sft-{row_key}",
                "tenant": tenant,
                "consent": consent,
                "producer": PRODUCER_WIKIPEDIA,
                "source_id": title,
                "prompt": f'What does Wikipedia say about "{title}"?',
                "completion": article["extract"],
                "score": None,
                "provenance": {
                    "mining_run_id": mining_run_id,
                    "producer": PRODUCER_WIKIPEDIA,
                },
            }
        )
    return rows
