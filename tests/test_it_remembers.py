"""The promise the whole repository is named for: it remembers you between conversations.

    "Tell Shadow something today and ask about it next week."

Groups 1 and 2 prove the agent refuses what it promised to refuse. Those tests would also pass on
a repo that does nothing at all -- an agent with no memory cannot send email either. This file is
the other half: the thing it is FOR actually works.

NEEDS POSTGRES. Semantic recall additionally needs Qdrant and Ollama. `docker compose up -d`
starts the databases. Without them these tests skip with a reason, so someone evaluating this repo
on a laptop with no Docker still gets a green run and can see exactly what was not covered. A skip
that explains itself is honest; a skip that reads like a pass is not.
"""

from __future__ import annotations

import socket
import time
import uuid

import httpx
import pytest

from madras.config import settings
from madras.memory.fabric import MemoryFabric
from madras.memory.retrieval import MemoryItem
from madras.memory.vector import QdrantVectorIndex


def _http_up(url: str, path: str) -> bool:
    try:
        return httpx.get(f"{url}{path}", timeout=3.0).status_code < 500
    except Exception:
        return False


def _embed_model_present() -> bool:
    """Ollama answering is not the same as the embedding model being pulled.

    Checking only that the server is up made this suite FAIL rather than skip on a machine with
    Ollama running and `nomic-embed-text` never pulled -- which is most machines, since the README
    used to tell you to pull the chat model and nothing else. embed() returns None on a missing
    model, semantic recall silently degrades to keyword matching, and the test that proves recall
    works by meaning cannot pass. Skip means "we did not check this"; fail means "this is broken".
    A missing model is the first, not the second.
    """
    try:
        r = httpx.get(f"{settings.ollama_url}/api/tags", timeout=3.0)
        names = [m.get("name", "") for m in r.json().get("models", [])]
        want = settings.embed_model.split(":")[0]
        return any(n.split(":")[0] == want for n in names)
    except Exception:
        return False


def _postgres_up() -> bool:
    try:
        hostport = settings.postgres_url.split("@")[-1].split("/")[0]
        host, _, port = hostport.partition(":")
        with socket.create_connection((host, int(port or 5432)), timeout=3):
            return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(
    not _postgres_up(), reason="needs Postgres — run `docker compose up -d` (see README)"
)
needs_semantic = pytest.mark.skipif(
    not (
        _postgres_up()
        and _http_up(settings.qdrant_url, "/collections")
        and _embed_model_present()
    ),
    reason=f"needs Postgres + Qdrant + Ollama with `ollama pull {settings.embed_model}`",
)


def _item(content: str, subject: str, *, now: float) -> MemoryItem:
    return MemoryItem(
        id=uuid.uuid4().hex,
        kind="fact",
        subject=subject,
        content=content,
        source="test",
        created_at=now,
        valid_from=now,
    )


async def _fresh(vector: bool = False) -> MemoryFabric:
    """Its own tenant every time, so tests never see each other's memories and never touch
    anything a real person stored."""
    tenant = f"test_{uuid.uuid4().hex[:8]}"
    vec = (
        QdrantVectorIndex(collection=f"test_mem_{uuid.uuid4().hex[:8]}", tenant=tenant)
        if vector
        else None
    )
    return MemoryFabric(postgres_url=settings.postgres_url, tenant=tenant, vector_index=vec)


