"""Azure SQL as the corpus source, behind the same lazy person-index seam as the file import.

`DeliveryIndex` reads a local SQLite index and one JSONL file per person; this reads the same
`Persona`/`Encounter`/`Record` objects out of Azure SQL, one person per lookup, so `store.Store` and
everything above it are unchanged. The reason it is an index and not a per-request query planner is the
same reason the file importer is one: 50 000 profiles must never be walked to render one briefing. What
crosses the wire per request is one person's rows, resolved by primary key.

**Written for the driver to be absent.** `pyodbc` is imported inside `connect()` and nowhere else, so this
module imports, type-checks and unit-tests on a machine with no ODBC driver at all. That is not a
convenience: the gate is Azure-free and driver-free by design, and a module that cannot be imported without
a network driver is a module nothing can test. The index takes an already-open connection, so a test passes
a stand-in and exercises the real row-to-`Persona` mapping.

**Authentication is Entra, not a password.** The app's user-assigned managed identity asks for a token for
`https://database.windows.net/.default` and hands it to the driver through `SQL_COPT_SS_ACCESS_TOKEN`.
`LUVN_SQL_ACCESS_TOKEN` exists for running the loader outside Azure — a container on a laptop has no
managed identity — and it is a token, not a credential: it expires and nothing stores it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
import os
import struct
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, cast

from .delivery import (
    DEFAULT_DELIVERY_DIR,
    ENCOUNTER_CHECK_SQL,
    REQUIRED_ENVELOPE_TARGETS,
    DeliveryShapeError,
    _bind_record,
    _iter_mapped_rows,
    _iter_rows,
    _read_encounters, _read_appointments, APPOINTMENT_COLUMNS,
    _read_profiles,
    _rules_matched_nothing,
    _safe_persona_id,
    derived_record_state,
)
from .encounters import Appointment, appointment_from_row, Encounter, EncounterMetadata, metadata_from_row
from .mapping import MappingSpec
from .purpose import Purpose
from .purpose import topics as declared_topics
from .source import RECORD_TYPES
from .upm import ConsentHolder, LifeStage, Persona, Record

SQL_SCOPE = "https://database.windows.net/.default"


class CorruptCorpusError(RuntimeError):
    """A stored record does not validate as a `Record`: the corpus, not the request, is wrong.

    A `ValidationError` is a `ValueError`, so before this the API's `_resolve` turned a planted or
    corrupted row into `404 unknown encounter` -- which names the wrong thing entirely and tells the
    professional the person does not exist. This is the "the data is broken" signal, distinct from "no such
    encounter", and the route answers it with a 5xx that says so.
    """

    def __init__(self, message: str, *, persona_id: str | None = None, record_id: str | None = None):
        super().__init__(message)
        self.persona_id = persona_id
        self.record_id = record_id


# `SQL_COPT_SS_ACCESS_TOKEN` from msodbcsql.h: the driver attribute that carries an Entra access token.
SQL_COPT_SS_ACCESS_TOKEN = 1256
DRIVER = "ODBC Driver 18 for SQL Server"
# Items (people, encounters or records) per COMMITTED transaction. 5,000, measured against the alternatives:
# fast_executemany allocates one buffer per call, so the batch is also the memory ceiling: at roughly 1 KB per
# record that is about 5 MB, far inside the job's 8 GiB. Above it, one failed batch costs a larger rollback and
# a longer replay; below it, each commit's fixed cost (the checkpoint write, the log flush, the persona trigger's
# people join) is paid per 1,000 rows instead of per 5,000. The 10 % stage run sets the rate; see the report.
LOAD_BATCH = 5000
CORPUS_BLOB_PREFIX = "corpus/current"
CORPUS_FILES = ("profiles.csv", "encounters.csv", "records.jsonl")
# Optional beside the three: the day view's appointments. A corpus without it loads with no appointments.
OPTIONAL_CORPUS_FILES = ("appointments.csv",)
# The corpus files are streamed to disk in chunks, and each is refused above this size. `readall()` used
# to materialize a whole blob in memory with no bound at all, so a swapped staging blob of several GB
# would OOM the loader job; a bound makes the failure a named refusal instead of an allocator error. The
# real delivery is ~258 MB in total, so 1 GiB per file is far above anything legitimate.
#
# This bounds the SDK's own HTTP GET chunk size (`BlobServiceClient(..., max_chunk_get_size=...)`), not an
# argument to `StorageStreamDownloader.chunks()`: azure-storage-blob 12.30 dropped that method's
# `chunk_size` keyword entirely (`chunks(self)`, no parameters), so passing it there raises `TypeError`
# before a single byte moves.
CORPUS_BLOB_CHUNK_BYTES = 1024 * 1024
MAX_CORPUS_FILE_BYTES = 1024 * 1024 * 1024


class CorpusBlobTooLarge(RuntimeError):
    """A corpus blob is larger than this loader will download, so it was refused by name."""


def fetch_corpus_blob(destination, container=None, prefix=None, account_url=None, client_id=None,
                      max_bytes: int = MAX_CORPUS_FILE_BYTES):
    """Download a corpus in the declared shape from the delivery container into `destination`.

    This is the hackathon-day path: the operator uploads the three files and the loader reads them from Blob
    rather than from the image. It is why the runtime image carries no corpus at all: a corpus compiled into
    an image is a corpus that needs a rebuild to change, which is the opposite of "upload new data and revise
    the mapping".

    Each file is STREAMED in chunks to disk and refused above `max_bytes`, so one oversized or swapped blob
    cannot hold the whole file in the process' memory.
    """
    from pathlib import Path as _Path

    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient

    account_url = account_url or os.environ.get("LUVN_STORAGE_ACCOUNT_URL") or ""
    container = container or os.environ.get("LUVN_CORPUS_CONTAINER") or "delivery"
    prefix = (prefix or os.environ.get("LUVN_CORPUS_PREFIX") or CORPUS_BLOB_PREFIX).strip("/")
    if not account_url:
        raise RuntimeError("reading a corpus from Blob needs LUVN_STORAGE_ACCOUNT_URL")
    service = BlobServiceClient(
        account_url=account_url,
        credential=ManagedIdentityCredential(
            client_id=client_id or os.environ.get("LUVN_STORAGE_CLIENT_ID") or None),
        max_chunk_get_size=CORPUS_BLOB_CHUNK_BYTES)
    destination = _Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    client = service.get_container_client(container)
    for name in CORPUS_FILES:
        blob_name = f"{prefix}/{name}" if prefix else name
        blob = client.get_blob_client(blob_name)
        # A missing file must fail here rather than import a corpus that is half of one.
        downloader = blob.download_blob()
        _stream_blob_to(destination / name, downloader, blob_name, max_bytes)
    from azure.core.exceptions import ResourceNotFoundError

    for name in OPTIONAL_CORPUS_FILES:
        blob_name = f"{prefix}/{name}" if prefix else name
        try:
            downloader = client.get_blob_client(blob_name).download_blob()
        except (ResourceNotFoundError, FileNotFoundError):
            # The SDK raises the former; a file-backed stand-in the latter. Either way: absent, and not an error.
            print(f"fetch_corpus_blob: no {blob_name}; the corpus carries no appointments", flush=True)
            continue
        _stream_blob_to(destination / name, downloader, blob_name, max_bytes)
    return destination


def _stream_blob_to(path, downloader, blob_name: str, max_bytes: int) -> None:
    """Write one blob to `path` in chunks, refusing it above `max_bytes`.

    The size is checked from the download's own metadata when the SDK reports it, and enforced again on the
    bytes actually written, so neither a lying Content-Length nor a chunked upload can get past the bound.
    The partial file is removed before the refusal propagates, so a caller that catches it cannot mistake
    a truncated file for a corpus.
    """
    declared = getattr(getattr(downloader, "properties", None), "size", None)
    if isinstance(declared, int) and declared > max_bytes:
        raise CorpusBlobTooLarge(
            f"blob {blob_name!r} is {declared} bytes, above the {max_bytes}-byte limit; refusing rather "
            f"than holding it in memory. Upload a smaller corpus or raise LUVN_CORPUS_MAX_BYTES")
    written = 0
    try:
        with path.open("wb") as stream:
            for chunk in downloader.chunks():
                written += len(chunk)
                if written > max_bytes:
                    raise CorpusBlobTooLarge(
                        f"blob {blob_name!r} exceeded the {max_bytes}-byte limit while downloading "
                        f"(read {written} bytes); refusing rather than holding it in memory")
                stream.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


# The records key is declared NONCLUSTERED because the dominant read is one person's rows, not one record:
# `cx_records` clusters storage on (source_person_id, record_id) so that read is a range scan of adjacent
# pages instead of one key lookup per row. That distinction is why this is not a cosmetic choice — Basic is
# the tier with 1-4 IOPS per DTU, and I/O is the scarce resource, not CPU (see `HARDEN_STATEMENTS`).
def _literal_list(values) -> str:
    """One SQL literal list for a CHECK, quoted for T-SQL."""
    return ", ".join("N'" + str(value).replace("'", "''") + "'" for value in values)


RECORD_TYPE_CHECK_SQL = _literal_list(RECORD_TYPES)
TOPIC_CHECK_SQL = _literal_list(sorted(declared_topics()))
# Every field `upm.Record` requires, plus a presence test for `payload`. The expression is shared by the
# fresh-schema CHECK, its hardening twin and the integrity readback, so the three cannot drift.
#
# The payload test is `JSON_QUERY(...) IS NOT NULL OR JSON_VALUE(...) IS NOT NULL`, and both halves are
# load-bearing. `JSON_VALUE` returns NULL for anything that is not a *scalar* — which includes the JSON
# objects every real record carries — so an earlier revision of this check tested
# `JSON_VALUE(record_json, '$.payload') IS NOT NULL` alone and refused **all 41 live rows** (Msg 547 on
# `ALTER TABLE`, measured against the deployed database). That earlier comment also claimed
# `JSON_QUERY` "returns arrays as happily as objects" so `"payload": []` had passed the old check; that is
# true but irrelevant, because `JSON_VALUE` fails on arrays too, so the array case was never the failure
# mode. What has to hold is that the *key is present*, whatever its JSON type: `JSON_QUERY` covers objects
# and arrays, `JSON_VALUE` covers scalars, and the two together cover every non-null JSON value without
# asserting a payload shape the envelope does not require.
RECORD_JSON_REQUIRED_FIELDS = ("record_id", "source", "record_type", "authored_by_role", "recorded_at",
                               "topic", "sensitivity_tag", "consent_scope")
# `record_json` stays NVARCHAR(MAX) with these ISJSON checks, and is NOT converted to the native `json` type. Every
# field the product filters on is promoted to a real column, which is the rule that matters; converting the
# document type is a full reload for no correctness gain.
RECORD_JSON_CHECK_SQL = (
    "record_json IS NOT NULL "
    "AND ISJSON(record_json) = 1 "
    + " ".join(f"AND JSON_VALUE(record_json, '$.{field}') IS NOT NULL"
               for field in RECORD_JSON_REQUIRED_FIELDS)
    + " AND (JSON_QUERY(record_json, '$.payload') IS NOT NULL "
    "OR JSON_VALUE(record_json, '$.payload') IS NOT NULL)")


@dataclass(frozen=True)
class SchemaStatement:
    """One DDL statement and the ONE object whose existence means it is already applied.

    The loader used to derive the guard from the SQL text (`statement.split("TABLE ")[1]`, else a test for
    `ix_records_person`), which guarded `ALTER TABLE ... ADD CONSTRAINT fk_*` on the TABLE (skipped forever,
    because the table exists) and every CREATE INDEX on an index that never existed (executed on every load,
    SQL Server Msg 1913, the load transaction rolled back). The object is now stated, not inferred.
    """

    kind: str                  # "table" | "constraint" | "index" | "trigger"
    name: str                  # the object this statement creates
    statement: str
    table: str | None = None   # the parent object, for the catalogue lookup

    @property
    def guard(self) -> str:
        """The existence predicate for THIS statement's own object, and nothing unrelated."""
        name = _quote_literal(self.name)
        if self.kind == "table":
            return f"IF OBJECT_ID(N'{name}', N'U') IS NULL"
        if self.kind == "trigger":
            return f"IF OBJECT_ID(N'{name}', N'TR') IS NULL"
        parent = _quote_literal(self.table or "")
        if self.kind == "index":
            # pi-lens-ignore: sql-injection -- reviewed: DDL built only from module literals, never runtime input
            return (f"IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'{name}' "
                    f"AND object_id = OBJECT_ID(N'{parent}'))")
        if self.kind == "constraint":
            # `sys.objects` carries FOREIGN KEY and CHECK constraints alike, so one predicate covers both.
            # pi-lens-ignore: sql-injection -- reviewed: DDL built only from module literals, never runtime input
            return (f"IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE name = N'{name}' "
                    f"AND parent_object_id = OBJECT_ID(N'{parent}'))")
        raise ValueError(f"unknown schema statement kind {self.kind!r}")

    @property
    def guarded(self) -> str:
        return f"{self.guard} EXEC(N'{_quote_literal(self.statement)}')"


