"""Public-knowledge SFT producer (2026-07-16 spec, Part 2 -- deliberately scoped separately
from the local-corpus producer in `dataset_compiler.py`): sources arXiv paper title+abstract
pairs via arXiv's public Atom API, feeding the same `pg_sft_rows` sink.

Abstracts only, never full paper PDFs -- arXiv hosts preprints under a mix of licenses (many
CC-BY, some all-rights-reserved by the eventual publisher), while the *abstract metadata*
itself is what arXiv's own API is built to serve for exactly this kind of bulk/programmatic
use (their Terms of Use: https://info.arxiv.org/help/api/tou.html). No LLM synthesis step --
same "purely local shaping, no external inference call" doctrine as
`dataset_compiler.split_into_qa_sections`, just with a network *fetch* instead of a *local
file read* as the input side.

Rate-limited to no more than 1 request per 3 seconds per arXiv's own API guidance.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any, Protocol
from xml.etree import ElementTree

PRODUCER_ARXIV = "arxiv-knowledge"

ARXIV_API_BASE = "https://export.arxiv.org/api/query"

# arXiv's own guidance: no more than one request every 3 seconds.
ARXIV_RATE_LIMIT_SECONDS = 3.0

_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
_WHITESPACE_RE = re.compile(r"\s+")


def parse_arxiv_atom_feed(xml_text: str) -> list[dict[str, str]]:
    """Parse arXiv's Atom XML feed into paper dicts: `id` (the arXiv abs URL), `title`,
    `summary` (the abstract). Whitespace in title/summary is collapsed -- arXiv's XML
    wraps long text across lines with irregular indentation."""
    root = ElementTree.fromstring(xml_text)
    papers: list[dict[str, str]] = []
    for entry in root.findall("atom:entry", _ATOM_NS):
        id_el = entry.find("atom:id", _ATOM_NS)
        title_el = entry.find("atom:title", _ATOM_NS)
        summary_el = entry.find("atom:summary", _ATOM_NS)
        if id_el is None or title_el is None or summary_el is None:
            continue
        arxiv_id = (id_el.text or "").strip()
        title = _WHITESPACE_RE.sub(" ", (title_el.text or "")).strip()
        summary = _WHITESPACE_RE.sub(" ", (summary_el.text or "")).strip()
        if not arxiv_id or not title or not summary:
            continue
        papers.append({"id": arxiv_id, "title": title, "summary": summary})
    return papers


class _HttpClient(Protocol):
    async def get(self, url: str, **kwargs: Any) -> Any: ...


async def fetch_arxiv_papers(
    categories: list[str],
    *,
    max_results_per_category: int = 50,
    client: _HttpClient,
    sleep: Any = asyncio.sleep,
) -> list[dict[str, str]]:
    """Query arXiv's public API once per category, rate-limited to
    `ARXIV_RATE_LIMIT_SECONDS` between requests (arXiv's own API guidance). `client` is
    injected (any object with an async `.get(url)` returning something with `.text`) so
    this is testable without a real network call; `sleep` is injected so tests don't
    actually wait `ARXIV_RATE_LIMIT_SECONDS` between fetches."""
    papers: list[dict[str, str]] = []
    for i, category in enumerate(categories):
        if i > 0:
            await sleep(ARXIV_RATE_LIMIT_SECONDS)
        url = (
            f"{ARXIV_API_BASE}?search_query=cat:{category}"
            f"&start=0&max_results={max_results_per_category}"
        )
        response = await client.get(url)
        papers.extend(parse_arxiv_atom_feed(response.text))
    return papers


def arxiv_papers_to_sft_rows(
    papers: list[dict[str, str]],
    *,
    tenant: str = "default",
    consent: bool = True,
    mining_run_id: str,
) -> list[dict[str, Any]]:
    """Pure shaping function (no network call): title becomes the prompt topic, abstract
    becomes the completion -- mirrors `split_into_qa_sections`'s header-as-topic /
    body-as-completion shape, just with a paper title standing in for a markdown header."""
    rows: list[dict[str, Any]] = []
    for paper in papers:
        row_key = hashlib.sha256(
            f"{PRODUCER_ARXIV}|{mining_run_id}|{paper['id']}".encode()
        ).hexdigest()[:16]
        rows.append(
            {
                "id": f"sft-{row_key}",
                "tenant": tenant,
                "consent": consent,
                "producer": PRODUCER_ARXIV,
                "source_id": paper["id"],
                "prompt": f'What is the paper "{paper["title"]}" about?',
                "completion": paper["summary"],
                "score": None,
                "provenance": {"mining_run_id": mining_run_id, "producer": PRODUCER_ARXIV},
            }
        )
    return rows
