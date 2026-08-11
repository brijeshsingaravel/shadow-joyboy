"""Built-in governed memory tools: qdrant_upsert, qdrant_search, embed_text.

Implements the L4 (semantic / episodic) memory layer using:
  - Ollama /api/embeddings  (nomic-embed-text, 768-dim)
  - Qdrant REST API         (collections prefixed madras_<name>)

All tools are fully resilient — any HTTP or network error returns
ToolResult(ok=False, error=...) and never raises.

ASI02: retrieved content is wrapped in <retrieved>...</retrieved>.
ASI06: every upserted point carries a provenance payload field.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from madras.config import settings
from madras.memory.vector import qdrant_headers
from madras.models.agent_config import Rank
from madras.tools.registry import ToolResult, tool

_MAX_CONTENT_CHARS = 4_000


# ---------------------------------------------------------------------------
# Internal helper: embed a string via Ollama
# ---------------------------------------------------------------------------


async def _embed(text: str) -> list[float] | None:
    """POST to Ollama /api/embeddings and return the float vector, or None on error."""
    url = f"{settings.ollama_url}/api/embeddings"
    payload = {"model": settings.embed_model, "prompt": text}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
            embedding: list[float] = data["embedding"]
            return embedding
    except Exception:
        return None


async def _ensure_collection(client: httpx.AsyncClient, collection: str, vector_size: int) -> None:
    """Create the Qdrant collection if it does not exist yet."""
    url = f"{settings.qdrant_url}/collections/{collection}"
    # HEAD / GET to check existence
    try:
        r = await client.get(url)
        if r.status_code == 200:
            return  # already exists
    except httpx.HTTPError:
        pass  # proceed to create

    # PUT to create
    body = {
        "vectors": {
            "size": vector_size,
            "distance": "Cosine",
        }
    }
    r = await client.put(url, json=body)
    # 200 (ok) or 409 (conflict = already exists) are both fine
    if r.status_code not in (200, 409):
        r.raise_for_status()


# ---------------------------------------------------------------------------
# Tool: qdrant_upsert
# ---------------------------------------------------------------------------


@tool(
    name="qdrant_upsert",
    toolset="memory",
    rank_required=Rank.SPECIALIST,
    description=(
        "Embed text and upsert it into a Qdrant collection for persistent semantic memory "
        "(L4 layer). Returns the upserted point id in extras.point_id."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to embed and store."},
            "collection": {
                "type": "string",
                "description": "Logical collection name (prefixed with madras_). Default: semantic.",  # noqa: E501
                "default": "semantic",
            },
            "metadata": {
                "type": "object",
                "description": "Provenance / source metadata attached to the memory point (ASI06).",
            },
        },
        "required": ["text"],
    },
)
async def qdrant_upsert(args: dict[str, Any]) -> ToolResult:
    text: str = args["text"]
    logical_col: str = args.get("collection", "semantic")
    metadata: dict[str, Any] = args.get("metadata") or {}
    collection = f"madras_{logical_col}"

    vector = await _embed(text)
    if vector is None:
        return ToolResult(ok=False, error="embed failed: Ollama unavailable or model not loaded")

    point_id = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "text": text,
        "provenance": metadata,
        "ts_marker": time.time(),
    }

    try:
        async with httpx.AsyncClient(timeout=15.0, headers=qdrant_headers()) as client:
            await _ensure_collection(client, collection, len(vector))
            upsert_url = f"{settings.qdrant_url}/collections/{collection}/points"
            body = {
                "points": [
                    {
                        "id": point_id,
                        "vector": vector,
                        "payload": payload,
                    }
                ]
            }
            r = await client.put(upsert_url, json=body)
            r.raise_for_status()
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, error=f"qdrant upsert failed: {exc}")
    except Exception as exc:
        return ToolResult(ok=False, error=f"qdrant upsert failed: {exc}")

    return ToolResult(
        ok=True,
        content=f"Stored in {collection} (point_id={point_id})",
        extras={"point_id": point_id, "collection": collection},
    )


# ---------------------------------------------------------------------------
# Tool: qdrant_search
# ---------------------------------------------------------------------------


@tool(
    name="qdrant_search",
    toolset="memory",
    rank_required=Rank.INTERN,
    description=(
        "Embed a query and search Qdrant for the nearest memories (L4 semantic recall). "
        "Returns top-k results wrapped in <retrieved>...</retrieved> (ASI02)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural-language search query."},
            "collection": {
                "type": "string",
                "description": "Logical collection name (prefixed with madras_). Default: semantic.",  # noqa: E501
                "default": "semantic",
            },
            "k": {
                "type": "integer",
                "description": "Number of nearest neighbours to return.",
                "default": 5,
            },
        },
        "required": ["query"],
    },
)
async def qdrant_search(args: dict[str, Any]) -> ToolResult:
    query: str = args["query"]
    logical_col: str = args.get("collection", "semantic")
    k: int = int(args.get("k", 5))
    collection = f"madras_{logical_col}"

    vector = await _embed(query)
    if vector is None:
        return ToolResult(ok=False, error="embed failed: Ollama unavailable or model not loaded")

    search_url = f"{settings.qdrant_url}/collections/{collection}/points/search"
    body = {
        "vector": vector,
        "limit": k,
        "with_payload": True,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=qdrant_headers()) as client:
            r = await client.post(search_url, json=body)
            if r.status_code == 404:
                # Collection does not exist yet — no memories stored
                return ToolResult(ok=True, content="(no memories yet)")
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return ToolResult(ok=True, content="(no memories yet)")
        return ToolResult(ok=False, error=f"qdrant search failed: {exc}")
    except httpx.HTTPError as exc:
        return ToolResult(ok=False, error=f"qdrant search failed: {exc}")
    except Exception as exc:
        return ToolResult(ok=False, error=f"qdrant search failed: {exc}")

    hits = data.get("result", [])
    if not hits:
        return ToolResult(ok=True, content="<retrieved>(no matching memories)</retrieved>")

    lines: list[str] = []
    for hit in hits:
        score = hit.get("score", 0.0)
        payload = hit.get("payload", {})
        text = payload.get("text", "")
        lines.append(f"[score={score:.4f}] {text}")

    content = "<retrieved>\n" + "\n\n".join(lines) + "\n</retrieved>"
    return ToolResult(ok=True, content=content[:_MAX_CONTENT_CHARS])


# ---------------------------------------------------------------------------
# Tool: embed_text
# ---------------------------------------------------------------------------


@tool(
    name="embed_text",
    toolset="memory",
    rank_required=Rank.INTERN,
    description=(
        "Embed a text string and return the raw float vector (utility / debugging tool). "
        "Content will be the dimension count and a short preview of the vector."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to embed."},
        },
        "required": ["text"],
    },
)
async def embed_text(args: dict[str, Any]) -> ToolResult:
    text: str = args["text"]
    vector = await _embed(text)
    if vector is None:
        return ToolResult(ok=False, error="embed failed: Ollama unavailable or model not loaded")
    preview = ", ".join(f"{v:.4f}" for v in vector[:5])
    return ToolResult(
        ok=True,
        content=f"Embedding: {len(vector)} dims. Preview: [{preview}, ...]",
        extras={"embedding": vector, "dims": len(vector)},
    )
