"""The deletion job: it must remove what it can, refuse to guess, and never claim total erasure.

The last property is the one worth testing hardest. A deletion record is a document somebody points at when
they say the data is gone, so these tests pin that it NAMES the copies it cannot reach -- Azure SQL backups
and point-in-time restore, the narration sent to Microsoft Foundry, container logs in Log Analytics -- in
its first lines as well as in its structured list, and that the seal is described as a content digest rather
than a signature.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.deletion import (
    DELETE_CONFIRMATION, Attribution, DeletionError, delete_delivery_run, people_from_index,
)
from app.landing import land_delivery
from scripts import reconcile_delivery

NOW = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)


def _landing(tmp_path: Path) -> Path:
    """A real landing zone, written by the real `land_delivery`, with a derived report beside it."""
    raw = tmp_path / "delivery-src"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "mittaukset.csv").write_bytes("Henkilötunniste;Paino\nH-1;72,5\n".encode("latin-1"))
    landing = tmp_path / "landing"
    manifest = land_delivery(raw, landing)
    results = landing / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / "delivery-shape-report.json").write_text('{"kind": "delivery-shape"}\n', encoding="utf-8")
    assert manifest.manifest_hash
    return landing


def _import_index(tmp_path: Path, people: list[str] | dict[str, str], name: str = "import") -> Path:
    """A small import-shaped directory holding an index.sqlite with the run's people.

    `people` is either a list of source ids -- persona = source, the shape the OLD tests used and the
    shape every shipped corpus does NOT have -- or a `{source_person_id: persona_id}` mapping, which is
    what the real importer writes and what the id-confusion defect turned on.
    """
    pairs = ([(person, person) for person in people] if isinstance(people, list)
             else list(people.items()))
    directory = tmp_path / name
    (directory / "people").mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(directory / "index.sqlite")
    connection.execute("CREATE TABLE people (persona_id TEXT PRIMARY KEY, source_person_id TEXT UNIQUE, "
                       "label TEXT, path TEXT)")
    for source, persona in pairs:
        connection.execute("INSERT INTO people VALUES (?, ?, '', '')", (persona, source))
    connection.commit()
    connection.close()
    return directory


def _sql_stand_in(tmp_path: Path, people: list[str] | dict[str, str],
                  name: str = "corpus.sqlite") -> sqlite3.Connection:
    """A stand-in connection with the same three tables the loader writes to.

    `people` is a list of source ids (persona = source) or a `{source: persona}` mapping. The mapping
    form is the one that reproduces the teardown defect: `records.persona_id` and `people.persona_id`
    hold the persona id while `encounters.source_person_id` holds the source id.
    """
    pairs = ([(person, person) for person in people] if isinstance(people, list)
             else list(people.items()))
    connection = sqlite3.connect(tmp_path / name)
    connection.execute("CREATE TABLE people (persona_id TEXT PRIMARY KEY, source_person_id TEXT UNIQUE, "
                       "label TEXT)")
    # `records` carries BOTH keys, exactly as Azure SQL's schema does (`source_person_id` plus the
    # persona trigger that keeps them the same person), so the after-count can check the other id space.
    connection.execute("CREATE TABLE records (record_id TEXT PRIMARY KEY, persona_id TEXT, "
                       "source_person_id TEXT)")
    connection.execute("CREATE TABLE encounters (encounter_id TEXT PRIMARY KEY, source_person_id TEXT)")
    for source, persona in pairs:
        connection.execute("INSERT INTO people VALUES (?, ?, '')", (persona, source))
        connection.execute("INSERT INTO records VALUES (?, ?, ?)", (f"R-{persona}", persona, source))
        connection.execute("INSERT INTO encounters VALUES (?, ?)", (f"E-{source}", source))
    connection.commit()
    return connection


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("people", "records", "encounters")}


def _freeze_delete(connection: sqlite3.Connection, table: str):
    """A connection where `DELETE FROM <table>` is a no-op: "the delete ran and matched nothing".

    Written against the real cursor's public API rather than reusing it, because "the delete changed no
    rows" is precisely the condition the after-count has to be able to contradict; a wrapper that skipped
    the statement entirely would leave the real cursor mid-batch instead. It generalises the `records`-only
    wrapper the earlier regression test carries, so each table can be frozen on its own -- `encounters` in
    particular, which is the table the old after-count structurally could not see.
    """

    class _Connection:
        def __init__(self, real):
            self._real = real

        def cursor(self):
            return _Cursor(self._real.cursor())

        def __getattr__(self, name):
            return getattr(self._real, name)

    class _Cursor:
        def __init__(self, real):
            self._real = real

        def execute(self, sql, *params):
            if sql.startswith(f"DELETE FROM {table}"):
                self._real.execute("SELECT 1")
                return self._real
            return self._real.execute(sql, *params)

        def __getattr__(self, name):
            return getattr(self._real, name)

    return _Connection(connection)


def _bare_stand_in(tmp_path: Path, name: str, *,
                   encounters_source_column: str | None = "source_person_id",
                   encounters_persona_column: str | None = None,
                   people: list[tuple] | None = None,
                   encounters: list[tuple] | None = None) -> sqlite3.Connection:
    """Three tables, each row supplied verbatim, for the schema variants the fixed loop must reason about.

    `_sql_stand_in` always writes a well-formed row per person, which is right for the happy path; these
    tests need a *variant* identifier (whitespace, case, NULL) or a table whose identifier column is named
    so the after-count cannot recognise it, so the rows and the column spelling are given here instead of
    derived.
    """
    connection = sqlite3.connect(tmp_path / name)
    connection.execute("CREATE TABLE people (persona_id TEXT PRIMARY KEY, source_person_id TEXT UNIQUE, "
                       "label TEXT)")
    connection.execute("CREATE TABLE records (record_id TEXT PRIMARY KEY, persona_id TEXT, "
                       "source_person_id TEXT)")
    connection.execute("CREATE TABLE encounters (encounter_id TEXT PRIMARY KEY"
                       + (f", {encounters_source_column} TEXT" if encounters_source_column else "")
                       + (f", {encounters_persona_column} TEXT" if encounters_persona_column else "") + ")")
    for row in people or []:
        connection.execute("INSERT INTO people VALUES (?, ?, '')", row)
    for row in encounters or []:
        connection.execute("INSERT INTO encounters VALUES " + f"({','.join('?' for _ in row)})", row)
    connection.commit()
    return connection


def test_a_deletion_refuses_without_the_confirmation_word(tmp_path: Path):
    landing = _landing(tmp_path)
    with pytest.raises(DeletionError, match="refusing to delete without explicit confirmation"):
        delete_delivery_run(landing.name, landing_dir=landing)
    with pytest.raises(DeletionError, match="I_CONFIRM_DELETE"):
        delete_delivery_run(landing.name, landing_dir=landing, confirm="yes")
    # Nothing was touched by the refused call.
    assert (landing / "manifest.json").is_file()
    assert list((landing / "files").rglob("*"))


def test_it_removes_the_landed_bytes_the_manifest_and_the_derived_reports(tmp_path: Path):
    landing = _landing(tmp_path)
    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 tmp_dir=tmp_path / "tmp", now=NOW)

    assert not landing.exists(), "the run's landing zone is gone"
    kinds = {entry["kind"] for entry in record.document["removed"]}
    assert {"landing_bytes", "landing_manifest", "landing_reports"} <= kinds
    assert record.document["totals"]["files_removed"] >= 2
    assert record.document["totals"]["bytes_removed"] > 0
    assert record.raises() is False


def test_the_sql_step_removes_only_the_people_this_run_indexed(tmp_path: Path):
    landing = _landing(tmp_path)
    # persona_id != source_person_id, exactly as every shipped corpus has it. The old expectation here
    # used persona == source and so could not see the defect; the ids are now distinct on purpose.
    index = _import_index(tmp_path, {"H-1": "P-1", "H-2": "P-2"})
    connection = _sql_stand_in(tmp_path, {"H-1": "P-1", "H-2": "P-2", "SOMEONE-ELSE": "SOMEONE-ELSE"})
    assert people_from_index(index) == [Attribution(persona_id="P-1", source_person_id="H-1"),
                                        Attribution(persona_id="P-2", source_person_id="H-2")]

    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 connection=connection, indexes=[index], tmp_dir=tmp_path / "tmp", now=NOW)

    assert _counts(connection) == {"people": 1, "records": 1, "encounters": 1}, (
        "a person this run did not index must survive")
    sql = next(entry for entry in record.document["removed"] if entry["kind"] == "sql_rows")
    assert sql["status"] == "removed" and sql["counts"]["records_before"] == 2
    assert sql["counts"]["people_before"] == 2 and sql["counts"]["encounters_before"] == 2
    assert sql["counts"]["people_after"] == 0 and sql["counts"]["records_after"] == 0
    assert sql["counts"]["encounters_after"] == 0
    assert not index.exists(), "the run's own import output is removed too"
    assert record.document["totals"]["removed"] >= 1


def test_the_receipt_can_only_say_removed_when_neither_id_space_has_a_survivor(tmp_path: Path):
    """D1: the regression the review found. Every table is deleted and counted by the column it holds.

    Before the fix this deleted `records.persona_id IN (source ids)`, which matched nothing, deleted the
    encounters, committed, and then counted the same wrong column -- so `remaining == 0` and the receipt
    said `removed` while `people` and `records` still held the person. The database is asserted directly
    here, not only through the receipt, because the receipt was the thing that lied.
    """
    landing = _landing(tmp_path)
    index = _import_index(tmp_path, {"demo-source-01": "demo-persona-01"})
    connection = _sql_stand_in(tmp_path, {"demo-source-01": "demo-persona-01"})

    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 connection=connection, indexes=[index], tmp_dir=tmp_path / "tmp", now=NOW)

    assert _counts(connection) == {"people": 0, "records": 0, "encounters": 0}, (
        "the person and their records must be gone from ALL THREE tables, not just encounters")
    sql = next(entry for entry in record.document["removed"] if entry["kind"] == "sql_rows")
    assert sql["status"] == "removed"
    assert sql["counts"]["people_before"] == 1 and sql["counts"]["records_before"] == 1
    assert record.raises() is False


def test_a_surviving_row_under_either_identifier_makes_the_receipt_a_named_failure(tmp_path: Path):
    """The negative half: one table left behind must NOT read as `removed`, and must name the survivor.

    Simulated by making the `records` DELETE a no-op, which is exactly the old bug's shape (the delete
    targets the wrong column while the after-count uses the same one). The receipt has to fail and name
    `records` and the identifier space that still holds the row.
    """
    landing = _landing(tmp_path)
    index = _import_index(tmp_path, {"H-1": "P-1"})
    connection = _sql_stand_in(tmp_path, {"H-1": "P-1"})

    class _RecordsSurvive:
        """A connection where the `records` DELETE is a no-op: the old defect's shape, isolated.

        Written against the real cursor's public API rather than reusing it, because "the delete matched
        nothing" is precisely a delete that ran and changed no rows; a wrapper that skips the statement
        entirely would leave the real cursor mid-batch instead.
        """

        def __init__(self, real):
            self._real = real

        def cursor(self):
            return _Cursor(self._real.cursor())

        def __getattr__(self, name):
            return getattr(self._real, name)

    class _Cursor:
        def __init__(self, real):
            self._real = real

        def execute(self, sql, *params):
            if sql.startswith("DELETE FROM records"):
                self._real.execute("SELECT 1")
                return self._real
            return self._real.execute(sql, *params)

        def __getattr__(self, name):
            return getattr(self._real, name)

    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 connection=_RecordsSurvive(connection), indexes=[index],
                                 tmp_dir=tmp_path / "tmp", now=NOW)

    sql = next(entry for entry in record.document["removed"] if entry["kind"] == "sql_rows")
    assert sql["status"] == "failed", "a survivor must never be reported as `removed`"
    assert "records.persona_id" in sql["detail"] and "persona_id" in sql["detail"]
    # The receipt says where: the `records` table, under the persona_id column, and it names the one row.
    assert "records.persona_id holds 1 row(s)" in sql["detail"]
    assert sql["counts"]["records_before"] == 1, "the row was there before the delete"
    assert record.raises() is True
    assert _counts(connection)["records"] == 1, "the stand-in really did leave the row"


def test_without_attribution_it_refuses_to_guess_and_says_so(tmp_path: Path):
    landing = _landing(tmp_path)
    connection = _sql_stand_in(tmp_path, ["H-1"])
    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 connection=connection, tmp_dir=tmp_path / "tmp", now=NOW)

    assert _counts(connection) == {"people": 1, "records": 1, "encounters": 1}
    sql = next(entry for entry in record.document["removed"] if entry["kind"] == "sql_rows")
    assert sql["status"] == "not_attributable"
    assert "does not delete a shared table on a hunch" in sql["detail"]
    assert any(entry["cause"] == "not_attributable" and "sql_rows" in entry["copy"]
               for entry in record.document["not_reachable"])


def test_without_a_connection_the_sql_step_is_named_as_not_reached(tmp_path: Path):
    landing = _landing(tmp_path)
    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 person_ids=["H-1"], tmp_dir=tmp_path / "tmp", now=NOW)
    sql = next(entry for entry in record.document["removed"] if entry["kind"] == "sql_rows")
    assert sql["status"] == "not_reached" and "no SQL connection" in sql["detail"]


def test_the_record_names_the_copies_it_cannot_reach_in_its_first_lines():
    """The limitation clause is the deliverable: a reader must not mistake the record for proof."""
    # A deletion with nothing to remove still has to name what it cannot reach.
    record = delete_delivery_run("no-such-run", confirm=DELETE_CONFIRMATION, tmp_dir=None,
                                 delivery_dir=None, keep_tmp=True, now=NOW)

    text = record.text
    head = text.splitlines()
    assert ">>>" in head[4] and "NOT PROOF THAT THE DATA IS GONE" in head[4], (
        "the disclaimer is in the first lines, before any removal list")
    for needle in ("Azure SQL automated backups", "point-in-time", "Microsoft Foundry", "Log Analytics"):
        assert needle in text, f"the record must name {needle}"
    copies = {entry["copy"] for entry in record.document["not_reachable"]}
    assert any("point-in-time restore" in copy for copy in copies)
    assert any("Foundry" in copy for copy in copies)
    assert any("Log Analytics" in copy for copy in copies)
    assert any("Blob container" in copy for copy in copies), "the un-exercised Blob path is named too"
    assert record.document["not_proof_of_erasure"].startswith("THIS RECORD IS NOT PROOF")


def test_the_seal_is_a_content_digest_and_is_not_called_a_signature(tmp_path: Path):
    landing = _landing(tmp_path)
    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 tmp_dir=tmp_path / "tmp", now=NOW)
    seal = record.document["seal"]
    assert seal["algorithm"] == "sha256" and len(seal["digest"]) == 64
    assert "NOT a signature" in seal["note"] and "NOT evidence that any copy was erased" in seal["note"]
    # The digest really covers the record body: change one removal and the digest changes.
    from app.deletion import _seal  # noqa: PLC0415 - the sealing helper is what the test pins

    altered = json.loads(json.dumps(record.document))
    altered["removed"][0]["detail"] = "tampered"
    assert _seal(altered)["digest"] != seal["digest"]


def test_a_second_run_is_idempotent_and_says_what_it_found_already_absent(tmp_path: Path):
    landing = _landing(tmp_path)
    receipt_dir = tmp_path / "receipts"
    first = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                tmp_dir=tmp_path / "tmp", receipt_dir=receipt_dir, now=NOW)
    second = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 tmp_dir=tmp_path / "tmp", receipt_dir=receipt_dir, now=NOW)

    assert second.document["totals"]["removed"] == 0, "a re-run removes nothing new"
    assert second.document["totals"]["already_absent"] >= 1
    assert second.document["previous_record"]["digest"] == first.document["seal"]["digest"]
    assert "safe to re-run" in second.text
    assert first.json_path.is_file() and first.text_path.is_file()
    assert second.json_path.is_file()
    # A receipt for a different run does not overwrite this one.
    assert first.json_path.name == "delivery-run-deletion-landing.json"


def test_the_tmp_sweep_removes_only_this_repositorys_working_directories(tmp_path: Path):
    landing = _landing(tmp_path)
    tmp_dir = tmp_path / "tmp"
    (tmp_dir / "luvn-import-20261013").mkdir(parents=True)
    (tmp_dir / "luvn-import-20261013" / "records.jsonl").write_text("x\n", encoding="utf-8")
    (tmp_dir / "unrelated").mkdir()
    (tmp_dir / "luvn-rehearsal").mkdir()

    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 tmp_dir=tmp_dir, now=NOW)

    assert not (tmp_dir / "luvn-import-20261013").exists()
    assert not (tmp_dir / "luvn-rehearsal").exists()
    assert (tmp_dir / "unrelated").is_dir(), "a directory that is not ours survives"
    assert any(entry["kind"] == "tmp_working_copies" and entry["status"] == "removed"
               for entry in record.document["removed"])


def test_a_delivery_dir_working_copy_is_removed_and_a_config_free_run_says_so(tmp_path: Path):
    landing = _landing(tmp_path)
    working = tmp_path / "serving-copy"
    working.mkdir()
    (working / "index.sqlite").write_bytes(b"x")

    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 delivery_dir=working, tmp_dir=tmp_path / "tmp", now=NOW)
    assert not working.exists()
    assert any(entry["kind"] == "delivery_dir" and entry["status"] == "removed"
               for entry in record.document["removed"])

    bare = delete_delivery_run("nothing", confirm=DELETE_CONFIRMATION, delivery_dir=None,
                               tmp_dir=None, keep_tmp=True, now=NOW)
    entry = next(item for item in bare.document["removed"] if item["kind"] == "delivery_dir")
    assert entry["status"] == "not_reached" and "LUVN_DELIVERY_DIR" in entry["target"]


def test_a_configured_blob_container_that_cannot_be_reached_is_a_loud_failure(tmp_path: Path, monkeypatch):
    """The Blob path is written but cannot be exercised offline; a failure must be in the record."""
    landing = _landing(tmp_path)
    monkeypatch.delenv("LUVN_STORAGE_ACCOUNT_URL", raising=False)
    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 blob_container="delivery", tmp_dir=tmp_path / "tmp", now=NOW)
    blob = next(entry for entry in record.document["removed"] if entry["kind"] == "blob_container")
    assert blob["status"] == "failed"
    assert "LUVN_STORAGE_ACCOUNT_URL" in blob["detail"] or "No module named" in blob["detail"]
    assert record.raises() is True, "a configured container that failed must fail the job"


def test_refuses_to_delete_a_landing_zone_that_holds_a_different_run(tmp_path: Path):
    landing = _landing(tmp_path)
    with pytest.raises(DeletionError, match="refusing to delete"):
        delete_delivery_run("0000000000000000", landing_dir=landing, confirm=DELETE_CONFIRMATION,
                            tmp_dir=tmp_path / "tmp")
    assert (landing / "manifest.json").is_file()


def test_the_delete_cli_refuses_without_the_word_and_then_removes(tmp_path: Path, capsys):
    landing = _landing(tmp_path)
    with pytest.raises(SystemExit) as error:
        reconcile_delivery.main(["delete", landing.name, "--landing-dir", str(landing), "--confirm", "no"])
    assert error.value.code == 2
    assert (landing / "manifest.json").is_file()

    receipt = tmp_path / "receipts"
    status = reconcile_delivery.main(["delete", landing.name, "--landing-dir", str(landing),
                                      "--confirm", DELETE_CONFIRMATION, "--tmp-dir", str(tmp_path / "tmp"),
                                      "--receipt", str(receipt), "--keep-tmp"])
    captured = capsys.readouterr()
    assert status == 0
    assert not landing.exists()
    assert "NOT PROOF THAT THE DATA IS GONE" in captured.out
    assert (receipt / "delivery-run-deletion-landing.json").is_file()
    assert "receipt:" in captured.out


def test_a_run_named_by_its_full_path_is_accepted_and_receipt_named_readably(tmp_path: Path, capsys):
    """The CLI's own example names the run by its directory path; that must work, and read well."""
    landing = _landing(tmp_path)
    receipt = tmp_path / "receipts"
    status = reconcile_delivery.main(["delete", str(landing), "--confirm", DELETE_CONFIRMATION,
                                      "--receipt", str(receipt), "--tmp-dir", str(tmp_path / "tmp")])
    assert status == 0
    assert not landing.exists()
    assert (receipt / f"delivery-run-deletion-{landing.name}.json").is_file()
    assert str(landing) in capsys.readouterr().out


