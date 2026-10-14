"""Azure SQL corpus source: the mapping is the delivery importer's, and the two indexes must agree.

The driver is deliberately absent here. `sqldelivery` imports `pyodbc` inside `connect()` and takes an
already-open connection, so these tests exercise the real row-to-`Persona` mapping against a stand-in
connection. That is not a workaround for a missing driver — a module that cannot be imported or tested
without a network driver is a module with no tests.

The equivalence test is the one that matters: the same corpus, loaded by `import_delivery` into its SQLite
index and served by `DeliveryIndex`, must produce exactly what `SqlDeliveryIndex` produces. If those two ever
disagree, a briefing depends on which source the environment happened to select.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
import shutil
import tempfile
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path

import pytest

from app import delivery, main
from app.sqldelivery import (CorruptCorpusError, CorpusBlobTooLarge, SqlDeliveryIndex,
                             _stream_blob_to, fetch_corpus_blob, load_corpus, stored_counts, token_attribute)
from app.store import Store, get_store
from fastapi.testclient import TestClient

PRINCIPAL = {"x-ms-client-principal-id": "oid-1234", "x-ms-client-principal-name": "nurse@example.fi"}

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "data" / "experimental" / "delivery-demo"


class _Cursor:
    def __init__(self, connection: "_StandIn") -> None:
        self._connection = connection
        self._result: list[tuple] = []

    def execute(self, sql: str, *params) -> None:
        self._connection.statements.append((sql, params))
        if self._connection.serves_data:
            self._result = self._connection.answer(sql, params)

    def executemany(self, sql: str, rows) -> None:
        for row in rows:
            self.execute(sql, *row)

    def fetchall(self) -> list[tuple]:
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None

    def close(self) -> None:
        pass


class _StandIn:
    """A connection that answers the index's three queries from the file import's own index.

    It is a *translator*, not a mock: it reads the SQLite index and the per-person JSONL that
    `import_delivery` produced, so the rows it hands back are the corpus, and any disagreement between the
    two index implementations is a real disagreement rather than a fixture difference.
    """

    def __init__(self, index: delivery.DeliveryIndex, output_dir: Path) -> None:
        self._index = index
        self._dir = output_dir
        self.statements: list[tuple[str, tuple]] = []
        self.serves_data = True
        self.committed = 0
        # Set to a source_person_id to make that person's records include one row that does NOT validate
        # as a `Record` -- a stand-in for a planted row written past the old, weaker CHECK. The read path
        # must fail closed and NAME it, not surface a bare `ValidationError`.
        self.corrupt_source_person_id: str | None = None

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self.committed += 1

    def close(self) -> None:
        pass

    def answer(self, sql: str, params: tuple) -> list[tuple]:
        flat = " ".join(sql.split())
        if flat.startswith("SELECT persona_id, source_person_id, label FROM people WHERE"):
            return [row for row in self._index.encodings() if row[0] == params[0]]
        if flat.startswith("SELECT persona_id, source_person_id, label FROM people"):
            return list(self._index.encodings())
        if flat.startswith("SELECT persona_id FROM people"):
            return [(identifier,) for identifier in self._index]
        if flat.startswith("SELECT 1 FROM people"):
            return [(1,)] if self._index.has(str(params[0])) else []
        if flat.startswith("SELECT COUNT(*)"):
            # Per table, because the operator's check is "did my upload land", and a stand-in that answered
            # every count with the people count would make that check unfalsifiable.
            if "FROM encounters" in flat:
                return [(len(self._index.encounter_index()),)]
            if "FROM records" in flat:
                return [(sum(len(persona.records) for persona in self._index.values()),)]
            if "FROM appointments" in flat:
                return [(0,)]  # the stand-in serves a corpus with no appointments.csv
            return [(len(self._index),)]
        if flat.startswith("SELECT record_json FROM records"):
            persona = next(p for p in self._index.values() if p.source_person_id == params[0])
            rows = [(record.model_dump_json(),) for record in persona.records]
            if self.corrupt_source_person_id and params[0] == self.corrupt_source_person_id:
                # The review's exact admitted row: every DB check the OLD constraint made passes, and the
                # envelope refuses it (missing recorded_at, authored_by_role, sensitivity_tag, consent_scope).
                rows.insert(0, ('{"record_id":"planted","source":"s","record_type":"perustieto",'
                                '"topic":"demografia","payload":{}}',))
            return rows
        if flat.startswith("SELECT e.encounter_id, e.source_person_id, e.role, e.purpose, e.example_label"):
            # The worklist's metadata read. It must be answered before the encounter-binding branch below,
            # whose prefix it shares. Derived from the file index's own binding and labels, so the equivalence
            # test compares the SQL mapping against the file index's answer rather than against itself.
            labels = self._index.encounter_labels()
            return sorted(((encounter.encounter_id, encounter.source_person_id, encounter.role,
                            encounter.purpose, labels[encounter.encounter_id][0],
                            labels[encounter.encounter_id][1], ",".join(encounter.consent_holders),
                            encounter.life_stage)
                           for encounter in self._index.encounter_index().values()), key=lambda row: row[0])
        if flat.startswith("SELECT e.encounter_id"):
            rows = []
            for encounter in self._index.encounter_index().values():
                rows.append((encounter.encounter_id, encounter.role, encounter.purpose,
                             encounter.source_person_id, ",".join(encounter.consent_holders),
                             encounter.life_stage, encounter.persona_id))
            return rows
        if flat.startswith("SELECT encounter_id, example_label, demo_name"):
            return [(key, value[0], value[1]) for key, value in self._index.encounter_labels().items()]
        if flat.startswith("SELECT generation FROM load_state"):
            # This stand-in has no `load_state` table: the fixture has no completed-load generation to name,
            # exactly the "not reachable" case `Store.corpus_generation()` must answer `None` for.
            return []
        raise AssertionError(f"the index issued a query this test does not know: {flat}")


@pytest.fixture(scope="module")
def file_import(tmp_path_factory) -> tuple[delivery.DeliveryIndex, Path]:
    output = tmp_path_factory.mktemp("sql-equivalence")
    delivery.import_delivery(DEMO, output)
    index = delivery.DeliveryIndex(output)
    yield index, output
    index.close()


def _sql_index(file_import) -> SqlDeliveryIndex:
    index, output = file_import
    return SqlDeliveryIndex(_StandIn(index, output))


def test_the_sql_index_answers_exactly_what_the_file_index_answers(file_import):
    """The whole reason this module exists: two sources, one corpus, identical pictures."""
    file_index, _ = file_import
    sql_index = _sql_index(file_import)
    assert sql_index.encodings() == file_index.encodings()
    assert sorted(sql_index) == sorted(file_index)
    assert len(sql_index) == len(file_index)
    assert sql_index.encounter_index() == file_index.encounter_index(), (
        "the encounter catalog carries the permission axes, so a disagreement here is a permission "
        "disagreement, not a cosmetic one")
    assert sql_index.encounter_labels() == file_index.encounter_labels()
    assert sql_index.encounter_metadata() == file_index.encounter_metadata(), (
        "the worklist's metadata read must answer the same rows from both sources")
    assert [entry.encounter_id for entry in sql_index.encounter_metadata()] == sorted(sql_index.encounter_index()), (
        "the worklist's order is the encounter id, so a page is the same page from either source")
    for persona_id in file_index:
        assert sql_index[persona_id] == file_index[persona_id], (
            f"{persona_id} differs between the two corpus sources")
        assert sql_index.has(persona_id) is True
    assert sql_index.has("no-such-person") is False


def test_the_token_attribute_is_length_prefixed():
    """The byte layout `msodbcsql18` requires, asserted where a driver is not needed.

    This is the defect that cost three job runs: raw UTF-16 bytes make the driver read the token's first four
    bytes as a length and segfault, which in a container presents as an empty log rather than an error.
    """
    import struct

    token = "abc"
    attribute = token_attribute(token)
    declared = struct.unpack("=i", attribute[:4])[0]
    assert declared == len(attribute) - 4, "the prefix is the length of what follows"
    assert attribute[4:] == token.encode("utf-16-le")
    assert attribute[4:] != token.encode("utf-8"), (
        "UTF-8 with a length prefix is still wrong: the driver wants UTF-16-LE after the length")
    # A long token, so the length does not happen to fit in one byte and hide an error.
    long_token = "x" * 3000
    assert struct.unpack("=i", token_attribute(long_token)[:4])[0] == 6000


def test_a_missing_person_is_a_key_error_not_an_empty_person(file_import):
    """An unknown person must not become a briefing with no records: absent is not empty."""
    sql_index = _sql_index(file_import)
    with pytest.raises(KeyError):
        sql_index["demo-persona-nobody"]


def test_the_guardian_lens_keeps_its_declared_holders_through_sql(file_import):
    """The axis that inverted once already, asserted at the second source as well as the first."""
    sql_index = _sql_index(file_import)
    catalogue = sql_index.encounter_index()
    assert catalogue["demo-enc-09-guardian-split"].consent_holders == ("guardian",)
    assert catalogue["demo-enc-09-guardian-split"].life_stage == "child"
    assert catalogue["demo-enc-01-medication-conflict"].consent_holders == ("self",)


def test_the_loader_uses_the_delivery_importers_own_mapping(tmp_path: Path):
    """One mapping, not two: a vendor key must be normalised identically for SQL and for the local index.

    The corpus is the shipped sample, whose lab result carries `tulkinta` and whose basic info carries
    `näyttötunniste` — names only `PAYLOAD_KEY_ALIASES` knows. If the SQL loader carried its own mapping,
    those rows would land here unnormalised and the record would be rejected downstream instead.
    """
    stand_in = _StandIn(delivery.DeliveryIndex(_empty_index(tmp_path)), tmp_path)
    stand_in.serves_data = False
    counts = load_corpus(REPO / "data" / "experimental" / "delivery-sample", stand_in)
    assert counts["profiles"] == 2 and counts["records"] == 11
    # Schema, the fresh-start reset, and ONE batch (13 items, under LOAD_BATCH): three commits, not one per row.
    assert stand_in.committed == 3

    inserted = [json.loads(params[-1]) for sql, params in stand_in.statements
                if "INSERT INTO records (record_id, source_person_id, persona_id, record_type, encounter_id, "
                   "case_id, record_json)" in " ".join(sql.split())]
    assert inserted, "the loader inserted no records"
    # The normalised key, not the vendor spelling: `tulkinta` is the canonical lab interpretation key.
    lab = next(record for record in inserted if record["record_type"] == "tutkimustulos")
    assert "tulkinta" in lab["payload"] and "interpretation" not in lab["payload"]
    # And `source` is the loader's own statement about provenance, exactly as the file import makes it.
    assert lab["source"] == "delivery"


def _empty_index(tmp_path: Path) -> Path:
    """An import of a corpus with no records, purely to give the stand-in something well-formed to read."""
    source = tmp_path / "empty-source"
    empty = tmp_path / "empty-corpus"
    shutil.copytree(REPO / "data" / "experimental" / "delivery-sample", empty)
    (empty / "records.jsonl").write_text("", encoding="utf-8")
    source.mkdir(exist_ok=True)
    delivery.import_delivery(empty, source)
    return source


class _FakeBlob:
    def __init__(self, name: str, payload: bytes | None) -> None:
        self._name, self._payload = name, payload

    def download_blob(self):
        if self._payload is None:
            raise FileNotFoundError(f"blob {self._name} does not exist")
        blob = self
        payload = self._payload

        class _Properties:
            size = len(payload)

        class _Download:
            properties = _Properties()

            def readall(self) -> bytes:
                return payload

            def chunks(self):
                # azure-storage-blob 12.30.3's `StorageStreamDownloader.chunks(self)` takes NO parameter;
                # this fake matches that exact signature so a caller that still passes `chunk_size=` (the
                # bug this module once had) fails here with the same `TypeError` production would raise,
                # rather than silently accepting an argument the real SDK rejects.
                size = 16
                for start in range(0, len(payload), size):
                    yield payload[start:start + size]

        return _Download()


class _FakeBlobService:
    """A stand-in for the container client: enough of the surface `fetch_corpus_blob` uses."""

    def __init__(self, files: dict[str, bytes | None]) -> None:
        self._files = files
        self.requested: list[str] = []

    def get_blob_client(self, name: str) -> _FakeBlob:
        self.requested.append(name)
        return _FakeBlob(name, self._files.get(name))


class _FakeServiceClient:
    def __init__(self, container: _FakeBlobService) -> None:
        self._container = container

    def get_container_client(self, name: str) -> _FakeBlobService:
        return self._container


def test_stream_blob_to_works_with_a_downloader_whose_chunks_takes_no_argument(tmp_path: Path):
    """azure-storage-blob 12.30.3's `StorageStreamDownloader.chunks(self)` takes no `chunk_size` keyword.

    An earlier revision called `downloader.chunks(chunk_size=CORPUS_BLOB_CHUNK_BYTES)`, which raises
    `TypeError: chunks() got an unexpected keyword argument 'chunk_size'` against that pinned version and
    took down the `luvn-load-corpus` job. This fake's `chunks()` has exactly the real no-parameter
    signature, so this test fails on the old call and passes once the keyword is dropped.
    """
    payload = b"hello world, this is corpus content" * 10

    class _Properties:
        size = len(payload)

    class _Downloader:
        properties = _Properties()

        def chunks(self):
            size = 7
            for start in range(0, len(payload), size):
                yield payload[start:start + size]

    destination = tmp_path / "out.bin"
    _stream_blob_to(destination, _Downloader(), "corpus/current/profiles.csv", max_bytes=10_000_000)
    assert destination.read_bytes() == payload


def test_the_corpus_can_be_read_from_blob_so_the_image_needs_none(tmp_path: Path, monkeypatch):
    """The hackathon-day path: upload three files, run the loader. Nothing is baked into the image."""
    files = {f"corpus/current/{name}": f"contents-of-{name}".encode()
             for name in ("profiles.csv", "encounters.csv", "records.jsonl")}
    container = _FakeBlobService(files)
    monkeypatch.setattr("azure.storage.blob.BlobServiceClient",
                        lambda account_url, credential, max_chunk_get_size=None: _FakeServiceClient(container))
    monkeypatch.setattr("azure.identity.ManagedIdentityCredential", lambda client_id=None: object())

    destination = tmp_path / "corpus"
    written = fetch_corpus_blob(destination, container="delivery", account_url="https://example.blob.core.windows.net")
    assert written == destination
    for name in ("profiles.csv", "encounters.csv", "records.jsonl"):
        assert (destination / name).read_text(encoding="utf-8") == f"contents-of-{name}"
    # The optional appointments file is asked for after the three and its absence is not an error.
    assert container.requested == ["corpus/current/profiles.csv", "corpus/current/encounters.csv",
                                   "corpus/current/records.jsonl", "corpus/current/appointments.csv"]
    assert not (destination / "appointments.csv").exists(), "an absent optional file writes nothing"


def test_a_missing_corpus_blob_fails_loudly_rather_than_importing_half_a_corpus(tmp_path: Path, monkeypatch):
    """An absent file must not become an empty table: a half corpus is worse than no corpus."""
    files = {"corpus/current/profiles.csv": b"source_person_id,persona_id,label\n",
             "corpus/current/encounters.csv": b"encounter_id\n"}
    container = _FakeBlobService(files)
    monkeypatch.setattr("azure.storage.blob.BlobServiceClient",
                        lambda account_url, credential, max_chunk_get_size=None: _FakeServiceClient(container))
    monkeypatch.setattr("azure.identity.ManagedIdentityCredential", lambda client_id=None: object())
    with pytest.raises(FileNotFoundError, match="records.jsonl"):
        fetch_corpus_blob(tmp_path / "corpus", account_url="https://example.blob.core.windows.net")


def test_the_runtime_image_copies_no_corpus():
    """The served data lives in Azure SQL. A corpus in the image needs a rebuild to change.

    Asserted against the Dockerfile because that is where the property can be lost in one line, and losing it
    would not fail anything else: the app would keep working, from a differently-sourced corpus.
    """
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    runtime = dockerfile.split("FROM python:3.12-slim-bookworm AS runtime", 1)[1]
    # Any COPY that could carry the corpus, not only the spelling used before: `COPY data /app/data` and
    # `COPY . .` would both put it back, and the second is the one a tidy-up would reach for.
    suspicious = []
    for line in runtime.splitlines():
        if not line.startswith("COPY "):
            continue
        sources = [part for part in line.split()[1:] if not part.startswith("--")]
        if any(part in {".", "./", "data", "data/"} or part.startswith("data/") for part in sources):
            suspicious.append(line)
    assert suspicious == [], (
        f"the runtime image copies what could be corpus data ({suspicious}); the corpus belongs in Azure SQL, "
        f"loaded with `python -m app.load_sql --from-blob`")
    assert "COPY backend/app ./app" in runtime, "the runtime image should still copy the application"
    # And the ignore file has to keep doing its part: `COPY . .` is only safe because these are excluded.
    ignored = [line.strip() for line in (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()]
    for required in ("data/experimental/bound-personas", "data/experimental/delivery-demo"):
        covered = any(line and not line.startswith("#") and (required == line or required.startswith(line))
                      for line in ignored)
        assert covered, (
            f"{required} is not covered by .dockerignore, so any `COPY . .` in the build would ship it")


def test_the_demo_corpus_feeds_every_record_type_the_ui_renders(tmp_path: Path, monkeypatch):
    """The completeness gate: every type the frontend can draw has data behind it in the demo corpus.

    "Fully live" is not a property of the frontend alone. A section whose type no lens serves renders its
    empty panel, which looks identical to a section that works and has nothing to say — so the check is that
    the served records cover the same set of record types the UI declares it can render.
    """
    rendered = set(re.findall(r"^  (?:'([\wäöå-]+)'|([\wäöå-]+)): \[",
                              (REPO / "frontend/src/challenge-demo.mjs").read_text(encoding="utf-8")
                              .split("const SOURCE_FIELDS = {", 1)[1].split("\n};", 1)[0], re.M))
    declared = {a or b for a, b in rendered}
    assert {"tutkimustulos", "asiointitapahtuma", "mittaus"} <= declared, (
        f"the UI's declared types were not parsed correctly: {sorted(declared)}")

    delivery.import_delivery(DEMO, tmp_path / "imported")
    monkeypatch.setenv("LUVN_DELIVERY_DIR", str(tmp_path / "imported"))
    store = Store(path=None)
    served: set[str] = set()
    main.app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(main.app) as client:
            for encounter_id in store.encounter_order():
                picture = client.get(f"/api/encounters/{encounter_id}/picture?mode=foundry-off", headers=PRINCIPAL).json()
                for record in picture["records"]["priority"] + picture["records"]["accessible_other"]:
                    served.add(record["record_type"])
    finally:
        main.app.dependency_overrides.clear()
    # `asiointitapahtuma` stays declared in the UI (a delivery may carry one) but no served lens may admit it:
    # Challenge 1.2 excludes contact history, so its empty panel is the intended state (plan item B).
    missing = declared - served - {"asiointitapahtuma"}
    assert "asiointitapahtuma" not in served
    assert not missing, (
        f"no lens in {DEMO.name} serves {sorted(missing)}: those UI surfaces would render their empty panel "
        f"at the hackathon. Types served: {sorted(served)}")


def _sql_backed_client(tmp_path: Path, monkeypatch):
    """The app, driven end to end with a SQL-backed store rather than the file index.

    The equivalence test ties the two indexes' *answers* together; this ties the API to the SQL path, which
    is what "the API calls read Azure SQL" actually claims. The stand-in connection hands back the file
    import's own rows, so a difference here is a difference in how the app is wired, not in the data.
    """
    output = tmp_path / "file-import"
    delivery.import_delivery(DEMO, output)
    file_index = delivery.DeliveryIndex(output)
    stand_in = _StandIn(file_index, output)
    monkeypatch.setenv("LUVN_SQL_SERVER", "sql.example.invalid")
    monkeypatch.setenv("LUVN_SQL_DATABASE", "luvn")
    monkeypatch.setattr("app.sqldelivery.open_sql_index",
                        lambda *a, **k: SqlDeliveryIndex(stand_in))
    store = Store(path=None)
    main.app.dependency_overrides[get_store] = lambda: store
    return store, stand_in


def test_the_api_serves_from_a_sql_backed_store(tmp_path: Path, monkeypatch):
    store, stand_in = _sql_backed_client(tmp_path, monkeypatch)
    try:
        with TestClient(main.app) as client:
            rows = client.get("/api/encounters").json()["encounters"]
            assert {row["encounter_id"] for row in rows} == set(store.encounter_order())
            picture = client.get("/api/encounters/demo-enc-03-phq9/picture?mode=foundry-off", headers=PRINCIPAL)
            assert picture.status_code == 200
            body = picture.json()
            assert body["encounter_id"] == "demo-enc-03-phq9"
            served = body["records"]["priority"] + body["records"]["accessible_other"]
            assert served, "a SQL-backed store served no records at all"
    finally:
        main.app.dependency_overrides.clear()
    queried = " ".join(sql for sql, _ in stand_in.statements)
    assert "SELECT record_json FROM records WHERE source_person_id = ?" in queried, (
        "the request path did not resolve one person's rows by key; that is the property the SQL source "
        "exists to preserve")


# The demo's content contract, named per lens. This replaces a global "every type is served somewhere"
# assertion with what the demo actually promises to demonstrate at each lens, because the global version
# cannot tell a working lens from one whose panels are all empty — and a per-lens "no panel is empty" rule
# would instead be a padding mandate, since social work has no labs and most encounters have no assessment.
# So the contract is the beats: the things a reader is meant to be able to see.
DEMO_BEATS = {
    "demo-enc-01-medication-conflict": "the medication conflict: an active drug the notes say was stopped",
    "demo-enc-02-missed-doses": "missed doses beside a measurement and a contact",
    "demo-enc-03-phq9": "a completed PHQ-9 with its item scores, including item 9",
    "demo-enc-04-iadl-rai": "an incomplete IADL beside a supplied RAI and a review proposal",
    "demo-enc-05-iadl-due": "a catalogue row that is due",
    "demo-enc-06-functioning-nurse": "the nurse's view of a person's functioning",
    "demo-enc-07-functioning-therapist": "the therapist's view of the same person and purpose",
    "demo-enc-08-vammaispalvelut": "the vammaispalvelut form and its service domain",
    "demo-enc-09-guardian-split": "a guardian lens: the guardian's record served, the child's withheld",
    "demo-enc-10-duplicate-facts": "one fact recorded twice with no winner",
}


def test_the_demo_corpus_demonstrates_each_named_beat(tmp_path: Path, monkeypatch):
    """Per lens, the thing that lens exists to show. The floor for a demo that has to work on the day."""
    store, _ = _sql_backed_client(tmp_path, monkeypatch)
    seen: dict[str, dict] = {}
    try:
        with TestClient(main.app) as client:
            for encounter_id in store.encounter_order():
                picture = client.get(f"/api/encounters/{encounter_id}/picture?mode=foundry-off", headers=PRINCIPAL).json()
                records = picture["records"]["priority"] + picture["records"]["accessible_other"]
                seen[encounter_id] = {
                    "records": records,
                    "types": {record["record_type"] for record in records},
                    "ids": {record["record_id"] for record in records},
                    "conflicts": picture.get("conflicts") or [],
                    "assessments": picture.get("assessments") or [],
                    "duplicates": picture.get("duplicate_facts") or [],
                    "forms": {entry["form_type"]: entry["status"] for entry in picture.get("form_catalogue") or []},
                    "proposals": picture.get("proposals") or [],
                    "purpose": picture.get("purpose"),
                    "role": picture.get("role"),
                }
    finally:
        main.app.dependency_overrides.clear()

    assert set(seen) == set(DEMO_BEATS), "the corpus and the contract disagree about which lenses exist"

    one = seen["demo-enc-01-medication-conflict"]
    assert one["conflicts"] and "mittaus" in one["types"] and "tutkimustulos" in one["types"] and one["proposals"]

    # INVERTED DELIBERATELY (2026-10-12, plan item B). This line used to assert that encounter 02 serves an
    # `asiointitapahtuma` -- it pinned Challenge 1.2's excluded contact history as expected behaviour. The two
    # contact records were deleted from the corpus; the contract is now that NO served lens exposes one.
    assert "mittaus" in seen["demo-enc-02-missed-doses"]["types"]
    assert not any("asiointitapahtuma" in lens["types"] for lens in seen.values()), (
        "Challenge 1.2 excludes contact/interaction history from every served lens")

    phq9 = [a for a in seen["demo-enc-03-phq9"]["assessments"] if a["instrumentti"] == "PHQ-9"]
    assert phq9 and phq9[0]["valmis"] and phq9[0]["total"] == 16 and len(phq9[0]["osiot"]) == 9, (
        "the PHQ-9 must be complete, sum to 16 and carry all nine items")
    assert any(item["osio"] == "q9" for item in phq9[0]["osiot"]), (
        "item 9 is the item whose score most changes what happens next; the demo exists to show it")
    assert "tutkimustulos" in seen["demo-enc-03-phq9"]["types"]

    four = seen["demo-enc-04-iadl-rai"]
    instruments = {a["instrumentti"]: a for a in four["assessments"]}
    assert instruments["IADL"]["valmis"] is False and len(instruments["IADL"]["osiot"]) == 8
    assert instruments["RAI"]["osiot"] == [] and instruments["RAI"]["source_answers"] == 2
    assert four["proposals"]

    assert any(status == "due" for status in seen["demo-enc-05-iadl-due"]["forms"].values()), (
        "the due catalogue row is the one state a list of forms cannot show any other way")

    six, seven = seen["demo-enc-06-functioning-nurse"], seen["demo-enc-07-functioning-therapist"]
    assert six["role"] != seven["role"] and six["purpose"] == seven["purpose"], (
        "the contrast is one person and one purpose under two lenses")
    assert "arviointi" in six["types"] and "arviointi" in seven["types"]

    # The vammaispalvelut form is provenance on the Kirjaus that is its instance, not a row in the
    # (purpose, life_stage)-keyed catalogue — `encounters.py` says why: a catalogue row would print "not shown
    # in this briefing" on every other functioning lens to say nothing about them.
    eight = seen["demo-enc-08-vammaispalvelut"]
    assert any((record.get("form") or {}).get("form_type")
               == "Vammaispalvelujen palvelutarpeen arvioinnin esitiedot" for record in eight["records"]), (
        "the fourth named example form must be visible as provenance on its own record")
    assert any((record.get("form") or {}).get("service") == "vammaispalvelut" for record in eight["records"]), (
        "and it must carry its service domain")

    nine = seen["demo-enc-09-guardian-split"]
    assert "demo-p8-1" in nine["ids"] and "demo-p8-2" not in nine["ids"], (
        "the guardian lens must serve the guardian-held record and withhold the child's own")
    assert "mittaus" in nine["types"], "the neuvola lens shows a growth measurement, not an empty panel"

    groups = seen["demo-enc-10-duplicate-facts"]["duplicates"]
    assert len(groups) == 1 and set(groups[0]["record_ids"]) == {"demo-p9-1", "demo-p9-2"}, (
        "the same-fact group is the beat: two records, two forms, no winner")


def test_an_encounter_change_appears_without_restarting_the_app(tmp_path: Path, monkeypatch):
    """The operator catalogue is re-read on a TTL, so a new encounter does not need a redeploy.

    Records were always read per request and appeared at once; the catalogue was read once for the process
    lifetime, so adding or renaming an encounter row meant "upload, load, restart the app". The refresh is
    bounded — two queries over at most 128 rows — and a read that FAILS keeps the catalogue rather than
    blanking the navigation, which is the difference between a database hiccup and an empty corpus.
    """
    output = tmp_path / "file-import"
    delivery.import_delivery(DEMO, output)
    file_index = delivery.DeliveryIndex(output)
    stand_in = _StandIn(file_index, output)
    monkeypatch.setenv("LUVN_SQL_SERVER", "sql.example.invalid")
    monkeypatch.setattr("app.sqldelivery.open_sql_index", lambda *a, **k: SqlDeliveryIndex(stand_in))
    monkeypatch.setattr("app.store.CATALOGUE_TTL_SECONDS", 0.0)
    store = Store(path=None)
    assert len(store.encounter_index()) == 10

    # The corpus gains a lens: the stand-in answers the same queries from a longer encounter list.
    # A genuinely new encounter: the catalog is keyed by encounter_id, so a duplicated object would
    # collapse back to the same count and the test would pass while proving nothing (it did, first time).
    extra = dict(delivery.DeliveryIndex.encounter_index(file_index))
    extra["demo-enc-11-added-later"] = dataclasses.replace(next(iter(extra.values())),
                                                           encounter_id="demo-enc-11-added-later")
    monkeypatch.setattr(delivery.DeliveryIndex, "encounter_index", lambda self: extra)
    assert len(store.encounter_index()) == 11, "a new encounter did not appear on the next request"
    assert "demo-enc-11-added-later" in store.encounter_order()

    # And a failing read keeps what the app already serves rather than emptying the navigation.
    def explode(self):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(delivery.DeliveryIndex, "encounter_index", explode)
    assert len(store.encounter_index()) == 11, "a failed refresh must not blank the catalogue"


def test_stored_counts_report_what_the_database_holds(file_import):
    """The counts an operator checks after an upload, against the stand-in that serves the same corpus."""
    sql_index = _sql_index(file_import)
    counts = stored_counts(sql_index._connection)
    assert counts == {"people": 9, "encounters": 10, "appointments": 0, "records": 39}  # 41 - 2 contact records (item B), counts


def test_the_catalogue_ttl_default_is_short_enough_to_be_useful():
    """The production default is a claim about behaviour, and nothing pinned it.

    The refresh test sets the TTL to zero to avoid sleeping, which means a default of a day would
    pass every test in this file while making "an encounter change needs no restart" true only in
    theory. The number is the promise, so the number is asserted.
    """
    import app.store as store_module

    assert 0 < store_module.CATALOGUE_TTL_SECONDS <= 300, (
        f"the catalogue TTL default is {store_module.CATALOGUE_TTL_SECONDS}s; a reload is meant to appear "
        f"without a restart, so a default measured in hours would make that promise hollow")


def test_stored_counts_on_an_empty_database_report_zeros(file_import):
    """The case the function exists for: telling an operator their upload did not land.

    It is only reachable when the tables exist and are empty — the state after a load that failed
    before its inserts, which is exactly when someone runs `--verify`.
    """
    empty = _StandIn(delivery.DeliveryIndex(_empty_index(pathlib.Path(tempfile.mkdtemp()))),
                     pathlib.Path(tempfile.mkdtemp()))
    empty.serves_data = False

    class _EmptyCursor:
        def __init__(self): self._rows = []
        def execute(self, sql, *params): self._rows = [(0,)]
        def fetchone(self): return self._rows[0]
        def close(self): pass

    class _EmptyConnection:
        def cursor(self): return _EmptyCursor()

    assert stored_counts(_EmptyConnection()) == {"people": 0, "encounters": 0, "appointments": 0, "records": 0}


def test_the_demo_corpus_is_clinically_coherent(tmp_path: Path, monkeypatch):
    """The numbers a clinician would check, checked.

    A demo whose blood pressure is 146/88 in one row and 158/92 for the same person the same day, or whose
    lab says the result is within a range it is not in, discredits itself in front of the one audience that
    reads it closely. Nothing here asserts a *clinical* claim — the ranges are sanity bands, not reference
    standards — and nothing here is measured, validated or approved by anyone; what it asserts is that the
    corpus does not contradict itself or its own arithmetic.
    """
    from app import assess as assess_module

    rows = [
        json.loads(line)
        for line in (DEMO / "records.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    delivery_date = date(2026, 10, 13)

    # ---- measurements: plausible, internally consistent, and unique per person per day ----
    measurements: dict[tuple[str, str], dict] = {}
    for row in rows:
        if row["record_type"] != "mittaus":
            continue
        payload = row["payload"]
        who, day = row["source_person_id"], row["recorded_at"][:10]
        key = (who, day)
        assert key not in measurements, (
            f"{who} has two measurements on {day} "
            f"({measurements[key]} and {payload}): a reader cannot tell which is the record")
        measurements[key] = payload

        weight, height = payload.get("paino_kg"), payload.get("pituus_cm")
        assert weight is None or 2 <= weight <= 300, f"{who} {day}: implausible weight {weight}"
        assert height is None or 30 <= height <= 220, f"{who} {day}: implausible height {height}"
        if weight is not None and height is not None:
            bmi = weight / (height / 100) ** 2
            assert 10 <= bmi <= 60, f"{who} {day}: weight {weight}kg at {height}cm is a BMI of {bmi:.0f}"
        if payload.get("verenpaine"):
            systolic, diastolic = (int(part) for part in payload["verenpaine"].split("/"))
            assert 70 <= systolic <= 260, f"{who} {day}: systolic {systolic}"
            assert 40 <= diastolic <= 160, f"{who} {day}: diastolic {diastolic}"
            assert systolic > diastolic, f"{who} {day}: {systolic}/{diastolic} is inverted"
        if payload.get("syke") is not None:
            assert 30 <= payload["syke"] <= 220, f"{who} {day}: pulse {payload['syke']}"

    # Every measurement is dated, and none is dated in the future of the delivery.
    for row in rows:
        recorded = datetime.fromisoformat(str(row["recorded_at"]).replace("Z", "+00:00")).date()
        assert recorded < delivery_date, (
            f"{row['record_id']} is recorded {recorded}, after the delivery date {delivery_date}")

    # ---- labs: the range parses, the value is a number, and the wording agrees with them ----
    labs = [row for row in rows if row["record_type"] == "tutkimustulos"]
    assert labs, "the corpus has no lab result, so this check would be vacuous"
    for row in labs:
        payload = row["payload"]
        name = payload.get("tutkimus", row["record_id"])
        value = float(str(payload["arvo"]).replace(",", "."))
        lo_text, hi_text = re.split(r"[\u2013\u2014-]", str(payload["viitearvo"]), maxsplit=1)
        lo, hi = float(lo_text), float(hi_text)
        assert lo < hi, f"{name}: reference range {payload['viitearvo']} is not ordered"
        assert payload.get("yksikkö"), f"{name}: a result with no unit"
        assert payload.get("tulkinta"), f"{name}: a result with no interpretation"
        wording = payload["tulkinta"].lower()
        if lo <= value <= hi:
            for phrase in ("viitealueen ulkopuolella", "yli viitealueen", "alle viitealueen", "koholla"):
                assert phrase not in wording, (
                    f"{name} is {value} inside {payload['viitearvo']} but its interpretation says "
                    f"{phrase!r}")

    # ---- assessments: the items are a subset of the instrument, in range, and the app's total is their sum
    answers_by_record = {
        row["record_id"]: row["payload"].get("vastaukset") or {}
        for row in rows
        if row["record_type"] == "arviointi"
    }
    for record_id, answers in answers_by_record.items():
        payload = next(row["payload"] for row in rows if row["record_id"] == record_id)
        spec = assess_module.INSTRUMENTS.get(payload["instrumentti"])
        if spec is None:
            # An instrument the app deliberately does not score — RAI, which ADR-0008 forbids implementing
            # because a supplied result is not ours to total. The requirement is not a specification but the
            # absence of invention: the answers are recorded, and the briefing must publish no total. The API
            # loop below asserts that half.
            assert answers, f"{record_id}: {payload['instrumentti']} carries no answers to publish"
            continue
        unknown = set(answers) - set(spec.items)
        assert not unknown, f"{record_id}: answers {sorted(unknown)} are not items of {spec.name}"
        for code, value in answers.items():
            assert 0 <= value <= spec.per_item_max, (
                f"{record_id}: {code}={value} is outside 0..{spec.per_item_max} for {spec.name}")

    store, _ = _sql_backed_client(tmp_path, monkeypatch)
    checked = 0
    try:
        with TestClient(main.app) as client:
            for encounter_id in store.encounter_order():
                picture = client.get(f"/api/encounters/{encounter_id}/picture?mode=foundry-off", headers=PRINCIPAL).json()
                for assessment in picture.get("assessments") or []:
                    source_id = (assessment.get("lahteet") or [None])[0]
                    if source_id not in answers_by_record:
                        continue
                    answers = answers_by_record[source_id]
                    spec = assess_module.INSTRUMENTS.get(assessment["instrumentti"])
                    if spec is None:
                        assert assessment["valmis"] is False and assessment["total"] is None, (
                            f"{source_id}: {assessment['instrumentti']} is not scored here, so the briefing "
                            f"must not present it as completed or totalled")
                        assert assessment["tulkinta"] is None, (
                            f"{source_id}: an unscored instrument got an interpretation")
                        continue
                    if assessment["valmis"]:
                        assert assessment["total"] == sum(answers.values()), (
                            f"{source_id}: the app totals {assessment['total']}, the recorded answers sum to "
                            f"{sum(answers.values())}")
                        assert assessment["max_total"] == spec.max_total
                        band = next(
                            label for low, high, label in spec.bands
                            if low <= assessment["total"] <= high
                        )
                        assert assessment["tulkinta"] == band, (
                            f"{source_id}: total {assessment['total']} is banded {band}, the briefing says "
                            f"{assessment['tulkinta']}")
                        checked += 1
                    else:
                        assert assessment["total"] is None, (
                            f"{source_id}: an incomplete answer set produced a total")
    finally:
        main.app.dependency_overrides.clear()

    # Second time this round a check would have passed by looking at nothing.
    assert checked >= 1, "no completed assessment was reached, so the arithmetic above was never tested"


def test_the_loader_takes_a_mapping_so_the_arrival_path_reaches_sql_too(tmp_path: Path):
    """`load_corpus` without a mapping is unchanged; WITH one it reads any layout, exactly like the file import.

    The people are derived from the mapped `source_person_id`, so each person must be merged into SQL on the
    first row that names them -- the records table has foreign keys to both `people.persona_id` and
    `people.source_person_id`, and a batch insert that named a person SQL had not seen would fail.
    """
    source_dir = tmp_path / "delivery"
    source_dir.mkdir()
    (source_dir / "kirjaukset.csv").write_bytes(
        ("TietueVersio;Henkilötunniste;Kirjaustunniste;Kirjaustyyppi;Kirjaaja;Kirjausaika;Aihe;"
         "Salassapitoluokka;SuostumuksenLaajuus;Sisältö\n"
         '4;H-1;R-1;VapaaTeksti;laakari;13.10.2026;Yleiskirjaus;julkinen;hoito;"{""teksti"": ""Yrjö""}"\n'
         '4;H-2;R-2;Laakemaarays;laakari;14.10.2026;Laakitys;salainen;hoito;'
         '"{""lääke"": ""Sertraliini""}"\n'
         '4;H-2;R-3;VapaaTeksti;laakari;15.10.2026;Yleiskirjaus;julkinen;hoito;"{""teksti"": ""Ääni""}"\n'
         ).encode("latin-1"))
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({
        "version": "sql-mapping-v1",
        "ignore_source_paths": ["TietueVersio"],
        "valuesets": {
            "record_type": {"VapaaTeksti": "vapaateksti", "Laakemaarays": "lääkitys"},
            "topic": {"Yleiskirjaus": "hoito", "Laakitys": "lääkitys"},
            "sensitivity_tag": {"julkinen": "none", "salainen": "sensitive"},
            "consent_scope": {"hoito": "treatment"},
        },
        "rules": [
            {"id": "source", "target_field": "source", "transform": "const", "value": "delivery"},
            {"id": "person", "source_path": "Henkilötunniste", "target_field": "source_person_id"},
            {"id": "record-id", "source_path": "Kirjaustunniste", "target_field": "record_id"},
            {"id": "record-type", "source_path": "Kirjaustyyppi", "target_field": "record_type",
             "transform": "lookup_valueset", "valueset": "record_type"},
            {"id": "author", "source_path": "Kirjaaja", "target_field": "authored_by_role"},
            {"id": "recorded-at", "source_path": "Kirjausaika", "target_field": "recorded_at",
             "transform": "date"},
            {"id": "topic", "source_path": "Aihe", "target_field": "topic",
             "transform": "lookup_valueset", "valueset": "topic"},
            {"id": "sensitivity", "source_path": "Salassapitoluokka",
             "target_field": "sensitivity_tag", "transform": "lookup_valueset",
             "valueset": "sensitivity_tag"},
            {"id": "consent", "source_path": "SuostumuksenLaajuus", "target_field": "consent_scope",
             "transform": "lookup_valueset", "valueset": "consent_scope"},
            {"id": "payload", "source_path": "Sisältö", "target_field": "payload"},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    stand_in = _StandIn(delivery.DeliveryIndex(_empty_index(tmp_path)), tmp_path)
    stand_in.serves_data = False
    counts = load_corpus(source_dir, stand_in, mapping=mapping)

    assert counts == {"profiles": 2, "encounters": 0, "appointments": 0, "records": 3} | {
        "generation": counts["generation"], "batches": 1, "items_seen": 5, "resumed_after": None}
    assert stand_in.committed == 3
    merged = [params for sql, params in stand_in.statements if "INSERT INTO people" in " ".join(sql.split())]
    assert {params[1] for params in merged} == {"H-1", "H-2"}, "each person is merged on first sight"
    inserted = [json.loads(params[-1]) for sql, params in stand_in.statements
                if "INSERT INTO records (record_id, source_person_id, persona_id, record_type, encounter_id, "
                   "case_id, record_json)" in " ".join(sql.split())]
    assert {record["record_type"] for record in inserted} == {"vapaateksti", "lääkitys"}
    # The vendor spelling never reaches SQL: the record is canonical, and the person is the vendor key.
    assert all(record["source_person_id"] in {"H-1", "H-2"} for record in inserted)
    assert any(record["payload"].get("teksti") == "Yrjö" for record in inserted)


# ---------------------------------------------------------------------------------------------------
# The provenance envelope the ADRs describe (G3), and the therapist contrast in record content (G2)
#
# ADR-0012 (per-encounter capacity/consent), ADR-0013 (case binding) and ADR-0015 (heading pairs,
# code-system intent, legal basis) described envelope fields that grep counted 0 of across every delivery
# corpus: `heading`, `käyntiaika`, `vastaanottajat`, `erikoisala`, `suorituspaikka`, `case_id`,
# `encounter_id`. The code-only ADRs shipped; the data did not. These tests are the data's half.
#
# `legal_basis` is deliberately NOT among them: ADR-0015 and the 2026-10-07 owner decision say no value
# ships until the owner approves one specific basis, so the negative assertion below is the contract.
# ---------------------------------------------------------------------------------------------------


def _demo_rows() -> list[dict]:
    return [json.loads(line) for line in
            (DEMO / "records.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def test_the_demo_corpus_carries_the_provenance_envelope_the_adrs_describe(tmp_path: Path,
                                                                          monkeypatch):
    """Code is not data: every ADR-0012/0013/0015 envelope field now has a row behind it, and renders."""
    rows = _demo_rows()
    by_id = {row["record_id"]: row for row in rows}

    headings = [row for row in rows if row.get("heading")]
    assert headings, "no demo record carries a heading (ADR-0015)"
    assert any(row["heading"]["local"] != row["heading"]["national"] for row in headings), (
        "no heading is a local/national pair; only an identical pair is carried")

    käyntiajat = [row for row in rows if row.get("käyntiaika")]
    assert käyntiajat, "no demo record carries käyntiaika"
    assert all(row["käyntiaika"] != row["recorded_at"] for row in käyntiajat), (
        "käyntiaika is the visit time and must be distinct from the entry time `recorded_at`")

    assert [row for row in rows if row.get("vastaanottajat")], "no demo record carries vastaanottajat"
    assert [row for row in rows if row.get("erikoisala") and row.get("suorituspaikka")], (
        "no demo record carries both erikoisala and suorituspaikka")
    assert [row for row in rows if row.get("case_id")], "no demo record carries case_id (ADR-0013)"
    assert [row for row in rows if row.get("encounter_id")], (
        "no demo record carries encounter_id, so the encounter-scoped path has no data")

    # The receiver still renders each of them. Provenance the API drops is not data behind an ADR.
    store, _ = _sql_backed_client(tmp_path, monkeypatch)
    rendered: dict[str, dict] = {}
    try:
        with TestClient(main.app) as client:
            for encounter_id in store.encounter_order():
                picture = client.get(f"/api/encounters/{encounter_id}/picture?mode=foundry-off", headers=PRINCIPAL).json()
                for record in picture["records"]["priority"] + picture["records"]["accessible_other"]:
                    rendered[record["record_id"]] = record
    finally:
        main.app.dependency_overrides.clear()

    therapist = rendered[by_id["demo-p6-4"]["record_id"]]
    assert therapist["heading"] == {"local": "Hoidon tarpeen arviointi", "national": "Suunnitelma"}
    assert therapist["käyntiaika"].startswith(by_id["demo-p6-4"]["käyntiaika"][:19])
    assert therapist["vastaanottajat"] == ["Reetta Kivelä", "Omaishoitaja"]
    assert therapist["erikoisala"] == "Fysioterapia"
    assert therapist["suorituspaikka"] == "Synteettinen fysioterapiayksikkö 006"
    assert therapist["state"] == "approved"
    assert therapist["responsible_approver"] == "fysioterapeutti"
    # Every carried encounter_id names a lens the corpus actually serves, not a dangling reference.
    for row in rows:
        if row.get("encounter_id"):
            assert row["encounter_id"] in store.encounter_order(), row["record_id"]
    social = rendered["demo-p4-4"]
    assert social["case_id"] == "demo-case-04-2026"
    assert social["encounter_id"] == "demo-enc-04-iadl-rai"


def test_the_therapist_lens_carries_a_therapist_authored_narrative(tmp_path: Path, monkeypatch):
    """G2: the persona's only fysioterapeutti record was a four-key measurement; the contrast was thin.

    The role contrast is real, but a lens whose professional contributed no narrative or assessment has
    nothing to set the nurse's note against. `demo-p6-4` is the therapist-authored narrative: same person,
    same purpose, authored by the role the therapist lens is.
    """
    rows = {row["record_id"]: row for row in _demo_rows()}
    narrative = rows["demo-p6-4"]
    assert narrative["authored_by_role"] == "fysioterapeutti"
    assert narrative["record_type"] == "vapaateksti"
    assert narrative["payload"]["otsikko"] and narrative["payload"]["teksti"]
    assert rows["demo-p6-2"]["authored_by_role"] == "sairaanhoitaja", (
        "the nurse's note is the other half of the contrast")

    store, _ = _sql_backed_client(tmp_path, monkeypatch)
    try:
        with TestClient(main.app) as client:
            nurse = client.get("/api/encounters/demo-enc-06-functioning-nurse"
                               "/picture?mode=foundry-off", headers=PRINCIPAL).json()
            therapist = client.get("/api/encounters/demo-enc-07-functioning-therapist"
                                   "/picture?mode=foundry-off", headers=PRINCIPAL).json()
    finally:
        main.app.dependency_overrides.clear()

    def served(picture: dict) -> dict[str, dict]:
        return {record["record_id"]: record
                for record in picture["records"]["priority"] + picture["records"]["accessible_other"]}

    assert nurse["role"] != therapist["role"] and nurse["purpose"] == therapist["purpose"]
    assert "demo-p6-4" in served(therapist), (
        "the therapist-authored narrative is not visible to the therapist lens")
    rendered = served(therapist)["demo-p6-4"]
    assert rendered["payload"]["teksti"] == narrative["payload"]["teksti"]
    assert rendered["authored_by_role"] == "fysioterapeutti"


def test_no_corpus_ships_a_legal_basis():
    """ADR-0015: an absent basis beats an unapproved one. A negative assertion, so one cannot slip in.

    Scans every record in every corpus under `data/experimental/` — the delivery corpora and the bound
    personas alike — for a non-empty `legal_basis`. The owner decision keeps the field in the design and
    the value out of the data; this is what "ships none" means mechanically.
    """
    offenders: list[tuple[str, str]] = []

    def records_in(document):
        if isinstance(document, dict):
            if "record_id" in document:
                yield document
            for record in document.get("records") or []:
                if isinstance(record, dict):
                    yield record

    for path in sorted((REPO / "data" / "experimental").rglob("*")):
        if not path.is_file() or path.suffix not in {".json", ".jsonl"}:
            continue
        text = path.read_text(encoding="utf-8")
        documents = ([json.loads(line) for line in text.splitlines() if line.strip()]
                     if path.suffix == ".jsonl" else [json.loads(text)])
        for document in documents:
            for record in records_in(document):
                if record.get("legal_basis") not in (None, ""):
                    offenders.append((path.name, str(record.get("record_id"))))
    assert not offenders, f"legal_basis must ship ABSENT until the owner approves one: {offenders}"


# ---------------------------------------------------------------------------------------------------
# The SQL core: the delivery index's own constraints (D4/D9/D10) and the loader's ADR-0013 columns (D7).
# Appended by the SQL-core workstream; the corpus/ADR-envelope tests above belong to a sibling.
# ---------------------------------------------------------------------------------------------------


def _imported_index(tmp_path: Path) -> Path:
    """A real import of the demo corpus, so the DDL under test is the code's own."""
    out = tmp_path / "index"
    delivery.import_delivery(DEMO, out)
    return out


