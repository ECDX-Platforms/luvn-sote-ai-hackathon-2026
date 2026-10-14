"""Phase 3 neuvola/guardian lens: permission, life-stage catalogue and the live duplicate fact.

Expectations for these lenses were written down before the implementation
before this file's first run. Everything here is offline: no model, no network.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from pathlib import Path

from app import source
from app.encounters import Encounter
from tests.fixtures.bound_encounters import ENCOUNTERS
from app.fixtures import load_personas
from app.pipeline import _duplicate_facts, _form_catalogue, build_picture_for_encounter
from app.policy import cut
from app.upm import FormMetadata, Persona, Record

TODAY = date(2026, 10, 2)
ENCOUNTER_ID = "bound-encounter-012-neuvola-terveydenhoitaja"
PERSONA_ID = "bound-persona-012-neuvola-lapsi"
SELF_RECORD = "bound-self-012"
PAIR = ("bound-mittaus-012", "bound-mittaus-013")
PERMITTED = {"bound-demo-012", "bound-mittaus-012", "bound-mittaus-013", "bound-note-020"}


def _picture():
    return asyncio.run(build_picture_for_encounter(
        ENCOUNTER_ID, personas=load_personas(), today=TODAY,
        decision_enabled=False, foundry_enabled=False, encounters=ENCOUNTERS
    ))


def _record(record_id: str, form_type: str, recorded_at: datetime, concept_key: str | None,
            topic: str = "kasvu") -> Record:
    return Record(
        record_id=record_id, source="synthetic-system", record_type="mittaus",
        authored_by_role="terveydenhoitaja", recorded_at=recorded_at, topic=topic,
        sensitivity_tag="none", consent_scope="treatment",
        payload={"pituus_cm": 121, "paino_kg": 22.4, "yksikkö": "cm/kg"},
        form=FormMetadata(form_type=form_type, version="2026.1", service="neuvola"),
        concept_key=concept_key,
    )


def test_lens_declares_the_illustrative_life_stage_and_covered_holder():
    encounter = ENCOUNTERS[ENCOUNTER_ID]
    assert (encounter.role, encounter.purpose) == ("terveydenhoitaja", "child_health_checkup")
    assert (encounter.life_stage, encounter.consent_holders) == ("child", ("guardian",))
    # The axis is illustrative and declared: no existing lens is silently re-labelled.
    assert {row.life_stage for row in ENCOUNTERS.values()} == {"adult", "child"}
    assert ENCOUNTERS["bound-encounter-003-mielenterveys-sairaanhoitaja"].consent_holders == ("self",)


def test_guardian_scope_permits_exactly_the_childs_guardian_records():
    personas = {persona.persona_id: persona for persona in load_personas()}
    encounter = ENCOUNTERS[ENCOUNTER_ID]
    scoped = cut(personas[PERSONA_ID], encounter.role, encounter.purpose, encounter.encounter_id,
                 encounter.consent_holders)
    assert set(scoped.ids) == PERMITTED
    assert SELF_RECORD not in scoped.ids
    # Without the holder scope the self record is otherwise admissible: topic, sensitivity and
    # consent_scope all pass, so the holder axis alone excludes it.
    unheld = cut(personas[PERSONA_ID], encounter.role, encounter.purpose, encounter.encounter_id)
    assert SELF_RECORD in unheld.ids
    self_record = next(row for row in personas[PERSONA_ID].records if row.record_id == SELF_RECORD)
    assert self_record.consent_holder == "self"
    assert self_record.topic in {"kouluterveydenhuolto"}


def test_self_consent_record_is_absent_as_one_neutral_absence():
    picture = _picture()
    visible = [row.record_id for row in [*picture.records.priority, *picture.records.accessible_other]]
    assert sorted(visible) == sorted(PERMITTED)
    assert [row.record_id for row in picture.citations] == []
    for group in picture.duplicate_facts:
        assert SELF_RECORD not in group.record_ids
    for entry in picture.form_catalogue:
        assert SELF_RECORD not in entry.record_ids
    # No identity, count, flag or "hidden" state for the withheld record crosses the seam at all.
    dumped = picture.model_dump_json()
    assert SELF_RECORD not in dumped
    assert "consent_holder" not in dumped, "the holder axis stays in the source envelope, not the wire"
    assert "bound-self" not in dumped


def test_duplicate_pair_is_grouped_and_both_rows_survive_with_provenance():
    picture = _picture()
    assert len(picture.duplicate_facts) == 1
    [group] = picture.duplicate_facts
    assert group.concept_key == "neuvola.kasvu.v1.2026-09-tarkastus"
    # Newest first: recency only, never form authority (the esitietolomake is not the winner).
    assert group.record_ids == ["bound-mittaus-013", "bound-mittaus-012"]
    views = {row.record_id: row for row in [*picture.records.priority, *picture.records.accessible_other]}
    assert set(views) >= set(PAIR), "both duplicate records stay as ordinary source rows"
    for record_id in PAIR:
        assert views[record_id].concept_key == group.concept_key
        assert views[record_id].payload == {"pituus_cm": 121, "paino_kg": 22.4, "yksikkö": "cm/kg"}
        assert views[record_id].form is not None
    esitieto, kasvukayra = views["bound-mittaus-012"].form, views["bound-mittaus-013"].form
    assert esitieto is not None and kasvukayra is not None
    assert esitieto.form_type == "neuvolan terveystarkastuksen esitietolomake"
    assert esitieto.filled_by_role == "huoltaja"
    assert kasvukayra.form_type == "neuvolan kasvukäyrä"
    # No winner, precedence or merge surface exists on the contract for a duplicate group.
    assert set(type(group).model_fields) == {"concept_key", "record_ids"}


def test_duplicate_group_ordering_follows_recency_not_identifier_or_form():
    early = _record("aaa-esitietolomake", "neuvolan terveystarkastuksen esitietolomake",
                    datetime(2026, 9, 5, tzinfo=timezone.utc), "neuvola.kasvu.v1.order")
    late = _record("zzz-kasvukayra", "neuvolan kasvukäyrä",
                   datetime(2026, 9, 18, tzinfo=timezone.utc), "neuvola.kasvu.v1.order")
    [newest_first] = _duplicate_facts([early, late])
    assert newest_first.record_ids == ["zzz-kasvukayra", "aaa-esitietolomake"]
    [swapped] = _duplicate_facts([late, early])
    assert swapped.record_ids == newest_first.record_ids, "input order must not decide the group order"


def test_life_stage_key_decides_the_catalogue_rows():
    personas = {persona.persona_id: persona for persona in load_personas()}
    encounter = ENCOUNTERS[ENCOUNTER_ID]
    scoped = cut(personas[PERSONA_ID], encounter.role, encounter.purpose, encounter.encounter_id,
                 encounter.consent_holders)
    records = list(scoped.records)
    rows = [(entry.form_type, entry.service, entry.required, entry.status, entry.record_ids,
             entry.suggested_next_step) for entry in _form_catalogue("child_health_checkup", "child",
                                                                    records, TODAY)]
    assert rows == [
        ("neuvolan terveystarkastuksen esitietolomake", "neuvola", True, "present",
         ["bound-mittaus-012"], None),
        ("neuvolan kasvukäyrä", "neuvola", True, "present", ["bound-mittaus-013"], None),
        ("neuvolan kuulotutkimus", "neuvola", False, "not_shown_in_briefing", [], None),
    ]
    # Same purpose, other life stage: the purpose alone would wrongly apply the child rows.
    assert _form_catalogue("child_health_checkup", "adult", records, TODAY) == []
    picture = _picture()
    assert [entry.form_type for entry in picture.form_catalogue] == [
        "neuvolan terveystarkastuksen esitietolomake", "neuvolan kasvukäyrä", "neuvolan kuulotutkimus"]


def test_impact_inputs_are_distinct_sources_and_form_tuples_never_minutes():
    picture = _picture()
    selected = [*picture.records.priority, *picture.records.accessible_other]
    assert len(selected) == 4
    labels = {row.source for row in selected if row.source}
    assert labels == {"potilastietojärjestelmä", "neuvolan esitietolomake", "neuvolan terveystarkastus",
                      "neuvolan kirjaus"}
    tuples = {(row.form.form_type, row.form.version, row.form.service)
              for row in selected if row.form is not None}
    assert tuples == {("neuvolan terveystarkastuksen esitietolomake", "2026.1", "neuvola"),
                      ("neuvolan kasvukäyrä", "2026.1", "neuvola")}
    # The count is derived from the selected view only, and the fixture declares no due date, so no
    # recurrence or due claim can be inferred from it.
    assert all(row.form.due_at is None for row in selected if row.form is not None)


def test_fixture_keeps_the_duplicate_as_the_only_unusual_feature():
    """Guards the 'keep it boring' constraint: five records, one fact pair, nothing else exotic."""
    persona = json.loads((Path(source.__file__).resolve().parents[2] / "data" / "experimental"
                          / "bound-personas" / "bound-persona-012-neuvola-lapsi.json").read_text(encoding="utf-8"))
    records = persona["records"]
    assert len(records) == 5
    assert [row["record_id"] for row in records if row.get("concept_key")] == ["bound-mittaus-012",
                                                                              "bound-mittaus-013"]
    assert {row["sensitivity_tag"] for row in records} == {"none"}
    assert {row["consent_scope"] for row in records} == {"treatment"}
    assert [row["consent_holder"] for row in records].count("self") == 1
    assert all("due_at" not in (row.get("form") or {}) for row in records)
    personas = load_personas()
    assert len(personas) == 6
    assert Persona.model_validate(persona).persona_id == PERSONA_ID
    assert Encounter.__dataclass_fields__["life_stage"].default == "adult"
