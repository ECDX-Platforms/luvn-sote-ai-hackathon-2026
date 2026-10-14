"""`knowledge_search.search`: the one retrieval seam, offline in every test (`llm.embed` is stubbed, no network)."""

from __future__ import annotations

import json

import pytest

from app import guidance, knowledge_forms, knowledge_search, llm, sqldelivery

EMBEDDING_ENV = ("LUVN_EMBEDDING_DEPLOYMENT", "LUVN_EMBEDDING_DIMENSIONS", "LUVN_EMBEDDING_TIMEOUT",
                 "LUNA_FOUNDRY_BASE", "LUNA_FOUNDRY_KEY", "LUVN_SQL_SERVER", "LUVN_SQL_DATABASE")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    for name in EMBEDDING_ENV:
        monkeypatch.delenv(name, raising=False)
    assert not llm.embedding_configured() and not knowledge_search.configured()


def _live(monkeypatch):
    monkeypatch.setenv("LUVN_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
    monkeypatch.setenv("LUNA_FOUNDRY_BASE", "https://example.invalid/openai/v1")
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "k")
    monkeypatch.setenv("LUVN_SQL_SERVER", "example.database.windows.net")
    monkeypatch.setenv("LUVN_SQL_DATABASE", "luvn")
    assert knowledge_search.configured()


class _Cursor:
    def __init__(self, rows, log):
        self.rows, self.log = rows, log

    def execute(self, sql, *params):
        self.log.append((sql, params))

    def fetchall(self):
        return self.rows

    def close(self):
        self.log.append(("cursor-closed", ()))


class _Connection:
    def __init__(self, rows, log):
        self.rows, self.log = rows, log

    def cursor(self):
        return _Cursor(self.rows, self.log)

    def close(self):
        self.log.append(("connection-closed", ()))


def _fagerstrom_refs() -> set[str]:
    return {item.as_ref() for item in knowledge_forms.items() if "fagerström" in item.form_name.casefold()
            and item.category_name != knowledge_forms.OHJE_CATEGORY}


def test_offline_tupakointi_reaches_the_fagerstrom_questions_through_the_recorded_guideline_topic():
    """The plan's own check. "tupakointi" shares no token with the HSI items ("savuke", "poltat") or the form's
    name ("nikotiini"); the bridge is `hoi40020`'s committed topic list, nothing invented."""
    hits = knowledge_search.search("tupakointi")
    assert hits and len(hits) <= 6
    assert hits[0].kind == "guideline" and hits[0].ref == "guideline:hoi40020"
    assert _fagerstrom_refs() <= {hit.ref for hit in hits}
    assert {hit.ref for hit in hits[1:3]} == _fagerstrom_refs(), \
        "the scored instrument items lead the form hits, as `candidates` ranks its own"
    assert all(hit.kind == "form" for hit in hits[1:])
    assert all(hit.distance is None for hit in hits), "the fallback measures nothing"


def test_a_guideline_hit_is_its_citation_and_never_content():
    hit = next(h for h in knowledge_search.search("tupakointi") if h.kind == "guideline")
    guideline = next(g for g in guidance.GUIDELINES if g.code == "hoi40020")
    assert hit.as_dict() == {"ref": "guideline:hoi40020", "kind": "guideline", "title": guideline.title,
                             "form_name": None, "form_version": None, "category_name": None,
                             "question_text": guideline.citation, "source": guideline.url}
    assert "www.kaypahoito.fi" in hit.text and hit.text.count(" ") < 20, "a citation line, not a text"


def test_the_fallback_is_candidates_as_it_stands_when_no_guideline_is_named():
    assert knowledge_search._named_guidelines("ADHD") == [], "no recorded topic names ADHD"
    expected = knowledge_forms.candidates("ADHD", limit=6)
    assert expected, "the query must retrieve something, or this test proves nothing"
    hits = knowledge_search.search("ADHD")
    assert [h.ref for h in hits] == [row["ref"] for row in expected]
    assert all(h.kind == "form" for h in hits)
    item = next(i for i in knowledge_forms.items() if i.as_ref() == hits[0].ref)
    assert hits[0].as_dict() == {**item.as_dict(), "kind": "form", "title": item.form_name}