def test_a_re_run_by_the_same_path_reports_already_absent_not_attributable(tmp_path: Path):
    """Idempotency for the CLI's own argument form: the path is gone, the bytes are not 'unattributable'."""
    landing = _landing(tmp_path)
    delete_delivery_run(str(landing), confirm=DELETE_CONFIRMATION, tmp_dir=tmp_path / "tmp", now=NOW)
    again = delete_delivery_run(str(landing), confirm=DELETE_CONFIRMATION, tmp_dir=tmp_path / "tmp", now=NOW)
    entry = next(item for item in again.document["removed"] if item["kind"] == "landing_bytes")
    assert entry["status"] == "already_absent"
    assert "no bytes to attribute and none were deleted" in entry["detail"]
    assert again.document["totals"]["removed"] == 0


def test_a_bare_hash_that_resolves_nowhere_is_not_claimed_as_already_removed(tmp_path: Path):
    """A run id that is not a path must never be recorded as an absence: that would be a false claim."""
    record = delete_delivery_run("deadbeefdeadbeef", confirm=DELETE_CONFIRMATION, tmp_dir=None,
                                 delivery_dir=None, keep_tmp=True, now=NOW)
    entry = next(item for item in record.document["removed"] if item["kind"] == "landing_bytes")
    assert entry["status"] == "not_attributable"
    assert "no landed bytes could be found or attributed" in entry["detail"]


