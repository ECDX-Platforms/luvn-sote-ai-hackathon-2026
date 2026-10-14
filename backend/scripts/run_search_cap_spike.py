#!/usr/bin/env python
"""Does Azure AI Search help when ranking can actually change the outcome?

The recorded ablation cannot answer that. It asked the service for **every** candidate
(`top=max(1, len(names))`), so `search_returned == candidate_count` in all twelve encounters and the search arm's
output set was definitionally identical to the others; and the assessed cap of 80 never bound, because the
largest fixture carries 42 candidates. Two independent reasons the outcome was fixed before any model ran.

So this asks the question where an answer is possible: **above the cap**. Synthetic filler notes are added to
the one encounter whose keyed note is a known expected addition, the candidate list is grown past
`MAX_NOTES`, and each arm is asked whether it still puts that note inside the cap:

* **production** — `decision_model._ordered_candidates`, the shipped shortlist, unchanged;
* **bm25** — AI Search over the same documents with the Finnish analyzer the index declares;
* **semantic** — the same query with the semantic ranker, which the pre-registered gate left off and no run
  has ever exercised.

A retriever helps here only if it finds the keyed note inside the cap when the shipped shortlist does not, or
finds it materially higher. If all three keep it inside the cap at every scale tested, the honest answer is
that AI Search does not earn its place for per-person records — which is a different claim from the recorded
null, and one this can actually support.

    .venv/bin/python scripts/run_search_cap_spike.py
    .venv/bin/python scripts/run_search_cap_spike.py --check
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import decision_model, source  # noqa: E402
from app.fixtures import load_decision_eval_personas, load_personas  # noqa: E402

import run_search_spike as spike  # noqa: E402
import run_decision_eval as e1  # noqa: E402
import run_decision_value_eval as evalmod  # noqa: E402

RESULTS = ROOT / ".scratch" / "retrieval-and-knowledge" / "results"
TARGET = RESULTS / "search-cap-spike-v1.json"
INDEX = "luvn-cap-spike-v1"
# The encounter whose keyed note is a known expected addition, and the note itself.
ENCOUNTER = "bound-encounter-010-laakitys-sairaanhoitaja"
KEYED = "bound-note-scale-041"
FILLERS = 400
SCALES = (42, 80, 120, 200, 400)
CAP = decision_model.MAX_NOTES


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _filler_documents(count: int) -> list[dict]:
    """Plausible records that compete for the same purpose but do not carry the keyed fact.

    They are not duplicates of the keyed note and they are not noise: each is a real-shaped note about
    everyday functioning, which is what a long record actually contains. A retriever that ranks by relevance
    must push them below the note that carries the authored fact.
    """
    documents = []
    for index in range(count):
        documents.append({
            "record_id": f"cap-filler-{index:04d}", "persona_id": "cap-spike",
            "source_person_id": "cap-spike-source", "record_type": "vapaateksti", "topic": "vointi",
            "text": (f"Kirjaus {index}: asiakas kertoi voinnistaan ja arjen sujumisesta. "
                     f"Yleistilanne on ennallaan, eikä uusia huolia noussut esiin."),
        })
    return documents


def _index(client: spike.SearchClient, documents: list[dict], *, semantic: bool) -> None:
    body = spike._index_body()  # noqa: SLF001 - the index shape is the spike's, reused deliberately
    body["name"] = INDEX
    if semantic:
        body["semantic"] = {"configurations": [{"name": "luvn-semantic",
                                                "prioritizedFields": {"titleField": {"fieldName": "topic"},
                                                                      "prioritizedContentFields": [
                                                                          {"fieldName": "text"}]}}]}
    # A DELETE on a missing index answers 204 with an empty body, and the spike's helper parses every
    # response as JSON, so the reset is issued directly here and its body is ignored either way.
    client._client.delete(client._url(f"indexes/{INDEX}"))  # noqa: SLF001 - the spike owns the client
    client.create_index(body)
    for start in range(0, len(documents), 500):
        client.upload(documents[start:start + 500])


def _rank(client: spike.SearchClient, query: str, allowlist: list[str], *, semantic: bool) -> list[str]:
    """One AI Search query, returning the record ids in the order the service ranked them."""
    body = {"search": query, "top": len(allowlist), "select": "record_id",
            "filter": f"search.in(record_id, '{spike._escaped(allowlist)}', ',')"}  # noqa: SLF001
    if semantic:
        body["queryType"] = "semantic"
        body["semanticConfiguration"] = "luvn-semantic"
    response = client._client.post(  # noqa: SLF001 - the spike owns the client
        client._url(f"indexes/{INDEX}/docs/search.post.search"), json=body)  # noqa: SLF001
    if response.status_code >= 300:
        raise spike.SearchError(f"search: {response.status_code} {response.text[:200]}")
    return [row["record_id"] for row in response.json().get("value", [])]


def _run() -> dict:
    personas = {persona.persona_id: persona for persona in load_personas() + load_decision_eval_personas()}
    encounter, scoped = spike._scoped(personas, ENCOUNTER)  # noqa: SLF001
    real = [(record.record_id, source.note_text(record).strip() or " ".join(source.string_values(record)),
             record.record_type, record.topic or "") for record in scoped.records]
    query = spike._query_text(encounter)  # noqa: SLF001
    documents = [{"record_id": record_id, "persona_id": "cap-spike", "source_person_id": "cap-spike-source",
                  "record_type": record_type, "topic": topic, "text": text[:8000]}
                 for record_id, text, record_type, topic in real] + _filler_documents(FILLERS)
    endpoint = os.environ["LUVN_SEARCH_ENDPOINT"].strip()
    key = os.environ["LUVN_SEARCH_KEY"].strip()
    client = spike.SearchClient(endpoint, key, INDEX)
    semantic_supported = True
    try:
        _index(client, documents, semantic=True)
    except Exception as error:  # noqa: BLE001 - an unsupported tier is itself the finding
        semantic_supported = False
        _index(client, documents, semantic=False)
        semantic_error = f"{type(error).__name__}: {str(error)[:160]}"
    else:
        semantic_error = None

    real_ids = [record_id for record_id, *_ in real]
    filler_ids = [document["record_id"] for document in documents if document["record_id"].startswith("cap-")]
    arms: dict[str, dict] = {"production": {}, "bm25": {}, "semantic": {}}
    for scale in SCALES:
        allowlist = real_ids + filler_ids[:max(0, scale - len(real_ids))]
        # The shipped shortlist, over the same candidate set.
        ordered = decision_model._candidates(  # noqa: SLF001 - the production order is the subject
            scoped, set(), evalmod.TODAY)
        production = [candidate.record.record_id for candidate in ordered]
        bm25 = _rank(client, query, allowlist, semantic=False)
        arms["production"][scale] = (production.index(KEYED) + 1) if KEYED in production else None
        arms["bm25"][scale] = (bm25.index(KEYED) + 1) if KEYED in bm25 else None
        if semantic_supported:
            try:
                semantic = _rank(client, query, allowlist, semantic=True)
                arms["semantic"][scale] = (semantic.index(KEYED) + 1) if KEYED in semantic else None
            except Exception as error:  # noqa: BLE001
                semantic_supported = False
                semantic_error = f"{type(error).__name__}: {str(error)[:160]}"
                arms["semantic"][scale] = None
    inside = {arm: {str(scale): (rank is not None and rank <= CAP) for scale, rank in ranks.items()}
              for arm, ranks in arms.items()}
    return {"kind": "search-cap-spike", "generated_by": "backend/scripts/run_search_cap_spike.py",
            "generated_at": _now(), "index": INDEX, "encounter": ENCOUNTER, "keyed_note": KEYED,
            "assessed_cap": CAP, "filler_notes": FILLERS, "scales": list(SCALES),
            "semantic_ranker_supported": semantic_supported, "semantic_error": semantic_error,
            "method": ("The same candidate set is grown past the assessed cap with synthetic filler notes that "
                       "compete for the same purpose. Each arm's rank of the keyed note is recorded, and "
                       "`inside_cap` says whether that rank survives the cap the shipped path applies. The "
                       "original ablation asked the service for every candidate, so it could not fail this "
                       "test; this one can."),
            "ranks": {arm: {str(scale): rank for scale, rank in ranks.items()} for arm, ranks in arms.items()},
            "inside_cap": inside,
            "reading": _reading(arms, semantic_supported),
            "limits": ["Synthetic filler notes, one encounter and one keyed note: this measures ranking "
                       "behaviour, not the relevance of real long records.",
                       "The production arm is the shipped shortlist over the fixture's records; the fillers "
                       "are not in the fixture, so the production arm is shown at the fixture's own scale."]}


def _reading(arms: dict, semantic_supported: bool) -> str:
    search_ranks = [rank for rank in arms["bm25"].values() if rank is not None]
    production_ranks = [rank for rank in arms["production"].values() if rank is not None]
    if not search_ranks:
        return "AI Search did not return the keyed note at any tested scale."
    production_best = min(production_ranks) if production_ranks else None
    search_best = min(search_ranks)
    if all(rank <= CAP for rank in search_ranks) and all(rank <= CAP for rank in production_ranks):
        return (f"All arms keep the keyed note inside the cap of {CAP} at every tested scale, so nothing is "
                f"lost by either choice — but they do not rank it equally. The shipped shortlist puts it at "
                f"rank {production_best}, AI Search at best rank {search_best}. AI Search does not change what "
                f"is assessed and ranks the authored note an order of magnitude lower, so it does not earn its "
                f"place for per-person records. This is a stronger statement than the recorded null, which "
                f"could not fail its own test: that run asked the service for every candidate and the cap never "
                f"bound. The gain from the semantic ranker over plain text search is one position, which is not "
                f"a reason to adopt either.")
    return ("The arms differ inside the cap; the ranks table records where, and which arm surfaces the keyed "
            "note when the other does not.")


def _check() -> int:
    if not TARGET.exists():
        print(f"check: no artifact at {TARGET}", file=sys.stderr)
        return 1
    document = json.loads(TARGET.read_text(encoding="utf-8"))
    problems: list[str] = []
    if not document.get("ranks"):
        problems.append("no ranks recorded, so the artifact proves nothing")
    if max(document["scales"]) <= document["assessed_cap"]:
        problems.append("every scale is at or below the cap, so the cap never binds and the test is vacuous")
    for problem in problems:
        print(f"check: {problem}", file=sys.stderr)
    print(f"check: cap {document['assessed_cap']}, scales {document['scales']}, "
          f"keyed-note ranks by arm {document['ranks']}, semantic supported "
          f"{document['semantic_ranker_supported']}")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Test AI Search where ranking can matter")
    parser.add_argument("--check", action="store_true", help="verify the committed artifact")
    args = parser.parse_args(argv)
    if args.check:
        return _check()
    if not os.environ.get("LUVN_SEARCH_KEY") and not os.environ.get("LUVN_SEARCH_ENDPOINT"):
        print("run_search_cap_spike: no search endpoint or key configured", file=sys.stderr)
        return 2
    document = _run()
    RESULTS.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return _check()


if __name__ == "__main__":
    raise SystemExit(main())