def _open_index_db(out: Path):
    import sqlite3

    connection = sqlite3.connect(out / "index.sqlite")
    # A NEW connection starts with `foreign_keys` OFF; the import's writer connection turns it ON right
    # after `sqlite3.connect` (app/delivery.py), and `DeliveryIndex` does the same for its read connection.
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def test_a_missing_index_file_is_the_deliberate_error_not_a_raw_sqlite_error(tmp_path: Path):
    """D4: a `LUVN_DELIVERY_DIR` with no `index.sqlite` used to raise `sqlite3.OperationalError`."""
    with pytest.raises(delivery.DeliveryShapeError) as raised:
        delivery.DeliveryIndex(tmp_path / "no-index-here")
    message = str(raised.value)
    assert "no delivery index" in message
    assert "index.sqlite" in message
    assert "reconcile_delivery.py import" in message


def test_an_index_from_an_older_schema_is_refused_by_name(tmp_path: Path):
    """The deliberate version error, and the reason the bump matters: a v2 index has no constraints."""
    out = _imported_index(tmp_path)
    connection = _open_index_db(out)
    try:
        connection.execute(f"PRAGMA user_version = {delivery.INDEX_SCHEMA_VERSION - 1}")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(delivery.DeliveryShapeError) as raised:
        delivery.DeliveryIndex(out)
    assert "older revision" in str(raised.value)


