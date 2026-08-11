"""Link understanding — comprehend URLs in a message, folded into Web Fetch (row 78).

When a message references URLs, detect them and bring back a compact understanding (title +
summary) instead of making the agent fetch blindly. Lifts OpenClaw's detection discipline: only
**bare** HTTP(S) URLs are considered — **markdown citations `[text](url)` are stripped** so
display-only links don't trigger fetches — and each is **SSRF/egress-filtered** ([[Network Egress
Policy]]), deduped, and **capped** (default 3). Comprehension reuses our `web_extract.fetch_clean`
(Trafilatura); fetched content is **ASI02-fenced** (untrusted DATA). Pure detection + injectable
fetcher → testable offline.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from madras.security.net_policy import NetPolicy

_MD_LINK = re.compile(r"\[[^\]]*\]\((https?://\S+?)\)")  # markdown link — display-only, skip
_BARE = re.compile(r"https?://\S+")
_TRAIL = ".,);]>'\""  # trailing punctuation to trim off a URL

# url -> clean main text (raises on failure). Live adapter wraps web_extract.fetch_clean.
Fetcher = Callable[[str], Awaitable[str]]


@dataclass
class LinkInsight:
    url: str
    ok: bool
    title: str = ""
    summary: str = ""
    error: str | None = None


def extract_links(
    message: str, *, max_links: int = 3, net_policy: NetPolicy | None = None
) -> list[str]:
    """Unique, SSRF-filtered, non-markdown bare HTTP(S) URLs from a message (OpenClaw rules)."""
    np = net_policy or NetPolicy()
    stripped = _MD_LINK.sub(" ", message or "")  # display-only citations don't fetch
    seen: set[str] = set()
    out: list[str] = []
    for m in _BARE.finditer(stripped):
        url = m.group(0).rstrip(_TRAIL)
        if url in seen:
            continue
        if not np.check(url).allow:  # SSRF / egress filter
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= max_links:
            break
    return out


def _title(text: str) -> str:
    for line in (text or "").splitlines():
        s = line.strip().lstrip("#").strip()
        if len(s) >= 3:
            return s[:120]
    return ""


def _summary(text: str, n: int) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:n]


@dataclass
class LinkUnderstanding:
    fetch: Fetcher
    net_policy: NetPolicy = field(default_factory=NetPolicy)
    max_links: int = 3
    summary_chars: int = 300
    audit: Callable[[dict[str, Any]], None] | None = None

    def _audit(self, record: dict[str, Any]) -> None:
        if self.audit is not None:
            self.audit(record)

    async def understand(self, message: str) -> list[LinkInsight]:
        insights: list[LinkInsight] = []
        for url in extract_links(message, max_links=self.max_links, net_policy=self.net_policy):
            try:
                text = await self.fetch(url)
            except Exception as exc:
                self._audit({"event": "link_fetch_fail", "url": url, "error": str(exc)})
                insights.append(LinkInsight(url, False, error=f"{type(exc).__name__}: {exc}"))
                continue
            insight = LinkInsight(
                url, True, title=_title(text), summary=_summary(text, self.summary_chars)
            )
            self._audit({"event": "link_understood", "url": url, "title": insight.title[:60]})
            insights.append(insight)
        return insights

    def render(self, insights: list[LinkInsight]) -> str:
        """Render the understandings for the model — fetched content ASI02-fenced as DATA."""
        parts: list[str] = []
        for it in insights:
            if not it.ok:
                parts.append(f"- {it.url} — (could not fetch: {it.error})")
                continue
            parts.append(
                f"- {it.url} — **{it.title}**\n  <retrieved url={it.url!r}>\n  "
                f"{it.summary}\n  </retrieved>"
            )
        return "\n".join(parts)
