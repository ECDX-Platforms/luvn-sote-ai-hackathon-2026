"""Offline tests for the SQL integrity definitions.

The DDL cannot be executed in this gate (there is no ODBC driver and no database), so what is pinned here
is the *shape* that makes it safe: every schema statement is guarded by the object it creates (never by a
substring of its SQL), every hardening statement is existence-guarded and re-runnable, the foreign keys
point at tables the schema actually creates, the delete order is children-first, the envelope checks are
non-vacuous, and the clustered-index move is idempotent against a cursor that reports it already happened.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app import sqldelivery
from app.sqldelivery import (
    DROP_SCHEMA,
    HARDEN_STATEMENTS,
    INTEGRITY_EXPECTED,
    INTEGRITY_QUERY,
    KNOWLEDGE_DROP_STATEMENTS,
    KNOWLEDGE_INSERT,
    KNOWLEDGE_INTEGRITY_EXPECTED,
    KNOWLEDGE_INTEGRITY_QUERY,
    KNOWLEDGE_REPLACE_STATEMENTS,
    KNOWLEDGE_SCHEMA_STATEMENTS,
    KNOWLEDGE_VECTOR_DIMENSIONS,
    RECLUSTER_STATEMENT,
    REPLACE_STATEMENTS,
    RETIRED_INDEX_STATEMENTS,
    SCHEMA,
    SCHEMA_STATEMENTS,
    hardening_sql,
    verify_integrity,
)
from app.upm import Record

RETIRED_INDEXES = ("ix_records_person_covering", "ix_records_persona_encounter", "ix_records_case")


def test_every_hardening_statement_is_existence_guarded():
    """A hardening step that is not guarded would fail the second time it runs, and loads are repeatable.

    The guard is now two conditions for a table-scoped object ("the table EXISTS and my object does not"),
    because `load_sql --harden` runs the migration before the first load, when the tables may not exist.
    """
    assert HARDEN_STATEMENTS
    for label, statement in HARDEN_STATEMENTS:
        upper = statement.lstrip().upper()
        assert upper.startswith("IF "), label
        assert any(token in upper for token in ("OBJECT_ID", "NOT EXISTS", "IS NULL")), label


def test_every_schema_statement_is_guarded_by_the_object_it_creates():
    """D1: the derived guard names the statement's OWN object, never an unrelated one.

    The old loader parsed the SQL text (`split("TABLE ")[1]`), so every `ALTER TABLE ... ADD CONSTRAINT
    fk_*` was guarded on the table and silently skipped, and every CREATE INDEX was guarded on the
    never-existing `ix_records_person` and re-run until SQL Server refused it (Msg 1913).
    """
    assert SCHEMA_STATEMENTS
    names = [entry.name for entry in SCHEMA_STATEMENTS]
    assert len(names) == len(set(names))
    for entry in SCHEMA_STATEMENTS:
        guard = entry.guard
        assert f"N'{entry.name}'" in guard, entry.name
        for other in names:
            if other in (entry.name, entry.table):
                continue
            assert f"N'{other}'" not in guard, (entry.name, "leaks", other)
        # It must not be the old substring test over the statement text, and not a bare DDL.
        assert "TABLE " not in guard
        assert "IS NULL" in guard or "NOT EXISTS" in guard
        assert entry.guarded == f"{guard} EXEC(N'{sqldelivery._quote_literal(entry.statement)}')"


def test_an_add_constraint_guards_on_the_constraint_not_a_table_or_an_index():
    """The exact defect: `ALTER TABLE records ADD CONSTRAINT fk_records_people_source` was skipped forever."""
    entry = next(e for e in SCHEMA_STATEMENTS if e.name == "fk_records_people_source")
    assert "fk_records_people_source" in entry.guard
    assert "ix_records_person" not in entry.guard
    assert "fk_encounters_people" not in entry.guard
    # Specifically NOT the table-existence guard the old loader derived from the word "TABLE".
    assert "OBJECT_ID(N'records', N'U') IS NULL" not in entry.guard


def test_the_schema_creates_every_table_the_hardening_references():
    created = {part.split(" ", 2)[2].split(" ")[0]
               for part in SCHEMA if part.upper().startswith("CREATE TABLE")}
    assert created == {"people", "encounters", "records", "appointments", "briefings"}
    for _label, statement in HARDEN_STATEMENTS:
        for table in ("people", "encounters", "records", "appointments", "briefings"):
            if f"REFERENCES {table}" in statement or f"OBJECT_ID('{table}')" in statement:
                assert table in created


def test_the_foreign_keys_are_removed_before_their_parents():
    """Children first, or the DELETEs on the next load fail against the new constraints."""
    order = [statement.split()[-1] for statement in REPLACE_STATEMENTS]
    assert order == ["briefings", "appointments", "records", "encounters", "people"]


def test_a_fresh_schema_clusters_records_on_the_read_path():
    joined = " ".join(SCHEMA)
    assert "PRIMARY KEY NONCLUSTERED" in joined
    assert "CREATE UNIQUE CLUSTERED INDEX cx_records ON records (source_person_id, record_id)" in joined
    # The redundant single-column index must not come back on a fresh load.
    assert "ix_records_person " not in joined
    assert "ix_records_person_covering" not in joined   # retired: redundant with cx_records


def test_a_fresh_schema_carries_the_envelope_checks_and_the_keys():
    joined = " ".join(SCHEMA)
    assert "ISJSON(record_json) = 1" in joined
    assert "record_json IS NOT NULL AND" in joined          # the NULL guard: ISJSON(NULL) is NULL
    assert joined.count("FOREIGN KEY") == 5  # people <- encounters, records (x2), appointments (person, lens)
    # D6: a document that is not a Record is refused at write time, not by the reader as a 500.
    for field in sqldelivery.RECORD_JSON_REQUIRED_FIELDS:
        assert f"JSON_VALUE(record_json, '$.{field}') IS NOT NULL" in joined, field
    # `JSON_QUERY` alone admitted an array payload; `JSON_VALUE` only yields a scalar, so an object is the
    # only thing that can pass both this and the model's `payload: dict`.
    assert "JSON_VALUE(record_json, '$.payload') IS NOT NULL" in joined
    assert "JSON_QUERY(record_json, '$.payload') IS NOT NULL" in joined
    assert "ck_records_record_type" in joined
    assert "ck_records_topic" in joined


def test_the_weaker_record_json_check_is_upgraded_not_left_beside_the_strong_one():
    """A database hardened by the earlier, payload-only check must be REPLACED, not accepted.

    The first hardening wrote `JSON_QUERY(..., '$.payload') IS NOT NULL`, so a drop predicate looking only
    for `JSON_VALUE` would be false on such a database and the weaker check would survive beside a
    never-created stronger one. The predicate now keys off a field only the current check requires.
    """
    drop = next(statement for label, statement in HARDEN_STATEMENTS
                if "DROP CONSTRAINT ck_records_json" in statement)
    assert "NOT LIKE N'%sensitivity_tag%'" in drop, drop
    create = next(statement for label, statement in HARDEN_STATEMENTS
                  if "ADD CONSTRAINT ck_records_json" in statement)
    for field in sqldelivery.RECORD_JSON_REQUIRED_FIELDS:
        assert f"JSON_VALUE(record_json, '$.{field}') IS NOT NULL" in create, field
    assert "JSON_VALUE(record_json, '$.payload') IS NOT NULL" in create
    # The CREATE must be guarded on the constraint's absence, so a re-run does not fail.
    assert "NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'ck_records_json')" in create


def test_every_envelope_field_the_model_requires_is_in_the_database_check():
    """The two layers must agree: whatever `Record` requires must also be a CHECK the DB enforces."""
    required = {name for name, field in Record.model_fields.items() if field.is_required()}
    covered = set(sqldelivery.RECORD_JSON_REQUIRED_FIELDS)
    missing = sorted(required - covered)
    assert not missing, f"the DB check does not require {missing}, which the envelope does"
    assert covered <= set(Record.model_fields), sorted(covered - set(Record.model_fields))


def test_the_two_person_keys_are_made_to_agree_with_the_json():
    """D5: `persona_id` and `source_person_id` are two FKs to `people`; a trigger makes them one person."""
    joined = " ".join(SCHEMA)
    assert "tr_records_persona_agrees" in joined
    assert "p.source_person_id <> i.source_person_id" in joined
    assert "ck_records_json_source_person_id" in joined
    assert "JSON_VALUE(record_json, '$.source_person_id') = source_person_id" in joined


def test_the_envelope_guard_is_non_vacuous_against_a_valid_record():
    """The SQL guard must accept a real Record; the model half of the same rule is asserted beside it.

    The live database is where the SQL text is executed (and that is verified there); what is pinned here
    is that the guard is the presence check it claims to be, plus the model rule that a non-Record document
    is refused before it can ever be written.
    """
    with_guard = next(e.statement for e in SCHEMA_STATEMENTS if e.name == "records")
    assert "JSON_VALUE(record_json, '$.record_id') IS NOT NULL" in with_guard

    valid = {"record_id": "r1", "source": "delivery", "record_type": "vapaateksti",
             "authored_by_role": "laakari", "recorded_at": "2026-01-01T00:00:00+00:00", "topic": "hoito",
             "sensitivity_tag": "none", "consent_scope": "treatment", "payload": {"teksti": "x"}}
    assert Record.model_validate(valid).record_id == "r1"
    with pytest.raises(ValidationError):
        Record.model_validate_json("{}")
    with pytest.raises(ValidationError):
        Record.model_validate({**valid, "record_type": "NOPE"})
    with pytest.raises(ValidationError):
        Record.model_validate({**valid, "topic": "NOPE"})
    assert json.loads(json.dumps(valid))["record_id"] == "r1"


def test_the_printed_hardening_is_an_idempotent_script_for_any_client():
    """`--print-sql` exists so a machine without the ODBC driver can still apply the hardening, so the
    emitted script must be complete, batched with GO, and guarded."""
    script = hardening_sql()
    assert script.count("GO") >= len(HARDEN_STATEMENTS) - 1
    for _label, statement in HARDEN_STATEMENTS:
        assert statement in script
    assert "cx_records" in script


def test_the_integrity_expectation_matches_what_the_hardening_creates():
    assert INTEGRITY_EXPECTED["foreign_keys"] == 5
    assert INTEGRITY_EXPECTED["legacy_index"] == 0      # the redundant index must be gone
    assert "ck_records_json" in RECLUSTER_STATEMENT or INTEGRITY_EXPECTED["json_check"] == 1


def test_the_index_set_is_exactly_what_the_integrity_contract_declares():
    """D8: the covering index, the delete index and the two ADR-0013 indexes are the declared set.

    A name added to `INTEGRITY_EXPECTED` without a statement (or the reverse) is drift in the thing an
    operator's `--verify` reads, so both halves are compared here.
    """
    declared = {"persona_index": "ix_records_persona",
                "clustered_key": "cx_records"}
    created = {entry.name for entry in SCHEMA_STATEMENTS if entry.kind == "index"}
    for key, name in declared.items():
        assert INTEGRITY_EXPECTED[key] == 1, key
        assert name in created, name
        assert f"'{name}'" in INTEGRITY_QUERY, name
    assert INTEGRITY_EXPECTED["legacy_index"] == 0
    assert "ix_records_person " not in " ".join(SCHEMA)


def test_the_retired_indexes_are_dropped_on_every_load_and_declared_nowhere_else():
    """Each retired index is dropped by the every-load statements and named in no create, hardening,
    integrity expectation or integrity query, so a stale expectation cannot bring it back."""
    assert len(RETIRED_INDEX_STATEMENTS) == len(RETIRED_INDEXES) == 3
    for name in RETIRED_INDEXES:
        drops = [s for s in RETIRED_INDEX_STATEMENTS if f"DROP INDEX IF EXISTS {name} ON records" in s]
        assert len(drops) == 1, name
        assert drops[0].startswith("IF OBJECT_ID(N'records', N'U') IS NOT NULL"), name
        assert name not in " ".join(SCHEMA), name
        assert not any(entry.name == name for entry in SCHEMA_STATEMENTS), name
        assert not any(name in statement for _label, statement in HARDEN_STATEMENTS), name
        assert name not in INTEGRITY_QUERY, name
        assert name not in INTEGRITY_EXPECTED, name
        assert name not in hardening_sql(), name


# --- the knowledge index (B6): its own schema, which a corpus load never touches -----------------------------


def test_the_knowledge_schema_is_separate_from_the_corpus_schema_and_its_replace_and_drop():
    """A corpus reload -- even `--recreate` -- must not drop or empty the embedded index, and nothing in the
    corpus contract may depend on it, so the table is named in none of the corpus statements."""
    assert KNOWLEDGE_SCHEMA_STATEMENTS and all(e.name == "knowledge_chunks" for e in KNOWLEDGE_SCHEMA_STATEMENTS)
    corpus_text = " ".join([*SCHEMA, *DROP_SCHEMA, *REPLACE_STATEMENTS, INTEGRITY_QUERY,
                            *(statement for _label, statement in HARDEN_STATEMENTS)])
    assert "knowledge_chunks" not in corpus_text
    assert not any(entry.name == "knowledge_chunks" for entry in SCHEMA_STATEMENTS)
    assert "knowledge" not in " ".join(INTEGRITY_EXPECTED), "INTEGRITY_EXPECTED is the corpus loader's contract"
    assert KNOWLEDGE_DROP_STATEMENTS == ("DROP TABLE IF EXISTS knowledge_chunks",)
    assert KNOWLEDGE_REPLACE_STATEMENTS == ("DELETE FROM knowledge_chunks",)


def test_every_knowledge_schema_statement_is_guarded_by_the_object_it_creates():
    for entry in KNOWLEDGE_SCHEMA_STATEMENTS:
        assert entry.kind == "table"
        assert entry.guard == "IF OBJECT_ID(N'knowledge_chunks', N'U') IS NULL"
        assert entry.guarded == f"{entry.guard} EXEC(N'{sqldelivery._quote_literal(entry.statement)}')"
        assert "'" not in entry.statement, "a quote in the DDL would need escaping inside EXEC(N'...')"


def test_the_knowledge_table_is_a_vector_column_the_server_can_create():
    """Azure SQL `VECTOR(n)` float32 allows at most 1,998 dimensions; the handoff's 3072 could not be created."""
    statement = KNOWLEDGE_SCHEMA_STATEMENTS[0].statement
    assert statement.startswith("CREATE TABLE knowledge_chunks (id NVARCHAR(128) NOT NULL PRIMARY KEY")
    assert f"embedding VECTOR({KNOWLEDGE_VECTOR_DIMENSIONS}) NOT NULL" in statement and "VECTOR(" in statement
    assert 0 < KNOWLEDGE_VECTOR_DIMENSIONS <= 1998 and KNOWLEDGE_VECTOR_DIMENSIONS == 1536
    for column in ("kind NVARCHAR(16) NOT NULL", "source NVARCHAR(64) NOT NULL", "text NVARCHAR(MAX) NOT NULL",
                   "embedding_model NVARCHAR(128) NOT NULL", "snapshot_version NVARCHAR(64) NOT NULL",
                   "loaded_at DATETIME2 NOT NULL"):
        assert column in statement, column
    # pyodbc binds a 30 KB JSON vector as ntext, which SQL Server will not convert to vector; NVARCHAR(MAX) first.
    assert f"CAST(CAST(? AS NVARCHAR(MAX)) AS VECTOR({KNOWLEDGE_VECTOR_DIMENSIONS}))" in KNOWLEDGE_INSERT
    assert KNOWLEDGE_INSERT.count("?") == 7 and "SYSUTCDATETIME()" in KNOWLEDGE_INSERT