def test_the_index_refuses_the_hand_built_invalid_rows(tmp_path: Path):
    """D9: every value the unconstrained SQLite index accepted is refused by the schema now.

    The six the brief names: an undeclared role, purpose, consent holder and life stage; a NULL persona;
    and a `people.path` pointing at a missing file. The foreign key is the seventh, and the valid inserts
    are the anti-vacuity half — a guard that refuses everything would pass a refusal-only test.
    """
    import sqlite3

    out = _imported_index(tmp_path)
    connection = _open_index_db(out)
    try:
        persona_id, source_person_id = connection.execute(
            "SELECT persona_id, source_person_id FROM people ORDER BY persona_id LIMIT 1").fetchone()

        def insert_encounter(encounter_id: str, **overrides):
            values = {"source_person_id": source_person_id, "role": "sairaanhoitaja",
                      "purpose": "medication_follow_up", "example_label": "", "demo_name": "",
                      "consent_holders": "self", "life_stage": "adult"}
            values.update(overrides)
            connection.execute(
                "INSERT INTO encounters (encounter_id, source_person_id, role, purpose, example_label, "
                "demo_name, consent_holders, life_stage) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (encounter_id, values["source_person_id"], values["role"], values["purpose"],
                 values["example_label"], values["demo_name"], values["consent_holders"],
                 values["life_stage"]))

        # Non-vacuous: a declared row is accepted, including a declared multi-holder lens.
        insert_encounter("valid-encounter")
        insert_encounter("valid-multi-holder", consent_holders="self,guardian")

        for name, overrides in (
                ("role", {"role": "NOT_A_ROLE"}),
                ("purpose", {"purpose": "NOT_A_PURPOSE"}),
                ("consent_holders", {"consent_holders": "NOT_A_HOLDER"}),
                ("life_stage", {"life_stage": "NOT_A_STAGE"}),
                ("foreign key", {"source_person_id": "no-such-person"})):
            with pytest.raises(sqlite3.IntegrityError):
                insert_encounter(f"bad-{name}", **overrides)

        # The NULL "person" the old schema accepted: SQLite does not imply NOT NULL from PRIMARY KEY.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO people (persona_id, source_person_id, label, path) "
                               "VALUES (NULL, 'x', 'x', 'people/x.jsonl')")

        # A missing person FILE cannot be a SQL CHECK; the reader refuses it by name, below.
        connection.execute("UPDATE people SET path = 'people/not-there.jsonl' WHERE persona_id = ?",
                           (persona_id,))
        connection.commit()
    finally:
        connection.close()

    index = delivery.DeliveryIndex(out)
    try:
        with pytest.raises(delivery.DeliveryShapeError) as raised:
            index[persona_id]
    finally:
        index.close()
    assert "not there" in str(raised.value) and "not-there.jsonl" in str(raised.value)