# --- the after-count has to be able to contradict the delete ------------------------------------------
#
# The audit found the earlier fix incomplete: `_sql_id_spaces` already returned each table's OWN delete
# column and id list, but the loop threw them away and probed `persona_id` twice. `encounters` is bound by
# `source_person_id` and has no `persona_id`, so both probes skipped, `encounters_after` stayed at its
# initialised zero, and the receipt said `removed` while the encounter rows were still in SQL. A `removed`
# receipt is evidence, so it may only be issued when every affected table was actually counted under every
# identifier space it has. The tests below are the ones that fail on that code.


def test_a_surviving_encounter_is_named_though_encounters_has_no_persona_id(tmp_path: Path):
    """`encounters` is the table the old loop structurally could not see, and a survivor must name it.

    `persona_id != source_person_id`, the delete for `encounters` is frozen so the row really survives, and
    the receipt must say `failed` naming `encounters.source_person_id` -- not `removed`, and not the
    initialised `encounters_after: 0` the old loop left behind.
    """
    landing = _landing(tmp_path)
    index = _import_index(tmp_path, {"H-1": "P-1"})
    connection = _sql_stand_in(tmp_path, {"H-1": "P-1"})
    assert connection.execute("SELECT COUNT(*) FROM encounters").fetchone()[0] == 1

    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 connection=_freeze_delete(connection, "encounters"), indexes=[index],
                                 tmp_dir=tmp_path / "tmp", now=NOW)

    sql = next(entry for entry in record.document["removed"] if entry["kind"] == "sql_rows")
    assert sql["status"] == "failed", "a surviving encounter must never read as `removed`"
    assert "encounters.source_person_id" in sql["detail"], (
        "the failure must name the table and the identifier space that still holds the row")
    assert sql["counts"]["encounters_after"] == 1, "this is a measured count, not the old fabricated zero"
    assert record.raises() is True
    assert _counts(connection)["encounters"] == 1, "the stand-in really did leave the encounter row"


