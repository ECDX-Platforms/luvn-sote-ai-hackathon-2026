"""Embed the committed knowledge snapshots into Azure SQL: `python -m app.load_knowledge [--verify] [--recreate]`.

Run it as the `luvn-load-corpus` Container Apps job with the loader identity, which owns the schema (the app
identity is `db_datareader` and is not widened). The chunks are the form questions of the form-knowledge
snapshot, the Käypä hoito citations (title and recorded topics, never content) and the pathway corpus's
steps; each is embedded with `llm.embed` in batches and written as a `knowledge_chunks` row whose `id` is the
ref `knowledge_search` resolves back through the same snapshots. The load is delete-then-insert in one
transaction, so a reader sees the previous index or the new one, never half of each. `--verify` reads the
catalogue and the row counts back and changes nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from . import guidance, knowledge_forms, llm, pathways
from .knowledge_search import GUIDELINE_PREFIX, PATHWAY_PREFIX
from .sqldelivery import (KNOWLEDGE_COUNTS, KNOWLEDGE_DROP_STATEMENTS, KNOWLEDGE_INSERT,
                          KNOWLEDGE_REPLACE_STATEMENTS, KNOWLEDGE_SCHEMA_STATEMENTS, KNOWLEDGE_VECTOR_DIMENSIONS,
                          KNOWLEDGE_VERSIONS, connect, verify_knowledge_integrity)

EMBED_BATCH = 64
INSERT_BATCH = 200
SEPARATOR = " · "
FORM_SOURCE = "luvn-form-knowledge"
GUIDELINE_SOURCE = guidance.CITATION_HOST
PATHWAY_SOURCE = pathways.REF_SOURCE


@dataclass(frozen=True)
class Chunk:
    id: str
    kind: str
    source: str
    text: str


class LoadRefused(RuntimeError):
    """The loader will not write: no embedding deployment, or a dimension that cannot fit the column."""


def form_chunks() -> list[Chunk]:
    """`"{name_fi} · {category} · {text_fi}"`; the category is omitted when the question has none."""
    out = []
    for item in knowledge_forms.items():
        parts = [item.form_name, *( [item.category_name] if item.category_name else [] ), item.question_text]
        out.append(Chunk(id=item.as_ref(), kind="form", source=FORM_SOURCE, text=SEPARATOR.join(parts)))
    return out


def guideline_chunks() -> list[Chunk]:
    """The title and the recorded topics: citation metadata, which the terms permit; no guideline content."""
    return [Chunk(id=f"{GUIDELINE_PREFIX}{g.code}", kind="guideline", source=GUIDELINE_SOURCE,
                  text=SEPARATOR.join([g.title, ", ".join(g.topics)]))
            for g in guidance.GUIDELINES]


def pathway_chunks() -> list[Chunk]:
    corpus = pathways.load_corpus()
    out = []
    for entry in corpus["entries"]:
        for step in entry.get("steps", []):
            out.append(Chunk(id=f"{PATHWAY_PREFIX}{entry['entry_id']}#{step['step_id']}", kind="pathway",
                             source=PATHWAY_SOURCE,
                             text=SEPARATOR.join([str(entry.get("title", "")), str(step["text"])])))
    return out


def chunks() -> list[Chunk]:
    """Every chunk, ids unique, forms first."""
    out = [*form_chunks(), *guideline_chunks(), *pathway_chunks()]
    ids = [chunk.id for chunk in out]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate knowledge chunk ids")
    return out


def embed_all(texts: Sequence[str], *, embed: Callable[[Sequence[str]], list[list[float]]] | None = None,
              batch_size: int = EMBED_BATCH) -> list[list[float]]:
    """One vector per text, embedded `batch_size` at a time. Refuses a vector the column cannot hold.

    `embed` defaults to `llm.embed`, looked up at call time so a test can stub the module attribute."""
    embed = embed if embed is not None else llm.embed
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = list(texts[start:start + batch_size])
        got = embed(batch)
        if len(got) != len(batch):
            raise ValueError(f"embedding returned {len(got)} vectors for {len(batch)} texts")
        vectors.extend(got)
    for vector in vectors:
        if len(vector) != KNOWLEDGE_VECTOR_DIMENSIONS:
            raise LoadRefused(f"a vector of {len(vector)} dimensions cannot be stored in "
                              f"VECTOR({KNOWLEDGE_VECTOR_DIMENSIONS})")
    return vectors


def load(connection: Any, items: Sequence[Chunk], vectors: Sequence[Sequence[float]], *, embedding_model: str,
         snapshot_version: str = knowledge_forms.SNAPSHOT_VERSION, recreate: bool = False) -> int:
    """Create the table if missing (drop first on `--recreate`), then replace every row in one transaction."""
    if len(items) != len(vectors):
        raise ValueError("one vector per chunk")
    cursor = connection.cursor()
    try:
        if recreate:
            for statement in KNOWLEDGE_DROP_STATEMENTS:
                cursor.execute(statement)
        for entry in KNOWLEDGE_SCHEMA_STATEMENTS:
            cursor.execute(entry.guarded)
        for statement in KNOWLEDGE_REPLACE_STATEMENTS:
            cursor.execute(statement)
        rows = [(chunk.id, chunk.kind, chunk.source, chunk.text, json.dumps(list(vector)), embedding_model,
                 snapshot_version) for chunk, vector in zip(items, vectors)]
        for start in range(0, len(rows), INSERT_BATCH):
            cursor.executemany(KNOWLEDGE_INSERT, rows[start:start + INSERT_BATCH])
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
    return len(items)


def stored(connection: Any) -> dict:
    """What the database holds: catalogue state, counts by kind, and the (snapshot, model) versions present."""
    cursor = connection.cursor()
    try:
        integrity = verify_knowledge_integrity(cursor)
        counts: dict[str, int] = {}
        versions: list[dict] = []
        if integrity["actual"].get("knowledge_table"):
            cursor.execute(KNOWLEDGE_COUNTS)
            counts = {str(kind): int(count) for kind, count in cursor.fetchall()}
            cursor.execute(KNOWLEDGE_VERSIONS)
            versions = [{"snapshot_version": str(s), "embedding_model": str(m), "rows": int(n),
                         "loaded_at": str(t)} for s, m, n, t in cursor.fetchall()]
    finally:
        cursor.close()
    return {"integrity": integrity, "counts": counts, "versions": versions}


def verify(connection: Any) -> dict:
    """`stored()` compared to what the snapshots would load now. `ok` when the index is current and whole."""
    state = stored(connection)
    expected = {}
    for chunk in chunks():
        expected[chunk.kind] = expected.get(chunk.kind, 0) + 1
    snapshots = {row["snapshot_version"] for row in state["versions"]}
    models = {row["embedding_model"] for row in state["versions"]}
    problems = []
    if not state["integrity"]["ok"]:
        problems.append(f"catalogue: missing {state['integrity']['missing']}")
    if state["counts"] != expected:
        problems.append(f"counts {state['counts']} differ from the snapshots' {expected}")
    if snapshots != {knowledge_forms.SNAPSHOT_VERSION}:
        problems.append(f"snapshot versions {sorted(snapshots)} are not [{knowledge_forms.SNAPSHOT_VERSION!r}]")
    if len(models) > 1:
        problems.append(f"rows embedded by more than one model: {sorted(models)}")
    if llm.embedding_deployment() and models and models != {llm.embedding_deployment()}:
        problems.append(f"rows embedded by {sorted(models)}, the configured deployment is {llm.embedding_deployment()!r}")
    return {**state, "expected": expected, "problems": problems, "ok": not problems}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Embed the knowledge snapshots into Azure SQL (knowledge_chunks)")
    parser.add_argument("--server", default=None, help="default: LUVN_SQL_SERVER")
    parser.add_argument("--database", default=None, help="default: LUVN_SQL_DATABASE")
    parser.add_argument("--client-id", default=None, help="default: LUVN_SQL_CLIENT_ID")
    parser.add_argument("--recreate", action="store_true",
                        help="drop and recreate knowledge_chunks, for a genuine schema change; a plain load "
                             "already replaces every row inside one transaction")
    parser.add_argument("--verify", action="store_true",
                        help="print what the database holds against the snapshots and change nothing")
    args = parser.parse_args(argv)

    if args.verify:
        connection = connect(server=args.server, database=args.database, client_id=args.client_id)
        try:
            state = verify(connection)
        finally:
            connection.close()
        print(f"load_knowledge: database holds {state['counts']} (expected {state['expected']})")
        print(f"load_knowledge: integrity {state['integrity']['actual']} (expected {state['integrity']['expected']})")
        for row in state["versions"]:
            print(f"load_knowledge: {row['rows']} rows, snapshot {row['snapshot_version']}, "
                  f"model {row['embedding_model']}, loaded {row['loaded_at']}")
        for problem in state["problems"]:
            print(f"load_knowledge: PROBLEM {problem}", file=sys.stderr)
        return 0 if state["ok"] else 1

    if not llm.embedding_configured():
        print("load_knowledge: refusing to load: LUVN_EMBEDDING_DEPLOYMENT, LUNA_FOUNDRY_BASE and LUNA_FOUNDRY_KEY "
              "must all be set", file=sys.stderr)
        return 2
    if llm.embedding_dimensions() != KNOWLEDGE_VECTOR_DIMENSIONS:
        print(f"load_knowledge: refusing to load: LUVN_EMBEDDING_DIMENSIONS={llm.embedding_dimensions()} but the "
              f"column is VECTOR({KNOWLEDGE_VECTOR_DIMENSIONS})", file=sys.stderr)
        return 2
    items = chunks()
    vectors = embed_all([chunk.text for chunk in items])
    connection = connect(server=args.server, database=args.database, client_id=args.client_id)
    try:
        written = load(connection, items, vectors, embedding_model=llm.embedding_deployment(), recreate=args.recreate)
    finally:
        connection.close()
    by_kind: dict[str, int] = {}
    for chunk in items:
        by_kind[chunk.kind] = by_kind.get(chunk.kind, 0) + 1
    print(f"load_knowledge: wrote {written} chunks {by_kind} with {llm.embedding_deployment()} "
          f"({KNOWLEDGE_VECTOR_DIMENSIONS} dimensions), snapshot {knowledge_forms.SNAPSHOT_VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
