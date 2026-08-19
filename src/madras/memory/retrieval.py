"""Memory Fabric — the pure retrieval/temporal/contradiction core (Step 1).

The benchmark-deciding heart of Shadow's memory, kept PURE + deterministic (no DB,
no LLM, no clock — `now` is always passed in) so it's fully testable and reused by
the store, the tools, and the nightly Lighthouse enforcement.

Design (Mem0/Zep + LongMemEval lessons):
* memories are ATOMIC items (one fact/preference/principle each), not raw dumps —
  "to the point" recall;
* every item is TEMPORAL: ``valid_from`` / ``valid_until`` (None = currently true) +
  ``supersedes`` — so knowledge UPDATES and CONTRADICTIONS are first-class (never a
  silent overwrite), which is exactly the LongMemEval knowledge-update/temporal split;
* recall scores relevance x recency x confidence over *currently-valid* items and
  returns a token-budgeted top-k.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field

# kinds of atomic memory across the 6 layers (the fabric is the shared substrate)
KINDS = ("fact", "preference", "principle", "relationship", "semantic", "episodic")
# kinds where a newer item about the same subject SUPERSEDES the older (knowledge update)
_SUPERSEDING_KINDS = {"fact", "preference"}

# Subjects that are a BUCKET rather than a topic — they say how a memory arrived, not what it
# is about, so two items sharing one are not contradicting each other. Kept deliberately tiny:
# every other subject ("user job", "user location") names one thing and must keep superseding.
_BUCKET_SUBJECTS = frozenset({"directive"})


def _is_bucket_subject(subject: str) -> bool:
    return (subject or "").strip().lower() in _BUCKET_SUBJECTS


_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "the a an of to in on at for and or is are was were be been being i you he she it "
    "they we my your his her our their this that these those with as by from".split()
)


@dataclass
class MemoryItem:
    id: str
    kind: str
    subject: str  # the entity/topic this is about (drives contradiction)
    content: str  # the atomic statement
    tags: list[str] = field(default_factory=list[str])
    confidence: float = 1.0
    source: str = ""  # provenance: where it came from
    session_id: str = ""
    agent_name: str = ""
    created_at: float = 0.0  # epoch seconds (passed in; no wall clock here)
    valid_from: float = 0.0
    valid_until: float | None = None  # None = still valid
    supersedes: str | None = None  # id of the item this replaced
    # E-X4 biological memory: reinforcement strength (Ebbinghaus spacing). Each recall
    # raises `strength` (slower decay) + refreshes `last_accessed` (recency resets).
    strength: float = 1.0
    last_accessed: float = 0.0
    recall_count: int = 0


_STRENGTH_CAP = 6.0  # max half-life multiplier from repeated recall


def _tokens(s: str) -> set[str]:
    return {w for w in _WORD.findall((s or "").lower()) if w not in _STOP and len(w) > 1}


def is_current(item: MemoryItem, now: float) -> bool:
    """True if the item is valid at `now` (born, not yet invalidated)."""
    if item.valid_from and now < item.valid_from:
        return False
    return item.valid_until is None or now < item.valid_until


def relevance(item: MemoryItem, query: str) -> float:
    """Token-overlap relevance of the item to a query (subject+content+tags)."""
    q = _tokens(query)
    if not q:
        return 0.0
    hay = _tokens(item.subject) | _tokens(item.content) | {t.lower() for t in item.tags}
    if not hay:
        return 0.0
    inter = len(q & hay)
    return inter / len(q)  # fraction of query terms matched (recall-oriented)


def recency(created_at: float, now: float, *, half_life_days: float = 30.0) -> float:
    """Exponential recency weight in [0,1]; half_life_days old → 0.5."""
    age_days = max(0.0, (now - created_at) / 86400.0)
    return math.pow(0.5, age_days / half_life_days) if half_life_days > 0 else 1.0


def reinforce(item: MemoryItem, now: float) -> MemoryItem:
    """Biological reinforcement on recall (mutates + returns the item): bump recall_count,
    raise strength (capped), and reset recency by refreshing last_accessed."""
    item.recall_count += 1
    item.strength = min(_STRENGTH_CAP, max(1.0, item.strength) + 1.0)
    item.last_accessed = now
    return item


def score(
    item: MemoryItem,
    query: str,
    now: float,
    *,
    half_life_days: float = 30.0,
    w_rel: float = 0.6,
    w_rec: float = 0.25,
    w_conf: float = 0.15,
) -> float:
    """Blended salience of a memory item for a query. Non-current → 0.

    Reinforcement (E-X4): recency is anchored on the most recent access and decays over an
    effective half-life extended by `strength`, so often-recalled memories persist longer."""
    if not is_current(item, now):
        return 0.0
    rel = relevance(item, query)
    if rel == 0.0:
        return 0.0  # never surface an irrelevant memory just because it's fresh
    anchor = item.last_accessed or item.created_at
    eff_half_life = half_life_days * max(1.0, item.strength)
    rec = recency(anchor, now, half_life_days=eff_half_life)
    conf = max(0.0, min(1.0, item.confidence))
    return w_rel * rel + w_rec * rec + w_conf * conf


def recall(
    items: list[MemoryItem],
    query: str,
    *,
    now: float,
    k: int = 6,
    half_life_days: float = 30.0,
    max_chars: int = 1200,
    semantic_ids: set[str] | None = None,
    semantic_floor: float = 0.5,
) -> list[MemoryItem]:
    """Top-k currently-valid items for the query, recency-weighted, then trimmed to a
    char budget (to-the-point — never dump the whole store into the prompt).

    ``semantic_ids`` are items a vector index judged relevant; they get a relevance FLOOR
    so semantically-related memories surface even with no literal token overlap (hybrid
    keyword+semantic recall — the L3 fold-in)."""
    sem = semantic_ids or set()

    def _eff_score(it: MemoryItem) -> float:
        s = score(it, query, now, half_life_days=half_life_days)
        if s == 0.0 and it.id in sem and is_current(it, now):
            # vector said it's relevant though tokens don't overlap → floor it in
            rec = recency(it.created_at, now, half_life_days=half_life_days)
            return 0.6 * semantic_floor + 0.25 * rec + 0.15 * max(0.0, min(1.0, it.confidence))
        return s

    scored = [(_eff_score(it), it) for it in items]
    ranked = [it for s, it in sorted(scored, key=lambda t: t[0], reverse=True) if s > 0]
    out: list[MemoryItem] = []
    used = 0
    for it in ranked[: max(0, k)]:
        used += len(it.content)
        if out and used > max_chars:
            break
        out.append(it)
    return out


def apply_order(
    items: list[MemoryItem],
    order: list[int],
    *,
    k: int,
    max_chars: int = 1200,
) -> list[MemoryItem]:
    """Reorder ``items`` by ``order`` (indices, best first), append any missing indices in
    original order (STABLE — a reranker miss never drops a candidate), take top-k, then
    trim to a char budget. Pure; shared by the sync (BM25) and async (semantic) rerankers."""
    if not items:
        return []
    seq = list(order or [])
    seen = set(seq)
    seq += [i for i in range(len(items)) if i not in seen]
    ranked = [items[i] for i in seq if 0 <= i < len(items)]
    out: list[MemoryItem] = []
    used = 0
    for it in ranked[: max(0, k)]:
        used += len(it.content)
        if out and used > max_chars:
            break
        out.append(it)
    return out


def rerank_items(
    items: list[MemoryItem],
    query: str,
    reranker: Callable[[str, list[str]], list[int]],
    *,
    k: int,
    max_chars: int = 1200,
) -> list[MemoryItem]:
    """Two-stage recall finish for a SYNC reranker: re-order a candidate pool, take top-k,
    budget. ``reranker(query, texts) -> indices`` (best first). The reranker is pluggable —
    BM25 today, a cross-encoder later — behind one seam. (Async rerankers go through
    ``apply_order`` directly in the store, where the embed call can be awaited.)"""
    if not items:
        return []
    texts = [f"{it.subject}. {it.content}" for it in items]
    return apply_order(items, list(reranker(query, texts) or []), k=k, max_chars=max_chars)


def same_subject(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return (a or "").strip().lower() == (b or "").strip().lower()
    # subjects match if one's token set is contained in the other (handles "Bob" vs
    # "Bob's job") — tight enough to avoid cross-topic false contradictions.
    return ta <= tb or tb <= ta


def find_contradictions(
    existing: list[MemoryItem], new: MemoryItem, now: float
) -> list[MemoryItem]:
    """Currently-valid items the `new` item supersedes: same subject, superseding kind,
    different content. The caller marks these valid_until=now (temporal reflection)."""
    if new.kind not in _SUPERSEDING_KINDS:
        return []
    # "directive" NAMES HOW A MEMORY ARRIVED, NOT WHAT IT IS ABOUT (s70).
    #
    # extract_salient gives every explicit "remember that ..." the subject "directive", so
    # subject-matching treated two entirely unrelated instructions as contradicting each other
    # and expired the older one. "Remember my daughter's exam is on Friday", then "remember I
    # don't eat coriander", and the exam was gone -- silently, from a product whose whole
    # promise is that it remembers.
    #
    # Found when correcting one wrong memory in the founder's account expired an unrelated one
    # about his neighbour's cat, days old and nothing to do with it.
    #
    # A directive can still be replaced by an identical-subject item once directives carry a
    # real topic; until then the safe failure is to keep both. Duplicate CONTENT is still
    # deduped by the caller, so this does not let the same instruction pile up.
    if _is_bucket_subject(new.subject):
        return []
    new_content = " ".join(_tokens(new.content))
    hits: list[MemoryItem] = []
    for it in existing:
        if it.id == new.id or it.kind != new.kind or not is_current(it, now):
            continue
        if same_subject(it.subject, new.subject) and " ".join(_tokens(it.content)) != new_content:
            hits.append(it)
    return hits


# Mem0's update-pass operations: what to DO with a new candidate vs current memory.
ADD, UPDATE, DELETE, NOOP = "add", "update", "delete", "noop"


@dataclass
class MemoryOp:
    """The decided operation for a new candidate (Mem0 ADD/UPDATE/DELETE/NOOP)."""

    op: str  # add | update | delete | noop
    target: MemoryItem | None = None  # the current item to augment (update) / supersede (delete)
    reason: str = ""


def reconcile(new: MemoryItem, existing: list[MemoryItem], now: float) -> MemoryOp:
    """Mem0's update pass, deterministic: pick ADD / UPDATE / DELETE / NOOP for a new candidate
    against currently-valid memory (the nightly LLM manager can refine). Sharpens our write path
    with the two ops it lacked — NOOP (skip equivalent → no duplicate writes) and UPDATE (augment
    a strict superset, keep lineage as refinement) — alongside ADD and DELETE (supersede a
    contradiction). UPDATE/DELETE both resolve via temporal reflection (supersede + link), never a
    silent overwrite; the label tells the store/arbiter refinement-vs-pivot."""
    same = [
        it
        for it in existing
        if is_current(it, now) and it.kind == new.kind and same_subject(it.subject, new.subject)
    ]
    new_toks = set(_tokens(new.content))
    for it in same:
        it_toks = set(_tokens(it.content))
        if it_toks == new_toks:
            return MemoryOp(NOOP, it, "equivalent memory already current")
    if new.kind in _SUPERSEDING_KINDS:
        for it in same:
            it_toks = set(_tokens(it.content))
            if it_toks and it_toks < new_toks:  # new strictly augments the old
                return MemoryOp(UPDATE, it, "augments existing (superset) — refinement")
        contradictions = find_contradictions(same, new, now)
        if contradictions:
            return MemoryOp(DELETE, contradictions[0], "contradicts current memory — supersede")
    return MemoryOp(ADD, None, "novel subject/content")


@dataclass
class Arbitration:
    """The resolved truth about a subject when its memories conflict (D1.10 / ConflictQA)."""

    winner: MemoryItem | None  # the item to believe (None if nothing is currently valid)
    conflicts: list[MemoryItem]  # currently-valid items that DISAGREE with the winner
    superseded: list[MemoryItem]  # already invalidated (valid_until set) = knowledge updates
    reason: str


def _content_key(content: str) -> str:
    return " ".join(sorted(_tokens(content)))


def arbitrate(items: list[MemoryItem], now: float) -> Arbitration:
    """Resolve conflicting memories about one subject by an explicit policy (not pure recency):
    currently-valid beats superseded; among valid, the most-CORROBORATED claim wins (more agreeing
    items), then higher summed confidence, then recency. Surfaces the dissenting valid items + the
    superseded (knowledge-update) ones so the agent can answer *and* flag the conflict."""
    valid = [it for it in items if is_current(it, now)]
    superseded = [it for it in items if not is_current(it, now)]
    if not valid:
        return Arbitration(None, [], superseded, "no currently-valid memory for this subject")

    groups: dict[str, list[MemoryItem]] = {}
    for it in valid:
        groups.setdefault(_content_key(it.content), []).append(it)

    def group_score(g: list[MemoryItem]) -> tuple[int, float, float]:
        return (len(g), sum(i.confidence for i in g), max(i.created_at for i in g))

    best_key = max(groups, key=lambda k: group_score(groups[k]))
    winner = max(groups[best_key], key=lambda i: (i.confidence, i.created_at))
    conflicts = [it for it in valid if _content_key(it.content) != best_key]
    if not conflicts:
        reason = (
            f"uncontested: {len(groups[best_key])} corroborating item(s), no valid disagreement"
            if len(valid) > 1
            else "single currently-valid item"
        )
    else:
        reason = (
            f"arbitrated: winner backed by {len(groups[best_key])} item(s) "
            f"(conf {winner.confidence:.2f}) over {len(conflicts)} dissenting valid item(s)"
        )
    return Arbitration(winner, conflicts, superseded, reason)