def _quote_literal(text: str) -> str:
    """The body of a T-SQL string literal: the only escape is a doubled apostrophe."""
    return text.replace("'", "''")


# The trigger is the cross-table rule a CHECK cannot express: `records.persona_id` and
# `records.source_person_id` are two independent foreign keys to `people`, and nothing but this makes them
# name the SAME person. A CHECK cannot read `people`; a composite FK would enforce it declaratively but would
# make one of the two documented single-column keys redundant and rename a constraint the integrity contract
# counts. Kept as a trigger so the three named relationships stay exactly the three the contract declares.
PERSONA_AGREEMENT_TRIGGER = (
    "CREATE TRIGGER tr_records_persona_agrees ON records AFTER INSERT, UPDATE AS "
    "BEGIN "
    "IF EXISTS (SELECT 1 FROM inserted i JOIN people p ON p.persona_id = i.persona_id "
    "WHERE p.source_person_id <> i.source_person_id) "
    "BEGIN "
    "RAISERROR (N'records.persona_id is not the persona of records.source_person_id', 16, 1); "
    "ROLLBACK TRANSACTION; "
    "END "
    "END")

# COLLATION: the database collation is `Finnish_Swedish_CI_AS` (recorded in the live readback), and that is
# deliberately accepted. Every key in this schema is NVARCHAR(128), so the failure a VARCHAR key under a SQL
# collation causes (a seek lost when a Unicode parameter binds against it) cannot occur, and the
# case-insensitive default is harmless for machine-generated ids. A future CREATE DATABASE must state its
# COLLATE clause explicitly. `ALTER DATABASE ... COLLATE` re-collates nothing that already exists, so the
# collation cannot be changed after the fact without a reload.
#
# Every statement a FRESH schema needs, each with the object it creates. Order is the one a fresh database
# needs: tables, then the indexes and keys over them, then the trigger.
SCHEMA_STATEMENTS: tuple[SchemaStatement, ...] = (
    SchemaStatement("table", "people",
                    "CREATE TABLE people (persona_id NVARCHAR(128) NOT NULL PRIMARY KEY, "
                    "source_person_id NVARCHAR(128) NOT NULL UNIQUE, label NVARCHAR(256) NULL)"),
    SchemaStatement(
        "table", "encounters",
        "CREATE TABLE encounters (encounter_id NVARCHAR(128) NOT NULL PRIMARY KEY, "
        "source_person_id NVARCHAR(128) NOT NULL, "
        f"role NVARCHAR(64) NOT NULL CONSTRAINT ck_encounters_role CHECK (role IN ({ENCOUNTER_CHECK_SQL['role']})), "
        "purpose NVARCHAR(64) NOT NULL CONSTRAINT ck_encounters_purpose "
        f"CHECK (purpose IN ({ENCOUNTER_CHECK_SQL['purpose']})), "
        "example_label NVARCHAR(256) NULL, demo_name NVARCHAR(256) NULL, "
        "consent_holders NVARCHAR(64) NULL CONSTRAINT ck_encounters_holders "
        f"CHECK (consent_holders IS NULL OR consent_holders IN ({ENCOUNTER_CHECK_SQL['consent_holders']})), "
        "life_stage NVARCHAR(16) NULL CONSTRAINT ck_encounters_life_stage "
        f"CHECK (life_stage IS NULL OR life_stage IN ({ENCOUNTER_CHECK_SQL['life_stage']})))"),
    SchemaStatement(
        "table", "records",
        "CREATE TABLE records (record_id NVARCHAR(128) NOT NULL PRIMARY KEY NONCLUSTERED, "
        "source_person_id NVARCHAR(128) NOT NULL, persona_id NVARCHAR(128) NOT NULL, "
        f"record_type NVARCHAR(64) NOT NULL CONSTRAINT ck_records_record_type CHECK (record_type IN ({RECORD_TYPE_CHECK_SQL})), "
        # ADR-0013's case-level binding, nullable because the corpora that carry it are optional and the
        # loader must NOT invent a value for a record that states none.
        "encounter_id NVARCHAR(128) NULL, case_id NVARCHAR(128) NULL, "
        f"record_json NVARCHAR(MAX) NOT NULL CONSTRAINT ck_records_json CHECK ({RECORD_JSON_CHECK_SQL}), "
        "CONSTRAINT ck_records_json_source_person_id CHECK ("
        "JSON_VALUE(record_json, '$.source_person_id') IS NULL "
        "OR JSON_VALUE(record_json, '$.source_person_id') = source_person_id), "
        "CONSTRAINT ck_records_topic CHECK (JSON_VALUE(record_json, '$.topic') IS NOT NULL "
        f"AND JSON_VALUE(record_json, '$.topic') IN ({TOPIC_CHECK_SQL})))"),
    SchemaStatement(
        "table", "appointments",
        "CREATE TABLE appointments (appointment_id NVARCHAR(128) NOT NULL PRIMARY KEY, "
        "source_person_id NVARCHAR(128) NOT NULL, "
        "domain NVARCHAR(16) NOT NULL CONSTRAINT ck_appointments_domain "
        "CHECK (domain IN ('treatment', 'social_care')), "
        "scheduled_on DATE NOT NULL, status_text NVARCHAR(64) NULL, urgency_text NVARCHAR(128) NULL, "
        "specialty NVARCHAR(16) NULL, contact_record_id NVARCHAR(128) NULL, encounter_id NVARCHAR(128) NULL)"),
    # The briefings cache (`app/briefings.py`). Created by the loader because the app identity reads the corpus
    # and writes only this table. No foreign key: a row is keyed by the generation it was built from, and the
    # replace path empties it with the corpus it described.
    SchemaStatement(
        "table", "briefings",
        "CREATE TABLE briefings (encounter_id NVARCHAR(128) NOT NULL, corpus_generation NVARCHAR(64) NOT NULL, "
        "rule_version NVARCHAR(80) NOT NULL, agent_version NVARCHAR(160) NOT NULL, "
        "built_at DATETIME2 NOT NULL, today DATE NOT NULL, status NVARCHAR(16) NOT NULL, "
        "picture_json NVARCHAR(MAX) NOT NULL, attention_json NVARCHAR(MAX) NOT NULL, "
        "CONSTRAINT pk_briefings PRIMARY KEY (encounter_id, corpus_generation, rule_version, agent_version))"),
    SchemaStatement("index", "cx_records",
                    "CREATE UNIQUE CLUSTERED INDEX cx_records ON records (source_person_id, record_id)",
                    table="records"),
    # The teardown delete is `records WHERE persona_id IN (...)`, which the covering index does not lead
    # with; without this it scans.
    SchemaStatement("index", "ix_records_persona",
                    "CREATE NONCLUSTERED INDEX ix_records_persona ON records (persona_id)", table="records"),
    SchemaStatement("index", "ix_encounters_person",
                    "CREATE INDEX ix_encounters_person ON encounters (source_person_id)", table="encounters"),
    SchemaStatement("constraint", "fk_encounters_people",
                    "ALTER TABLE encounters ADD CONSTRAINT fk_encounters_people "
                    "FOREIGN KEY (source_person_id) REFERENCES people (source_person_id)",
                    table="encounters"),
    SchemaStatement("constraint", "fk_records_people_source",
                    "ALTER TABLE records ADD CONSTRAINT fk_records_people_source "
                    "FOREIGN KEY (source_person_id) REFERENCES people (source_person_id)",
                    table="records"),
    SchemaStatement("index", "ix_appointments_day",
                    "CREATE INDEX ix_appointments_day ON appointments (scheduled_on, encounter_id)",
                    table="appointments"),
    SchemaStatement("constraint", "fk_appointments_people",
                    "ALTER TABLE appointments ADD CONSTRAINT fk_appointments_people "
                    "FOREIGN KEY (source_person_id) REFERENCES people (source_person_id)",
                    table="appointments"),
    SchemaStatement("constraint", "fk_appointments_encounters",
                    "ALTER TABLE appointments ADD CONSTRAINT fk_appointments_encounters "
                    "FOREIGN KEY (encounter_id) REFERENCES encounters (encounter_id)",
                    table="appointments"),
    SchemaStatement("constraint", "fk_records_people_persona",
                    "ALTER TABLE records ADD CONSTRAINT fk_records_people_persona "
                    "FOREIGN KEY (persona_id) REFERENCES people (persona_id)",
                    table="records"),
    SchemaStatement("trigger", "tr_records_persona_agrees", PERSONA_AGREEMENT_TRIGGER, table="records"),
)
# The statements as plain strings, in creation order, for callers that only want the DDL text.
SCHEMA = tuple(entry.statement for entry in SCHEMA_STATEMENTS)
DROP_SCHEMA = ("DROP TABLE IF EXISTS briefings", "DROP TABLE IF EXISTS appointments", "DROP TABLE IF EXISTS records",
               "DROP TABLE IF EXISTS encounters", "DROP TABLE IF EXISTS people", "DROP TABLE IF EXISTS load_state")