def test_instruments_reach_the_fallback_and_an_empty_query_returns_nothing():
    assert knowledge_search.search("") == [] and knowledge_search.search("   ") == []
    assert knowledge_search.search("masennus", limit=0) == []
    plain = [h.ref for h in knowledge_search.search("oireet", limit=4)]
    with_instrument = [h.ref for h in knowledge_search.search("oireet", limit=4, instruments=("PHQ-9",))]
    assert len(plain) <= 4 and len(with_instrument) <= 4


def test_resolve_drops_what_the_snapshots_do_not_say():
    assert knowledge_search.resolve("form:no-such#question") is None
    assert knowledge_search.resolve("guideline:hoi99999") is None
    assert knowledge_search.resolve("pathway:nope#s1") is None
    assert knowledge_search.resolve("records:anything") is None
    first = next(iter(knowledge_search._pathway_steps()))
    hit = knowledge_search.resolve(first, 0.25)
    assert hit is not None and hit.kind == "pathway" and hit.distance == 0.25
    assert knowledge_search.PATHWAY_LABEL in hit.source, "a pathway step says what it is"


def test_the_live_path_reads_ids_only_and_resolves_them_against_the_snapshots(monkeypatch):
    _live(monkeypatch)
    item = knowledge_forms.items()[0]
    stale = "form:00000000-0000-0000-0000-000000000000#gone"
    rows = [(item.as_ref(), "form", "luvn-form-knowledge", 0.11),
            (stale, "form", "luvn-form-knowledge", 0.12),            # dropped: resolves to nothing
            ("guideline:hoi50023", "guideline", "www.kaypahoito.fi", 0.2),
            (next(iter(knowledge_search._pathway_steps())), "pathway", "pathway-corpus", 0.3)]
    log: list = []
    calls: list = []
    monkeypatch.setattr(llm, "embed", lambda texts: calls.append(list(texts)) or [[0.5] * 1536])
    monkeypatch.setattr(sqldelivery, "connect", lambda **kwargs: _Connection(rows, log))
    hits = knowledge_search.search("masennus", limit=3)
    assert calls == [["masennus"]]
    sql, params = log[0]
    assert sql.startswith("SELECT TOP (?) id, kind, source, VECTOR_DISTANCE('cosine', embedding, CAST(CAST(? AS NVARCHAR(MAX)) AS VECTOR(1536)))")
    assert "text" not in sql.split("FROM")[0], "the row's text is never read"
    assert params[0] == 3 * knowledge_search.OVERFETCH and json.loads(params[1]) == [0.5] * 1536
    assert [h.ref for h in hits] == [item.as_ref(), "guideline:hoi50023", rows[3][0]]
    assert [h.kind for h in hits] == ["form", "guideline", "pathway"]
    assert hits[0].text == item.question_text and hits[0].distance == 0.11
    assert hits[1].title == "Depressio"
    assert ("cursor-closed", ()) in log and log[-1] == ("connection-closed", ())


def test_a_failing_live_path_falls_back_with_a_warning(monkeypatch, caplog):
    _live(monkeypatch)

    def boom(texts):
        raise ConnectionError("no network in tests")

    monkeypatch.setattr(llm, "embed", boom)
    monkeypatch.setattr(sqldelivery, "connect", lambda **kwargs: pytest.fail("connect must not be reached"))
    with caplog.at_level("WARNING", logger="app.knowledge_search"):
        hits = knowledge_search.search("tupakointi")
    assert _fagerstrom_refs() <= {h.ref for h in hits}
    assert any("fell back" in record.message and "ConnectionError" in record.message for record in caplog.records)
    assert not any("no network" in record.message for record in caplog.records), "the exception text is not logged"