def test_every_expected_knowledge_catalogue_name_is_actually_queried():
    for name in KNOWLEDGE_INTEGRITY_EXPECTED:
        assert f"AS {name}" in KNOWLEDGE_INTEGRITY_QUERY, name
    assert set(KNOWLEDGE_INTEGRITY_EXPECTED) == {"knowledge_table", "knowledge_primary_key", "knowledge_vector_column"}


def test_verify_knowledge_integrity_reports_a_miss_rather_than_raising():
    class _Cursor:
        description = [(name,) for name in KNOWLEDGE_INTEGRITY_EXPECTED]

        def execute(self, sql):
            assert sql == KNOWLEDGE_INTEGRITY_QUERY

        def fetchone(self):
            return (1, 1, 0)

    state = sqldelivery.verify_knowledge_integrity(_Cursor())
    assert state["ok"] is False and state["missing"] == ["knowledge_vector_column"] and state["unexpected"] == []


def test_every_expected_catalogue_name_is_actually_queried():
    """A typo in a key would make `actual.get(name, 0)` always 0 and `--verify` never notice it."""
    for name in INTEGRITY_EXPECTED:
        assert f"AS {name}" in INTEGRITY_QUERY, name


# The order of `INTEGRITY_QUERY`'s select list, which is what a real cursor reports. `_IntegrityCursor`
# below answers from it, so a reordered query without this list keeps the offline test honest.
_INTEGRITY_COLUMNS = ("foreign_keys", "appointments_day_index", "json_check", "json_source_check",
                      "record_type_check", "topic_check",
                      "persona_trigger", "encounter_role_check", "encounter_purpose_check",
                      "encounter_holders_check", "encounter_life_stage_check", "clustered_key",
                      "persona_index", "legacy_index", "primary_key")