# Indexes with no reader, retired on EVERY load (run by `load_corpus` in the schema path, not only on
# `--recreate`), so a plain load removes them from a database that already holds them. Nothing re-creates
# them: they are in neither SCHEMA_STATEMENTS nor HARDEN_STATEMENTS. Each is guarded by its table, because a
# DROP INDEX ... ON a missing table is an error even with IF EXISTS. The COLUMNS they led on are kept: the
# envelope still carries encounter_id and case_id (policy.cut reads them), so only the indexes go.
#   ix_records_person_covering: redundant; cx_records already serves the per-person read from the clustered leaf.
#   ix_records_persona_encounter, ix_records_case: lead on encounter_id/case_id, NULL on every row, never read.
RETIRED_INDEX_STATEMENTS: tuple[str, ...] = tuple(
    f"IF OBJECT_ID(N'records', N'U') IS NOT NULL DROP INDEX IF EXISTS {name} ON records"
    for name in ("ix_records_person_covering", "ix_records_persona_encounter", "ix_records_case"))
# Integrity for a database that already exists, as `ALTER` statements guarded by their own existence test so
# the list is safe to re-run. `load_sql --harden` and `scripts/harden_sql.py` both call this one definition.
#
# Each entry: what it buys, then the statement.
# The two provenance columns ADR-0013 needs. Separate from the rest of the hardening because `load_sql
# --harden` applies THEM before the load: the records insert below names them, and on a database created by
# an earlier revision they do not exist yet. Nullable on purpose — a record that states no binding gets NULL,
# never an invented value.
RECORD_BINDING_COLUMN_STATEMENTS: tuple[tuple[str, str], ...] = (
    ("records carries the encounter the envelope names, when it names one",
     "IF OBJECT_ID(N'records', N'U') IS NOT NULL AND COL_LENGTH('records', 'encounter_id') IS NULL "
     "ALTER TABLE records ADD encounter_id NVARCHAR(128) NULL"),
    ("records carries the case the envelope names, when it names one",
     "IF OBJECT_ID(N'records', N'U') IS NOT NULL AND COL_LENGTH('records', 'case_id') IS NULL "
     "ALTER TABLE records ADD case_id NVARCHAR(128) NULL"),
)