@needs_db
class TestItRemembers:
    async def test_what_you_tell_it_comes_back(self) -> None:
        """The whole promise, in five lines."""
        f = await _fresh()
        now = time.time()
        try:
            await f.remember(
                _item("Maasha is a dog who stays at a boarding centre.", "Maasha", now=now),
                now=now,
            )
            got = await f.recall("Maasha", now=now, k=5)
            assert any("boarding" in m.content for m in got), (
                f"stored a fact and could not get it back: {[m.content for m in got]}"
            )
        finally:
            await f.close()

    async def test_it_survives_a_new_connection(self) -> None:
        """'Ask about it next week' means the memory outlives the process, not just the object.
        A cache that forgets on restart would pass a naive round-trip test and fail the promise."""
        tenant = f"test_{uuid.uuid4().hex[:8]}"
        now = time.time()
        a = MemoryFabric(postgres_url=settings.postgres_url, tenant=tenant)
        try:
            await a.remember(
                _item("My grandfather grows brinjal in Kumbakonam.", "grandfather", now=now),
                now=now,
            )
        finally:
            await a.close()

        b = MemoryFabric(postgres_url=settings.postgres_url, tenant=tenant)
        try:
            got = await b.recall("grandfather", now=now, k=5)
            assert any("brinjal" in m.content for m in got), "the memory did not survive"
        finally:
            await b.close()

    async def test_one_persons_memories_are_not_another_persons(self) -> None:
        """Tenancy. The single most important property once more than one person uses this."""
        now = time.time()
        a, b = await _fresh(), await _fresh()
        try:
            await a.remember(_item("My PIN is on the fridge.", "pin", now=now), now=now)
            leaked = await b.recall("PIN", now=now, k=10)
            assert not any("fridge" in m.content for m in leaked), (
                f"another tenant could read it: {[m.content for m in leaked]}"
            )
        finally:
            await a.close()
            await b.close()

    async def test_recall_of_nothing_is_empty_not_an_error(self) -> None:
        """A brand-new user asking a question before telling it anything must get a clean empty
        list, not an exception. First impressions are made on empty databases."""
        f = await _fresh()
        try:
            assert await f.recall("anything at all", now=time.time(), k=5) == []
        finally:
            await f.close()


@needs_semantic
class TestItFindsThingsByMeaning:
    """The difference between memory and a search box. The README claims it specifically:

        "ask about 'my sister's wedding' and it finds the day you called it the function in
         December"
    """

    async def test_it_finds_a_memory_that_shares_no_words_with_the_question(self) -> None:
        f = await _fresh(vector=True)
        now = time.time()
        try:
            await f.remember(
                _item("The function in December is at a hall in Coimbatore.", "function", now=now),
                now=now,
            )
            await f.remember(_item("I prefer filter coffee to tea.", "coffee", now=now), now=now)

            got = await f.recall("where is my sister's wedding", now=now, k=5)
            contents = [m.content for m in got]
            assert any("Coimbatore" in c for c in contents), (
                "keyword recall would miss this -- 'wedding' appears nowhere in the memory. "
                f"That is the point of the semantic layer. Got: {contents}"
            )
        finally:
            await f.close()

    async def test_keyword_recall_alone_would_have_missed_it(self) -> None:
        """Proves the test above is not passing by luck.

        With only two memories stored, a recall that simply returned everything would look like
        success. So the same store and the same question are run with NO vector index: keyword
        recall returns an empty list, because "wedding" appears nowhere in the memory. The
        semantic layer is doing the work, and this test fails the day it silently stops.
        """
        f = MemoryFabric(
            postgres_url=settings.postgres_url,
            tenant=f"test_{uuid.uuid4().hex[:8]}",
            vector_index=None,  # deliberately none
        )
        now = time.time()
        try:
            await f.remember(
                _item("The function in December is at a hall in Coimbatore.", "function", now=now),
                now=now,
            )
            got = await f.recall("where is my sister's wedding", now=now, k=5)
            assert not got, (
                f"keyword recall found it without help: {[m.content for m in got]}. "
                "If this starts passing, the semantic test above no longer proves anything."
            )
        finally:
            await f.close()

    async def test_the_semantic_layer_is_actually_attached(self) -> None:
        """Guards the test above from passing for the wrong reason. If the vector index silently
        failed to attach, recall would fall back to keywords and a lucky word overlap could still
        look like success."""
        f = await _fresh(vector=True)
        try:
            assert f._vec is not None, "no vector index attached — the test above proves nothing"
        finally:
            await f.close()