def test_a_table_no_column_of_which_can_be_counted_is_never_removed(tmp_path: Path):
    """A table the job cannot count under ANY identifier column is unverified, and that is a `failed`.

    `_table_columns` compares the names an engine reports against the names this code knows, so a table whose
    `source_person_id` is reported in another case (an older schema shape, a differently-cased DDL) is one
    whose rows cannot be counted even though the case-insensitive before-count and DELETE both work. The
    `encounters` DELETE is frozen so the row really survives; the receipt must name
    `encounters.source_person_id`, and it must leave `encounters_after` ABSENT rather than a zero nothing
    measured -- the fabricated count this whole fix exists to forbid.
    """
    landing = _landing(tmp_path)
    index = _import_index(tmp_path, {"H-1": "P-1"})
    connection = _bare_stand_in(tmp_path, "unlistable.sqlite", encounters_source_column="SOURCE_PERSON_ID",
                                people=[("P-1", "H-1")], encounters=[("E-H-1", "H-1")])

    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 connection=_freeze_delete(connection, "encounters"), indexes=[index],
                                 tmp_dir=tmp_path / "tmp", now=NOW)

    sql = next(entry for entry in record.document["removed"] if entry["kind"] == "sql_rows")
    assert sql["status"] == "failed", "an uncountable table must never be treated as verified"
    assert "encounters.source_person_id" in sql["detail"]
    assert "encounters_after" not in sql["counts"], (
        "no count ran, so no count may be reported -- an unmeasured table is not zero")
    assert "encounters_after_unmeasured" in sql["counts"], "the receipt must show it was not measured"
    assert record.raises() is True
    assert _counts(connection)["encounters"] == 1, "the row survived and the receipt had to say so"