def test_an_unloaded_index_falls_back_rather_than_answering_nothing(monkeypatch):
    _live(monkeypatch)
    monkeypatch.setattr(llm, "embed", lambda texts: [[0.0] * 1536])
    monkeypatch.setattr(sqldelivery, "connect", lambda **kwargs: _Connection([], []))
    assert [h.ref for h in knowledge_search.search("ADHD")] == [r["ref"] for r in knowledge_forms.candidates("ADHD")]


def test_phq_9_names_the_depression_guideline_through_its_recorded_topic():
    """"PHQ-9" is both an instrument and a topic `hoi50023` records, so the citation leads and the instrument's
    own items follow: the guideline topics are the committed bridge, applied the same way for every query."""
    hits = knowledge_search.search("PHQ-9")
    assert hits[0].ref == "guideline:hoi50023" and hits[0].kind == "guideline"
    forms = [h.ref for h in hits[1:]]
    direct = [r["ref"] for r in knowledge_forms.candidates("PHQ-9", limit=6)]
    assert set(direct) <= set(forms), "the question's own retrieval is never thinned by the widening"
    assert [ref for ref in forms if ref in direct] == direct, "and keeps its relative order"
    scored = knowledge_forms._scored_refs()
    flags = [ref in scored for ref in forms]
    assert flags == sorted(flags, reverse=True), "scored instrument items lead, as `candidates` ranks its own"
    assert any("BDI-21" in (h.form_name or "") for h in hits[1:]), "the topic `masennus` adds the other instrument"


def test_a_dimension_mismatch_never_reaches_the_provider(monkeypatch):
    _live(monkeypatch)
    monkeypatch.setenv("LUVN_EMBEDDING_DIMENSIONS", "3072")
    monkeypatch.setattr(llm, "embed", lambda texts: pytest.fail("embed must not be called"))
    assert knowledge_search.search("tupakointi"), "falls back"


def test_embed_asks_the_deployment_for_the_column_dimension(monkeypatch):
    """`llm.embed` is synchronous and asks for `dimensions=1536`; the client is stubbed, nothing leaves the test."""
    _live(monkeypatch)
    import openai

    seen = {}

    class _Embeddings:
        def create(self, **kwargs):
            seen.update(kwargs)
            data = [type("Item", (), {"index": i, "embedding": [float(i)] * kwargs["dimensions"]})()
                    for i in reversed(range(len(kwargs["input"])))]
            return type("Response", (), {"data": data, "model": "text-embedding-3-large", "usage": None})()

    class _Client:
        def __init__(self, **kwargs):
            seen["client"] = kwargs
            self.embeddings = _Embeddings()

    monkeypatch.setattr(openai, "OpenAI", _Client)
    vectors = llm.embed(["a", "b"])
    assert seen["model"] == "text-embedding-3-large" and seen["dimensions"] == 1536 == sqldelivery.KNOWLEDGE_VECTOR_DIMENSIONS
    assert seen["input"] == ["a", "b"] and seen["client"]["timeout"] == 10.0
    assert vectors == [[0.0] * 1536, [1.0] * 1536], "in input order, whatever order the provider answered"
    monkeypatch.delenv("LUVN_EMBEDDING_DEPLOYMENT")
    with pytest.raises(RuntimeError):
        llm.embed(["a"])


def test_the_embedding_base_is_the_account_route_not_the_project_route(monkeypatch):
    """`/embeddings` answers 404 on the project-scoped Responses route and 200 on the account-level `/openai/v1`
    route of the same resource (2026-10-14), so the project segment is dropped unless overridden."""
    from app import llm
    monkeypatch.setenv("LUNA_FOUNDRY_BASE", "https://x.services.ai.azure.com/api/projects/proj-default/openai/v1")
    monkeypatch.delenv("LUVN_EMBEDDING_BASE", raising=False)
    assert llm.embedding_base() == "https://x.services.ai.azure.com/openai/v1"
    monkeypatch.setenv("LUVN_EMBEDDING_BASE", "https://x.openai.azure.com/openai/v1")
    assert llm.embedding_base() == "https://x.openai.azure.com/openai/v1"