# Every hardening statement is guarded by BOTH the parent table existing and its own object being absent, so
# the whole list is a no-op on an empty database (which lets `load_sql --harden` run it BEFORE the first load
# to bring an existing schema up to what the loader writes) and safe to re-run in any order.
HARDEN_STATEMENTS: tuple[tuple[str, str], ...] = (
    ("encounters cannot reference a person who is not in the corpus",
     # pi-lens-ignore: sql-injection -- reviewed: DDL built only from module literals, never runtime input
     "IF OBJECT_ID(N'encounters', N'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys "
     "WHERE name = 'fk_encounters_people') "
     "ALTER TABLE encounters WITH CHECK ADD CONSTRAINT fk_encounters_people "
     "FOREIGN KEY (source_person_id) REFERENCES people (source_person_id)"),
    ("records cannot reference a person who is not in the corpus",
     # pi-lens-ignore: sql-injection -- reviewed: DDL built only from module literals, never runtime input
     "IF OBJECT_ID(N'records', N'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys "
     "WHERE name = 'fk_records_people_source') "
     "ALTER TABLE records WITH CHECK ADD CONSTRAINT fk_records_people_source "
     "FOREIGN KEY (source_person_id) REFERENCES people (source_person_id)"),
    ("a record's persona must exist",
     # pi-lens-ignore: sql-injection -- reviewed: DDL built only from module literals, never runtime input
     "IF OBJECT_ID(N'records', N'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys "
     "WHERE name = 'fk_records_people_persona') "
     "ALTER TABLE records WITH CHECK ADD CONSTRAINT fk_records_people_persona "
     "FOREIGN KEY (persona_id) REFERENCES people (persona_id)"),
    *RECORD_BINDING_COLUMN_STATEMENTS,
    ("an encounter's role is one the policy layer knows",
     # pi-lens-ignore: sql-injection -- reviewed: DDL built only from module literals, never runtime input
     "IF OBJECT_ID(N'encounters', N'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.check_constraints "
     "WHERE name = 'ck_encounters_role') "
     "ALTER TABLE encounters WITH CHECK ADD CONSTRAINT ck_encounters_role "
     f"CHECK (role IN ({ENCOUNTER_CHECK_SQL['role']}))"),
    ("an encounter's purpose is a declared Palvelukonteksti",
     # pi-lens-ignore: sql-injection -- reviewed: DDL built only from module literals, never runtime input
     "IF OBJECT_ID(N'encounters', N'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.check_constraints "
     "WHERE name = 'ck_encounters_purpose') "
     "ALTER TABLE encounters WITH CHECK ADD CONSTRAINT ck_encounters_purpose "
     f"CHECK (purpose IN ({ENCOUNTER_CHECK_SQL['purpose']}))"),
    ("a lens covers declared consent holders, and only those",
     # pi-lens-ignore: sql-injection -- reviewed: DDL built only from module literals, never runtime input
     "IF OBJECT_ID(N'encounters', N'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.check_constraints "
     "WHERE name = 'ck_encounters_holders') "
     "ALTER TABLE encounters WITH CHECK ADD CONSTRAINT ck_encounters_holders "
     f"CHECK (consent_holders IS NULL OR consent_holders IN ({ENCOUNTER_CHECK_SQL['consent_holders']}))"),
    ("a lens declares a declared life stage, or none",
     # pi-lens-ignore: sql-injection -- reviewed: DDL built only from module literals, never runtime input
     "IF OBJECT_ID(N'encounters', N'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.check_constraints "
     "WHERE name = 'ck_encounters_life_stage') "
     "ALTER TABLE encounters WITH CHECK ADD CONSTRAINT ck_encounters_life_stage "
     f"CHECK (life_stage IS NULL OR life_stage IN ({ENCOUNTER_CHECK_SQL['life_stage']}))"),
    # The upgrade path for a database hardened by an earlier, weaker JSON check: replace it when its
    # definition is not the envelope check. `OBJECT_DEFINITION` is how a CHECK's text is read back; the
    # LIKE names a field the current check requires and the old one did not, and it is false on every
    # later run. `sensitivity_tag` is used rather than `payload` because the FIRST hardening also wrote
    # `JSON_QUERY(..., '$.payload')`, so a `payload`-based predicate would leave that weaker check in
    # place on a database this repository already hardened once.
    ("the original ISJSON-only check is replaced rather than left as the weaker of two",
     "IF OBJECT_ID(N'records', N'U') IS NOT NULL AND EXISTS (SELECT 1 FROM sys.check_constraints "
     "WHERE name = 'ck_records_json' AND OBJECT_DEFINITION(object_id) NOT LIKE N'%sensitivity_tag%') "
     "ALTER TABLE records DROP CONSTRAINT ck_records_json"),
    ("the JSON is a Record document, not merely JSON",
     # pi-lens-ignore: sql-injection -- reviewed: DDL built only from module literals, never runtime input
     "IF OBJECT_ID(N'records', N'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.check_constraints "
     "WHERE name = 'ck_records_json') "
     f"ALTER TABLE records WITH CHECK ADD CONSTRAINT ck_records_json CHECK ({RECORD_JSON_CHECK_SQL})"),
    ("the JSON's source person is the column's source person when it names one",
     # pi-lens-ignore: sql-injection -- reviewed: DDL built only from module literals, never runtime input
     "IF OBJECT_ID(N'records', N'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.check_constraints "
     "WHERE name = 'ck_records_json_source_person_id') "
     "ALTER TABLE records WITH CHECK ADD CONSTRAINT ck_records_json_source_person_id CHECK ("
     "JSON_VALUE(record_json, '$.source_person_id') IS NULL "
     "OR JSON_VALUE(record_json, '$.source_person_id') = source_person_id)"),
    ("a record's type is one this codebase models",
     # pi-lens-ignore: sql-injection -- reviewed: DDL built only from module literals, never runtime input
     "IF OBJECT_ID(N'records', N'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.check_constraints "
     "WHERE name = 'ck_records_record_type') "
     "ALTER TABLE records WITH CHECK ADD CONSTRAINT ck_records_record_type "
     f"CHECK (record_type IN ({RECORD_TYPE_CHECK_SQL}))"),
    ("a record's topic is one this codebase declares",
     # pi-lens-ignore: sql-injection -- reviewed: DDL built only from module literals, never runtime input
     "IF OBJECT_ID(N'records', N'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.check_constraints "
     "WHERE name = 'ck_records_topic') "
     "ALTER TABLE records WITH CHECK ADD CONSTRAINT ck_records_topic "
     f"CHECK (JSON_VALUE(record_json, '$.topic') IS NOT NULL "
     f"AND JSON_VALUE(record_json, '$.topic') IN ({TOPIC_CHECK_SQL}))"),
    ("the two person keys on a record must name the same person",
     "IF OBJECT_ID(N'records', N'U') IS NOT NULL AND OBJECT_ID(N'tr_records_persona_agrees', N'TR') IS NULL "
     "EXEC(N'" + _quote_literal(PERSONA_AGREEMENT_TRIGGER) + "')"),
    ("the day view's appointments table exists on a database loaded before it did",
     "IF OBJECT_ID(N'people', N'U') IS NOT NULL AND OBJECT_ID(N'appointments', N'U') IS NULL "
     "CREATE TABLE appointments (appointment_id NVARCHAR(128) NOT NULL PRIMARY KEY, "
     "source_person_id NVARCHAR(128) NOT NULL, "
     "domain NVARCHAR(16) NOT NULL CONSTRAINT ck_appointments_domain "
     "CHECK (domain IN ('treatment', 'social_care')), "
     "scheduled_on DATE NOT NULL, status_text NVARCHAR(64) NULL, urgency_text NVARCHAR(128) NULL, "
     "specialty NVARCHAR(16) NULL, contact_record_id NVARCHAR(128) NULL, encounter_id NVARCHAR(128) NULL)"),
    ("appointments cannot name a person who is not in the corpus",
     "IF OBJECT_ID(N'appointments', N'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys "
     "WHERE name = 'fk_appointments_people') "
     "ALTER TABLE appointments WITH CHECK ADD CONSTRAINT fk_appointments_people "
     "FOREIGN KEY (source_person_id) REFERENCES people (source_person_id)"),
    ("appointments cannot name a lens the corpus does not declare",
     "IF OBJECT_ID(N'appointments', N'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys "
     "WHERE name = 'fk_appointments_encounters') "
     "ALTER TABLE appointments WITH CHECK ADD CONSTRAINT fk_appointments_encounters "
     "FOREIGN KEY (encounter_id) REFERENCES encounters (encounter_id)"),
    ("the day view reads one day's appointments through an index",
     "IF OBJECT_ID(N'appointments', N'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes "
     "WHERE name = 'ix_appointments_day' AND object_id = OBJECT_ID('appointments')) "
     "CREATE INDEX ix_appointments_day ON appointments (scheduled_on, encounter_id)"),
    ("the briefings cache table exists on a database loaded before it did",
     "IF OBJECT_ID(N'people', N'U') IS NOT NULL AND OBJECT_ID(N'briefings', N'U') IS NULL "
     "CREATE TABLE briefings (encounter_id NVARCHAR(128) NOT NULL, corpus_generation NVARCHAR(64) NOT NULL, "
     "rule_version NVARCHAR(80) NOT NULL, agent_version NVARCHAR(160) NOT NULL, "
     "built_at DATETIME2 NOT NULL, today DATE NOT NULL, status NVARCHAR(16) NOT NULL, "
     "picture_json NVARCHAR(MAX) NOT NULL, attention_json NVARCHAR(MAX) NOT NULL, "
     "CONSTRAINT pk_briefings PRIMARY KEY (encounter_id, corpus_generation, rule_version, agent_version))"),
    ("the encounters foreign key can be validated without scanning records",
     # pi-lens-ignore: sql-injection -- reviewed: DDL built only from module literals, never runtime input
     "IF OBJECT_ID(N'encounters', N'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes "
     "WHERE name = 'ix_encounters_person' AND object_id = OBJECT_ID('encounters')) "
     "CREATE INDEX ix_encounters_person ON encounters (source_person_id)"),
    ("the teardown delete resolves a persona through an index instead of scanning",
     # pi-lens-ignore: sql-injection -- reviewed: DDL built only from module literals, never runtime input
     "IF OBJECT_ID(N'records', N'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes "
     "WHERE name = 'ix_records_persona' AND object_id = OBJECT_ID('records')) "
     "CREATE NONCLUSTERED INDEX ix_records_persona ON records (persona_id)"),
)
# The default is a REPLACE, and by DELETE rather than by DROP. Two reasons, both from review:
#
#   * `MERGE` alone left orphans. Uploading a corpus with fewer people showed the old rows as well as the new
#     ones, which at the hackathon is "we uploaded the real data and the demo data is still there".
#   * A DROP takes a schema lock on all three tables for the whole load, so every request the running app
#     serves would block behind it. `DELETE` takes row locks, and Azure SQL's read-committed-snapshot default
#     means readers keep seeing the previous corpus until the transaction commits.
# The order is children first, which the foreign keys above now make mandatory rather than merely tidy.
REPLACE_STATEMENTS = ("DELETE FROM briefings", "DELETE FROM appointments", "DELETE FROM records",
                      "DELETE FROM encounters", "DELETE FROM people")


# --- the knowledge index: its own schema, which the corpus loader never touches ------------------------------
# `knowledge_chunks` holds one embedded row per committed knowledge item (a form question, a Käypä hoito
# citation, a pathway step) for `knowledge_search`. It is deliberately NOT in `SCHEMA_STATEMENTS`,
# `DROP_SCHEMA` or `REPLACE_STATEMENTS`: a corpus reload, even `--recreate`, must not drop the index, and the
# index must not depend on the corpus. Only `load_knowledge` (run as the loader identity, which owns DDL)
# creates, replaces or verifies it; the app identity reads it as `db_datareader` and is not widened.
# The row's `text` is what was embedded and is never returned to a reader: `id` is a ref resolved back
# through the committed snapshots, so a stored row cannot say anything the snapshot does not.
# Azure SQL's `VECTOR(n)` (float32) allows at most 1,998 dimensions; 1536 is what the embedding request asks for.
KNOWLEDGE_VECTOR_DIMENSIONS = 1536
KNOWLEDGE_TABLE = "knowledge_chunks"
KNOWLEDGE_SCHEMA_STATEMENTS: tuple[SchemaStatement, ...] = (
    SchemaStatement("table", KNOWLEDGE_TABLE,
                    f"CREATE TABLE {KNOWLEDGE_TABLE} (id NVARCHAR(128) NOT NULL PRIMARY KEY, "
                    "kind NVARCHAR(16) NOT NULL, source NVARCHAR(64) NOT NULL, text NVARCHAR(MAX) NOT NULL, "
                    f"embedding VECTOR({KNOWLEDGE_VECTOR_DIMENSIONS}) NOT NULL, "
                    "embedding_model NVARCHAR(128) NOT NULL, snapshot_version NVARCHAR(64) NOT NULL, "
                    "loaded_at DATETIME2 NOT NULL)"),
)
KNOWLEDGE_DROP_STATEMENTS = (f"DROP TABLE IF EXISTS {KNOWLEDGE_TABLE}",)
KNOWLEDGE_REPLACE_STATEMENTS = (f"DELETE FROM {KNOWLEDGE_TABLE}",)
# The vector arrives as a JSON array string (`json.dumps(vector)`) and is cast on the server.
KNOWLEDGE_INSERT = (f"INSERT INTO {KNOWLEDGE_TABLE} (id, kind, source, text, embedding, embedding_model, "
                    f"snapshot_version, loaded_at) VALUES (?, ?, ?, ?, CAST(CAST(? AS NVARCHAR(MAX)) AS VECTOR({KNOWLEDGE_VECTOR_DIMENSIONS})), "
                    "?, ?, SYSUTCDATETIME())")
