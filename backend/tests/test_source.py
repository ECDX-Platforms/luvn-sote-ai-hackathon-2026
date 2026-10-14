"""Source adapter contract: the only module that knows source field names.

If a real Veil.AI export renames a payload key, this file (and `source.py`) are
what change. These checks pin the current mapping so a silent rename fails here.
"""

from datetime import date, datetime, timezone
from typing import cast

import pytest

from app import source
from app.fixtures import load_personas
from app.upm import Record

RECORDS = [r for p in load_personas() for r in p.records]


def _vitals() -> Record:
    return Record(
        record_id="synthetic-vitals",
        source="synthetic",
        record_type="mittaus",
        authored_by_role="sairaanhoitaja",
        recorded_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
        topic="verenpaine",  # a declared measurement topic; `Record` refuses an undeclared one (D6)
        sensitivity_tag="none",
        consent_scope="treatment",
        payload={"lämpö_c": 38.5, "spo2": 97},
    )


def test_medication_accessors_read_the_source_fields():
    meds = [r for r in RECORDS if source.is_medication(r)]
    assert meds
    assert any(source.is_active_medication(r) for r in meds)
    assert all(source.drug_name(r) for r in meds if source.is_active_medication(r))


def test_basic_info_and_vitals_accessors():
    records = [*RECORDS, _vitals()]
    assert any(source.birth_year(r) is not None for r in records if r.record_type == source.BASIC_INFO)
    assert any(source.temperature(r) is not None for r in records)


def test_free_text_notes_have_text():
    notes = [r for r in RECORDS if r.record_type == source.FREE_TEXT]
    assert notes
    assert all(source.note_text(r).strip() for r in notes)


def test_kayntiaika_anchors_temporal_status_and_absence_falls_back():
    today = date(2026, 10, 2)
    current_entry_old_visit = _vitals().model_copy(update={
        "record_type": source.FREE_TEXT,
        "recorded_at": datetime(2026, 9, 30, tzinfo=timezone.utc),
        "käyntiaika": datetime(2025, 9, 30, tzinfo=timezone.utc),
    })
    old_entry_current_visit = current_entry_old_visit.model_copy(update={
        "recorded_at": datetime(2025, 9, 30, tzinfo=timezone.utc),
        "käyntiaika": datetime(2026, 9, 30, tzinfo=timezone.utc),
    })
    no_visit_time = current_entry_old_visit.model_copy(update={"käyntiaika": None})
    assert source.temporal_status(current_entry_old_visit, today) == "history"
    assert source.temporal_status(old_entry_current_visit, today) == "current"
    assert source.temporal_status(no_visit_time, today) == "current"


def test_missing_field_is_none_not_a_keyerror():
    class Fake:
        payload = {}

    assert source.temperature(cast(Record, Fake())) is None
    assert source.birth_year(cast(Record, Fake())) is None
    assert source.drug_name(cast(Record, Fake())) is None


def test_document_kinds_are_constants_with_no_record_instance():
    """Lomakepalvelu kinds are vocabulary only: no fixture record, no count, no rendering."""
    assert source.DOCUMENT_KINDS == ("todistus", "lausunto", "ilmoitus")
    assert {record.record_type for record in RECORDS}.isdisjoint(source.DOCUMENT_KINDS)


def test_missed_doses_only_counts_medications():
    """The medication gate is part of the mapping; a stray key on another
    record type must not move the risk level."""

    class Stray:
        record_type = source.FREE_TEXT
        payload = {"puuttuneet_annokset_30d": 9}

    assert source.missed_doses(cast(Record, Stray())) is None
    assert any(source.missed_doses(r) is not None for r in RECORDS if source.is_medication(r))


def _basics(record_id="basics", payload=None) -> Record:
    return _vitals().model_copy(update={
        "record_id": record_id,
        "record_type": source.BASIC_INFO,
        "topic": "demografia",
        "payload": payload if payload is not None else {"nimi": "Aino Esimerkki", "näyttötunniste": "DEMO-007"},
    })


def test_perustieto_accessor_returns_none_or_the_single_match():
    assert source.perustieto_for([]) is None
    assert source.perustieto_for([_vitals()]) is None
    row = _basics()
    assert source.perustieto_for([row]) is row


def test_perustieto_accessor_rejects_ambiguous_basics():
    with pytest.raises(ValueError, match="ambiguous basics"):
        source.perustieto_for([_basics("b1"), _basics("b2")])
    with pytest.raises(ValueError, match="^ambiguous basics records$"):
        source.identity_header([_basics("b1"), _basics("b2")])


def test_identity_header_joins_display_fields_and_falls_back_neutral():
    assert source.identity_header([]) == source.NEUTRAL_IDENTITY
    assert source.identity_header([_basics()]) == "Aino Esimerkki · DEMO-007"
    assert source.identity_header([_basics(payload={"nimi": "Aino"})]) == "Aino"
    assert source.identity_header([_basics(payload={"syntymävuosi": 1990})]) == source.NEUTRAL_IDENTITY
    assert source.identity_header([_vitals()]) == source.NEUTRAL_IDENTITY
