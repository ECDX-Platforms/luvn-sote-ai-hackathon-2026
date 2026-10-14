"""The chunked, resumable corpus load, against a recording fake that enforces primary keys and rollback.

What these tests can and cannot show, stated once so nobody reads more into them:

* They prove the loader's ORDER and COUNTING logic: chunk sizes, commit counts, parents before children, a
  rejected row raising, a retry resuming after its checkpoint with no duplicate key, and a changed corpus
  starting fresh. The fake enforces the primary keys the real tables have and discards uncommitted work on
  rollback, so a double insert or a leaked uncommitted batch fails here.
* They do NOT measure the database. Nothing here is a throughput, a log-rate governor reading, a wait type or
  a timing. Those exist only on a live stage, and this file invents none of them.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app import delivery
from app.sqldelivery import (LOAD_BATCH, RETIRED_INDEX_STATEMENTS, LoadRejected, LoadResumeMismatch,
                             load_corpus)

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "data" / "experimental" / "delivery-sample"
PRIMARY_KEY = {"people": "persona_id", "encounters": "encounter_id", "records": "record_id"}


class _Refused(Exception):
    """A stand-in for the engine refusing a row (a constraint, a duplicate key, a trigger)."""


class _FakeDatabase:
    """Committed tables, a pending transaction, and the commit log. Enough of T-SQL for `load_corpus`.

    `reject` is a predicate over (table, key) that makes the engine refuse that row, the way a planted row
    would. A duplicate primary key is refused the same way, because that is what the real tables do.
    """

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, tuple]] = {name: {} for name in PRIMARY_KEY}
        self.state: tuple | None = None
        self.pending: list[tuple] = []
        self.commit_log: list[list[tuple[str, str]]] = []
        self.executemany_sizes: list[int] = []
        self.rollbacks = 0
        self.reject = lambda table, key: False

    def view(self, table: str) -> dict[str, tuple]:
        """What the table holds if the pending transaction committed now."""
        rows = dict(self.tables[table])
        for op in self.pending:
            if op[0] == "clear" and op[1] == table:
                rows.clear()
            elif op[0] == "put" and op[1] == table:
                rows[op[2]] = op[3]
        return rows

    def counts(self) -> dict[str, int]:
        return {name: len(rows) for name, rows in self.tables.items()}

    def commit(self) -> None:
        written = []
        for op in self.pending:
            if op[0] == "clear":
                self.tables[op[1]].clear()
            elif op[0] == "put":
                self.tables[op[1]][op[2]] = op[3]
                written.append((op[1], op[2]))
            elif op[0] == "state":
                self.state = op[1]
        self.pending = []
        self.commit_log.append(written)

    def rollback(self) -> None:
        self.pending = []
        self.rollbacks += 1


# The statements the loader issues, written out as literals so the fake matches exactly what runs.
_TABLE_STATEMENTS = (
    ("people", "INSERT INTO people ", "DELETE FROM people"),
    ("encounters", "INSERT INTO encounters ", "DELETE FROM encounters"),
    ("records", "INSERT INTO records ", "DELETE FROM records"),
)


def _state(db: _FakeDatabase) -> tuple:
    assert db.state is not None, "the checkpoint was never written"
    return db.state


class _FakeCursor:
    def __init__(self, database: _FakeDatabase) -> None:
        self.db = database
        self.fast_executemany = False
        self._result: list[tuple] = []

    def execute(self, sql: str, *params) -> None:
        db = self.db
        flat = " ".join(sql.split())
        if flat.startswith("SELECT generation, items_committed"):
            self._result = [db.state] if db.state is not None else []
            return
        for table, insert, clear in _TABLE_STATEMENTS:
            if flat.startswith(insert):
                key = params[0]
                if self.db.reject(table, key):
                    raise _Refused(f"engine refused {table} {key!r}")
                if key in db.view(table):
                    raise _Refused(f"duplicate primary key in {table}: {key!r}")
                db.pending.append(("put", table, key, tuple(params)))
                return
            if flat == clear:
                db.pending.append(("clear", table))
                return
        if flat == "DELETE FROM load_state":
            db.pending.append(("state", None))
        elif flat.startswith("INSERT INTO load_state"):
            db.pending.append(("state", (params[0], 0, None, False)))
        elif flat.startswith("UPDATE load_state"):
            items, last_key, complete, generation = params
            db.pending.append(("state", (generation, items, last_key, bool(complete))))
        # Schema guards, DROP, and the savepoint statements of the rejects replay change nothing here.

    def executemany(self, sql: str, rows) -> None:
        rows = list(rows)
        self.db.executemany_sizes.append(len(rows))
        for row in rows:
            self.execute(sql, *row)

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self) -> list[tuple]:
        return self._result

    def close(self) -> None:
        pass


class _FakeConnection:
    def __init__(self, database: _FakeDatabase) -> None:
        self.db = database

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.db)

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()

    def close(self) -> None:
        pass


def _expected_items(source: Path) -> dict[str, int]:
    """Counted from the files themselves, not from the loader's answer, so the test cannot agree with a bug."""
    profiles = {line.split(",")[0] for line in (source / "profiles.csv").read_text(encoding="utf-8").splitlines()[1:]
                if line.strip()}
    encounters = [line for line in (source / "encounters.csv").read_text(encoding="utf-8").splitlines()[1:]
                  if line.strip()]
    records = [line for line in (source / "records.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"people": len(profiles), "encounters": len(encounters), "records": len(records)}


def _copy_sample(tmp_path: Path) -> Path:
    source = tmp_path / "corpus"
    shutil.copytree(SAMPLE, source)
    return source


def _record_ids(source: Path) -> list[str]:
    return [json.loads(line)["record_id"] for line in (source / "records.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _load(db: _FakeDatabase, source: Path, tmp_path: Path, batch_rows: int, **kwargs):
    return load_corpus(source, _FakeConnection(db), batch_rows=batch_rows,
                       rejects_path=tmp_path / "rejects.jsonl", **kwargs)


def test_the_default_chunk_sits_in_the_measured_band_and_is_what_the_loader_uses():
    """The order asks for 1,000 to 5,000 rows per commit. The value is a reasoned choice, not a measurement."""
    assert 1000 <= LOAD_BATCH <= 5000
    import inspect

    assert inspect.signature(load_corpus).parameters["batch_rows"].default is LOAD_BATCH


def test_rows_are_committed_in_chunks_with_the_commit_count_the_chunk_size_implies(tmp_path: Path):
    source = _copy_sample(tmp_path)
    items = sum(_expected_items(source).values())
    db = _FakeDatabase()
    result = _load(db, source, tmp_path, batch_rows=4)

    # Every chunk is at most the batch size, and the rows are all written, once each.
    assert db.executemany_sizes and max(db.executemany_sizes) <= 4
    assert sum(db.executemany_sizes) == items
    # Commits: the schema, the fresh-start reset, then one per chunk, the last being the (possibly empty) tail.
    assert result["batches"] == items // 4 + 1
    assert len(db.commit_log) == 2 + result["batches"]
    assert [len(written) for written in db.commit_log[2:]][:-1] == [4] * (items // 4)


def test_the_row_counts_per_table_equal_the_manifest_the_loader_read(tmp_path: Path):
    source = _copy_sample(tmp_path)
    db = _FakeDatabase()
    result = _load(db, source, tmp_path, batch_rows=4)
    expected = _expected_items(source)
    assert db.counts() == expected
    assert (result["profiles"], result["encounters"], result["records"]) == (
        expected["people"], expected["encounters"], expected["records"])


def test_parents_are_committed_no_later_than_the_children_that_reference_them(tmp_path: Path):
    source = _copy_sample(tmp_path)
    db = _FakeDatabase()
    _load(db, source, tmp_path, batch_rows=3)
    committed_people: set[str] = set()
    for written in db.commit_log:
        for table, key in written:
            if table == "records":
                persona = db.tables["records"][key][2]
                # The persona this record names was committed in this chunk or an earlier one.
                assert persona in committed_people | {p for t, p in written if t == "people"}
        committed_people.update(key for table, key in written if table == "people")


def test_a_rejected_row_raises_and_is_named_not_skipped(tmp_path: Path):
    source = _copy_sample(tmp_path)
    victim = _record_ids(source)[5]
    db = _FakeDatabase()
    db.reject = lambda table, key: table == "records" and key == victim

    with pytest.raises(LoadRejected) as raised:
        _load(db, source, tmp_path, batch_rows=4)

    assert victim in str(raised.value) or "rejected" in str(raised.value)
    rejects = [json.loads(line) for line in (tmp_path / "rejects.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [entry["key"] for entry in rejects] == [f"record:{victim}"]
    # The rejected batch rolled back whole, so none of its rows, and not the victim, reached the database.
    assert victim not in db.tables["records"]
    assert db.rollbacks >= 1
    # The checkpoint still names a batch that did land, and the load is not marked complete.
    assert db.state is not None and db.state[3] is False


def test_a_row_that_does_not_read_is_a_hard_failure_not_a_skipped_line(tmp_path: Path):
    source = _copy_sample(tmp_path)
    lines = (source / "records.jsonl").read_text(encoding="utf-8").splitlines()
    lines.insert(3, "{not json")
    (source / "records.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    db = _FakeDatabase()
    with pytest.raises(delivery.DeliveryShapeError):
        _load(db, source, tmp_path, batch_rows=4)
    assert db.state is None or db.state[3] is False


def test_a_retry_resumes_after_the_checkpoint_with_no_duplicate_and_the_full_count(tmp_path: Path):
    source = _copy_sample(tmp_path)
    expected = _expected_items(source)
    order = _reference_order(source, tmp_path)
    victim = _record_ids(source)[5]
    db = _FakeDatabase()
    db.reject = lambda table, key: table == "records" and key == victim
    with pytest.raises(LoadRejected):
        _load(db, source, tmp_path, batch_rows=4)

    checkpoint_items = _state(db)[1]
    assert checkpoint_items == (order.index(f"record:{victim}")) // 4 * 4
    db.reject = lambda table, key: False
    result = _load(db, source, tmp_path, batch_rows=4)

    # The retry picks up exactly after the checkpoint, and writes nothing that was already committed (the fake
    # would refuse a duplicate key, so reaching the end without a refusal is the proof).
    assert result["resumed_after"] == checkpoint_items
    assert db.counts() == expected
    assert _state(db)[3] is True


def test_a_resume_refuses_when_the_checkpoint_key_no_longer_names_the_same_item(tmp_path: Path):
    source = _copy_sample(tmp_path)
    victim = _record_ids(source)[5]
    db = _FakeDatabase()
    db.reject = lambda table, key: table == "records" and key == victim
    with pytest.raises(LoadRejected):
        _load(db, source, tmp_path, batch_rows=4)
    before = db.counts()
    generation, items, _last_key, complete = _state(db)
    db.state = (generation, items, "record:not-in-the-corpus", complete)
    db.reject = lambda table, key: False

    with pytest.raises(LoadResumeMismatch):
        _load(db, source, tmp_path, batch_rows=4)
    assert db.counts() == before, "a refused resume must not write a row"


def test_a_changed_corpus_starts_fresh_and_does_not_splice_onto_the_old_checkpoint(tmp_path: Path):
    source = _copy_sample(tmp_path)
    db = _FakeDatabase()
    first = _load(db, source, tmp_path, batch_rows=4)
    assert first["resumed_after"] is None

    # A different byte in one input is a different generation: the stage is replaced, not resumed.
    (source / "profiles.csv").write_text((source / "profiles.csv").read_text(encoding="utf-8") + "\n",
                                         encoding="utf-8")
    second = _load(db, source, tmp_path, batch_rows=4)
    assert second["generation"] != first["generation"]
    assert second["resumed_after"] is None
    assert db.counts() == _expected_items(source)


def test_a_completed_generation_loaded_again_writes_nothing_new(tmp_path: Path):
    source = _copy_sample(tmp_path)
    db = _FakeDatabase()
    _load(db, source, tmp_path, batch_rows=4)
    commits_before = len(db.commit_log)
    again = _load(db, source, tmp_path, batch_rows=4)
    # A complete checkpoint reports no resume point (None): nothing is re-read into the database.
    assert again["resumed_after"] is None
    assert _state(db)[3] is True
    assert db.counts() == _expected_items(source)
    # The schema commit and the one (empty, complete) checkpoint write; no chunk of rows is written again.
    assert again["batches"] == 1 and len(db.commit_log) == commits_before + 2


def test_the_progress_line_names_rows_chunk_and_commit_count_after_each_commit(tmp_path: Path, capsys):
    source = _copy_sample(tmp_path)
    db = _FakeDatabase()
    result = _load(db, source, tmp_path, batch_rows=4)
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("load_corpus: chunk ")]
    assert len(lines) == result["batches"]
    assert lines[-1].startswith(f"load_corpus: chunk {result['batches']} committed; rows committed this run "
                                f"{result['items_seen']}")
    assert f"data commits {result['batches']}" in lines[-1]


def _reference_order(source: Path, tmp_path: Path) -> list[str]:
    """The plan order of a clean load, as `kind:key` strings, so a resume can be checked against it."""
    db = _FakeDatabase()
    _load(db, source, tmp_path, batch_rows=10_000)
    order: list[str] = []
    for written in db.commit_log:
        order.extend(f"{'person' if t == 'people' else 'encounter' if t == 'encounters' else 'record'}:{k}"
                     for t, k in written)
    return order


class _LoggingCursor(_FakeCursor):
    """The fake cursor, plus the text of every statement the loader issues, in order."""

    def __init__(self, database: _FakeDatabase, log: list[str]) -> None:
        super().__init__(database)
        self.log = log

    def execute(self, sql: str, *params) -> None:
        self.log.append(" ".join(sql.split()))
        super().execute(sql, *params)


def test_a_plain_load_retires_the_dead_indexes_and_never_creates_them(tmp_path: Path):
    """The retired indexes come off on EVERY load, not only `--recreate`, and no load creates them again.

    The fake engine does not hold a catalogue, so this proves what is offline-provable: the DROP statements
    are issued by a plain load (recreate=False, the default) and no CREATE for a retired index is ever
    issued. The catalogue itself is checked by `load_sql --verify` against the stage database.
    """
    db = _FakeDatabase()
    log: list[str] = []

    class _Connection(_FakeConnection):
        def cursor(self) -> _LoggingCursor:
            return _LoggingCursor(self.db, log)

    counts = load_corpus(_copy_sample(tmp_path), _Connection(db), batch_rows=4,
                         rejects_path=tmp_path / "rejects.jsonl")
    assert counts["records"] > 0
    for statement in RETIRED_INDEX_STATEMENTS:
        assert statement in log, statement
    for name in ("ix_records_person_covering", "ix_records_persona_encounter", "ix_records_case"):
        assert not any(line.startswith("CREATE") and name in line for line in log), name
    # The drops sit in the schema path, before the schema commit, not after the data.
    first_drop = min(next(i for i, line in enumerate(log) if line == statement) for statement in RETIRED_INDEX_STATEMENTS)
    first_insert = next(i for i, line in enumerate(log) if line.startswith("INSERT INTO people "))
    assert first_drop < first_insert
