"""Delivery importer: streaming, indexed, and honest about the unverified schema."""

import csv
import importlib
import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import assess, main, policy, source
from app.picture import Picture
from app.store import Store, get_store

PRINCIPAL = {"x-ms-client-principal-id": "oid-1234", "x-ms-client-principal-name": "nurse@example.fi"}

delivery = importlib.import_module("app.delivery")


def _sample_dir() -> Path:
    # Explicitly the two-person SAMPLE fixture. It used to read `delivery.DEFAULT_DELIVERY_DIR`, which then
    # pointed at the sample; the default is now `delivery-demo` (a shipped default must pass its own
    # reconciliation — D3), so these tests name the fixture they actually mean.
    return Path(__file__).resolve().parents[2] / "data" / "experimental" / "delivery-sample"


def test_sample_delivery_imports_every_record_type_and_indexes_people(tmp_path: Path):
    report = delivery.import_delivery(_sample_dir(), tmp_path)
    shape = json.loads(report.shape_report.read_text(encoding="utf-8"))
    assert shape["kind"] == "delivery-payload-shape"
    assert shape["types"][source.LAB_RESULT]["rows_seen"] == 1
    assert sum(item["rows_seen"] for item in shape["types"].values()) == report.records
    assert "shape_report" not in Picture.model_fields, "ingestion totals are not encounter-view totals"
    registry = json.loads((report.shape_report.parent / "artifacts.json").read_text(encoding="utf-8"))
    # The run is self-describing: the payload shape AND the mechanical checks over the mapped output are
    # both in the registry, so a reader cannot find one and miss the other.
    assert {"kind": "delivery-payload-shape", "path": report.shape_report.name} in registry["artifacts"]
    assert {"kind": "delivery-sanity-checks", "path": "delivery-sanity-report.json"} in registry["artifacts"]
    assert report.sanity_report == report.shape_report.parent / "delivery-sanity-report.json"
    assert json.loads(report.sanity_report.read_text(encoding="utf-8"))["kind"] == "delivery-sanity-checks"
    assert report.personas == 2
    assert report.records == 11
    assert report.encounters == 2
    assert report.skipped == 0

    index = delivery.DeliveryIndex(tmp_path)
    try:
        assert index.has("delivery-persona-001") and index.has("delivery-persona-002")
        assert not index.has("delivery-persona-999")
        persona = index.persona("delivery-persona-001")
    finally:
        index.close()
    assert persona.source_person_id == "delivery-source-001"
    types = {record.record_type for record in persona.records}
    # The problem statement names service events; they must survive import.
    assert source.APPOINTMENT in types
    assert all(record.source_person_id == "delivery-source-001" for record in persona.records)

    second = delivery.DeliveryIndex(tmp_path)
    try:
        other = second.persona("delivery-persona-002")
    finally:
        second.close()
    assert source.DECISION in {record.record_type for record in other.records}
    assert source.is_assessment(other.records[2])

    index = delivery.DeliveryIndex(tmp_path)
    try:
        encounters = index.encounter_index()
        assert set(encounters) == {"delivery-encounter-medication", "delivery-encounter-service-needs"}
        assert encounters["delivery-encounter-medication"].source_person_id == "delivery-source-001"
        assert encounters["delivery-encounter-service-needs"].purpose == "functioning_service_needs"
        assert index.encounter_labels()["delivery-encounter-service-needs"] == (
            "Onni Laakso", "Synteettinen esimerkki E")
    finally:
        index.close()


def test_delivery_dry_run_reports_unmapped_type_and_payload_keys_without_importing(tmp_path: Path):
    source_dir = tmp_path / "source"
    shutil.copytree(_sample_dir(), source_dir)
    records_path = source_dir / "records.jsonl"
    rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines()]
    # No measurement patch any more: the sample's pressure/pulse fields are render keys themselves now, so
    # this case is focused on key/type warnings without standing in for a hard miss.
    lab = next(row for row in rows if row["record_type"] == source.LAB_RESULT)
    lab["payload"]["interpretation"] = lab["payload"].pop("tulkinta")
    decision = next(row for row in rows if row["record_type"] == source.DECISION)
    decision["record_type"] = "vendor-decision-v9"
    decision["payload"] = {"unmapped_value": "synthetic"}
    records_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    output_dir = tmp_path / "dry-run-output"
    result = delivery.dry_run_delivery(source_dir, output_dir)
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert result.status == report["status"] == "warning"
    assert result.profiles_seen == 2 and result.records_seen == 11
    assert report["unmapped_record_types"] == {"vendor-decision-v9": 1}
    assert report["unmapped_payload_keys_by_unmapped_type"]["vendor-decision-v9"] == {"unmapped_value": 1}
    assert report["types"][source.LAB_RESULT]["unmapped_payload_keys"] == {"interpretation": 1}
    assert report["types"][source.LAB_RESULT]["missing_render_keys"]["tulkinta"] == 1
    assert not (output_dir / "people").exists()
    assert not (output_dir / "index.sqlite").exists()


def test_a_blood_pressure_only_measurement_is_renderable(tmp_path: Path):
    """A measurement carrying only a pressure and a pulse is renderable, not "no displayable content".

    This used to read the SAMPLE's own measurement, which at the time carried no weight or height. The
    corpus workstream has since authored `paino_kg`/`pituus_cm` into that row, so the sample now carries
    all four render keys and reconciles `ready` — a fact about the corpus, not about this rule. The rule
    is the row's, so the row is built here: blood pressure and pulse are render keys (since 2026-10-09),
    so this content must NOT be counted as mapping to nothing, while the missing weight/height remain an
    honest coverage warning rather than a refusal.
    """
    source_dir = tmp_path / "source"
    row = _record("bp-only", "person-key")
    row["record_type"] = source.MEASUREMENT
    row["topic"] = "verenpaine"
    row["payload"] = {"verenpaine": "158/92", "syke": 78}
    _write_source(source_dir, [("person-key", "persona-a")], [row])

    report = delivery.dry_run_delivery(source_dir, tmp_path / "dry-run")
    document = json.loads(report.report_path.read_text(encoding="utf-8"))
    measurement = document["types"][source.MEASUREMENT]
    assert measurement["content_maps_to_nothing"] == 0
    assert measurement["content_maps_to_nothing_record_ids"] == []
    assert measurement["unmapped_payload_keys"] == {}
    # A coverage fact, not a refusal — which is the difference between `warning` and `failed`.
    assert report.status == "warning"
    # `lämpö_c` and `spo2` are render keys too, but optional ones: their absence is not a gap (OPTIONAL_RENDER_KEYS).
    assert measurement["missing_render_keys"] == {"paino_kg": 1, "pituus_cm": 1}


