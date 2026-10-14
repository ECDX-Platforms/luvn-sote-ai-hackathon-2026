"""The batched, resumable load: commits, checkpoints, resume, and named rejects.

The fake here is stateful, unlike the stand-in in test_sqldelivery: it keeps committed rows apart from pending
ones, so a rollback really discards a batch and a resume really sees what was committed. It is still not an
engine; the engine's behaviour is measured in the cloud run, not asserted here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.sqldelivery import LoadRejected, LoadResumeMismatch, load_corpus

PROFILES = "source_person_id,persona_id,label\n" + "".join(f"k{i},p{i},Synthetic {i}\n" for i in range(1, 4))


def _record(i: int, person: int) -> dict:
    return {"record_id": f"r{i}", "source_person_id": f"k{person}", "record_type": "vapaateksti",
            "authored_by_role": "laakari", "recorded_at": "2026-01-01T00:00:00+00:00", "topic": "hoito",
            "sensitivity_tag": "none", "consent_scope": "treatment", "payload": {"teksti": f"t{i}"}}


def _corpus(tmp_path: Path, records: int = 10) -> Path:
    source = tmp_path / "corpus"
    source.mkdir()
    (source / "profiles.csv").write_text(PROFILES, encoding="utf-8")
    (source / "records.jsonl").write_text(
        "".join(json.dumps(_record(i, 1 + i % 3), ensure_ascii=False) + "\n" for i in range(1, records + 1)),
        encoding="utf-8")
    return source


class _Db:
    """Committed state, and a pending transaction that a rollback discards."""

    def __init__(self, fail_on_record: str | None = None) -> None:
        self.committed: dict[str, dict] = {"people": {}, "records": {}, "state": {}}
        self.pending: list[tuple[str, tuple]] = []
        self.fail_on_record = fail_on_record
        self.commits = 0

    def cursor(self):
        return _Cursor(self)

    def commit(self) -> None:
        for table, row in self.pending:
            self._apply(table, row)
        self.pending = []
        self.commits += 1

    def rollback(self) -> None:
        self.pending = []

    def _apply(self, table: str, row: tuple) -> None:
        if table == "people":
            if row[0] in self.committed["people"]:
                raise RuntimeError(f"duplicate people {row[0]}")
            self.committed["people"][row[0]] = row
        elif table == "records":
            if row[0] in self.committed["records"]:
                raise RuntimeError(f"Violation of PRIMARY KEY: duplicate key ({row[0]})")
            self.committed["records"][row[0]] = row
        elif table == "state":
            self.committed["state"]["row"] = row

    def close(self) -> None:
        pass


class _Cursor:
    fast_executemany = False

    def __init__(self, db: _Db) -> None:
        self.db = db
        self.result: list = []

    def execute(self, sql: str, *params) -> None:
        flat = " ".join(sql.split())
        if flat.startswith("INSERT INTO records"):
            if self.db.fail_on_record and params[0] == self.db.fail_on_record:
                raise RuntimeError(f"Violation of PRIMARY KEY: duplicate key ({params[0]})")
            self.db.pending.append(("records", params))
        elif flat.startswith("INSERT INTO people"):
            self.db.pending.append(("people", params))
        elif flat.startswith("INSERT INTO load_state"):
            self.db.pending.append(("state", (params[0], 0, None, 0)))
        elif flat.startswith("UPDATE load_state"):
            self.db.pending.append(("state", (params[3], params[0], params[1], params[2])))
        elif flat.startswith("DELETE FROM people") or flat.startswith("DELETE FROM records"):
            self.db.committed[flat.split()[-1]].clear()
        elif flat.startswith("DELETE FROM load_state"):
            self.db.committed["state"].clear()
        elif flat.startswith("SELECT generation, items_committed"):
            row = self.db.committed["state"].get("row")
            self.result = [row] if row else []
        elif flat.startswith("SAVE TRANSACTION") or flat.startswith("ROLLBACK TRANSACTION"):
            pass
        # Every other statement (DDL, deletes of the replaced corpus) is accepted and has no effect here.

    def executemany(self, sql: str, rows) -> None:
        for row in rows:
            self.execute(sql, *row)

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return self.result

    def close(self) -> None:
        pass


def _records(db: _Db) -> set[str]:
    return set(db.committed["records"])


def test_the_load_commits_per_batch_and_the_checkpoint_lands_with_each_batch(tmp_path: Path):
    db = _Db()
    counts = load_corpus(_corpus(tmp_path, records=10), db, batch_rows=4, rejects_path=tmp_path / "r.jsonl")
    assert _records(db) == {f"r{i}" for i in range(1, 11)}
    assert counts["records"] == 10 and counts["profiles"] == 3
    # schema, fresh reset, then 13 items in batches of 4 (4, 4, 4, 1 with the completion write): 2 + 4 commits.
    assert db.commits == 6
    assert db.committed["state"]["row"][3] == 1, "the final batch marks the checkpoint complete"


def test_a_failed_batch_is_rejected_by_name_and_nothing_after_it_is_written(tmp_path: Path):
    db = _Db(fail_on_record="r6")
    rejects = tmp_path / "rejects.jsonl"
    with pytest.raises(LoadRejected, match="rejected"):
        load_corpus(_corpus(tmp_path, records=10), db, batch_rows=4, rejects_path=rejects)
    named = [json.loads(line) for line in rejects.read_text(encoding="utf-8").splitlines()]
    assert [entry["key"] for entry in named] == ["record:r6"], "the rejected row is named, with no record content"
    assert "t6" not in rejects.read_text(encoding="utf-8"), "the reject file carries keys and errors, not content"
    assert "r6" not in _records(db), "the failed row never reached the committed state"
    assert db.committed["state"]["row"][1] < 13, "the checkpoint is before the failed batch, not past it"


def test_a_resume_after_a_failure_inserts_the_rest_and_never_the_committed_rows_again(tmp_path: Path):
    db = _Db(fail_on_record="r6")
    source = _corpus(tmp_path, records=10)
    with pytest.raises(LoadRejected):
        load_corpus(source, db, batch_rows=4, rejects_path=tmp_path / "r.jsonl")
    committed_before = set(_records(db))
    db.fail_on_record = None  # the row is fixed in the source, the database is the same
    counts = load_corpus(source, db, batch_rows=4, rejects_path=tmp_path / "r.jsonl")
    assert counts["resumed_after"] is not None and counts["resumed_after"] > 0
    assert _records(db) == {f"r{i}" for i in range(1, 11)}
    assert committed_before <= _records(db), "committed rows survive; nothing was re-inserted over them"


def test_a_changed_corpus_is_a_new_generation_and_never_resumes_the_old_checkpoint(tmp_path: Path):
    db = _Db(fail_on_record="r6")
    source = _corpus(tmp_path, records=10)
    with pytest.raises(LoadRejected):
        load_corpus(source, db, batch_rows=4, rejects_path=tmp_path / "r.jsonl")
    db.fail_on_record = None
    (source / "records.jsonl").write_text(
        "".join(json.dumps(_record(i, 1), ensure_ascii=False) + "\n" for i in range(1, 4)), encoding="utf-8")
    counts = load_corpus(source, db, batch_rows=4, rejects_path=tmp_path / "r.jsonl")
    assert counts["resumed_after"] is None, "a different generation starts over, it does not resume"
    assert _records(db) == {"r1", "r2", "r3"}, "the replaced stage holds only the new corpus"


def test_a_checkpoint_whose_item_no_longer_matches_refuses_to_resume(tmp_path: Path):
    db = _Db(fail_on_record="r6")
    source = _corpus(tmp_path, records=10)
    with pytest.raises(LoadRejected):
        load_corpus(source, db, batch_rows=4, rejects_path=tmp_path / "r.jsonl")
    row = db.committed["state"]["row"]
    db.committed["state"]["row"] = (row[0], row[1], "record:not-the-item", row[3])
    db.fail_on_record = None
    with pytest.raises(LoadResumeMismatch):
        load_corpus(source, db, batch_rows=4, rejects_path=tmp_path / "r.jsonl")