def test_a_surviving_encounter_with_a_trailing_space_is_not_a_removal(tmp_path: Path):
    """The audit's no-instrumentation repro: the identifier differs only by a trailing space.

    The delete's exact `IN` list cannot see `'H-1 '`, so the row survives. An after-count built from the
    same exact predicate cannot see it either, which is why the count predicate is now strictly wider.
    """
    landing = _landing(tmp_path)
    index = _import_index(tmp_path, {"H-1": "P-1"})
    connection = _bare_stand_in(tmp_path, "trailing.sqlite", people=[("P-1", "H-1")],
                                encounters=[("E-H-1", "H-1 ")])

    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 connection=connection, indexes=[index], tmp_dir=tmp_path / "tmp", now=NOW)

    sql = next(entry for entry in record.document["removed"] if entry["kind"] == "sql_rows")
    assert sql["status"] == "failed", "a whitespace-variant survivor must be visible to the after-count"
    assert "encounters.source_person_id" in sql["detail"]
    assert sql["counts"]["encounters_after"] == 1
    assert record.raises() is True
    assert _counts(connection)["encounters"] == 1, "the exact delete really did miss the padded row"


@pytest.mark.parametrize("stored", [("P-1 ", "H-1 "), ("p-1", "h-1"), (None, None)],
                         ids=["trailing-space", "case", "null"])