KNOWLEDGE_COUNTS = f"SELECT kind, COUNT(*) FROM {KNOWLEDGE_TABLE} GROUP BY kind ORDER BY kind"
KNOWLEDGE_VERSIONS = (f"SELECT snapshot_version, embedding_model, COUNT(*), MAX(loaded_at) FROM {KNOWLEDGE_TABLE} "
                      "GROUP BY snapshot_version, embedding_model ORDER BY snapshot_version, embedding_model")
# Checked by `load_knowledge --verify` only; `INTEGRITY_EXPECTED` (the corpus loader's contract) is unchanged.
KNOWLEDGE_INTEGRITY_EXPECTED = {"knowledge_table": 1, "knowledge_primary_key": 1, "knowledge_vector_column": 1}
KNOWLEDGE_INTEGRITY_QUERY = f"""
SELECT
 (SELECT COUNT(*) FROM sys.tables WHERE name = '{KNOWLEDGE_TABLE}') AS knowledge_table,
 (SELECT COUNT(*) FROM sys.key_constraints
   WHERE parent_object_id = OBJECT_ID('{KNOWLEDGE_TABLE}') AND type = 'PK') AS knowledge_primary_key,
 (SELECT COUNT(*) FROM sys.columns c JOIN sys.types t ON c.user_type_id = t.user_type_id
   WHERE c.object_id = OBJECT_ID('{KNOWLEDGE_TABLE}') AND c.name = 'embedding' AND t.name = 'vector')
   AS knowledge_vector_column
"""


def verify_knowledge_integrity(cursor) -> dict:
    """Compare the catalogue against `KNOWLEDGE_INTEGRITY_EXPECTED`. Never raises: a miss is the result."""
    cursor.execute(KNOWLEDGE_INTEGRITY_QUERY)
    row = cursor.fetchone()
    columns = [column[0] for column in cursor.description]
    actual = {column: int(value or 0) for column, value in zip(columns, row)}
    missing = sorted(name for name, want in KNOWLEDGE_INTEGRITY_EXPECTED.items() if actual.get(name, 0) < want)
    unexpected = sorted(name for name, want in KNOWLEDGE_INTEGRITY_EXPECTED.items() if actual.get(name, 0) > want)
    return {"expected": dict(KNOWLEDGE_INTEGRITY_EXPECTED), "actual": actual,
            "missing": missing, "unexpected": unexpected, "ok": not missing and not unexpected}


# The one statement that cannot be an existence-guarded `ALTER`: the clustered key is whatever the engine
# named for the inline `PRIMARY KEY`, so it has to be discovered, dropped and recreated as `NONCLUSTERED`.
# Written as a single idempotent T-SQL batch — not as imperative Python — so the same text can be executed
# by a DB-API cursor, by `sqlcmd`, or pasted into a query editor. `scripts/harden_sql.py --print-sql` emits
# the whole hardening for exactly that reason.
RECLUSTER_STATEMENT = """
IF OBJECT_ID(N'records', N'U') IS NOT NULL
   # pi-lens-ignore: sql-injection -- reviewed: DDL built only from module literals, never runtime input
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID('records') AND name = 'cx_records')
BEGIN
    DECLARE @clustered sysname, @pk sysname, @sql nvarchar(max);
    SELECT @clustered = name FROM sys.indexes
        WHERE object_id = OBJECT_ID('records') AND type = 1;
    SELECT @pk = name FROM sys.key_constraints
        WHERE parent_object_id = OBJECT_ID('records') AND type = 'PK';
    IF @clustered IS NOT NULL
    BEGIN
        SET @sql = N'ALTER TABLE records DROP CONSTRAINT ' + QUOTENAME(@clustered);
        EXEC sp_executesql @sql;
    END
    SET @sql = N'CREATE UNIQUE CLUSTERED INDEX cx_records ON records (source_person_id, record_id)';
    EXEC sp_executesql @sql;
    IF @pk IS NOT NULL
    BEGIN
        SET @sql = N'ALTER TABLE records ADD CONSTRAINT ' + QUOTENAME(@pk)
                   + N' PRIMARY KEY NONCLUSTERED (record_id)';
        EXEC sp_executesql @sql;
    END
    IF EXISTS (SELECT 1 FROM sys.indexes
               WHERE object_id = OBJECT_ID('records') AND name = 'ix_records_person')
        DROP INDEX ix_records_person ON records;
    SELECT N'reclustered records on (source_person_id, record_id)' AS result;
END
ELSE
    SELECT N'already clustered on (source_person_id, record_id)' AS result;
"""


STATISTICS_STATEMENTS = tuple(f"UPDATE STATISTICS {table} WITH FULLSCAN"
                              for table in ("records", "encounters", "people", "appointments"))

# What the hardening is supposed to have produced, read back from the catalogue. `load_sql --verify` asserts
# this, so a dropped index or constraint is reported rather than assumed. Every named object here is one the
# hardening creates; the index set is exactly the set the read and delete paths depend on.
INTEGRITY_EXPECTED = {"foreign_keys": 5, "json_check": 1, "appointments_day_index": 1, "json_source_check": 1, "record_type_check": 1,
                      "topic_check": 1, "persona_trigger": 1,
                      "encounter_role_check": 1, "encounter_purpose_check": 1,
                      "encounter_holders_check": 1, "encounter_life_stage_check": 1,
                      "clustered_key": 1, "persona_index": 1,
                      "primary_key": 1, "legacy_index": 0}
INTEGRITY_QUERY = """
SELECT
 (SELECT COUNT(*) FROM sys.foreign_keys
   WHERE name IN ('fk_encounters_people', 'fk_records_people_source', 'fk_records_people_persona',
                  'fk_appointments_people', 'fk_appointments_encounters'))
   AS foreign_keys,
 (SELECT COUNT(*) FROM sys.indexes WHERE object_id = OBJECT_ID('appointments')
   AND name = 'ix_appointments_day') AS appointments_day_index,
 (SELECT COUNT(*) FROM sys.check_constraints WHERE name = 'ck_records_json') AS json_check,
 (SELECT COUNT(*) FROM sys.check_constraints
   WHERE name = 'ck_records_json_source_person_id') AS json_source_check,
 (SELECT COUNT(*) FROM sys.check_constraints WHERE name = 'ck_records_record_type') AS record_type_check,
 (SELECT COUNT(*) FROM sys.check_constraints WHERE name = 'ck_records_topic') AS topic_check,
 (SELECT COUNT(*) FROM sys.triggers WHERE name = 'tr_records_persona_agrees') AS persona_trigger,
 (SELECT COUNT(*) FROM sys.check_constraints WHERE name = 'ck_encounters_role') AS encounter_role_check,
 (SELECT COUNT(*) FROM sys.check_constraints WHERE name = 'ck_encounters_purpose') AS encounter_purpose_check,
 (SELECT COUNT(*) FROM sys.check_constraints WHERE name = 'ck_encounters_holders') AS encounter_holders_check,
 (SELECT COUNT(*) FROM sys.check_constraints
   WHERE name = 'ck_encounters_life_stage') AS encounter_life_stage_check,
 (SELECT COUNT(*) FROM sys.indexes WHERE object_id = OBJECT_ID('records')
   AND name = 'cx_records' AND type = 1) AS clustered_key,
 (SELECT COUNT(*) FROM sys.indexes WHERE object_id = OBJECT_ID('records')
   AND name = 'ix_records_persona') AS persona_index,
 (SELECT COUNT(*) FROM sys.indexes WHERE object_id = OBJECT_ID('records')
   AND name = 'ix_records_person') AS legacy_index,
 (SELECT COUNT(*) FROM sys.key_constraints
   WHERE parent_object_id = OBJECT_ID('records') AND type = 'PK') AS primary_key
"""


def hardening_sql() -> str:
    """The whole hardening as one idempotent script, for a client this package cannot drive."""
    statements = [statement for _label, statement in HARDEN_STATEMENTS]
    return "\nGO\n".join([*statements, RECLUSTER_STATEMENT, *STATISTICS_STATEMENTS])


def apply_schema_upgrade(cursor) -> None:
    """Add the record-binding columns an existing database lacks, BEFORE a load that writes them.

    Every statement here is also in `HARDEN_STATEMENTS`; this is the subset `load_sql --harden` must run
    before the load, because the records insert names the columns. It is a no-op on an empty database.
    """
    for _label, statement in RECORD_BINDING_COLUMN_STATEMENTS:
        cursor.execute(statement)


def apply_hardening(cursor) -> dict:
    """Run the hardening through a DB-API cursor, then read back what the database now holds."""
    for _label, statement in HARDEN_STATEMENTS:
        cursor.execute(statement)
    cursor.execute(RECLUSTER_STATEMENT)
    while cursor.description is not None and cursor.nextset():
        pass
    for statement in STATISTICS_STATEMENTS:
        cursor.execute(statement)
    return verify_integrity(cursor)


