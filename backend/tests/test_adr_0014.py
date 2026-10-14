import asyncio
from pathlib import Path

from app import pipeline
from app.fixtures import load_personas
from app.picture import RecordView
from app.pipeline import _narration_eligible
from app.policy import cut
from app.upm import Persona, Record
from tests.fixtures.bound_encounters import ENCOUNTERS

ROOT = Path(__file__).resolve().parents[1]


def test_fixture_drafts_are_the_only_records_excluded_from_narration():
    drafts = []
    for persona in load_personas():
        eligible = [record for record in persona.records if record.state != "draft"]
        assert _narration_eligible(persona.records) == eligible
        drafts.extend(record.record_id for record in persona.records if record.state == "draft")
    assert set(drafts) == {"bound-note-002-draft", "bound-note-003-draft", "bound-note-004-draft"}


def test_product_draft_stays_visible_but_is_removed_before_narration(monkeypatch):
    captured = {}
    narrate = pipeline.narrate

    async def capture_scope(permitted, selected_record_ids, **kwargs):
        captured["ids"] = list(selected_record_ids)
        return await narrate(permitted, selected_record_ids, **kwargs)

    monkeypatch.setattr(pipeline, "narrate", capture_scope)
    picture = asyncio.run(pipeline.build_picture_for_encounter(
        "bound-encounter-002-laakitys-sairaanhoitaja", decision_enabled=False, foundry_enabled=False, encounters=ENCOUNTERS
    ))
    selected_ids = {record.record_id for record in picture.records.priority + picture.records.accessible_other}
    assert "bound-note-002-draft" in selected_ids
    assert "bound-note-002-draft" not in captured["ids"]


def test_explicit_draft_is_visible_provenance_but_not_narration_input():
    draft = Record.model_validate_json((ROOT / "tests/fixtures/draft-record.json").read_text(encoding="utf-8"))
    baseline = Record.model_validate({**draft.model_dump(), "record_id": "legacy-note", "state": None,
                                      "responsible_approver": None})
    rows = [baseline, draft]
    assert _narration_eligible(rows) == [baseline]
    persona = Persona(persona_id="draft-test", label="Synthetic", records=[draft])
    scoped = cut(persona, "sairaanhoitaja", "mental_health_assessment")
    assert scoped.records == [draft], "state is provenance, not a selection input"
    assert _narration_eligible(scoped.records) == []
    rendered = RecordView.from_record(draft).model_dump(mode="json")
    assert rendered["state"] == "draft"
    assert rendered["authored_by_role"] == "sairaanhoitaja"
    assert rendered["responsible_approver"] == "laakari"