def test_a_whitespace_case_or_null_identifier_variant_is_not_a_removal(tmp_path: Path, stored):
    """Fix 2: the after-count must be strictly WIDER than the delete, or it can never contradict it.

    Each stored `people` row differs from the run's ids only by trailing whitespace, by case, or by being
    NULL. The exact delete (under both `persona_id` and `source_person_id`) misses all three, so each row
    survives; only a normalised, NULL-aware after-count can see it and keep the receipt off `removed`.
    """
    landing = _landing(tmp_path)
    index = _import_index(tmp_path, {"H-1": "P-1"})
    connection = _bare_stand_in(tmp_path, "variant.sqlite", people=[stored])

    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 connection=connection, indexes=[index], tmp_dir=tmp_path / "tmp", now=NOW)

    sql = next(entry for entry in record.document["removed"] if entry["kind"] == "sql_rows")
    assert sql["status"] == "failed", f"a {stored!r} identifier variant is still a survivor"
    assert "people.persona_id" in sql["detail"]
    # `people_after` sums a row once per id space it matched, so a row matching under both spaces is 2 here;
    # the per-space match counter is the exact measure, and it must be non-zero.
    assert sql["counts"]["rows_matched_by_persona_id"] == 1
    assert sql["counts"]["people_after"] >= 1
    assert record.raises() is True
    assert _counts(connection)["people"] == 1