def verify_integrity(cursor) -> dict:
    """Compare the catalogue against `INTEGRITY_EXPECTED`. Never raises: a miss is the result."""
    cursor.execute(INTEGRITY_QUERY)
    row = cursor.fetchone()
    columns = [column[0] for column in cursor.description]
    actual = {column: int(value or 0) for column, value in zip(columns, row)}
    missing = sorted(name for name, want in INTEGRITY_EXPECTED.items() if actual.get(name, 0) < want)
    unexpected = sorted(name for name, want in INTEGRITY_EXPECTED.items() if actual.get(name, 0) > want)
    return {"expected": dict(INTEGRITY_EXPECTED), "actual": actual,
            "missing": missing, "unexpected": unexpected, "ok": not missing and not unexpected}


def token_attribute(token: str) -> bytes:
    """The `SQL_COPT_SS_ACCESS_TOKEN` value: a 4-byte little-endian LENGTH, then the token in UTF-16-LE.

    The length prefix is not decoration. Passing raw UTF-16 bytes — which is what this did first — makes the
    driver read the token's first four bytes as its length, and it SEGFAULTS: the process dies with exit code
    139 and writes nothing to stdout or stderr, so a container running it reports only that its logs are
    empty. Three job runs went by before the build-time import check moved the question somewhere it could be
    answered, and this is the answer: `msodbcsql18` accepts the buffer only in this layout.
    """
    encoded = token.encode("utf-16-le")
    return struct.pack("=i", len(encoded)) + encoded


def connection_string(server: str, database: str) -> str:
    """The ODBC string. Encryption is not optional and the certificate is verified."""
    return (f"DRIVER={{{DRIVER}}};SERVER={server};DATABASE={database};"
            f"Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30")


def access_token(client_id: str | None = None) -> str:
    """An Entra token for Azure SQL: the environment's, else the managed identity's."""
    explicit = os.environ.get("LUVN_SQL_ACCESS_TOKEN")
    if explicit:
        return explicit
    from azure.identity import ManagedIdentityCredential

    return ManagedIdentityCredential(client_id=client_id or None).get_token(SQL_SCOPE).token


def connect(server: str | None = None, database: str | None = None,
            client_id: str | None = None) -> Any:
    """Open a connection with an Entra access token. The only place `pyodbc` is imported."""
    import pyodbc

    server = server or os.environ.get("LUVN_SQL_SERVER") or ""
    database = database or os.environ.get("LUVN_SQL_DATABASE") or ""
    if not server or not database:
        raise RuntimeError("Azure SQL needs LUVN_SQL_SERVER and LUVN_SQL_DATABASE")
    token = access_token(client_id if client_id is not None else os.environ.get("LUVN_SQL_CLIENT_ID"))
    # The token goes in through a driver attribute, never as a connection-string value, so it cannot end up
    # in a log line or an exception message that quotes the string.
    return pyodbc.connect(connection_string(server, database), timeout=30,
                          attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_attribute(token)})


class SqlDeliveryIndex(Mapping[str, Persona]):
    """Lazy person mapping and owner catalog over Azure SQL; one person's rows per lookup.

    Mirrors `delivery.DeliveryIndex` method for method, including `close()`, because `store.Store` chooses
    between them by environment and must not be able to tell which it got.
    """

    def __init__(self, connection: Any):
        self._connection = connection
        self._lock = RLock()

    def _rows(self, sql: str, *params: Any) -> list[Sequence[Any]]:
        with self._lock:
            cursor = self._connection.cursor()
            try:
                cursor.execute(sql, *params) if params else cursor.execute(sql)
                return list(cursor.fetchall())
            finally:
                cursor.close()

    def encodings(self) -> list[tuple[str, str, str]]:
        return [(str(row[0]), str(row[1]), str(row[2] or ""))
                for row in self._rows("SELECT persona_id, source_person_id, label FROM people")]

    def has(self, persona_id: str) -> bool:
        return bool(self._rows("SELECT 1 FROM people WHERE persona_id = ?", persona_id))

    def __getitem__(self, persona_id: str) -> Persona:
        row = self._rows("SELECT persona_id, source_person_id, label FROM people WHERE persona_id = ?",
                         persona_id)
        if not row:
            raise KeyError(persona_id)
        found = row[0]
        records = [self._record(str(entry[0]), persona_id=str(found[0]))
                   for entry in self._rows("SELECT record_json FROM records WHERE source_person_id = ?",
                                           found[1])]
        return Persona(persona_id=str(found[0]), label=str(found[2] or found[0]),
                       source_person_id=str(found[1]), records=records)

    @staticmethod
    def _record(stored: Any, *, persona_id: str) -> Record:
        """One stored `record_json` as a `Record`, or a NAMED `CorruptCorpusError`.

        The check constraint is now the full envelope, so this should not fire on a database this loader
        wrote. It still exists because a `db_datawriter` identity (or a migration) can plant a row the
        older CHECK admitted, and because a person whose read fails must fail CLOSED and SPECIFICALLY: the
        message names the record, the field and the reason, rather than surfacing a bare `ValidationError`
        that the API reports as a missing encounter.
        """
        try:
            if hasattr(stored, "model_validate_json"):
                return stored  # already a Record (a stand-in that pre-validated its rows)
            text = stored.decode("utf-8") if isinstance(stored, (bytes, bytearray)) else str(stored)
            return derived_record_state(Record.model_validate_json(text))
        except Exception as exc:  # noqa: BLE001 - every failure here is "this stored row is not a Record"
            fields = ", ".join(str(error.get("loc", ("?",))[0]) for error in
                               getattr(exc, "errors", lambda: [])()) or "unknown field"
            record_id = ""
            try:
                record_id = str(json.loads(stored).get("record_id") or "")
            except Exception:  # noqa: BLE001 - an unreadable row has no id to name
                record_id = ""
            raise CorruptCorpusError(
                f"the stored corpus is corrupt: a record for person {persona_id!r}"
                + (f" ({record_id!r})" if record_id else "")
                + f" does not validate as the record envelope ({type(exc).__name__}: {fields}). "
                  f"This is a data defect, not a missing encounter; reload the corpus with "
                  f"`python -m app.load_sql --recreate`",
                persona_id=persona_id, record_id=record_id) from exc

    def persona(self, persona_id: str) -> Persona:
        return self[persona_id]

    def __iter__(self) -> Iterator[str]:
        return iter([str(row[0]) for row in self._rows("SELECT persona_id FROM people ORDER BY persona_id")])

    def __len__(self) -> int:
        row = self._rows("SELECT COUNT(*) FROM people")
        count = row[0][0] if row else 0
        return count if isinstance(count, int) and not isinstance(count, bool) else int(count)

    def encounter_index(self) -> dict[str, Encounter]:
        rows = self._rows(
            "SELECT e.encounter_id, e.role, e.purpose, e.source_person_id, e.consent_holders, "
            "e.life_stage, p.persona_id FROM encounters e "
            "JOIN people p ON p.source_person_id = e.source_person_id")
        return {str(row[0]): Encounter(
            encounter_id=str(row[0]), persona_id=str(row[6]), role=str(row[1]),
            purpose=cast(Purpose, str(row[2])), source_person_id=str(row[3]),
            consent_holders=cast(tuple[ConsentHolder, ...],
                                 tuple(part for part in str(row[4] or "self").split(",") if part)),
            life_stage=cast(LifeStage, str(row[5] or "adult")))
                for row in rows}

    def encounter_labels(self) -> dict[str, tuple[str, str]]:
        return {str(row[0]): (str(row[1] or ""), str(row[2] or ""))
                for row in self._rows("SELECT encounter_id, example_label, demo_name FROM encounters")}

    def encounter_metadata(self) -> list[EncounterMetadata]:
        """Every encounter's worklist metadata, joined to its person, in id order. Never reads `records`.

        The statement names `encounters` and `people` only. Records are read one person at a time, by
        `__getitem__`, and only the picture route resolves a person; the worklist must not reach it.
        """
        rows = self._rows(
            "SELECT e.encounter_id, e.source_person_id, e.role, e.purpose, e.example_label, e.demo_name, "
            "e.consent_holders, e.life_stage FROM encounters e "
            "JOIN people p ON p.source_person_id = e.source_person_id ORDER BY e.encounter_id")
        return [metadata_from_row(*tuple(row)) for row in rows]

    def appointments_for_day(self, day: date) -> list[Appointment]:
        """The day's scheduled occasions, each joined to the lens that opens it. Never reads `records`."""
        rows = self._rows(
            "SELECT a.appointment_id, a.source_person_id, a.domain, a.scheduled_on, a.status_text, "
            "a.urgency_text, a.specialty, a.contact_record_id, a.encounter_id, "
            "e.role, e.purpose, e.example_label FROM appointments a "
            "LEFT JOIN encounters e ON e.encounter_id = a.encounter_id "
            "WHERE a.scheduled_on = ? ORDER BY a.appointment_id", day)
        return [appointment_from_row(*tuple(row)) for row in rows]

    def appointment_days(self, start: date, end: date, role: str | None = None) -> dict[str, int]:
        """How many scheduled occasions each day in [start, end] carries (for one role's lens when given),
        keyed by ISO date; empty days absent."""
        if role is None:
            rows = self._rows("SELECT scheduled_on, COUNT(*) FROM appointments WHERE scheduled_on BETWEEN ? AND ? "
                              "GROUP BY scheduled_on", start, end)
        else:
            rows = self._rows("SELECT a.scheduled_on, COUNT(*) FROM appointments a "
                              "JOIN encounters e ON e.encounter_id = a.encounter_id "
                              "WHERE a.scheduled_on BETWEEN ? AND ? AND e.role = ? GROUP BY a.scheduled_on",
                              start, end, role)
        return {str(row[0])[:10]: int(row[1]) for row in rows}

    def briefing_cache(self):
        """The `briefings` table, through this index's own connection and lock."""
        from .briefings import SqlBriefingCache

        return SqlBriefingCache(self._connection, self._lock)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def corpus_generation(self) -> str | None:
        """The generation `load_state` names for a completed load — read back, not re-derived.

        This is a plain read of the table the loader (below) already maintains; it adds no column and
        changes nothing about how a load runs. An in-progress or absent load has no completed generation to
        report, so this is `None` rather than a half-loaded id.
        """
        rows = self._rows("SELECT generation FROM load_state WHERE complete = 1")
        return str(rows[0][0]) if rows else None

    def probe(self) -> None:
        """A cheap liveness probe against the database this index reads. Raises when it cannot answer.

        Deliberately minimal: it proves the connection this index already holds can round-trip a statement,
        nothing more. It is used by readiness, never by liveness — a database outage must not restart the
        container, because the process is healthy and the data is not.
        """
        self._rows("SELECT 1")


