"""`load_knowledge`: the chunks, the batched embedding and the one-transaction load, with `llm.embed` stubbed."""

from __future__ import annotations

import json

import pytest

from app import guidance, knowledge_forms, knowledge_search, llm, load_knowledge, pathways, sqldelivery


class _Cursor:
    def __init__(self, log, answers):
        self.log, self.answers, self.description = log, answers, None

    def execute(self, sql, *params):
        self.log.append(("execute", sql, params))
        self._last = sql

    def executemany(self, sql, rows):
        self.log.append(("executemany", sql, list(rows)))

    def fetchone(self):
        self.description = [(name,) for name in sqldelivery.KNOWLEDGE_INTEGRITY_EXPECTED]
        return tuple(self.answers["integrity"])

    def fetchall(self):
        return self.answers["counts"] if "COUNT(*) FROM" in self._last and "GROUP BY kind" in self._last \
            else self.answers["versions"]

    def close(self):
        self.log.append(("cursor-closed", None, None))


class _Connection:
    def __init__(self, answers=None, fail_on=None):
        self.log, self.answers, self.fail_on = [], answers or {}, fail_on

    def cursor(self):
        cursor = _Cursor(self.log, self.answers)
        if self.fail_on:
            original = cursor.executemany

            def failing(sql, rows):
                original(sql, rows)
                raise RuntimeError(self.fail_on)
            cursor.executemany = failing
        return cursor

    def commit(self):
        self.log.append(("commit", None, None))

    def rollback(self):
        self.log.append(("rollback", None, None))

    def close(self):
        self.log.append(("connection-closed", None, None))


def test_the_chunks_are_every_committed_item_once_and_every_id_resolves():
    chunks = load_knowledge.chunks()
    by_kind = {}
    for chunk in chunks:
        by_kind[chunk.kind] = by_kind.get(chunk.kind, 0) + 1
    steps = sum(len(entry.get("steps", [])) for entry in pathways.load_corpus()["entries"])
    assert by_kind == {"form": len(knowledge_forms.items()), "guideline": len(guidance.GUIDELINES), "pathway": steps}
    assert by_kind["form"] == 486 and by_kind["guideline"] == 18
    assert len({chunk.id for chunk in chunks}) == len(chunks)
    for chunk in chunks:
        hit = knowledge_search.resolve(chunk.id)
        assert hit is not None and hit.kind == chunk.kind, chunk.id
        assert len(chunk.source) <= 64 and len(chunk.id) <= 128 and len(chunk.kind) <= 16, chunk.id
    item = knowledge_forms.items()[0]
    form = next(chunk for chunk in chunks if chunk.id == item.as_ref())
    assert form.text == f"{item.form_name} · {item.category_name} · {item.question_text}"
    guideline = next(chunk for chunk in chunks if chunk.id == "guideline:hoi40020")
    assert guideline.text.startswith("Tupakka- ja nikotiiniriippuvuuden ehkäisy ja hoito · tupakka, nikotiini")


def test_embedding_runs_in_batches_of_64_and_refuses_the_wrong_width():
    calls = []

    def embed(texts):
        calls.append(len(texts))
        return [[0.1] * 1536 for _ in texts]

    texts = [f"t{i}" for i in range(150)]
    vectors = load_knowledge.embed_all(texts, embed=embed)
    assert calls == [64, 64, 22] and len(vectors) == 150
    with pytest.raises(load_knowledge.LoadRefused):
        load_knowledge.embed_all(["x"], embed=lambda texts: [[0.1] * 3072])
    with pytest.raises(ValueError):
        load_knowledge.embed_all(["x", "y"], embed=lambda texts: [[0.1] * 1536])


def test_the_load_is_guarded_create_then_delete_then_insert_in_one_transaction():
    chunks = load_knowledge.chunks()[:3]
    vectors = [[float(i)] * 1536 for i in range(3)]
    connection = _Connection()
    written = load_knowledge.load(connection, chunks, vectors, embedding_model="text-embedding-3-large")
    assert written == 3
    kinds = [entry[0] for entry in connection.log]
    assert kinds == ["execute", "execute", "executemany", "commit", "cursor-closed"]
    create, delete = connection.log[0][1], connection.log[1][1]
    assert create.startswith("IF OBJECT_ID(N'knowledge_chunks', N'U') IS NULL EXEC(N'CREATE TABLE knowledge_chunks")
    assert delete == "DELETE FROM knowledge_chunks"
    sql, rows = connection.log[2][1], connection.log[2][2]
    assert sql == sqldelivery.KNOWLEDGE_INSERT and len(rows) == 3
    assert rows[0][:4] == (chunks[0].id, chunks[0].kind, chunks[0].source, chunks[0].text)
    assert json.loads(rows[1][4]) == [1.0] * 1536
    assert rows[0][5:] == ("text-embedding-3-large", knowledge_forms.SNAPSHOT_VERSION)
    assert "DROP TABLE" not in " ".join(entry[1] or "" for entry in connection.log)