def test_encounters_hold_the_same_binding_in_both_engines(tmp_path: Path):
    """D10: SQLite carried `persona_id` (Azure SQL does not) and a nullable source person. Now it agrees."""
    from app.sqldelivery import SCHEMA_STATEMENTS

    out = _imported_index(tmp_path)
    connection = _open_index_db(out)
    try:
        info = {row[1]: row for row in connection.execute("PRAGMA table_info(encounters)")}
    finally:
        connection.close()
    assert "persona_id" not in info, "the persona is resolved through `people`, exactly as in Azure SQL"
    assert info["source_person_id"][3] == 1, "source_person_id is NOT NULL in Azure SQL too"
    assert info["role"][3] == 1 and info["purpose"][3] == 1
    azure = next(entry.statement for entry in SCHEMA_STATEMENTS if entry.name == "encounters")
    assert "persona_id" not in azure
    assert "source_person_id NVARCHAR(128) NOT NULL" in azure


def test_the_loader_writes_the_case_and_encounter_when_the_envelope_carries_them(tmp_path: Path):
    """D7 structural half: the columns are written from the envelope when present, and never invented."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "profiles.csv").write_text("source_person_id,persona_id,label\nk1,p1,Synthetic\n",
                                         encoding="utf-8")
    row = {"record_id": "r1", "source_person_id": "k1", "record_type": "vapaateksti",
           "authored_by_role": "laakari", "recorded_at": "2026-01-01T00:00:00+00:00", "topic": "hoito",
           "sensitivity_tag": "none", "consent_scope": "treatment", "payload": {"teksti": "x"},
           "encounter_id": "enc-1", "case_id": "case-1"}
    (source / "records.jsonl").write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    stand_in = _StandIn(delivery.DeliveryIndex(_empty_index(tmp_path)), tmp_path)
    stand_in.serves_data = False
    load_corpus(source, stand_in)
    merges = [params for sql, params in stand_in.statements if "INSERT INTO records" in " ".join(sql.split())]
    assert merges, "the loader issued no records INSERT"
    assert merges[0][4] == "enc-1" and merges[0][5] == "case-1"
    assert json.loads(merges[0][6])["encounter_id"] == "enc-1"
    assert json.loads(merges[0][6])["case_id"] == "case-1"


# --- M2/D5: a planted row fails the read CLOSED and SPECIFICALLY, not as a 404 ---------------------------

def test_a_planted_record_fails_the_read_with_a_named_error_not_a_validation_error(file_import):
    """The review's admitted row (missing four envelope fields) must not surface as `ValueError`.

    It is a `ValidationError`, which is a `ValueError`, which the API turns into `404 unknown encounter`.
    `CorruptCorpusError` is the distinct "the corpus is broken" signal, and it names the record and field.
    """
    file_index, output = file_import
    stand_in = _StandIn(file_index, output)
    victim = next(iter(file_index.encodings()))[1]
    stand_in.corrupt_source_person_id = victim
    sql_index = SqlDeliveryIndex(stand_in)
    persona_id = next(row[0] for row in file_index.encodings() if row[1] == victim)

    with pytest.raises(CorruptCorpusError) as error:
        sql_index[persona_id]
    assert "corrupt" in str(error.value)
    assert persona_id in str(error.value) and "planted" in str(error.value)
    assert error.value.record_id == "planted"


def test_one_bad_row_does_not_hide_the_other_people(file_import):
    """Only the person whose row is bad fails; everyone else still resolves."""
    file_index, output = file_import
    stand_in = _StandIn(file_index, output)
    encodings = list(file_index.encodings())
    stand_in.corrupt_source_person_id = encodings[0][1]
    sql_index = SqlDeliveryIndex(stand_in)
    assert sql_index[encodings[1][0]].persona_id == encodings[1][0]


def test_the_worklist_lists_a_corrupt_person_and_the_picture_reports_the_corruption_as_503(tmp_path: Path, monkeypatch):
    """The worklist reads no record, so a corrupt stored record is not its failure to report: it lists the
    encounter. The defect surfaces where a record is read, on that person's picture, as a 503 that names the
    corruption, and a genuinely unknown encounter is still a 404 rather than a corruption message."""
    output = tmp_path / "file-import"
    delivery.import_delivery(DEMO, output)
    file_index = delivery.DeliveryIndex(output)
    stand_in = _StandIn(file_index, output)
    corrupt_person = next(iter(file_index.encodings()))[1]
    stand_in.corrupt_source_person_id = corrupt_person
    corrupt_encounter = next(encounter_id for encounter_id, encounter in file_index.encounter_index().items()
                             if encounter.source_person_id == corrupt_person)
    monkeypatch.setenv("LUVN_SQL_SERVER", "sql.example.invalid")
    monkeypatch.setattr("app.sqldelivery.open_sql_index", lambda *a, **k: SqlDeliveryIndex(stand_in))
    store = Store(path=None)
    main.app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(main.app) as client:
            listing = client.get("/api/encounters?limit=500")
            broken = client.get(f"/api/encounters/{corrupt_encounter}/picture", headers=PRINCIPAL)
            unknown = client.get("/api/encounters/no-such-encounter/picture", headers=PRINCIPAL)
    finally:
        main.app.dependency_overrides.clear()
    assert listing.status_code == 200, listing.text
    assert corrupt_encounter in {row["encounter_id"] for row in listing.json()["encounters"]}
    assert broken.status_code == 503, broken.text
    assert "corrupt" in broken.json()["detail"]
    assert unknown.status_code == 404, "a genuinely unknown encounter is still a 404"


# --- M3/D6: one persona lookup per request, however many encounters share the person ---------------------

class _CountingPersonMapping(Mapping):
    """The real persona mapping, with every lookup counted.

    It must be a real `Mapping`: `resolve_encounter` branches on `isinstance(personas, Mapping)`, and a
    duck-typed object that is not one would take the list-validation branch and fail. That subtlety is why
    these tests assert on the real path rather than a stub.
    """

    def __init__(self, inner):
        self._inner = inner
        self.lookups: list[str] = []

    def get(self, persona_id, default=None):
        self.lookups.append(persona_id)
        return self._inner.get(persona_id, default)

    def __getitem__(self, persona_id):
        self.lookups.append(persona_id)
        return self._inner[persona_id]

    def __iter__(self):
        return iter(self._inner)

    def __len__(self):
        return len(self._inner)


WORKLIST_QUERIES = ("/api/encounters", "/api/encounters?limit=2&offset=3", "/api/encounters?q=Vähäkangas",
                    "/api/encounters?role=terveydenhoitaja&limit=1")


def test_the_worklist_resolves_no_person_and_the_picture_resolves_only_its_own(tmp_path: Path, monkeypatch):
    """The spy on the record reader: a worklist request looks up no person at all, and the picture looks up
    exactly the one person it shows. Before the metadata read this was one lookup per distinct person."""
    output = tmp_path / "file-import"
    delivery.import_delivery(DEMO, output)
    file_index = delivery.DeliveryIndex(output)
    stand_in = _StandIn(file_index, output)
    monkeypatch.setenv("LUVN_SQL_SERVER", "sql.example.invalid")
    monkeypatch.setattr("app.sqldelivery.open_sql_index", lambda *a, **k: SqlDeliveryIndex(stand_in))
    store = Store(path=None)
    counting = _CountingPersonMapping(file_index)
    monkeypatch.setattr(store, "_personas", counting, raising=False)
    encounter_id = "demo-enc-03-phq9"
    persona_id = store.encounter_index()[encounter_id].persona_id
    main.app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(main.app) as client:
            for path in WORKLIST_QUERIES:
                response = client.get(path)
                assert response.status_code == 200, (path, response.text)
            assert counting.lookups == [], "the worklist resolved a person's records"

            picture = client.get(f"/api/encounters/{encounter_id}/picture?mode=foundry-off", headers=PRINCIPAL)
            assert picture.status_code == 200, picture.text
    finally:
        main.app.dependency_overrides.clear()
    assert set(counting.lookups) == {persona_id}, counting.lookups


def test_the_worklist_issues_no_statement_against_records_on_the_sql_source(tmp_path: Path, monkeypatch):
    """The spy on the SQL read path: no worklist request names the records table or its JSON, and no person
    is resolved through `SqlDeliveryIndex.__getitem__` (the one method that reads a person's records)."""
    store, stand_in = _sql_backed_client(tmp_path, monkeypatch)
    reads: list[str] = []
    original = SqlDeliveryIndex.__getitem__

    def spy(self, persona_id):
        reads.append(persona_id)
        return original(self, persona_id)

    monkeypatch.setattr(SqlDeliveryIndex, "__getitem__", spy)
    try:
        with TestClient(main.app) as client:
            before = len(stand_in.statements)
            for path in WORKLIST_QUERIES:
                assert client.get(path).status_code == 200, path
            worklist_statements = [sql for sql, _ in stand_in.statements[before:]]
            assert worklist_statements, "the worklist issued no statement at all, so the spy proves nothing"
            touched = [sql for sql in worklist_statements if re.search(r"\brecords\b|record_json", sql, re.I)]
            assert touched == [], f"the worklist read the records table: {touched}"
            assert reads == [], f"the worklist resolved person records through the index: {reads}"
    finally:
        main.app.dependency_overrides.clear()


def test_the_worklist_on_the_local_delivery_index_opens_no_person_file(tmp_path: Path, monkeypatch):
    """The same spy on the local index, where a person's records are a `people/*.jsonl` file."""
    output = tmp_path / "file-import"
    delivery.import_delivery(DEMO, output)
    monkeypatch.delenv("LUVN_SQL_SERVER", raising=False)
    monkeypatch.setenv("LUVN_DELIVERY_DIR", str(output))
    reads: list[str] = []
    original = delivery.DeliveryIndex.__getitem__

    def spy(self, persona_id):
        reads.append(persona_id)
        return original(self, persona_id)

    monkeypatch.setattr(delivery.DeliveryIndex, "__getitem__", spy)
    store = Store(path=None)
    encounter_id = "demo-enc-03-phq9"
    persona_id = store.encounter_index()[encounter_id].persona_id
    main.app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(main.app) as client:
            for path in WORKLIST_QUERIES:
                assert client.get(path).status_code == 200, path
            assert reads == [], f"the worklist opened a person's records: {reads}"
            assert client.get(f"/api/encounters/{encounter_id}/picture?mode=foundry-off", headers=PRINCIPAL).status_code == 200
    finally:
        main.app.dependency_overrides.clear()
    assert set(reads) == {persona_id}, reads


def test_search_folds_finnish_diacritics_over_the_sql_metadata_read(tmp_path: Path, monkeypatch):
    """Parity of `q` with the file source: `Vähäkangas` = `VÄHÄKANGAS` = `Vahakangas`, through SQL."""
    _sql_backed_client(tmp_path, monkeypatch)
    try:
        with TestClient(main.app) as client:
            for needle in ("Vähäkangas", "VÄHÄKANGAS", "vähäkangas", "Vahakangas"):
                payload = client.get("/api/encounters", params={"q": needle}).json()
                assert [row["example_label"] for row in payload["encounters"]] == ["Eero Vähäkangas"], needle
                assert payload["total"] == 1, needle
            # Two encounters for one person: both match, and the count is of rows, not of people.
            kivela = client.get("/api/encounters", params={"q": "kivelä"}).json()
            assert kivela["total"] == 2
            assert kivela["aggregate"]["matching"]["people"] == 1
    finally:
        main.app.dependency_overrides.clear()


def test_a_corpus_blob_above_the_bound_is_refused_by_name_and_leaves_no_partial_file(tmp_path: Path,
                                                                                     monkeypatch):
    """L3: `readall()` materialized a whole blob with no bound, so a swapped multi-GB blob OOMs the job."""
    files = {f"corpus/current/{name}": (b"x" * 5000 if name == "profiles.csv" else b"ok")
             for name in ("profiles.csv", "encounters.csv", "records.jsonl")}
    container = _FakeBlobService(files)
    monkeypatch.setattr("azure.storage.blob.BlobServiceClient",
                        lambda account_url, credential, max_chunk_get_size=None: _FakeServiceClient(container))
    monkeypatch.setattr("azure.identity.ManagedIdentityCredential", lambda client_id=None: object())

    destination = tmp_path / "corpus"
    with pytest.raises(CorpusBlobTooLarge) as error:
        fetch_corpus_blob(destination, container="delivery",
                          account_url="https://example.blob.core.windows.net", max_bytes=1000)
    assert "profiles.csv" in str(error.value) and "1000" in str(error.value)
    assert not (destination / "profiles.csv").exists(), "a refused blob must not leave a partial file"


def test_a_corpus_blob_under_the_bound_streams_in_chunks(tmp_path: Path, monkeypatch):
    """The size bound must not break the normal path: every file still lands byte-for-byte."""
    payload = b"a" * 4000
    files = {f"corpus/current/{name}": payload
             for name in ("profiles.csv", "encounters.csv", "records.jsonl")}
    container = _FakeBlobService(files)
    monkeypatch.setattr("azure.storage.blob.BlobServiceClient",
                        lambda account_url, credential, max_chunk_get_size=None: _FakeServiceClient(container))
    monkeypatch.setattr("azure.identity.ManagedIdentityCredential", lambda client_id=None: object())

    destination = fetch_corpus_blob(tmp_path / "corpus", container="delivery",
                                    account_url="https://example.blob.core.windows.net", max_bytes=8000)
    for name in ("profiles.csv", "encounters.csv", "records.jsonl"):
        assert (destination / name).read_bytes() == payload