def test_a_genuinely_clean_deletion_is_still_a_removal_with_every_count_measured(tmp_path: Path):
    """Non-vacuity: the fix must not make every deletion fail.

    All three `*_after` counts must be present and measured -- `encounters_after` in particular -- and the
    receipt must still say `removed` with `raises()` false when nothing survived.
    """
    landing = _landing(tmp_path)
    index = _import_index(tmp_path, {"H-1": "P-1"})
    connection = _sql_stand_in(tmp_path, {"H-1": "P-1"})

    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 connection=connection, indexes=[index], tmp_dir=tmp_path / "tmp", now=NOW)

    sql = next(entry for entry in record.document["removed"] if entry["kind"] == "sql_rows")
    assert sql["status"] == "removed"
    assert sql["counts"]["people_after"] == 0
    assert sql["counts"]["records_after"] == 0
    assert sql["counts"]["encounters_after"] == 0, "encounters was counted, and honestly zero"
    assert record.raises() is False
    assert _counts(connection) == {"people": 0, "records": 0, "encounters": 0}


def test_the_delete_cli_exits_non_zero_when_the_after_count_fails(tmp_path: Path, capsys, monkeypatch):
    """The exit status is the decision the operator's shell reads, so a failed verification must reach it.

    `run_delete` returns 1 when `record.raises()` is true. The surviving `encounters` row is the case the
    old loop could not see, and the CLI must not report success for it.
    """
    from app import sqldelivery  # noqa: PLC0415 - the CLI opens SQL through this module

    landing = _landing(tmp_path)
    index = _import_index(tmp_path, {"H-1": "P-1"})
    connection = _sql_stand_in(tmp_path, {"H-1": "P-1"})
    monkeypatch.setattr(sqldelivery, "connect",
                        lambda *args, **kwargs: _freeze_delete(connection, "encounters"))

    status = reconcile_delivery.main(["delete", landing.name, "--landing-dir", str(landing),
                                      "--confirm", DELETE_CONFIRMATION, "--import-dir", str(index),
                                      "--sql", "--tmp-dir", str(tmp_path / "tmp")])
    captured = capsys.readouterr()
    assert status == 1, "a failed SQL verification must exit non-zero"
    assert "at least one step FAILED" in captured.err
    assert "encounters.source_person_id" in captured.out


# --- a BARE STRING attribution must not be able to certify a half-deletion ---------------------------------
#
# `_attributions` treats a bare string as an id whose space the caller did not say, so it goes into both id
# spaces. On the shipped schema `encounters` has only `source_person_id`, so a string that is really a
# PERSONA id matched nothing there, the persona-space probe was skipped (the column does not exist), and the
# receipt said `removed` while the encounter row survived: string "P1" -> status=removed, remaining
# (people, encounters, records) = (0, 1, 0). These tests fail on that code.

def test_a_bare_persona_id_string_cannot_certify_removal_while_an_encounter_survives(tmp_path: Path):
    """The report's repro on the shipped shape: `person_ids=["P1"]` where P1 is the persona id.

    `encounters` is bound by `source_person_id` and has no `persona_id` at all, so the ambiguous value
    cannot be folded into a probe the table does not have -- and the receipt must therefore FAIL, not claim
    `removed`. The row really does survive; the database is asserted directly, because the receipt lied.
    """
    landing = _landing(tmp_path)
    connection = _bare_stand_in(tmp_path, "persona-id.sqlite", people=[("P1", "H1")],
                                encounters=[("E1", "H1")])

    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 connection=connection, person_ids=["P1"],
                                 tmp_dir=tmp_path / "tmp", now=NOW)

    sql = next(entry for entry in record.document["removed"] if entry["kind"] == "sql_rows")
    assert sql["status"] == "failed", (
        "a bare string whose persona-id reading leaves an encounter behind must never read as `removed`")
    assert "encounters.persona_id" in sql["detail"], (
        "the failure names the probe that could not run -- the persona column encounters does not have")
    assert "encounters.source_person_id" not in sql["detail"]
    # The source probe DID run and is an honest measured zero; the receipt still fails because the persona
    # space is unmeasurable, which is the property the old receipt violated by reporting a zero it had never
    # taken a probe for.
    assert sql["counts"]["encounters_after"] == 0
    assert sql["counts"]["encounters_after_unmeasured"] == 1
    assert record.raises() is True
    assert _counts(connection) == {"people": 0, "records": 0, "encounters": 1}, (
        "the encounter row really survived; the old receipt said removed anyway")