def test_the_offline_cursor_columns_are_the_query_order():
    for name in _INTEGRITY_COLUMNS:
        assert f"AS {name}" in INTEGRITY_QUERY, name
    assert len(_INTEGRITY_COLUMNS) == len(INTEGRITY_EXPECTED)


class _IntegrityCursor:
    """A cursor that reports a catalogue state, so `verify_integrity` can be exercised offline."""

    def __init__(self, values: list[int]):
        self._values = values

    def execute(self, _statement):
        return self

    def fetchone(self):
        return tuple(self._values)

    @property
    def description(self):
        return [(name,) for name in _INTEGRITY_COLUMNS]


def _healthy_values() -> list[int]:
    return [INTEGRITY_EXPECTED[name] for name in _INTEGRITY_COLUMNS]


def test_verify_integrity_reports_missing_objects_rather_than_assuming_them():
    assert verify_integrity(_IntegrityCursor(_healthy_values()))["ok"] is True
    broken = _healthy_values()
    broken[_INTEGRITY_COLUMNS.index("clustered_key")] = 0
    broken[_INTEGRITY_COLUMNS.index("legacy_index")] = 1
    state = verify_integrity(_IntegrityCursor(broken))
    assert state["ok"] is False
    assert "clustered_key" in state["missing"] and "legacy_index" in state["unexpected"]