def open_sql_index(server: str | None = None, database: str | None = None,
                   client_id: str | None = None) -> SqlDeliveryIndex:
    return SqlDeliveryIndex(connect(server=server, database=database, client_id=client_id))


def stored_counts(connection: Any) -> dict[str, int]:
    """What the database actually holds, for an operator asking "is my upload in there?".

    The staging container is where a corpus is uploaded and the database is where it is served from; the two
    can differ after a failed or partial load, and the difference is invisible from the API when the ids
    happen to match. Three counts answer it in one query each, and they are the counts the loader reports on
    the way in — so a mismatch is the load, not the corpus.
    """
    cursor = connection.cursor()
    try:
        counts = {}
        for table in ("people", "encounters", "appointments", "records"):
            # pi-lens-ignore: sql-injection -- reviewed: table names are a literal tuple
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            row = cursor.fetchone()
            counts[table] = int(row[0]) if row else 0
        return counts
    finally:
        cursor.close()


# ---------------------------------------------------------------------------------------------------------
# The batched, resumable load (work order L1B; ADR-0025 section 6 as the oracle states it; the installed
# `bulk-load-and-bulk-copy` skill).
#
# The old loader was one transaction over ~15k person MERGEs, ~14.8k encounter MERGEs and ~401k record MERGEs,
# so nothing committed until the whole corpus had gone through, and a failure at the end rolled back everything.
# This one commits every LOAD_BATCH items, and keeps a checkpoint in the SAME transaction as the batch it
# describes. The checkpoint is the only thing a resume trusts:
#
#   * `load_state` holds the corpus generation (a content hash of the input files), how many items are committed,
#     and the key of the last committed item. A resume happens only for the SAME generation, and only if the item at
#     the checkpoint still has the recorded key; otherwise the load starts again from a replaced stage.
#   * Writes are plain INSERTs, not MERGE. In a stage that a fresh start has just emptied, a row that already
#     exists is a duplicate, and a duplicate is a rejected row: a hard failure, never a silent upsert.
#   * A failed batch is rolled back whole, then replayed one row at a time under savepoints to name the rejected
#     rows in a readable JSONL file (keys and error text only: never record content). The load then stops.
# ---------------------------------------------------------------------------------------------------------
LOAD_STATE_STATEMENT = (
    "IF OBJECT_ID(N'load_state', N'U') IS NULL "
    "CREATE TABLE load_state (generation NVARCHAR(64) NOT NULL PRIMARY KEY, "
    "items_committed INT NOT NULL, last_key NVARCHAR(256) NULL, complete BIT NOT NULL)")
