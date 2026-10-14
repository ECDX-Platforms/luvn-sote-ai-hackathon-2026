"""Knowledge retrieval behind one seam: `search(query)` over the Azure SQL `VECTOR` index, else the snapshots.

The assistant's `search_knowledge` tool (ADR-0027) reads through this module and nothing else. With an
embedding deployment and a SQL server configured, the query is embedded (`llm.embed`) and the nearest
`knowledge_chunks` rows are read by cosine distance. Without either -- and on any failure of the live path --
retrieval falls back to the committed snapshots' token overlap (`knowledge_forms.candidates`, widened by the
Käypä hoito topics the query names), so every test and every offline briefing gets the same answer shape.

**Verifiability.** A stored row's text is never returned. The row's `id` is a ref -- `form:{form_id}#{question_id}`
(`FormItem.as_ref()`), `guideline:{code}`, `pathway:{entry_id}#{step_id}` -- and every hit is resolved back
through `knowledge_forms.items()`, `guidance.GUIDELINES` and `pathways.load_corpus()`. An id that resolves to
nothing is dropped, so the index can only surface what the committed snapshots already say, and Decision-1
verifies a sentence against the same snapshot as before. A guideline hit is its citation (code, title, URL),
never its content: that is the licence line `guidance.py` draws.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

from . import guidance, knowledge_forms, llm, pathways, sqldelivery
from .text import tokens as _tokens, overlap as _overlap

log = logging.getLogger(__name__)

FORM_PREFIX = "form:"
GUIDELINE_PREFIX = "guideline:"
PATHWAY_PREFIX = "pathway:"
KINDS = ("form", "guideline", "pathway")
# The shape of every ref, for a caller sorting the model's one citation list into record ids and refs.
PREFIXES = (FORM_PREFIX, GUIDELINE_PREFIX, PATHWAY_PREFIX)
PATHWAY_LABEL = "illustrative stand-in, not clinical guidance"
# The live read asks for more rows than it returns, so a stale id (a chunk from an older snapshot) thins
# nothing: the surplus covers what resolution drops.
OVERFETCH = 2
# At most this many guideline citations in a fallback result; the rest of `limit` is for form questions.
FALLBACK_GUIDELINE_LIMIT = 2


@dataclass(frozen=True)
class KnowledgeHit:
    """One resolved hit. `text` is the snapshot's own wording for the item, never a stored row's."""

    ref: str
    kind: str
    title: str
    text: str
    source: str
    form_name: str | None = None
    form_version: str | None = None
    category_name: str | None = None
    distance: float | None = None

    def as_dict(self) -> dict:
        """The model-facing shape: the keys `FormItem.as_dict()` has always had, plus `kind` and `title`."""
        return {"ref": self.ref, "kind": self.kind, "title": self.title, "form_name": self.form_name,
                "form_version": self.form_version, "category_name": self.category_name,
                "question_text": self.text, "source": self.source}


# --- resolution: a ref is only ever what the committed snapshots say it is ----------------------------------


@lru_cache(maxsize=1)
def _form_items() -> dict[str, knowledge_forms.FormItem]:
    return {item.as_ref(): item for item in knowledge_forms.items()}


@lru_cache(maxsize=1)
def _guidelines() -> dict[str, guidance.Guideline]:
    return {f"{GUIDELINE_PREFIX}{guideline.code}": guideline for guideline in guidance.GUIDELINES}


@lru_cache(maxsize=1)
def _pathway_steps() -> dict[str, tuple[str, pathways.PathwayStep]]:
    corpus = pathways.load_corpus()
    out: dict[str, tuple[str, pathways.PathwayStep]] = {}
    for entry in corpus["entries"]:
        for step in pathways.steps_of(entry, str(corpus["corpus_version"]), bool(corpus["illustrative"]),
                                      str(corpus["provenance"])):
            out[f"{PATHWAY_PREFIX}{step.entry_id}#{step.step_id}"] = (str(entry.get("title", "")), step)
    return out


def _form_hit(item: knowledge_forms.FormItem, distance: float | None = None) -> KnowledgeHit:
    return KnowledgeHit(ref=item.as_ref(), kind="form", title=item.form_name, text=item.question_text,
                        source=item.source, form_name=item.form_name, form_version=item.form_version,
                        category_name=item.category_name, distance=distance)


def _guideline_hit(ref: str, guideline: guidance.Guideline, distance: float | None = None) -> KnowledgeHit:
    return KnowledgeHit(ref=ref, kind="guideline", title=guideline.title, text=guideline.citation,
                        source=guideline.url, distance=distance)


def _pathway_hit(ref: str, title: str, step: pathways.PathwayStep, distance: float | None = None) -> KnowledgeHit:
    return KnowledgeHit(ref=ref, kind="pathway", title=title, text=step.text,
                        source=f"{pathways.REF_SOURCE}@{step.version} · {PATHWAY_LABEL}", distance=distance)


def resolve(ref: str, distance: float | None = None) -> KnowledgeHit | None:
    """The hit a ref names in the committed snapshots, or `None` when it names nothing."""
    if ref.startswith(FORM_PREFIX):
        item = _form_items().get(ref)
        return _form_hit(item, distance) if item is not None else None
    if ref.startswith(GUIDELINE_PREFIX):
        guideline = _guidelines().get(ref)
        return _guideline_hit(ref, guideline, distance) if guideline is not None else None
    if ref.startswith(PATHWAY_PREFIX):
        found = _pathway_steps().get(ref)
        return _pathway_hit(ref, found[0], found[1], distance) if found is not None else None
    return None


# --- the live path ------------------------------------------------------------------------------------------


def configured() -> bool:
    """Both halves of the live path are present: an embedding deployment and a SQL server."""
    return llm.embedding_configured() and bool(os.environ.get("LUVN_SQL_SERVER"))


def _search_sql() -> str:
    # The dimension is a module literal (`sqldelivery.KNOWLEDGE_VECTOR_DIMENSIONS`), never runtime input.
    return (f"SELECT TOP (?) id, kind, source, VECTOR_DISTANCE('cosine', embedding, "
            f"CAST(CAST(? AS NVARCHAR(MAX)) AS VECTOR({sqldelivery.KNOWLEDGE_VECTOR_DIMENSIONS}))) AS distance "
            f"FROM {sqldelivery.KNOWLEDGE_TABLE} ORDER BY distance")


def _nearest(query: str, limit: int) -> list[tuple[str, float | None]]:
    """`(id, distance)` for the nearest rows. Raises on any failure; `search` turns that into the fallback."""
    if llm.embedding_dimensions() != sqldelivery.KNOWLEDGE_VECTOR_DIMENSIONS:
        raise ValueError(f"LUVN_EMBEDDING_DIMENSIONS={llm.embedding_dimensions()} does not match the "
                         f"VECTOR({sqldelivery.KNOWLEDGE_VECTOR_DIMENSIONS}) column")
    vector = llm.embed([query])[0]
    connection = sqldelivery.connect()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(_search_sql(), limit, json.dumps(vector))
            rows = cursor.fetchall()
        finally:
            cursor.close()
    finally:
        connection.close()
    return [(str(row[0]), float(row[3]) if row[3] is not None else None) for row in rows]


# --- the fallback: the committed snapshots' own token overlap ------------------------------------------------


def _topic_matches(topic: str, query_tokens: set[str]) -> bool:
    """A topic the query names: a short term (an ICD code, "phq") exactly, a longer one by stem (`text.overlap`)."""
    terms = [t for t in _tokens(topic) if len(t) >= 2]
    if not terms:
        return False
    return all(t in query_tokens if len(t) < 4 else bool(_overlap({t}, query_tokens)) for t in terms)


def _named_guidelines(query: str) -> list[tuple[str, guidance.Guideline]]:
    """The guidelines whose recorded topics the query names, in `GUIDELINES` order."""
    query_tokens = _tokens(query)
    return [(ref, guideline) for ref, guideline in _guidelines().items()
            if any(_topic_matches(topic, query_tokens) for topic in guideline.topics)]


def _fallback(query: str, *, limit: int, instruments: Sequence[str]) -> list[KnowledgeHit]:
    """`knowledge_forms.candidates` as it stands, widened by the topics of the guidelines the query names.

    The question's own retrieval leads. Then each single-word topic of a named guideline is retrieved on its
    own, so "tupakointi" reaches the Fagerström test through `hoi40020`'s recorded topic "nikotiini" -- the
    committed guideline metadata is the one bridge between a lay word and an instrument's name, and no
    synonym list is invented here. The merged form items are re-ranked the way `candidates` ranks its own
    (scored instrument items first, stable), and the named guidelines are returned as citations.
    """
    named = _named_guidelines(query)
    queries = [query]
    for _ref, guideline in named:
        for topic in guideline.topics:
            terms = _tokens(topic)
            if len(terms) == 1 and len(topic.strip()) >= 4 and topic not in queries:
                queries.append(topic)
    found: list[knowledge_forms.FormItem] = []
    seen: set[str] = set()
    for text in queries:
        for row in knowledge_forms.candidates(text, instruments=instruments, limit=limit):
            if row["ref"] not in seen:
                seen.add(row["ref"])
                item = _form_items().get(row["ref"])
                if item is not None:
                    found.append(item)
    # `_substantive_first` is the module's own ranking of its own items; applying it to the merged list keeps
    # one policy rather than inventing a second.
    ranked = list(knowledge_forms._substantive_first(found).values())
    guideline_hits = [_guideline_hit(ref, guideline) for ref, guideline in named[:FALLBACK_GUIDELINE_LIMIT]]
    form_hits = [_form_hit(item) for item in ranked[:max(0, limit - len(guideline_hits))]]
    return [*guideline_hits, *form_hits][:limit]


# --- the seam -----------------------------------------------------------------------------------------------


def search(query: str, *, limit: int = 6, instruments: Sequence[str] = ()) -> list[KnowledgeHit]:
    """The knowledge items a query reaches, each resolved against the committed snapshots.

    Live when `configured()`: embed, read the nearest rows, resolve their ids, drop the unresolvable. On any
    exception -- or when nothing resolves, which is what an unloaded index looks like -- log a warning and
    fall back. `instruments` are the encounter's own instrument names and reach the fallback only.
    """
    query = (query or "").strip()
    if not query or limit <= 0:
        return []
    named = [name for name in instruments if name]
    if not configured():
        return _fallback(query, limit=limit, instruments=named)
    try:
        nearest = _nearest(query, limit * OVERFETCH)
    except Exception as exc:  # noqa: BLE001 -- a retrieval failure degrades to the snapshots, never to an error
        log.warning("knowledge search fell back to the snapshots: %s", type(exc).__name__)
        return _fallback(query, limit=limit, instruments=named)
    hits: list[KnowledgeHit] = []
    for ref, distance in nearest:
        hit = resolve(ref, distance)
        if hit is not None:
            hits.append(hit)
        if len(hits) == limit:
            break
    if not hits:
        log.warning("knowledge search resolved no rows (index unloaded or stale); using the snapshots")
        return _fallback(query, limit=limit, instruments=named)
    return hits