def test_a_bare_source_id_string_is_caught_by_the_column_that_holds_the_survivor(tmp_path: Path):
    """The mirror case the previous rounds already made honest: a bare SOURCE id.

    `H-1` is the source person id, so treating it as a persona id too leaves `records.persona_id` untouched
    while the DELETE for records matches nothing; the surviving record is caught by the persona-space probe
    and the receipt fails by name. The encounter IS deleted (`H-1` is its source id), and the receipt must
    say so rather than fabricate a survivor there.
    """
    landing = _landing(tmp_path)
    connection = _sql_stand_in(tmp_path, {"H-1": "P-1"})

    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 connection=connection, person_ids=["H-1"],
                                 tmp_dir=tmp_path / "tmp", now=NOW)

    sql = next(entry for entry in record.document["removed"] if entry["kind"] == "sql_rows")
    assert sql["status"] == "failed"
    # The record row survives because `H-1` is not a persona id; the encounter is gone because `H-1` is its
    # source id. The survivor is named by the column that holds it, and the encounter is an honest zero --
    # neither fact is fabricated.
    assert "records.source_person_id holds 1 row(s)" in sql["detail"]
    assert sql["counts"]["encounters_after"] == 0
    assert record.raises() is True
    assert _counts(connection) == {"people": 0, "records": 1, "encounters": 0}


def test_an_explicit_both_id_attribution_is_measured_under_both_spaces(tmp_path: Path):
    """The explicit form states both spaces, so nothing is ambiguous and every probe can run.

    `person_ids` does not carry an `Attribution` (the CLI and the public argument are strings), so the
    explicit shape is built the way the report does: one value in each space, which is exactly what an
    `Attribution` contributes. With both ids supplied, `records.persona_id` and `encounters.source_person_id`
    both match, both spaces are measured, and the receipt can say `removed` honestly.
    """
    landing = _landing(tmp_path)
    connection = _bare_stand_in(tmp_path, "both-ids.sqlite", people=[("P1", "H1")],
                                encounters=[("E1", "H1")])

    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 connection=connection, person_ids=["P1", "H1"],
                                 tmp_dir=tmp_path / "tmp", now=NOW)

    sql = next(entry for entry in record.document["removed"] if entry["kind"] == "sql_rows")
    assert sql["status"] == "failed", (
        "the shipped encounters table still cannot be probed under the persona space, so the ambiguous "
        "value that only matches in the other space leaves the receipt unverified -- it must not say removed")
    assert _counts(connection) == {"people": 0, "records": 0, "encounters": 0}


def test_the_ambiguous_both_column_shape_is_measured_and_still_removed(tmp_path: Path):
    """Where `encounters` HAS both columns, the ambiguous value is folded into both and the run is clean.

    This is the shape the fix's fold exists for: the string is applied to `source_person_id` and
    `persona_id`, both columns exist, so the delete and the after-count widen together and the receipt can
    say `removed` with every space measured. It also shows the failure above is about a missing column, not
    about string attributions being refused outright.
    """
    landing = _landing(tmp_path)
    connection = _bare_stand_in(tmp_path, "both-columns.sqlite", people=[("P1", "H1")],
                                encounters_persona_column="persona_id", encounters=[("E1", "H1", "P1")])

    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 connection=connection, person_ids=["P1", "H1"],
                                 tmp_dir=tmp_path / "tmp", now=NOW)

    sql = next(entry for entry in record.document["removed"] if entry["kind"] == "sql_rows")
    assert sql["status"] == "removed"
    assert sql["counts"]["encounters_after"] == 0
    assert record.raises() is False
    assert _counts(connection) == {"people": 0, "records": 0, "encounters": 0}


def test_the_clean_index_attribution_is_still_removed(tmp_path: Path):
    """Non-vacuity for the string fix: a run attributed from its own import index still says `removed`.

    The index supplies a real `Attribution` per person (both id spaces stated), so no value is ambiguous,
    no probe is required that the schema cannot supply, and the receipt keeps the `removed` the existing
    `test_a_genuinely_clean_deletion_is_still_a_removal_with_every_count_measured` pins.
    """
    landing = _landing(tmp_path)
    index = _import_index(tmp_path, {"H-1": "P-1"})
    connection = _sql_stand_in(tmp_path, {"H-1": "P-1"})

    record = delete_delivery_run(landing.name, landing_dir=landing, confirm=DELETE_CONFIRMATION,
                                 connection=connection, indexes=[index], tmp_dir=tmp_path / "tmp", now=NOW)

    sql = next(entry for entry in record.document["removed"] if entry["kind"] == "sql_rows")
    assert sql["status"] == "removed"
    assert sql["counts"]["people_after"] == 0
    assert sql["counts"]["records_after"] == 0
    assert sql["counts"]["encounters_after"] == 0
    assert record.raises() is False
    assert _counts(connection) == {"people": 0, "records": 0, "encounters": 0}