def test_recreate_drops_first_and_a_failed_insert_rolls_back():
    chunks = load_knowledge.chunks()[:2]
    vectors = [[0.0] * 1536] * 2
    connection = _Connection()
    load_knowledge.load(connection, chunks, vectors, embedding_model="m", recreate=True)
    assert connection.log[0][1] == "DROP TABLE IF EXISTS knowledge_chunks"
    failing = _Connection(fail_on="disk full")
    with pytest.raises(RuntimeError):
        load_knowledge.load(failing, chunks, vectors, embedding_model="m")
    assert [entry[0] for entry in failing.log][-2:] == ["rollback", "cursor-closed"]
    assert "commit" not in [entry[0] for entry in failing.log]
    with pytest.raises(ValueError):
        load_knowledge.load(_Connection(), chunks, vectors[:1], embedding_model="m")


def test_verify_compares_the_database_to_the_snapshots(monkeypatch):
    monkeypatch.delenv("LUVN_EMBEDDING_DEPLOYMENT", raising=False)
    expected = {}
    for chunk in load_knowledge.chunks():
        expected[chunk.kind] = expected.get(chunk.kind, 0) + 1
    whole = _Connection({"integrity": (1, 1, 1),
                         "counts": [(kind, count) for kind, count in sorted(expected.items())],
                         "versions": [(knowledge_forms.SNAPSHOT_VERSION, "text-embedding-3-large", 514, "2026-10-14")]})
    state = load_knowledge.verify(whole)
    assert state["ok"] and state["counts"] == expected and state["problems"] == []
    stale = _Connection({"integrity": (1, 1, 1), "counts": [("form", 10)],
                         "versions": [("luvn-form-knowledge-v0", "m", 10, "t")]})
    state = load_knowledge.verify(stale)
    assert not state["ok"] and len(state["problems"]) == 2
    absent = _Connection({"integrity": (0, 0, 0), "counts": [], "versions": []})
    state = load_knowledge.verify(absent)
    assert not state["ok"] and state["counts"] == {} and state["problems"][0].startswith("catalogue")
    monkeypatch.setenv("LUVN_EMBEDDING_DEPLOYMENT", "other-model")
    assert any("configured deployment" in p for p in load_knowledge.verify(whole)["problems"])


def test_main_refuses_to_load_without_a_deployment_or_with_the_wrong_width(monkeypatch, capsys):
    for name in ("LUVN_EMBEDDING_DEPLOYMENT", "LUNA_FOUNDRY_BASE", "LUNA_FOUNDRY_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(load_knowledge, "connect", lambda **kwargs: pytest.fail("no connection without a deployment"))
    assert load_knowledge.main([]) == 2
    assert "LUVN_EMBEDDING_DEPLOYMENT" in capsys.readouterr().err
    monkeypatch.setenv("LUVN_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
    monkeypatch.setenv("LUNA_FOUNDRY_BASE", "https://example.invalid/openai/v1")
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "k")
    monkeypatch.setenv("LUVN_EMBEDDING_DIMENSIONS", "3072")
    assert load_knowledge.main([]) == 2
    assert "VECTOR(1536)" in capsys.readouterr().err


def test_main_loads_every_chunk_with_the_configured_deployment(monkeypatch, capsys):
    monkeypatch.setenv("LUVN_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
    monkeypatch.setenv("LUNA_FOUNDRY_BASE", "https://example.invalid/openai/v1")
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "k")
    monkeypatch.delenv("LUVN_EMBEDDING_DIMENSIONS", raising=False)
    monkeypatch.setattr(llm, "embed", lambda texts: [[0.2] * 1536 for _ in texts])
    connection = _Connection()
    monkeypatch.setattr(load_knowledge, "connect", lambda **kwargs: connection)
    assert load_knowledge.main(["--server", "s", "--database", "d"]) == 0
    inserted = sum(len(entry[2]) for entry in connection.log if entry[0] == "executemany")
    assert inserted == len(load_knowledge.chunks()) == 514
    assert connection.log[-1][0] == "connection-closed" and "commit" in [e[0] for e in connection.log]
    out = capsys.readouterr().out
    assert "wrote 514 chunks" in out and knowledge_forms.SNAPSHOT_VERSION in out
