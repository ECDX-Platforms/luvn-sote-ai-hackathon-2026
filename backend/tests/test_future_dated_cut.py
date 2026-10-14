"""A record dated after the day the picture is built for is not a fact of that day (UI review 2026-10-14).

The cut lives in `policy.cut(today=...)`, the one seam everything downstream reads through, so a future-dated
diagnosis, medication or visit reaches neither the selection, Decision-1, the pre-fill nor the record views.
The count of held-back permitted records travels on `selection.future_dated_count`; no id does.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

from app import pipeline, source
from app.encounters import Encounter
from app.policy import cut_for_encounter
from app.upm import Persona, Record

TODAY = date(2026, 9, 30)
WALL_CLOCK_SAFE_FUTURE = datetime(2026, 10, 5, 9, tzinfo=timezone.utc)  # after TODAY, before the real today


def _record(record_id: str, record_type: str, payload: dict, recorded_at: datetime, *, topic: str = "hoito",
            scope: str = "treatment", käyntiaika: datetime | None = None) -> Record:
    return Record(record_id=record_id, source="synthetic", record_type=record_type,
                  authored_by_role="sairaanhoitaja", recorded_at=recorded_at, topic=topic,
                  sensitivity_tag="none", consent_scope=scope, payload=payload, käyntiaika=käyntiaika)


def _persona() -> Persona:
    past = datetime(2026, 9, 15, 8, tzinfo=timezone.utc)
    on_the_day = datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc)
    return Persona(persona_id="synthetic-future", label="Synthetic", records=[
        _record("basics", "perustieto", {"syntymävuosi": 1950}, past, topic="demografia"),
        # Dated after TODAY: must not appear anywhere.
        _record("dx-future", "diagnoosi", {"nimi": "Tuleva diagnoosi", "icd10": "I10", "tila": "aktiivinen"},
                WALL_CLOCK_SAFE_FUTURE),
        _record("med-future", "lääkitys", {"lääke": "Tuleva lääke", "annos": "1 x 1", "tila": "aktiivinen"},
                WALL_CLOCK_SAFE_FUTURE),
        _record("visit-future", "asiointitapahtuma", {"kanava": "käynti", "suunta": "kotihoito", "aihe": "Kotikäynti"},
                WALL_CLOCK_SAFE_FUTURE, topic="kotihoidon palvelu"),
        # Recorded before TODAY, but the source visit time is after it: the effective date is the visit.
        _record("note-future-visit", "vapaateksti", {"otsikko": "Käynti", "teksti": "Toimintakyky arvioitu."}, past,
                topic="toimintakyky", käyntiaika=WALL_CLOCK_SAFE_FUTURE),
        # Dated on TODAY and before it: present.
        _record("dx-today", "diagnoosi", {"nimi": "Tämän päivän diagnoosi", "icd10": "E11", "tila": "aktiivinen"},
                on_the_day),
        _record("med-today", "lääkitys", {"lääke": "Metformiini", "annos": "500 mg", "tila": "aktiivinen"}, on_the_day),
        _record("visit-past", "asiointitapahtuma", {"kanava": "käynti", "suunta": "kotihoito", "aihe": "Kotikäynti"},
                past, topic="kotihoidon palvelu"),
    ])


ENCOUNTER = Encounter("synthetic-future-encounter", "synthetic-future", "sairaanhoitaja", "functioning_service_needs")
FUTURE_IDS = {"dx-future", "med-future", "visit-future", "note-future-visit"}
PRESENT_IDS = {"basics", "dx-today", "med-today", "visit-past"}


def test_effective_date_is_the_visit_time_else_the_entry_time():
    persona = _persona()
    by_id = {record.record_id: record for record in persona.records}
    assert source.effective_date(by_id["note-future-visit"]) == WALL_CLOCK_SAFE_FUTURE.date()
    assert source.effective_date(by_id["dx-today"]) == TODAY
    assert source.is_after(by_id["note-future-visit"], TODAY)
    assert not source.is_after(by_id["dx-today"], TODAY)


def test_the_policy_cut_holds_back_records_dated_after_today_and_counts_them():
    scoped = cut_for_encounter(_persona(), ENCOUNTER, TODAY)
    assert set(scoped.ids) == PRESENT_IDS
    assert scoped.future_dated_count == len(FUTURE_IDS)
    # Without a day, the cut is the permission cut alone (the compare and feedback paths that pass none).
    assert set(cut_for_encounter(_persona(), ENCOUNTER).ids) == PRESENT_IDS | FUTURE_IDS
    # The day after the future records' date, they are facts.
    assert set(cut_for_encounter(_persona(), ENCOUNTER, WALL_CLOCK_SAFE_FUTURE.date()).ids) == PRESENT_IDS | FUTURE_IDS


def test_a_future_dated_record_reaches_nothing_in_the_picture_and_the_cut_uses_the_pipelines_today(monkeypatch):
    assert WALL_CLOCK_SAFE_FUTURE.date() < date.today(), "the future records must be in the wall clock's past"
    seen_by_decision: list[set[str]] = []

    async def classify(scoped, baseline_ids, today):
        seen_by_decision.append(set(scoped.ids))
        # Decision-1 calling a future record relevant must not bring it back: it never sees one.
        return [{"record_id": record_id, "status": "complete", "relevance": {"choice": "relevant", "confidence": 0.9,
                 "probabilities": {"relevant": 0.9, "irrelevant": 0.1}}} for record_id in scoped.ids]

    monkeypatch.setattr(pipeline, "classify_decision_notes", classify)
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: False)
    picture = asyncio.run(pipeline._build_resolved_picture(ENCOUNTER, _persona(), TODAY))

    rendered = {view.record_id for view in [*picture.records.priority, *picture.records.accessible_other]}
    assert rendered & FUTURE_IDS == set()
    assert {"dx-today", "med-today", "visit-past"} <= rendered
    assert seen_by_decision and not any(ids & FUTURE_IDS for ids in seen_by_decision)
    selection = picture.selection
    assert selection is not None
    selected = set(selection.baseline_record_ids) | set(selection.additional_record_ids) | set(selection.emphasized_record_ids)
    assert selected & FUTURE_IDS == set()
    assert {note.record_id for note in selection.decision_notes} & FUTURE_IDS == set()
    assert selection.future_dated_count == len(FUTURE_IDS)
    cited_by_prefill = {record_id for section in picture.esitiedot for item in section.items
                        for record_id in item.source_record_ids}
    assert cited_by_prefill & FUTURE_IDS == set()
    assert "visit-past" in cited_by_prefill
    homecare = [item.text for section in picture.esitiedot for item in section.items if "kotihoidon käyntiä" in item.text]
    assert homecare == ["1 kotihoidon käyntiä, viimeisin 15.9.2026"]
    # Never named: the picture carries the count, not the ids.
    assert not any(record_id in picture.model_dump_json() for record_id in FUTURE_IDS)


def test_the_day_after_the_wall_clock_shows_the_record_when_the_pipeline_is_built_for_that_day(monkeypatch):
    tomorrow = date.today() + timedelta(days=1)
    persona = Persona(persona_id="synthetic-future", label="Synthetic", records=[
        _record("basics", "perustieto", {"syntymävuosi": 1950}, datetime(2026, 9, 15, tzinfo=timezone.utc),
                topic="demografia"),
        _record("dx-tomorrow", "diagnoosi", {"nimi": "Huomisen diagnoosi", "icd10": "I10", "tila": "aktiivinen"},
                datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc)),
    ])
    assert "dx-tomorrow" not in cut_for_encounter(persona, ENCOUNTER, date.today()).ids
    assert "dx-tomorrow" in cut_for_encounter(persona, ENCOUNTER, tomorrow).ids