def test_verify_integrity_notices_a_missing_foreign_key():
    """The state the live database was actually in: 0 of 3 foreign keys, everything else present."""
    values = _healthy_values()
    values[_INTEGRITY_COLUMNS.index("foreign_keys")] = 0
    state = verify_integrity(_IntegrityCursor(values))
    assert state["ok"] is False and state["missing"] == ["foreign_keys"], state


def test_no_hardening_statement_drops_or_truncates_a_table():
    """Hardening must not be able to lose the corpus (a guarded DROP CONSTRAINT is not a DROP TABLE)."""
    for label, statement in HARDEN_STATEMENTS:
        upper = statement.upper()
        assert "DROP TABLE" not in upper and "TRUNCATE" not in upper, label


# --- M1/D4: a `--grant-*` identity cannot alter the batch -------------------------------------------------

HOSTILE_IDENTITY = "x'); EXEC xp_logevent 55000 N'PWNED-by-grant', INFORMATION; --"


def test_a_hostile_grant_identity_is_refused_outright_before_any_statement_or_connection(capsys):
    """REFUSED, not escaped: escaping a payload means executing something shaped like SQL.

    The value is interpolated into BOTH a T-SQL string literal (`''` escapes) and a bracketed identifier
    (`]]` escapes) and runs as the loader (`db_datawriter+db_ddladmin`) or, with `--harden`, the Entra
    administrator. Validation runs BEFORE `connect()`, so a hostile value cannot even prove a connection.
    """
    from app import load_sql

    status = load_sql.main(["--grant-app-user", HOSTILE_IDENTITY])
    assert status == 2
    captured = capsys.readouterr()
    assert "refusing to grant" in captured.err
    assert "EXEC xp_logevent" in captured.err, "the refusal names the value it refused"
    assert "Azure SQL needs" not in captured.err, "the identity was refused before any connection attempt"


def test_a_plausible_principal_name_is_accepted_and_escaped_for_both_contexts():
    """The ordinary names still work, and the two escapes are the right ones for their own context."""
    from app import load_sql

    assert load_sql._validate_identity("luvn-app-identity") == "luvn-app-identity"
    assert load_sql._validate_identity("app@example.onmicrosoft.com") == "app@example.onmicrosoft.com"
    assert load_sql._validate_identity("O'Brien (dev)") == "O'Brien (dev)"
    # The escapes differ, and each is the only escape its context has.
    assert load_sql._quote_literal("a'b") == "a''b"
    assert load_sql._quote_identifier("a]b") == "a]]b"


@pytest.mark.parametrize("identity", [
    "x'); EXEC xp_logevent 55000 N'PWNED', INFORMATION; --",
    "app]; DROP TABLE records; --",
    "app\n--",
    "",
    "a" * 300,
])
def test_no_identity_that_needs_escaping_to_be_a_statement_is_ever_accepted(identity: str):
    from app import load_sql

    with pytest.raises(load_sql.GrantRefused):
        load_sql._validate_identity(identity)