def test_dry_run_names_and_counts_rendered_record_with_no_mapped_content(tmp_path: Path):
    source_dir = tmp_path / "source"
    row = _record("record-renamed-text", "person-key")
    row["payload"] = {"vendor_note_text": "synthetic content"}
    _write_source(source_dir, [("person-key", "persona-a")], [row])
    output_dir = tmp_path / "dry-run-output"

    with pytest.raises(delivery.DeliveryShapeError, match="record-renamed-text") as error:
        delivery.dry_run_delivery(source_dir, output_dir)
    assert "maps to no displayable content" in str(error.value)
    report_path = output_dir / "results" / "delivery-reconciliation-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed" and report["imports_records"] is False
    detail = report["types"][source.FREE_TEXT]
    assert detail["content_maps_to_nothing"] == 1
    assert detail["content_maps_to_nothing_record_ids"] == ["record-renamed-text"]
    assert detail["unmapped_payload_keys"] == {"vendor_note_text": 1}
    assert not (output_dir / "people").exists()
    assert not (output_dir / "index.sqlite").exists()


def test_delivery_aliases_known_historical_payload_names(tmp_path: Path):
    source_dir = tmp_path / "source"
    shutil.copytree(_sample_dir(), source_dir)
    records_path = source_dir / "records.jsonl"
    rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines()]
    measurement = next(row for row in rows if row["record_type"] == source.MEASUREMENT)
    measurement["payload"]["paino_kg"] = 72.5
    lab = next(row for row in rows if row["record_type"] == source.LAB_RESULT)
    lab["payload"]["tulos"] = lab["payload"].pop("arvo")
    lab["payload"]["viitealue"] = lab["payload"].pop("viitearvo")
    records_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    dry_run = delivery.dry_run_delivery(source_dir, tmp_path / "dry-run")
    report = json.loads(dry_run.report_path.read_text(encoding="utf-8"))
    aliases = report["payload_key_aliases_applied"][source.LAB_RESULT]
    assert aliases["tulos->arvo"] == aliases["viitealue->viitearvo"] == 1

    imported = delivery.import_delivery(source_dir, tmp_path / "import")
    index = delivery.DeliveryIndex(imported.output_dir)
    try:
        records = [record for persona_id in index for record in index.persona(persona_id).records]
    finally:
        index.close()
    normalized = next(record for record in records if record.record_type == source.LAB_RESULT)
    assert normalized.payload["arvo"] == 3.4 and normalized.payload["viitearvo"] == "< 3.0"
    assert "tulos" not in normalized.payload and "viitealue" not in normalized.payload
    decision = next(record for record in records if record.record_type == source.DECISION)
    assert decision.payload["päätös"] == "Kotihoidon palvelu myönnetty"


