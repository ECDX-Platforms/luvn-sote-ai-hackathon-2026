"""Gate 6 — the arrival rehearsal, and the import failure modes it does not already cover.

Two jobs, both about making the real 2026-10-13 run the *second* run:

1. **The rehearsal.** `data/experimental/delivery-rehearsal/` is a committed corpus in the delivery's
   declared shape — the real column names, the declared payload keys, the code systems the alignment spec
   names, 360 records over 40 people and a bounded operator catalogue. `dry_run_delivery` over it must
   report `ready` with no flag on any type, and `import_delivery` over it must import every record and
   resolve through the lazy index. If either stops being true, arrival day is the first time anyone finds
   out.

2. **The named failure modes that had no test at the strict path.** The dry run already had coverage for an
   unmapped record type, an unmapped payload key, a missing permission-critical field, a renamed lab render
   key and an unbound source person (`tests/test_delivery.py`::

       test_delivery_dry_run_reports_unmapped_type_and_payload_keys_without_importing   # unmapped type + key
       test_renamed_lab_render_key_is_named_in_shape_report                             # renamed lab key
       test_dry_run_keeps_permission_critical_field_misses_hard                         # invalid critical field
       test_dry_run_missing_permission_critical_fields_remain_hard                      # absent critical field
       test_unbound_source_person_raises_instead_of_being_dropped_silently              # unbound person

   ). What follows extends rather than repeats: the same modes at the **strict import** path, where a dry
   run's warning becomes a hard refusal, plus the profile-file and catalogue failures the dry run cannot
   reach because it never opens them for writing.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from app import delivery, source
from app.encounters import resolve_encounter
from app.policy import cut_for_encounter

ROOT = Path(__file__).resolve().parents[2]
REHEARSAL = ROOT / "data" / "experimental" / "delivery-rehearsal"
TODAY = date(2026, 10, 13)


def _write_source(source_dir: Path, profiles: list[tuple[str, str]], records: list[dict],
                  encounters: list[tuple[str, str, str, str]] | None = None) -> None:
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "profiles.csv").write_text(
        "source_person_id,persona_id,label\n"
        + "".join(f"{key},{persona},Synthetic\n" for key, persona in profiles), encoding="utf-8")
    with (source_dir / "records.jsonl").open("w", encoding="utf-8") as stream:
        for row in records:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    if encounters is not None:
        with (source_dir / "encounters.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("encounter_id", "source_person_id", "role", "purpose", "example_label", "demo_name",
                             "consent_holders", "life_stage"))
            for encounter_id, key, role, purpose in encounters:
                writer.writerow((encounter_id, key, role, purpose, "Synthetic", "Synthetic", "", ""))
    elif (source_dir / "encounters.csv").exists():
        (source_dir / "encounters.csv").unlink()


def _record(record_id: str, person_key: str, record_type: str = source.FREE_TEXT,
            payload: dict | None = None) -> dict:
    return {"record_id": record_id, "source_person_id": person_key, "record_type": record_type,
            "authored_by_role": "laakari", "recorded_at": "2026-10-01T00:00:00+00:00", "topic": "hoito",
            "sensitivity_tag": "none", "consent_scope": "treatment",
            "payload": {"teksti": record_id} if payload is None else payload}


# --------------------------------------------------------------------------------------------
# 1. The rehearsal
# --------------------------------------------------------------------------------------------

def test_the_rehearsal_corpus_is_the_declared_shape_at_a_realistic_size():
    """A renamed column would make the rehearsal rehearse something else. Anti-vacuity included."""
    with (REHEARSAL / "profiles.csv").open(encoding="utf-8", newline="") as stream:
        profiles = list(csv.DictReader(stream))
    with (REHEARSAL / "encounters.csv").open(encoding="utf-8", newline="") as stream:
        encounters = list(csv.DictReader(stream))
    rows = [json.loads(line) for line in (REHEARSAL / "records.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    assert tuple(profiles[0]) == delivery.PROFILE_COLUMNS
    assert tuple(encounters[0]) == delivery.ENCOUNTER_COLUMNS
    assert set(rows[0]) - {"payload", "code_system", "form"} <= set(delivery.RECORD_COLUMNS)
    assert len(rows) >= 300, f"a few hundred rows is the point; the corpus has {len(rows)}"
    assert len(profiles) >= 20
    assert 0 < len(encounters) <= delivery.MAX_OPERATOR_ENCOUNTERS
    # Every one of the nine canonical types appears, so a type-specific reconciliation cannot pass by
    # never being exercised.
    assert {row["record_type"] for row in rows} == {
        source.BASIC_INFO, source.DIAGNOSIS, source.MEDICATION, source.FREE_TEXT, source.ARVIOINTI,
        source.MEASUREMENT, source.LAB_RESULT, source.APPOINTMENT, source.DECISION,
    }
    # The code systems the alignment spec names are carried, and carried as a property of the fact.
    assert {"ICD-10", "ATC", "FinLOINC"} <= {row.get("code_system") for row in rows}
    assert all(row["source_person_id"] in {p["source_person_id"] for p in profiles} for row in rows)


def test_the_rehearsal_dry_run_reports_ready_with_no_flag_on_any_type(tmp_path: Path):
    """`status: ready` is the output the runbook tells the operator to expect. This is that output."""
    report = delivery.dry_run_delivery(REHEARSAL, tmp_path)
    assert report.status == "ready", report.report_path.read_text(encoding="utf-8")
    document = json.loads(report.report_path.read_text(encoding="utf-8"))
    assert document["imports_records"] is False and document["kind"] == "delivery-reconciliation"
    assert document["unmapped_record_types"] == {}
    assert document["unmapped_payload_keys_by_unmapped_type"] == {}
    assert set(document["types"]) == {
        source.BASIC_INFO, source.DIAGNOSIS, source.MEDICATION, source.FREE_TEXT, source.ARVIOINTI,
        source.MEASUREMENT, source.LAB_RESULT, source.APPOINTMENT, source.DECISION,
    }
    flagged = {name: item for name, item in document["types"].items()
               if item["missing_render_keys"] or item["unmapped_payload_keys"]
               or item["useful_set_miss_rows"] or item["content_maps_to_nothing"]}
    assert flagged == {}, f"a status of `ready` with flags on a type would be a lie: {flagged}"
    assert all(item["rows_seen"] > 0 for item in document["types"].values())
    # A dry run imports nothing; the whole point of running it first.
    assert not (tmp_path / "index.sqlite").exists() and not (tmp_path / "people").exists()


def test_the_rehearsal_imports_every_record_and_resolves_through_the_lazy_index(tmp_path: Path):
    """The rehearsal is only worth anything if the operator catalogue still binds after the import."""
    report = delivery.import_delivery(REHEARSAL, tmp_path)
    assert (report.personas, report.records, report.skipped) == (40, 360, 0)
    assert report.encounters == 6
    shape = json.loads(report.shape_report.read_text(encoding="utf-8"))
    assert all(item["rows_seen"] > 0 and item["useful_set_met"] > 0 for item in shape["types"].values()), (
        "the strict import's own gate is `a present type with zero useful rows fails`; this is it passing")

    index = delivery.DeliveryIndex(tmp_path)
    try:
        encounters = index.encounter_index()
        assert len(encounters) == report.encounters
        for encounter_id, encounter in encounters.items():
            persona = index[encounter.persona_id]
            assert persona.records, f"{encounter_id} resolved to a person with no records"
            resolved, bound = resolve_encounter(encounter_id, {persona.persona_id: persona}, encounters)
            scoped = cut_for_encounter(bound, resolved)
            # The permission cut runs on the imported records exactly as it does on the curated fixtures;
            # an imported record with no consent_scope could not have got this far.
            assert scoped.records, f"{encounter_id} cut to nothing"
            assert all(record.record_id for record in scoped.records)
    finally:
        index.close()


def test_every_types_useful_set_includes_a_render_key():
    """The invariant the rehearsal broke on, pinned so it cannot come back.

    A type whose "useful" keys are disjoint from its RENDER keys can never reconcile clean: carrying the
    useful keys is counted as an unmapped payload key, and carrying only the render keys is counted as a
    useful-set miss. `mittaus` was exactly that — `{verenpaine, syke}` against `{paino_kg, pituus_cm}` — so a
    delivery containing measurements could never reach `status: ready`. Measured on the rehearsal corpus
    before the fix: `status: warning` with `unmapped_payload_keys: {verenpaine: 40, syke: 40}` on `mittaus`
    and nothing else wrong anywhere. After it: `ready`.
    """
    assert set(delivery.USEFUL_PAYLOAD_KEYS) == set(delivery.RENDER_PAYLOAD_KEYS)
    for record_type, useful in delivery.USEFUL_PAYLOAD_KEYS.items():
        render = delivery.RENDER_PAYLOAD_KEYS[record_type]
        assert useful & render, (
            f"{record_type} has useful keys {sorted(useful)} disjoint from its render keys "
            f"{sorted(render)}: no row of that type can ever be both useful and renderable")
    # The historical defect, still named because it is what the loop protects: `{verenpaine, syke}` against
    # `{paino_kg, pituus_cm}`. Those two sets are no longer disjoint — blood pressure and pulse became render
    # keys on 2026-10-09, which is the fix — so the defect is asserted as gone rather than reused as the
    # probe, because a probe built from current data stops probing the moment the data changes.
    assert {"verenpaine", "syke"} <= delivery.RENDER_PAYLOAD_KEYS[source.MEASUREMENT], (
        "the measurement render keys now include the names our own vocabulary uses: the two sets that were "
        "disjoint are not any more")
    # Non-vacuity, by executing the loop's own body against a synthetic disjoint pair. The version this
    # replaces asserted `not ({"a"} & {"b"})`, which is true by definition and therefore proved nothing
    # while its comment claimed the loop was not vacuous — the same defect as a comment claiming coverage
    # no assertion provides, which is what ADR-0003 was corrected for earlier in this very round.
    with pytest.raises(AssertionError, match="disjoint"):
        for record_type, useful in {"synthetic-type": {"a"}}.items():
            render = {"b"}
            assert useful & render, (
                f"{record_type} has useful keys {sorted(useful)} disjoint from its render keys "
                f"{sorted(render)}: no row of that type can ever be both useful and renderable")


# --------------------------------------------------------------------------------------------
# 2. The named failure modes at the strict import path
# --------------------------------------------------------------------------------------------

def test_import_refuses_an_unmapped_record_type_and_names_the_record(tmp_path: Path):
    """The dry run reports an unmapped type as a warning; the strict path must refuse it.

    Extends `test_delivery_dry_run_reports_unmapped_type_and_payload_keys_without_importing`, which covers
    the reporting half only.
    """
    source_dir = tmp_path / "source"
    _write_source(source_dir, [("k1", "p1")], [_record("r1", "k1", record_type="vendor-observation-v9")])
    with pytest.raises(delivery.DeliveryShapeError) as raised:
        delivery.import_delivery(source_dir, tmp_path / "out")
    assert "unknown record_type" in str(raised.value) and "vendor-observation-v9" in str(raised.value)
    assert "r1" in str(raised.value), "the refusal must name the record, not only the type"


def test_import_refuses_a_present_type_whose_rows_say_nothing_useful(tmp_path: Path):
    """The importer's final gate, with no dry-run equivalent: a present type with zero useful rows.

    `diagnoosi`'s useful key is `nimi`. A row that carries an unknown key and never `nimi` is an imported
    record with no renderable content, and the importer refuses the whole delivery rather than writing a
    people index whose diagnoses are invisible. The dry run's own hard failure covers
    `content_maps_to_nothing`; this is the strict path's separate gate.
    """
    source_dir = tmp_path / "source"
    _write_source(source_dir, [("k1", "p1")], [
        _record("r1", "k1", record_type=source.DIAGNOSIS, payload={"tuntematon": "x"}),
    ])
    with pytest.raises(delivery.DeliveryShapeError, match="zero useful rows"):
        delivery.import_delivery(source_dir, tmp_path / "out")


def test_import_refuses_a_delivery_whose_profile_file_cannot_bind_anything(tmp_path: Path):
    """A missing, empty or duplicated `profiles.csv` is the difference between "no records" and "no corpus".

    No dry-run test reaches this: the dry run reads profiles for binding but never writes the people index,
    so a duplicated key there cannot corrupt anything. At import it would silently merge two people.
    """
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "records.jsonl").write_text(json.dumps(_record("r1", "k1")) + "\n", encoding="utf-8")

    with pytest.raises(delivery.DeliveryShapeError, match="delivery needs profiles.csv"):
        delivery.import_delivery(source_dir, tmp_path / "no-profiles")

    (source_dir / "profiles.csv").write_text(
        "source_person_id,persona_id,label\n", encoding="utf-8")
    with pytest.raises(delivery.DeliveryShapeError, match="profiles.csv is empty"):
        delivery.import_delivery(source_dir, tmp_path / "empty-profiles")

    (source_dir / "profiles.csv").write_text(
        "source_person_id,persona_id,label\nk1,p1,Synthetic\nk1,p2,Synthetic\n", encoding="utf-8")
    with pytest.raises(delivery.DeliveryShapeError, match="duplicate source_person_id"):
        delivery.import_delivery(source_dir, tmp_path / "duplicate-profiles")


def test_a_malformed_records_line_is_named_by_its_line_number(tmp_path: Path):
    """A truncated delivery is the likeliest real failure, and `line 3` is what makes it findable."""
    source_dir = tmp_path / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "profiles.csv").write_text(
        "source_person_id,persona_id,label\nk1,p1,Synthetic\n", encoding="utf-8")
    (source_dir / "records.jsonl").write_text(
        json.dumps(_record("r1", "k1"), ensure_ascii=False) + "\n{ this line is truncated\n",
        encoding="utf-8")
    with pytest.raises(delivery.DeliveryShapeError, match="records.jsonl line 2 is not JSON"):
        delivery.import_delivery(source_dir, tmp_path / "out")


def test_an_encounter_whose_role_is_not_permitted_for_its_purpose_is_refused(tmp_path: Path):
    """The operator catalogue is the one place arrival day can quietly mis-scope a lens."""
    source_dir = tmp_path / "source"
    _write_source(source_dir, [("k1", "p1")], [_record("r1", "k1")],
                  encounters=[("e1", "k1", "fysioterapeutti", "mental_health_assessment")])
    with pytest.raises(delivery.DeliveryShapeError, match="not permitted for purpose"):
        delivery.import_delivery(source_dir, tmp_path / "out")

    # The control: the same role and purpose pairing that the policy does permit imports.
    _write_source(source_dir, [("k1", "p1")], [_record("r1", "k1")],
                  encounters=[("e1", "k1", "fysioterapeutti", "functioning_service_needs")])
    report = delivery.import_delivery(source_dir, tmp_path / "permitted")
    assert report.encounters == 1
