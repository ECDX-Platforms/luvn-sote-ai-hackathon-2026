"""Purpose vocabulary: one owner, complete, and mirrored by display labels."""

from datetime import date, datetime, timezone

from app import purpose, source
from app.policy import cut
from app.upm import Persona, Record
from app.main import PURPOSE_LABELS


def test_purpose_value_set_matches_the_literal_and_the_display_labels():
    assert set(purpose.palvelukontekstit()) == set(purpose.Purpose.__args__)
    assert set(PURPOSE_LABELS) == set(purpose.palvelukontekstit())


def test_every_palvelukonteksti_row_is_complete():
    for name in purpose.palvelukontekstit():
        row = purpose.palvelukonteksti(name)
        assert row.roles and row.topics and row.baseline_terms and row.description


def test_relevance_criteria_cover_the_answer_vocabulary():
    assert set(purpose.RELEVANCE_ANSWERS) == set(purpose.RELEVANCE_CRITERIA)


def test_service_events_and_decisions_can_reach_purpose_scoped_reads():
    records = [Record(
        record_id=record_type, source="synthetic", record_type=record_type,
        authored_by_role="sairaanhoitaja", recorded_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        topic=topic, sensitivity_tag="none", consent_scope="treatment", payload={},
    ) for record_type, topic in ((source.APPOINTMENT, "mielenterveys"),
                                 (source.DECISION, "palvelutarve"))]
    persona_row = Persona(persona_id="synthetic", label="synthetic", records=records)
    mental = cut(persona_row, "sairaanhoitaja", "mental_health_assessment")
    services = cut(persona_row, "sairaanhoitaja", "functioning_service_needs")
    assert source.APPOINTMENT in mental.ids
    assert source.DECISION in services.ids
    assert source.temporal_status(records[0], date(2026, 9, 30)) == "current"
    assert source.temporal_status(records[1], date(2026, 9, 30)) == "current"