LOAD_STATE_READ = "SELECT generation, items_committed, last_key, complete FROM load_state"
LOAD_STATE_RESET = "DELETE FROM load_state"
LOAD_STATE_START = "INSERT INTO load_state (generation, items_committed, last_key, complete) VALUES (?, 0, NULL, 0)"
LOAD_STATE_ADVANCE = "UPDATE load_state SET items_committed = ?, last_key = ?, complete = ? WHERE generation = ?"
PEOPLE_INSERT = "INSERT INTO people (persona_id, source_person_id, label) VALUES (?, ?, ?)"
ENCOUNTER_INSERT = ("INSERT INTO encounters (encounter_id, source_person_id, role, purpose, example_label, "
                    "demo_name, consent_holders, life_stage) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
RECORD_INSERT = ("INSERT INTO records (record_id, source_person_id, persona_id, record_type, encounter_id, "
                 "case_id, record_json) VALUES (?, ?, ?, ?, ?, ?, ?)")
APPOINTMENT_INSERT = ("INSERT INTO appointments (appointment_id, source_person_id, domain, scheduled_on, status_text, "
                      "urgency_text, specialty, contact_record_id, encounter_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
_INSERT_BY_KIND = {"person": PEOPLE_INSERT, "encounter": ENCOUNTER_INSERT, "appointment": APPOINTMENT_INSERT,
                   "record": RECORD_INSERT}
# Hashing reads in this chunk size; the 147 MB records file is streamed, never held whole.
_HASH_CHUNK_BYTES = 1024 * 1024


class LoadRejected(RuntimeError):
    """One or more rows in a batch were refused by the database. The batch is rolled back; the rejects are on disk."""


class LoadResumeMismatch(RuntimeError):
    """A checkpoint exists for this generation, but the item it names is not the item the input now yields there."""


def corpus_generation(source_dir: Path, mapping_path: Path | None = None) -> str:
    """A content hash of every input file, so a generation is the bytes it was loaded from.

    The same bytes give the same id on a retry, which is what makes a retry a resume; different bytes give a
    different id, which is what stops a resume from splicing one corpus onto another.
    """
    digest = hashlib.sha256()
    for path in sorted(entry for entry in source_dir.iterdir() if entry.is_file()):
        digest.update(path.name.encode("utf-8") + b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(_HASH_CHUNK_BYTES), b""):
                digest.update(chunk)
    if mapping_path is not None:
        digest.update(b"mapping\0" + Path(mapping_path).read_bytes())
    return digest.hexdigest()[:32]


def _write_batch(connection: Any, cursor: Any, generation: str, pending: list, position: int, last_key: str | None,
                 complete: bool, rejects_path: Path) -> None:
    """Write one batch and its checkpoint, then commit. On failure: roll back, name the rejects, raise.

    `pending` is in plan order, so consecutive items of one kind go out as one executemany: people before the
    encounters and records that reference them, exactly as the single transaction used to order them.
    """
    try:
        index = 0
        while index < len(pending):
            kind = pending[index][0]
            end = index
            while end < len(pending) and pending[end][0] == kind:
                end += 1
            cursor.executemany(_INSERT_BY_KIND[kind], [item[2] for item in pending[index:end]])
            index = end
        cursor.execute(LOAD_STATE_ADVANCE, position, last_key, bool(complete), generation)
        connection.commit()
    except Exception as error:  # noqa: BLE001 - every failure here is a batch that did not land
        connection.rollback()
        rejects = _name_rejects(connection, pending)
        if not rejects:
            raise
        rejects_path.parent.mkdir(parents=True, exist_ok=True)
        with rejects_path.open("w", encoding="utf-8") as stream:
            for reject in rejects:
                stream.write(json.dumps(reject, ensure_ascii=False) + "\n")
        raise LoadRejected(
            f"{len(rejects)} row(s) rejected in the batch ending at item {position}: "
            f"{type(error).__name__}: {error}. The batch is rolled back; earlier batches are committed and the "
            f"checkpoint is at the batch before it. Rejects, with keys and error text only, are in {rejects_path}"
        ) from error


def _name_rejects(connection: Any, pending: list) -> list[dict]:
    """Replay a failed batch row by row, each under a savepoint, and name the rows that the database refuses.

    Nothing here commits: the transaction is rolled back at the end whatever it found. A trigger that rolls the
    whole transaction back (the persona-agreement trigger does) ends the replay; the rows after it are then
    reported as one unattributed entry rather than guessed at.
    """
    cursor = connection.cursor()
    rejects: list[dict] = []
    try:
        for kind, key, params in pending:
            cursor.execute("SAVE TRANSACTION row_probe")
            try:
                cursor.execute(_INSERT_BY_KIND[kind], *params)
            except Exception as error:  # noqa: BLE001 - the database's refusal is the datum
                rejects.append({"kind": kind, "key": key, "error": f"{type(error).__name__}: {error}"})
                cursor.execute("ROLLBACK TRANSACTION row_probe")
    except Exception as error:  # noqa: BLE001 - the engine ended the transaction; stop, do not guess
        rejects.append({"kind": "unattributed", "key": "", "error": f"replay stopped: {type(error).__name__}: {error}"})
    finally:
        connection.rollback()
        cursor.close()
    return rejects


def load_corpus(source_dir: Any = None, connection: Any = None, recreate: bool = False,
                replace: bool = True, mapping: MappingSpec | str | Path | None = None,
                batch_rows: int = LOAD_BATCH, rejects_path: Path | None = None) -> dict[str, Any]:
    """Load a corpus directory in the declared shape into Azure SQL, through the SAME mapping.

    It calls the delivery importer's own `_read_profiles`, `_read_encounters`, `_iter_rows` and
    `_bind_record`, so a vendor name is normalised by `PAYLOAD_KEY_ALIASES` and a record is validated
    exactly once, in one place, whether it is destined for the local index or SQL. Two loaders with two
    mappings would be two different products wearing one name.

    `mapping` makes this the arrival-day path as well: the same declarative file and the same streaming
    readers `import_delivery` uses, with the people derived from the mapped `source_person_id` and each
    person written the first time a row names them, so the foreign keys hold. Without `mapping` nothing
    changes: `source_dir` is a canonical directory holding `profiles.csv`, `encounters.csv` and
    `records.jsonl`/`records.csv`.

    Returns the manifest counts this load read from its inputs. `main()` checks them against what the database
    holds; this function does not, because the count is only meaningful against a real engine.
    """
    source_dir = Path(source_dir or os.environ.get("LUVN_DELIVERY_DIR") or DEFAULT_DELIVERY_DIR)
    spec: MappingSpec | None = None
    mapping_path: Path | None = None
    if mapping is not None:
        if isinstance(mapping, MappingSpec):
            spec = mapping
        else:
            mapping_path = Path(mapping)
            spec = MappingSpec.load(mapping_path)
        spec.require_targets(REQUIRED_ENVELOPE_TARGETS)
    connection = connection if connection is not None else connect()
    profiles = _read_profiles(source_dir) if spec is None else {}
    encounters = _read_encounters(source_dir, profiles) if spec is None else []
    appointments = (_read_appointments(source_dir, profiles, {row["encounter_id"] for row in encounters})
                    if spec is None else [])
    generation = corpus_generation(source_dir, mapping_path)
    rejects_path = rejects_path or Path(os.environ.get("LUVN_LOAD_REJECTS")
                                        or Path(tempfile.gettempdir()) / "luvn-load-rejects.jsonl")
    records_seen = {"count": 0}
    rule_matches: dict[str, int] = {}

    def plan() -> Iterator[tuple[str, str, tuple]]:
        """Every item the load writes, in the one order a resume can rely on: people, encounters, appointments, records."""
        for source_person_id, profile in profiles.items():
            yield "person", source_person_id, (profile["persona_id"], source_person_id, profile.get("label") or "")
        for row in encounters:
            yield "encounter", row["encounter_id"], (
                row["encounter_id"], row["source_person_id"], row["role"], row["purpose"],
                row.get("example_label") or "", row.get("demo_name") or "",
                ",".join(row.get("consent_holders") or ("self",)), row.get("life_stage") or "adult")
        for row in appointments:
            yield "appointment", row["appointment_id"], tuple(row[column] for column in APPOINTMENT_COLUMNS)
        rows: Any
        if spec is None:
            rows = ((None, row, None) for row in _iter_rows(source_dir))
        else:
            rows = _iter_mapped_rows(source_dir, spec, rule_matches)
        for _locator, row, row_error in rows:
            if row_error is not None:
                raise row_error
            if row is None:
                continue
            if spec is not None and row["source_person_id"] not in profiles:
                key = row["source_person_id"]
                profiles[key] = {"persona_id": _safe_persona_id(key, key), "label": ""}
                yield "person", key, (profiles[key]["persona_id"], key, "")
            record = _bind_record(row, profiles)
            # `_bind_record` has already refused a record with no source person, so the key is present here.
            persona_id = profiles[cast(str, record.source_person_id)]["persona_id"]
            records_seen["count"] += 1
            # ADR-0013's binding, written only when the envelope carries it; NULL means the corpus stated
            # none, which is the current state of every shipped corpus and is deliberately not invented.
            yield "record", record.record_id, (record.record_id, record.source_person_id, persona_id,
                                              record.record_type, record.encounter_id, record.case_id,
                                              record.model_dump_json())

    cursor = connection.cursor()
    cursor.fast_executemany = True
    batches = 0
    committed = {"person": 0, "encounter": 0, "appointment": 0, "record": 0}

    def commit_chunk(pending: list, position: int, last_key: str | None, complete: bool) -> None:
        nonlocal batches
        _write_batch(connection, cursor, generation, pending, position, last_key, complete, rejects_path)
        batches += 1
        for kind, _key, _params in pending:
            committed[kind] += 1
        # The job log's progress line. It is printed only after the commit returned, so a line in the log means
        # those rows are in the database; `commits` counts data commits, not the schema and reset commits.
        print(f"load_corpus: chunk {batches} committed; rows committed this run {sum(committed.values())} "
              f"(people {committed['person']}, encounters {committed['encounter']}, "
              f"appointments {committed['appointment']}, records {committed['record']}); position {position}; "
              f"data commits {batches}", flush=True)
    try:
        if recreate:
            for statement in DROP_SCHEMA:
                cursor.execute(statement)
        cursor.execute(LOAD_STATE_STATEMENT)
        for entry in SCHEMA_STATEMENTS:
            # Each statement is guarded by the ONE object it creates (`entry.guard`), never by a substring of
            # its SQL text: the old substring test skipped every ADD CONSTRAINT and re-ran every CREATE INDEX.
            cursor.execute(entry.guarded)
        # Retired indexes come off on every load, not only on --recreate; see RETIRED_INDEX_STATEMENTS.
        for statement in RETIRED_INDEX_STATEMENTS:
            cursor.execute(statement)
        # The schema commits on its own: a failed load must not roll back a table it has already created.
        connection.commit()

        cursor.execute(LOAD_STATE_READ)
        state = cursor.fetchone()
        if state is not None and state[0] == generation:
            # Same bytes as a checkpoint: resume after what is committed. A complete checkpoint skips everything.
            skip = sys.maxsize if state[3] else int(state[1])
            resume_key = state[2]
        else:
            # A different generation, or none: the stage is replaced, so nothing from another corpus can survive
            # into this one. Deleted and checkpointed in one transaction, so a crash here leaves no checkpoint.
            skip, resume_key = 0, None
            if replace:
                for statement in REPLACE_STATEMENTS:
                    cursor.execute(statement)
            cursor.execute(LOAD_STATE_RESET)
            cursor.execute(LOAD_STATE_START, generation)
            connection.commit()

        position = 0
        pending: list[tuple[str, str, tuple]] = []
        for kind, key, params in plan():
            position += 1
            if position == skip and resume_key is not None and resume_key != f"{kind}:{key}":
                raise LoadResumeMismatch(
                    f"checkpoint for generation {generation} names {resume_key!r} as its last committed item, "
                    f"but the input yields {kind}:{key!r} at that position; the corpus or its mapping changed. "
                    f"Refusing to resume; the stage will be replaced by a fresh load")
            if position <= skip:
                continue
            pending.append((kind, f"{kind}:{key}", params))
            if len(pending) >= batch_rows:
                commit_chunk(pending, position, pending[-1][1], False)
                pending = []
        if skip != sys.maxsize and position < skip:
            raise LoadResumeMismatch(
                f"checkpoint for generation {generation} claims {skip} committed items, but the input yields only "
                f"{position}; refusing to resume")
        if spec is not None:
            silent = _rules_matched_nothing(spec, rule_matches)
            if silent:
                # Before the final checkpoint, so a mapping typo leaves the stage INCOMPLETE rather than marked
                # complete with a partly-empty corpus. It is a stage, so the serving database was never at risk.
                raise DeliveryShapeError(
                    f"mapping {spec.version} has rules that matched no row: {'; '.join(silent)}; a "
                    f"source_path that exists nowhere is a typo, not a column the vendor left empty",
                    code="rules_matched_nothing", values={"rules": silent})
        last_key = pending[-1][1] if pending else resume_key
        commit_chunk(pending, position, last_key, True)
    except Exception:
        # Explicit, as the old single transaction was: anything not yet committed is discarded here, and the
        # checkpoint on disk still names the last batch that did land.
        connection.rollback()
        raise
    finally:
        cursor.close()
    return {"profiles": len(profiles), "encounters": len(encounters), "appointments": len(appointments),
            "records": records_seen["count"],
            "generation": generation, "batches": batches, "items_seen": position,
            "resumed_after": skip if 0 < skip < sys.maxsize else None}


def log_rate_readings(connection: Any) -> dict[str, Any]:
    """The governor's ceiling and the waits it caused, for the database this connection is on. Never raises.

    The governor is a service behaviour: on the local container the first query is refused (Msg 208) and that is
    reported as "unavailable", not as zero. The waits are cumulative for the database since it was created, so a
    load's own cost is the difference between two readings.
    """
    readings: dict[str, Any] = {}
    cursor = connection.cursor()
    try:
        try:
            cursor.execute("SELECT primary_max_log_rate / 1048576.0 FROM sys.dm_user_db_resource_governance")
            row = cursor.fetchone()
            readings["primary_max_log_MiB_per_sec"] = float(row[0]) if row and row[0] is not None else None
        except Exception as error:  # noqa: BLE001 - absence is a reading, not a crash
            readings["primary_max_log_MiB_per_sec"] = f"unavailable: {type(error).__name__}: {error}"
        try:
            cursor.execute("SELECT wait_type, waiting_tasks_count, wait_time_ms FROM sys.dm_db_wait_stats "
                           "WHERE wait_type LIKE N'%LOG_RATE_GOVERNOR%'")
            readings["log_rate_governor_waits"] = [
                {"wait_type": str(row[0]), "waiting_tasks": int(row[1]), "wait_ms": int(row[2])}
                for row in cursor.fetchall()]
        except Exception as error:  # noqa: BLE001 - absence is a reading, not a crash
            readings["log_rate_governor_waits"] = f"unavailable: {type(error).__name__}: {error}"
    finally:
        cursor.close()
    return readings