@pytest.mark.parametrize(("field", "bad_value"), [
    ("source_person_id", "not-in-profiles"), ("recorded_at", "not-a-date"),
    ("sensitivity_tag", "unclassified"), ("consent_scope", "everything"),
])
def test_dry_run_keeps_permission_critical_field_misses_hard(tmp_path: Path, field, bad_value):
    source_dir = tmp_path / "source"
    _write_source(source_dir, [("person-key", "persona-a")], [_record("r-1", "person-key")])
    row = json.loads((source_dir / "records.jsonl").read_text(encoding="utf-8"))
    row[field] = bad_value
    (source_dir / "records.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(delivery.DeliveryShapeError):
        delivery.dry_run_delivery(source_dir, tmp_path / "dry-run")


@pytest.mark.parametrize("field", ["source_person_id", "recorded_at", "sensitivity_tag", "consent_scope"])
def test_dry_run_missing_permission_critical_fields_remain_hard(tmp_path: Path, field):
    source_dir = tmp_path / "source"
    _write_source(source_dir, [("person-key", "persona-a")], [_record("r-1", "person-key")])
    row = json.loads((source_dir / "records.jsonl").read_text(encoding="utf-8"))
    row.pop(field)
    (source_dir / "records.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(delivery.DeliveryShapeError):
        delivery.dry_run_delivery(source_dir, tmp_path / "dry-run")


def test_operator_encounter_file_is_bounded_independently_of_profile_count(tmp_path: Path, monkeypatch):
    """The ceiling is a ceiling, not the literal 128 of the demo era: pinch it to three so the semantics stay
    pinned without writing a quarter of a million rows, and prove profiles alone do not raise the encounter
    cap. The real ceiling is sized for the release (45,000 people / 38,469 encounters)."""
    monkeypatch.setattr(delivery, "MAX_OPERATOR_ENCOUNTERS", 3)
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    with (source_dir / "profiles.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(delivery.PROFILE_COLUMNS))
        writer.writeheader()
        for index in range(delivery.MAX_OPERATOR_ENCOUNTERS + 1):
            writer.writerow({"source_person_id": f"person-{index}", "persona_id": f"persona-{index}", "label": "Synthetic"})
    with (source_dir / "encounters.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(delivery.ENCOUNTER_COLUMNS))
        writer.writeheader()
        for index in range(delivery.MAX_OPERATOR_ENCOUNTERS + 1):
            writer.writerow({"encounter_id": f"encounter-{index}", "source_person_id": f"person-{index}",
                             "role": "sairaanhoitaja", "purpose": "mental_health_assessment",
                             "example_label": f"Case {index}", "demo_name": f"Synthetic {index}"})
    with pytest.raises(delivery.DeliveryShapeError, match="bounded limit"):
        delivery.import_delivery(source_dir, tmp_path / "out")


def test_store_and_runtime_routes_use_lazy_imported_people_and_operator_encounters(tmp_path: Path, monkeypatch):
    delivery.import_delivery(_sample_dir(), tmp_path)
    monkeypatch.setenv("LUVN_DELIVERY_DIR", str(tmp_path))
    store = Store(path=None)
    assert store.encounter_order() == ("delivery-encounter-medication", "delivery-encounter-service-needs")
    with pytest.raises(RuntimeError, match="whole-delivery enumeration"):
        store.personas()

    main.app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(main.app) as client:
            rows = client.get("/api/encounters").json()["encounters"]
            assert {row["encounter_id"] for row in rows} == set(store.encounter_order())
            picture = client.get("/api/encounters/delivery-encounter-medication/picture", headers=PRINCIPAL)
            assert picture.status_code == 200
            assert picture.json()["encounter_id"] == "delivery-encounter-medication"
    finally:
        main.app.dependency_overrides.clear()
        delivery_index = store._delivery_index
        if delivery_index is not None:
            delivery_index.close()


def test_index_lookup_does_not_read_other_people_or_the_whole_corpus(tmp_path: Path, monkeypatch):
    delivery.import_delivery(_sample_dir(), tmp_path)
    reads: list[str] = []
    original = Path.read_text

    def counting(self, *args, **kwargs):
        reads.append(str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)
    index = delivery.DeliveryIndex(tmp_path)
    try:
        index.persona("delivery-persona-002")
    finally:
        index.close()
    assert reads == [str(tmp_path / "people" / "delivery-persona-002.jsonl")]


def _write_source(source_dir: Path, profiles: list[tuple[str, str]], records: list[dict]) -> None:
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "profiles.csv").write_text(
        "source_person_id,persona_id,label\n"
        + "".join(f"{key},{persona},Synthetic\n" for key, persona in profiles), encoding="utf-8")
    with (source_dir / "records.jsonl").open("w", encoding="utf-8") as stream:
        for row in records:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def _record(record_id: str, person_key: str) -> dict:
    return {"record_id": record_id, "source_person_id": person_key, "record_type": "vapaateksti",
            "authored_by_role": "laakari", "recorded_at": "2026-01-01T00:00:00+00:00", "topic": "hoito",
            "sensitivity_tag": "none", "consent_scope": "treatment", "payload": {"teksti": record_id}}


def test_import_opens_each_person_file_once_when_records_are_grouped(tmp_path: Path, monkeypatch):
    """One write handle per person, not per record: the 50k-profile import cost lived here."""
    source_dir = tmp_path / "src"
    _write_source(source_dir, [("key-a", "persona-a"), ("key-b", "persona-b")],
                  [_record("r-a1", "key-a"), _record("r-a2", "key-a"), _record("r-b1", "key-b")])
    people_dir = tmp_path / "out" / "people"
    opened: list[str] = []
    original = Path.open

    def counting(self, *args, **kwargs):
        if str(self).startswith(str(people_dir)):
            opened.append(str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting)
    delivery.import_delivery(source_dir, tmp_path / "out")
    assert sorted(opened) == sorted([str(people_dir / "persona-a.jsonl"),
                                     str(people_dir / "persona-b.jsonl")])


def test_import_keeps_each_persons_records_ordered_when_input_is_interleaved(tmp_path: Path):
    """Rotation is on the person key, so an ungrouped file stays correct — it only costs more opens."""
    source_dir = tmp_path / "src"
    _write_source(source_dir, [("key-a", "persona-a"), ("key-b", "persona-b")],
                  [_record("r-a1", "key-a"), _record("r-b1", "key-b"),
                   _record("r-a2", "key-a"), _record("r-b2", "key-b")])
    report = delivery.import_delivery(source_dir, tmp_path / "out")
    assert report.records == 4

    index = delivery.DeliveryIndex(tmp_path / "out")
    try:
        first, second = index.persona("persona-a"), index.persona("persona-b")
    finally:
        index.close()
    assert [record.record_id for record in first.records] == ["r-a1", "r-a2"]
    assert [record.record_id for record in second.records] == ["r-b1", "r-b2"]


def test_unbound_source_person_raises_instead_of_being_dropped_silently(tmp_path: Path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "profiles.csv").write_text("source_person_id,persona_id,label\nknown,persona-known,Known\n", encoding="utf-8")
    unbound = {"record_id": "r-unbound", "source_person_id": "not-in-profiles", "record_type": "vapaateksti",
               "authored_by_role": "laakari", "recorded_at": "2026-01-01T00:00:00+00:00", "topic": "hoito",
               "sensitivity_tag": "none", "consent_scope": "treatment", "payload": {"teksti": "x"}}
    (source_dir / "records.jsonl").write_text(json.dumps(unbound) + "\n", encoding="utf-8")
    with pytest.raises(delivery.DeliveryShapeError, match="no bound source person"):
        delivery.import_delivery(source_dir, tmp_path / "out")


def test_duplicate_record_id_is_rejected(tmp_path: Path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "profiles.csv").write_text("source_person_id,persona_id,label\nknown,persona-known,Known\n", encoding="utf-8")
    good = {"record_id": "r-good", "source_person_id": "known", "record_type": "vapaateksti",
            "authored_by_role": "laakari", "recorded_at": "2026-01-01T00:00:00+00:00", "topic": "hoito",
            "sensitivity_tag": "none", "consent_scope": "treatment", "payload": {"teksti": "x"}}
    (source_dir / "records.jsonl").write_text(
        "\n".join(json.dumps(row) for row in (good, good)) + "\n", encoding="utf-8")
    with pytest.raises(delivery.DeliveryShapeError, match="duplicate record ID"):
        delivery.import_delivery(source_dir, tmp_path / "out")


def _frontend_source_fields() -> dict[str, set[str]]:
    root = Path(__file__).resolve().parents[2]
    text = (root / "frontend/src/challenge-demo.mjs").read_text(encoding="utf-8")
    block = text.split("const SOURCE_FIELDS = {", 1)[1].split("\n};", 1)[0]
    found = {}
    for line in block.splitlines():
        match = re.match(r"\s*(['\w-]+): \[(.*)\],?", line)
        if match:
            keys = re.findall(r"\[['\"]([^'\"]+)['\"]", match.group(2))
            found[match.group(1).strip("'\\\"")] = set(keys)
    return found


def _assert_render_map_matches(render_map: dict[str, set[str]]) -> None:
    assert render_map == _frontend_source_fields()


def test_python_render_map_tracks_frontend_declaration_and_detects_mutation():
    _assert_render_map_matches(delivery.RENDER_PAYLOAD_KEYS)
    mutated = {key: set(value) for key, value in delivery.RENDER_PAYLOAD_KEYS.items()}
    mutated[source.LAB_RESULT].add("renamed_key")
    with pytest.raises(AssertionError):
        _assert_render_map_matches(mutated)


def test_a_delivery_encounter_declares_its_consent_holders_and_life_stage(tmp_path: Path):
    """The two lens axes the delivery shape could not express, and what their absence did.

    `DeliveryIndex.encounter_index` built every `Encounter` from five fields, so `consent_holders`
    defaulted to `("self",)` for every delivered lens. That INVERTS a guardian lens: the record the
    child holds under their own confidentiality right is served, and the guardian-held record is
    silently absent with no withheld count. A permission axis that turns itself around by omission is
    the failure this codebase is least willing to accept, so it is declared by column and checked.
    """
    source_dir = tmp_path / "source"
    _write_source(source_dir, [("k1", "p1")], [{
        "record_id": "r-guardian", "source_person_id": "k1", "record_type": source.FREE_TEXT,
        "authored_by_role": "terveydenhoitaja", "recorded_at": "2026-10-01T00:00:00+00:00",
        "topic": "kasvu", "sensitivity_tag": "none", "consent_scope": "treatment",
        "consent_holder": "guardian",
        "payload": {"otsikko": "Kasvu", "teksti": "Huoltajan kanssa käyty läpi kasvun seuranta.",
                    "käyntityyppi": "neuvola"},
    }, {
        "record_id": "r-self", "source_person_id": "k1", "record_type": source.FREE_TEXT,
        "authored_by_role": "terveydenhoitaja", "recorded_at": "2026-10-01T00:00:00+00:00",
        "topic": "uni", "sensitivity_tag": "none", "consent_scope": "treatment",
        "consent_holder": "self",
        "payload": {"otsikko": "Uni", "teksti": "Nuori kertoi nukkuneensa huonosti.",
                    "käyntityyppi": "neuvola"},
    }])
    (source_dir / "encounters.csv").write_text(
        "encounter_id,source_person_id,role,purpose,example_label,demo_name,consent_holders,life_stage\n"
        "e-child,k1,terveydenhoitaja,child_health_checkup,Esimerkki,Lapsi,guardian,child\n"
        "e-default,k1,terveydenhoitaja,child_health_checkup,Esimerkki,Oletus,,\n",
        encoding="utf-8")

    delivery.import_delivery(source_dir, tmp_path / "imported")
    index = delivery.DeliveryIndex(tmp_path / "imported")
    try:
        catalogue = index.encounter_index()
        index_persona = index["p1"]
    finally:
        index.close()

    declared = catalogue["e-child"]
    assert declared.consent_holders == ("guardian",), "a declared guardian lens is a guardian lens"
    assert declared.life_stage == "child"
    # The default stays what `Encounter` says, so a delivery that says nothing is not silently widened.
    assert catalogue["e-default"].consent_holders == ("self",)
    assert catalogue["e-default"].life_stage == "adult"

    # The permission consequence, through the single cut: the guardian lens keeps the guardian's record
    # and the child's own record is absent. Reversed, this test is the defect.
    persona = index_persona
    guardian_scoped = policy.cut_for_encounter(persona, declared)
    serving = {record.record_id for record in guardian_scoped.records}
    assert "r-guardian" in serving, "a guardian lens receives the guardian-held record"
    assert "r-self" not in serving, (
        "a guardian lens must not receive the record the child holds under their own right — with the "
        "default ('self',) this assertion inverts, which is exactly the defect")
    default_scoped = policy.cut_for_encounter(persona, catalogue["e-default"])
    assert {record.record_id for record in default_scoped.records} == {"r-self"}


def test_a_lens_may_cover_both_holders(tmp_path: Path):
    """The declared multi-value branch: `self,guardian` covers both, and both separators are accepted.

    Declared but never exercised until now, which is how a branch becomes a claim instead of a capability.
    """
    source_dir = tmp_path / "both"
    _write_source(source_dir, [("k1", "p1")], [{
        "record_id": "r-guardian", "source_person_id": "k1", "record_type": source.FREE_TEXT,
        "authored_by_role": "terveydenhoitaja", "recorded_at": "2026-10-01T00:00:00+00:00",
        "topic": "kasvu", "sensitivity_tag": "none", "consent_scope": "treatment",
        "consent_holder": "guardian",
        "payload": {"otsikko": "K", "teksti": "Huoltajan kanssa.", "käyntityyppi": "neuvola"},
    }, {
        "record_id": "r-self", "source_person_id": "k1", "record_type": source.FREE_TEXT,
        "authored_by_role": "terveydenhoitaja", "recorded_at": "2026-10-01T00:00:00+00:00",
        "topic": "uni", "sensitivity_tag": "none", "consent_scope": "treatment",
        "consent_holder": "self",
        "payload": {"otsikko": "U", "teksti": "Nuori kertoi.", "käyntityyppi": "neuvola"},
    }])
    (source_dir / "encounters.csv").write_text(
        "encounter_id,source_person_id,role,purpose,example_label,demo_name,consent_holders,life_stage\n"
        "e-both,k1,terveydenhoitaja,child_health_checkup,E,N,\"self,guardian\",child\n"
        "e-semi,k1,terveydenhoitaja,child_health_checkup,E,N,guardian;self,child\n",
        encoding="utf-8")
    delivery.import_delivery(source_dir, tmp_path / "imported")
    index = delivery.DeliveryIndex(tmp_path / "imported")
    try:
        catalogue = index.encounter_index()
        persona = index["p1"]
    finally:
        index.close()
    assert catalogue["e-both"].consent_holders == ("self", "guardian")
    assert catalogue["e-semi"].consent_holders == ("guardian", "self"), "a semicolon separates too"
    for encounter_id in ("e-both", "e-semi"):
        serving = {record.record_id
                   for record in policy.cut_for_encounter(persona, catalogue[encounter_id]).records}
        assert serving == {"r-guardian", "r-self"}, (
            f"{encounter_id} covers both holders, so both records are served")


def test_an_unknown_consent_holder_or_life_stage_is_refused_not_guessed(tmp_path: Path):
    """A permission axis is not something to infer. An unknown value fails the import."""
    for column, value in (("consent_holders", "parent"), ("life_stage", "teenager")):
        source_dir = tmp_path / column
        _write_source(source_dir, [("k1", "p1")], [{
            "record_id": "r1", "source_person_id": "k1", "record_type": source.FREE_TEXT,
            "authored_by_role": "terveydenhoitaja", "recorded_at": "2026-10-01T00:00:00+00:00",
            "topic": "kasvu", "sensitivity_tag": "none", "consent_scope": "treatment",
            "payload": {"otsikko": "O", "teksti": "T", "käyntityyppi": "neuvola"},
        }])
        (source_dir / "encounters.csv").write_text(
            "encounter_id,source_person_id,role,purpose,example_label,demo_name,"
            "consent_holders,life_stage\ne1,k1,terveydenhoitaja,child_health_checkup,E,N,"
            + (f"{value}," if column == "consent_holders" else ",")
            + (f"{value}\n" if column == "life_stage" else "\n"),
            encoding="utf-8")
        with pytest.raises(delivery.DeliveryShapeError, match=column.split("_")[0]):
            delivery.dry_run_delivery(source_dir, tmp_path / f"dry-{column}")
def test_a_header_without_consent_holders_is_refused_not_defaulted_to_self(tmp_path: Path):
    """The guardian-lens guard. The reader checks only the columns it declares, so before the contract was
    widened a header that lost or misspelled `consent_holders` was accepted, every lens defaulted to
    `("self",)`, and a guardian lens was inverted with no refusal. Such a header is now refused by name, by
    both the import and the dry run (both read through `_read_encounters`)."""
    source_dir = _bounded_source(tmp_path)
    headers = {
        "missing": "encounter_id,source_person_id,role,purpose,example_label,demo_name,life_stage\n"
                   "e1,k,laakari,medication_follow_up,E,N,child\n",
        "misspelled": "encounter_id,source_person_id,role,purpose,example_label,demo_name,consent_holder,life_stage\n"
                      "e1,k,laakari,medication_follow_up,E,N,guardian,child\n",
    }
    for label, text in headers.items():
        (source_dir / "encounters.csv").write_text(text, encoding="utf-8")
        with pytest.raises(delivery.DeliveryShapeError) as import_error:
            delivery.import_delivery(source_dir, tmp_path / f"out-{label}")
        assert import_error.value.code == "missing_columns", label
        assert import_error.value.values == {"missing": ["consent_holders"]}, label
        with pytest.raises(delivery.DeliveryShapeError) as dry_error:
            delivery.dry_run_delivery(source_dir, tmp_path / f"dry-{label}")
        assert "consent_holders" in str(dry_error.value), label


def test_a_delivery_can_carry_a_scored_instrument(tmp_path: Path):
    """The shape carries an instrument's answers, and the answers actually score after import.

    This is the gap between "the delivery shape" and "what the demo demonstrates": the instrument answers
    live in `payload.vastaukset`, which nothing renders directly — `assess.score` turns them into the item
    rows and the total. Until 2026-10-09 that key was not registered for `arviointi`, so every delivery
    carrying a scored instrument reported it as an *unmapped* payload key: a warning that was really a
    declaration the map had not made, and the reason a clean scored corpus could not reconcile clean.
    """
    source_dir = tmp_path / "source"
    answers = {"q1": 2, "q2": 3, "q3": 1, "q4": 2, "q5": 1, "q6": 2, "q7": 2, "q8": 2, "q9": 1}
    _write_source(source_dir, [("k1", "p1")], [{
        "record_id": "r-phq9", "source_person_id": "k1", "record_type": source.ARVIOINTI,
        "authored_by_role": "laakari", "recorded_at": "2026-10-01T00:00:00+00:00",
        "topic": "mielenterveys", "sensitivity_tag": "sensitive", "consent_scope": "treatment",
        "payload": {"instrumentti": "PHQ-9", "versio": "demo-v2", "vastaukset": answers},
    }])

    report = delivery.dry_run_delivery(source_dir, tmp_path / "dry-run")
    document = json.loads(report.report_path.read_text(encoding="utf-8"))
    assert report.status == "ready", document["types"][source.ARVIOINTI]
    assert document["types"][source.ARVIOINTI]["unmapped_payload_keys"] == {}

    delivery.import_delivery(source_dir, tmp_path / "imported")
    index = delivery.DeliveryIndex(tmp_path / "imported")
    try:
        [record] = index["p1"].records
    finally:
        index.close()
    scored = assess.score(record)
    assert (scored.instrumentti, scored.valmis, scored.total, scored.max_total) == ("PHQ-9", True, 16, 27)
    assert len(scored.osiot) == 9 and {item.osio for item in scored.osiot} == set(answers)
    # The answers survive the round trip, which is what makes the imported briefing equal to the curated one.
    assert record.payload["vastaukset"] == answers


def test_renamed_lab_render_key_is_named_in_shape_report(tmp_path: Path):
    source_dir = _sample_dir()
    rows = [json.loads(line) for line in (source_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    lab = next(row for row in rows if row["record_type"] == source.LAB_RESULT)
    lab["payload"]["interpretation"] = lab["payload"].pop("tulkinta", None)
    lab["payload"].pop("tulkinta", None)
    _write_source(tmp_path / "src", [("delivery-source-001", "person-1"),
                                       ("delivery-source-002", "person-2")], rows)
    report = delivery.import_delivery(tmp_path / "src", tmp_path / "out")
    shape = json.loads(report.shape_report.read_text(encoding="utf-8"))["types"][source.LAB_RESULT]
    assert shape["render_keys_missing"]["tulkinta"] >= 1
    assert shape["unexpected_keys"]["interpretation"] == 1


def test_unsafe_persona_id_cannot_escape_the_people_directory(tmp_path: Path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "profiles.csv").write_text(
        'source_person_id,persona_id,label\nknown,../../../escaped,Label\n', encoding="utf-8")
    good = {"record_id": "r1", "source_person_id": "known", "record_type": "vapaateksti",
            "authored_by_role": "laakari", "recorded_at": "2026-01-01T00:00:00+00:00", "topic": "hoito",
            "sensitivity_tag": "none", "consent_scope": "treatment", "payload": {"teksti": "x"}}
    (source_dir / "records.jsonl").write_text(json.dumps(good) + "\n", encoding="utf-8")
    with pytest.raises(delivery.DeliveryShapeError, match="unsafe persona_id"):
        delivery.import_delivery(source_dir, tmp_path / "out")
    assert not (tmp_path / "escaped.jsonl").exists()


def test_reimport_clears_old_person_file_when_profile_has_no_records(tmp_path: Path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    profiles = "source_person_id,persona_id,label\nk,person-k,K\n"
    (source_dir / "profiles.csv").write_text(profiles, encoding="utf-8")
    row = {"record_id": "r1", "source_person_id": "k", "record_type": "vapaateksti",
           "authored_by_role": "laakari", "recorded_at": "2026-01-01T00:00:00+00:00", "topic": "hoito",
           "sensitivity_tag": "none", "consent_scope": "treatment", "payload": {"teksti": "x"}}
    (source_dir / "records.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    output_dir = tmp_path / "out"
    delivery.import_delivery(source_dir, output_dir)
    (source_dir / "records.jsonl").write_text("", encoding="utf-8")
    delivery.import_delivery(source_dir, output_dir)
    index = delivery.DeliveryIndex(output_dir)
    try:
        assert index.persona("person-k").records == []
    finally:
        index.close()


def test_profile_without_records_still_resolves_through_the_index(tmp_path: Path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "profiles.csv").write_text(
        "source_person_id,persona_id,label\nwith-records,persona-a,A\nno-records,persona-b,B\n", encoding="utf-8")
    good = {"record_id": "r1", "source_person_id": "with-records", "record_type": "vapaateksti",
            "authored_by_role": "laakari", "recorded_at": "2026-01-01T00:00:00+00:00", "topic": "hoito",
            "sensitivity_tag": "none", "consent_scope": "treatment", "payload": {"teksti": "x"}}
    (source_dir / "records.jsonl").write_text(json.dumps(good) + "\n", encoding="utf-8")
    delivery.import_delivery(source_dir, tmp_path / "out")
    index = delivery.DeliveryIndex(tmp_path / "out")
    try:
        assert index.persona("persona-b").records == []
    finally:
        index.close()


def test_missing_required_column_fails_loudly_not_silently(tmp_path: Path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "profiles.csv").write_text("source_person_id,persona_id,label\nknown,persona-known,Known\n", encoding="utf-8")
    (source_dir / "records.jsonl").write_text(
        json.dumps({"record_id": "r1", "source_person_id": "known"}) + "\n", encoding="utf-8")
    with pytest.raises(delivery.DeliveryShapeError, match="missing columns"):
        delivery.import_delivery(source_dir, tmp_path / "out")


def test_csv_delivery_imports_all_sample_record_types_and_encounters(tmp_path: Path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    sample = _sample_dir()
    (source_dir / "profiles.csv").write_text((sample / "profiles.csv").read_text(encoding="utf-8"), encoding="utf-8")
    (source_dir / "encounters.csv").write_text((sample / "encounters.csv").read_text(encoding="utf-8"), encoding="utf-8")
    records = [json.loads(line) for line in (sample / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    with (source_dir / "records.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(delivery.RECORD_COLUMNS))
        writer.writeheader()
        for record in records:
            writer.writerow({**record, "payload": json.dumps(record["payload"], ensure_ascii=False)})
    report = delivery.import_delivery(source_dir, tmp_path / "out")
    assert report.records == 11 and report.encounters == 2
    index = delivery.DeliveryIndex(tmp_path / "out")
    try:
        types = {record.record_type for persona_id in index for record in index.persona(persona_id).records}
        assert {source.APPOINTMENT, source.DECISION, source.ARVIOINTI} <= types
    finally:
        index.close()


def test_delivery_mode_requires_operator_encounter_mapping(tmp_path: Path, monkeypatch):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "profiles.csv").write_text("source_person_id,persona_id,label\nk,p,Label\n", encoding="utf-8")
    (source_dir / "records.jsonl").write_text("", encoding="utf-8")
    delivery.import_delivery(source_dir, tmp_path / "out")
    monkeypatch.setenv("LUVN_DELIVERY_DIR", str(tmp_path / "out"))
    with pytest.raises(RuntimeError, match="encounters.csv owner mapping"):
        Store(path=None)


def test_csv_format_is_accepted_with_json_payload(tmp_path: Path):

    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "profiles.csv").write_text("source_person_id,persona_id,label\nk,p,Label\n", encoding="utf-8")
    with (source_dir / "records.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(delivery.RECORD_COLUMNS))
        writer.writeheader()
        writer.writerow({"record_id": "r1", "source_person_id": "k", "record_type": "vapaateksti",
                         "authored_by_role": "laakari", "recorded_at": "2026-01-01T00:00:00+00:00",
                         "topic": "hoito", "sensitivity_tag": "none", "consent_scope": "treatment",
                         "payload": json.dumps({"teksti": "csv muoto"})})
    report = delivery.import_delivery(source_dir, tmp_path / "out")
    assert report.records == 1
    index = delivery.DeliveryIndex(tmp_path / "out")
    try:
        assert index.persona("p").records[0].payload == {"teksti": "csv muoto"}
    finally:
        index.close()


def test_index_is_read_only_and_reports_the_encodings(tmp_path: Path):
    delivery.import_delivery(_sample_dir(), tmp_path)
    index = delivery.DeliveryIndex(tmp_path)
    try:
        assert {row[0] for row in index.encodings()} == {"delivery-persona-001", "delivery-persona-002"}
        with pytest.raises(sqlite3.OperationalError):
            index._connection.execute("DELETE FROM people")
    finally:
        index.close()


# --- the local index is a hand-editable file: its `path` and `persona_id` columns are untrusted ---------

def _envelope(record_id: str, source_person_id: str, **overrides) -> dict:
    return {"record_id": record_id, "source": "delivery", "source_person_id": source_person_id,
            "record_type": "vapaateksti", "authored_by_role": "laakari",
            "recorded_at": "2026-01-01T00:00:00+00:00", "topic": "hoito",
            "sensitivity_tag": "none", "consent_scope": "treatment",
            "payload": {"teksti": "x"}, **overrides}


def _hand_built_index(directory: Path, rows: list[tuple[str, str, str, str]]) -> Path:
    """An index that satisfies the version check but points wherever the tuple says.

    Built by hand on purpose: the whole finding is that `path` is a column an operator (or a repair tool)
    can write anything into, so a test that only ever goes through `import_delivery` cannot reach it.
    """
    (directory / "people").mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(directory / "index.sqlite")
    try:
        connection.execute("CREATE TABLE people (persona_id TEXT NOT NULL PRIMARY KEY, "
                           "source_person_id TEXT NOT NULL UNIQUE, label TEXT, path TEXT NOT NULL, "
                           "CHECK (persona_id <> ''), CHECK (source_person_id <> ''))")
        connection.execute("CREATE TABLE encounters (encounter_id TEXT NOT NULL PRIMARY KEY, "
                           "source_person_id TEXT NOT NULL REFERENCES people(source_person_id))")
        connection.execute("CREATE TABLE seen_records (record_id TEXT NOT NULL PRIMARY KEY)")
        connection.execute("CREATE TABLE seen_basics (source_person_id TEXT NOT NULL PRIMARY KEY)")
        for row in rows:
            connection.execute("INSERT INTO people VALUES (?, ?, '', ?)",
                               (row[0], row[1], row[3]))
        connection.execute(f"PRAGMA user_version = {delivery.INDEX_SCHEMA_VERSION}")
        connection.commit()
    finally:
        connection.close()
    return directory


@pytest.mark.parametrize("kind", ["relative-escape", "absolute"])
def test_a_hand_built_index_cannot_point_the_app_at_a_file_outside_the_import_dir(
        tmp_path: Path, kind: str):
    out = tmp_path / "out"
    out.mkdir()
    (out / "people").mkdir()
    # The file really exists and really is person-shaped: the refusal is the path rule, not a 404.
    outside = tmp_path / "outside-secret.jsonl"
    path = "people/../../outside-secret.jsonl" if kind == "relative-escape" else str(outside)
    outside.write_text(json.dumps(_envelope("secret-r1", "s")) + "\n", encoding="utf-8")
    try:
        _hand_built_index(out, [("my-persona", "my-source", "Me", path)])
        index = delivery.DeliveryIndex(out)
        try:
            with pytest.raises(delivery.DeliveryShapeError) as error:
                index["my-persona"]
        finally:
            index.close()
    finally:
        outside.unlink(missing_ok=True)
    assert error.value.code == "unsafe_person_path"
    assert "outside" in str(error.value) and "my-persona" in str(error.value)


def test_a_hand_built_index_cannot_serve_another_persons_file_as_this_person(tmp_path: Path):
    """`path` staying inside the dir is not enough: the row's binding must match the file's own records."""
    out = tmp_path / "out"
    out.mkdir()
    _hand_built_index(out, [("persona-x", "source-x", "X", "people/persona-x.jsonl"),
                            ("persona-y", "source-y", "Y", "people/persona-x.jsonl")])
    (out / "people" / "persona-x.jsonl").write_text(
        json.dumps(_envelope("r-x1", "source-x")) + "\n", encoding="utf-8")
    index = delivery.DeliveryIndex(out)
    try:
        assert index["persona-x"].records[0].record_id == "r-x1"  # the honest binding still reads
        with pytest.raises(delivery.DeliveryShapeError) as error:
            index["persona-y"]
    finally:
        index.close()
    assert error.value.code == "person_binding_mismatch"
    # The message names BOTH id spaces, so an operator can see which binding is wrong.
    assert "source-y" in str(error.value) and "source-x" in str(error.value)
    assert error.value.values["records"] == ["r-x1"]


def test_an_index_from_an_older_schema_is_refused_by_name_not_trusted(tmp_path: Path):
    out = tmp_path / "out"
    out.mkdir()
    _hand_built_index(out, [("p", "s", "", "people/p.jsonl")])
    (out / "people" / "p.jsonl").write_text(json.dumps(_envelope("r1", "s")) + "\n", encoding="utf-8")
    connection = sqlite3.connect(out / "index.sqlite")
    connection.execute("PRAGMA user_version = 3")
    connection.commit()
    connection.close()
    with pytest.raises(delivery.DeliveryShapeError, match="older revision"):
        delivery.DeliveryIndex(out)


def test_the_index_ddl_refuses_a_path_that_is_not_the_import_shape(tmp_path: Path):
    """The DDL check is the boundary a hand-built index cannot get past even before the read-path check."""
    delivery.import_delivery(_sample_dir(), tmp_path)
    connection = sqlite3.connect(tmp_path / "index.sqlite")
    try:
        # `GLOB 'people/*.jsonl'` would have ADMITTED the first of these: GLOB `*` spans separators, so the
        # pattern a first draft used accepted every traversal path. The shipped predicate is INSTR-based.
        for path in ("people/../../elsewhere.jsonl", "not-people/p.jsonl", "people/p.txt", "/abs/p.jsonl"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO people VALUES (?, 'sx', '', ?)", (f"p-{len(path)}", path))
    finally:
        connection.close()


# --- deeply nested JSON is a refused row, never a process-killing traceback ------------------------------

def _deeply_nested_payload(levels: int = 20000) -> str:
    return "[" * levels + "]" * levels


def test_a_deeply_nested_records_line_is_refused_in_strict_mode_and_the_process_lives(tmp_path: Path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "profiles.csv").write_text("source_person_id,persona_id,label\nk,p,Label\n", encoding="utf-8")
    (source_dir / "records.jsonl").write_text(
        json.dumps(_envelope("r-deep", "k"))[:-1] + ',"payload":{"deep":' + _deeply_nested_payload() + "}}\n",
        encoding="utf-8")
    with pytest.raises(delivery.DeliveryShapeError, match="is not JSON") as error:
        delivery.import_delivery(source_dir, tmp_path / "strict")
    assert error.value.code == "malformed_row"
    # The process is alive: this line is reached only if the `RecursionError` was caught.
    assert "RecursionError" in str(error.value)


def test_a_deeply_nested_records_line_is_quarantined_and_the_rest_loads(tmp_path: Path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "profiles.csv").write_text("source_person_id,persona_id,label\nk,p,Label\n", encoding="utf-8")
    good = json.dumps(_envelope("r-good", "k"))
    deep = json.dumps(_envelope("r-deep", "k"))[:-1] + ',"payload":{"deep":' + _deeply_nested_payload() + "}}"
    (source_dir / "records.jsonl").write_text(good + "\n" + deep + "\n", encoding="utf-8")

    report = delivery.import_delivery(source_dir, tmp_path / "out", on_error="quarantine")

    assert report.records == 1 and report.quarantined == 1
    assert report.quarantine_path is not None and report.quarantine_path.is_file()
    entries = [json.loads(line) for line in report.quarantine_path.read_text(encoding="utf-8").splitlines()]
    assert [entry["reason_code"] for entry in entries] == ["malformed_row"]
    assert "RecursionError" in entries[0]["reason"]
    assert report.quarantine_summary is not None
    assert json.loads(report.quarantine_summary.read_text(encoding="utf-8"))["quarantined"] == 1
    index = delivery.DeliveryIndex(tmp_path / "out")
    try:
        assert [record.record_id for record in index["p"].records] == ["r-good"]
    finally:
        index.close()


# --- M4/D7: the two engines must refuse the same corpus, before any write ---------------------------------

def _bounded_source(tmp_path: Path, record_id: str = "r1", label: str = "Label") -> Path:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "profiles.csv").write_text(
        f"source_person_id,persona_id,label\nk,p,{label}\n", encoding="utf-8")
    (source_dir / "records.jsonl").write_text(
        json.dumps(_envelope(record_id, "k")) + "\n", encoding="utf-8")
    return source_dir


def test_an_over_long_record_id_is_refused_by_the_envelope_with_the_field_and_the_limit(tmp_path: Path):
    """The envelope now carries the width SQL already had, so neither engine can be the first to fail.

    Before this, `import_delivery` accepted a 200-character id and the SQL INSERT died with Msg 8152 and
    rolled the whole load transaction back -- one vendor id losing an entire arrival-day load.
    """
    source_dir = _bounded_source(tmp_path, record_id="r" * 200)
    with pytest.raises(delivery.DeliveryShapeError) as error:
        delivery.import_delivery(source_dir, tmp_path / "out")
    assert error.value.code == "field_too_long"
    assert "record_id" in str(error.value) and "128" in str(error.value)


def test_an_over_long_source_person_key_is_refused_before_the_write(tmp_path: Path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    long_key = "k" * 200
    (source_dir / "profiles.csv").write_text(
        f"source_person_id,persona_id,label\n{long_key},p,Label\n", encoding="utf-8")
    (source_dir / "records.jsonl").write_text(
        json.dumps(_envelope("r1", long_key)) + "\n", encoding="utf-8")
    with pytest.raises(delivery.DeliveryShapeError) as error:
        delivery.import_delivery(source_dir, tmp_path / "out")
    assert error.value.code == "field_too_long"
    assert "source_person_id" in str(error.value) and "128" in str(error.value)


def test_an_over_long_label_is_refused_by_the_importer_and_the_envelope(tmp_path: Path):
    """The 256-character label in `people.label` and `encounters.example_label`: both halves, both engines."""
    source_dir = _bounded_source(tmp_path, label="L" * 300)
    with pytest.raises(delivery.DeliveryShapeError) as error:
        delivery.import_delivery(source_dir, tmp_path / "out")
    assert "label" in str(error.value) and str(delivery.LABEL_MAX_LENGTH) in str(error.value)

    # The envelope's own bound, independent of the importer's check.
    from pydantic import (
        ValidationError,  # noqa: PLC0415 - the model bound is what is under test
    )

    from app.upm import Persona  # noqa: PLC0415
    with pytest.raises(ValidationError, match="at most 256 characters"):
        Persona(persona_id="p", label="L" * 300,
                records=[delivery.Record.model_validate(_envelope("r1", "k"))])


def test_an_over_long_encounter_label_is_refused_by_the_importer(tmp_path: Path):
    source_dir = _bounded_source(tmp_path)
    (source_dir / "encounters.csv").write_text(
        "encounter_id,source_person_id,role,purpose,example_label,demo_name,consent_holders,life_stage\n"
        f"e1,k,laakari,medication_follow_up,{'E' * 300},,,\n", encoding="utf-8")
    with pytest.raises(delivery.DeliveryShapeError) as error:
        delivery.import_delivery(source_dir, tmp_path / "out")
    assert error.value.code == "field_too_long"
    assert "example_label" in str(error.value) and "256" in str(error.value)


def test_the_sql_column_widths_and_the_envelope_bounds_are_the_same_numbers():
    """The two engines' declarations, compared as data so a widened column cannot drift from the model."""
    from app import sqldelivery  # noqa: PLC0415
    from app.upm import ID_MAX_LENGTH, LABEL_MAX_LENGTH  # noqa: PLC0415

    records = next(entry.statement for entry in sqldelivery.SCHEMA_STATEMENTS if entry.name == "records")
    people = next(entry.statement for entry in sqldelivery.SCHEMA_STATEMENTS if entry.name == "people")
    encounters = next(entry.statement for entry in sqldelivery.SCHEMA_STATEMENTS
                      if entry.name == "encounters")
    assert f"record_id NVARCHAR({ID_MAX_LENGTH})" in records
    assert f"source_person_id NVARCHAR({ID_MAX_LENGTH})" in records
    assert f"persona_id NVARCHAR({ID_MAX_LENGTH})" in people
    assert f"label NVARCHAR({LABEL_MAX_LENGTH})" in people
    assert f"example_label NVARCHAR({LABEL_MAX_LENGTH})" in encounters
    assert f"demo_name NVARCHAR({LABEL_MAX_LENGTH})" in encounters
