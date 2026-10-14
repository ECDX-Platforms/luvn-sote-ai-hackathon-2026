"""The pre-filled esitiedot (`app/prefill.py`): the deterministic structure and the verified official forms."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app import assess, decision_model, knowledge_forms, llm, prefill, source
from app.picture import Picture
from app.policy import Scoped
from app.upm import Persona, Record
from tests.fixtures.bound_encounters import ENCOUNTERS, EXAMPLE_LABELS

TODAY = datetime(2026, 10, 13, tzinfo=timezone.utc).date()
NOTE_TEXT = ("## TULOSYY JA ESITIEDOT\nPotilaan kertoman mukaan olkapään kipu.\n\n## TERVEYTEEN VAIKUTTAVAT TEKIJÄT\n"
             "Potilaan mukaan hän tupakoi noin 5 savuketta päivässä eikä käytä alkoholia.\n\n"
             "## RISKITIEDOT\nLääkeaineallergioista ei ole tietoa.\n\n## TOIMINTAKYKY\nYläraajan käyttö on vaikeutunut.")
SECTIONS = {"TULOSYY JA ESITIEDOT": "Potilaan kertoman mukaan olkapään kipu.",
            "TERVEYTEEN VAIKUTTAVAT TEKIJÄT": "Potilaan mukaan hän tupakoi noin 5 savuketta päivässä eikä käytä alkoholia.",
            "RISKITIEDOT": "Lääkeaineallergioista ei ole tietoa.", "TOIMINTAKYKY": "Yläraajan käyttö on vaikeutunut."}


def _record(record_id, record_type, payload, *, topic="hoito", when="2026-09-20T10:00:00+00:00", scope="treatment",
            heading=None):
    return Record(record_id=record_id, source="delivery", record_type=record_type, authored_by_role="laakari",
                  recorded_at=datetime.fromisoformat(when), topic=topic, sensitivity_tag="none", consent_scope=scope,
                  payload=payload, source_person_id="P-1", heading=heading)


def _scoped(records, purpose="medication_follow_up", role="sairaanhoitaja"):
    return Scoped(persona=Persona(persona_id="p-1", label="P", records=records, source_person_id="P-1"),
                  role=role, purpose=purpose, encounter_id="enc-1")


CLINICAL = [
    _record("note-1", "vapaateksti", {"otsikko": "Käynti", "teksti": NOTE_TEXT, "osiot": SECTIONS}),
    _record("med-1", "lääkitys", {"lääke": "amlodipiini", "annos": "säännöllinen", "tila": "aktiivinen"}, topic="lääkitys"),
    _record("med-2", "lääkitys", {"lääke": "parasetamoli", "annos": "tarvittaessa", "tila": "päättynyt"},
            topic="lääkitys", when="2025-01-01T10:00:00+00:00"),
    _record("dx-1", "diagnoosi", {"nimi": "Kohonnut verenpaine", "icd10": "I10", "tila": "aktiivinen"}),
    _record("dx-2", "diagnoosi", {"nimi": "Masennusjakso, määrittämätön", "icd10": "F32.9", "tila": "ei_aktiivinen"}),
    _record("meas-1", "mittaus", {"verenpaine": "158/92", "lähde_record_id": "note-1"}),
    _record("meas-2", "mittaus", {"lämpö_c": 37.2, "lähde_record_id": "note-1"}),
    _record("risk-1", "vapaateksti", {"otsikko": "Riskitiedot", "teksti": "Lääkeaineallergioista ei ole tietoa.",
                                      "lähde_record_id": "note-1"}),
]


def test_level_one_fills_the_countys_own_headings_from_the_permitted_records_and_marks_the_rest_open():
    sections = prefill.esitieto_sections(_scoped(CLINICAL), "medication_follow_up", TODAY)
    by_key = {s.key: s for s in sections}
    assert [s.key for s in sections] == [k for k, _h, _s in prefill.CLINICAL_HEADINGS]
    assert by_key["tulosyy"].status == "answered"
    assert by_key["tulosyy"].items[0].text == SECTIONS["TULOSYY JA ESITIEDOT"]
    assert by_key["tulosyy"].items[0].source_record_ids == ["note-1"]
    assert by_key["aikaisempi_hoito"].status == "answered", "no note section, but the diagnoses answer it"
    assert [i.text for i in by_key["aikaisempi_hoito"].items] == [
        "Kohonnut verenpaine (I10) · pitkäaikainen", "Masennusjakso, määrittämätön (F32.9)"]
    assert [i.kind for i in by_key["aikaisempi_hoito"].items] == ["diagnosis", "diagnosis"]
    assert [i.text for i in by_key["laakehoito"].items] == ["amlodipiini · säännöllinen · käytössä",
                                                             "parasetamoli · tarvittaessa · päättynyt"]
    assert by_key["laakehoito"].items[1].temporal_status == "history"
    assert [i.text for i in by_key["mittaukset"].items] == ["Verenpaine 158/92 mmHg", "Lämpö 37,2 °C"]
    # The risk section is read once: the note's own section and the derived record carry the same words.
    assert [i.text for i in by_key["riskitiedot"].items] == ["Lääkeaineallergioista ei ole tietoa."]
    assert by_key["riskitiedot"].items[0].source_record_ids == ["note-1"]
    assert by_key["toimintakyky"].status == "answered"


def test_level_one_without_a_note_answers_only_from_structured_records():
    records = [r for r in CLINICAL if r.record_type in {"lääkitys", "diagnoosi"}]
    by_key = {s.key: s for s in prefill.esitieto_sections(_scoped(records), "medication_follow_up", TODAY)}
    assert by_key["tulosyy"].status == "unknown" and by_key["tulosyy"].items == []
    assert by_key["riskitiedot"].status == "unknown"
    assert by_key["laakehoito"].status == "answered" and by_key["aikaisempi_hoito"].status == "answered"


def test_level_one_for_the_service_needs_lens_uses_the_social_structure():
    records = [
        _record("needs-1", "vapaateksti", {"otsikko": "Palvelutarpeen arviointi", "teksti": "Kotona selviytymisen tuki"},
                topic="palvelutarve", scope="social_care"),
        _record("dec-1", "päätös", {"päätös": "Säännöllinen kotihoito", "voimassa": "toistaiseksi"},
                topic="palvelutarve", scope="social_care"),
        _record("func-1", "vapaateksti", {"otsikko": "Toimintakyky", "teksti": "Yläraajan käyttö on vaikeutunut."},
                topic="toimintakyky", scope="social_care", heading="Toimintakyky"),
        _record("home-1", "asiointitapahtuma", {"kanava": "SIM-Kotikäynti", "aihe": "Kotihoito"},
                topic="kotihoidon palvelu", scope="social_care", when="2026-10-01T09:00:00+00:00"),
        _record("home-2", "asiointitapahtuma", {"kanava": "SIM-Kotikäynti", "aihe": "Kotihoito"},
                topic="kotihoidon palvelu", scope="social_care", when="2026-10-08T09:00:00+00:00"),
    ]
    sections = prefill.esitieto_sections(_scoped(records, "functioning_service_needs", "sosiaalityontekija"),
                                         "functioning_service_needs", TODAY)
    by_key = {s.key: s for s in sections}
    assert [s.key for s in sections] == ["palvelutarve", "toimintakyky", "paatokset", "kotihoito"]
    assert by_key["palvelutarve"].items[0].text == "Kotona selviytymisen tuki"
    assert by_key["toimintakyky"].items[0].source_record_ids == ["func-1"]
    assert by_key["paatokset"].items[0].text == "Säännöllinen kotihoito"
    assert by_key["kotihoito"].items[0].text == "2 kotihoidon käyntiä, viimeisin 8.10.2026"
    assert sorted(by_key["kotihoito"].items[0].source_record_ids) == ["home-1", "home-2"]


def test_the_forms_declared_for_each_purpose_resolve_against_the_snapshot_and_say_what_they_are():
    names = [(f.name_fi, role) for f, role, _n in prefill.forms_for_purpose("medication_follow_up", [])]
    assert names == [("Alkoholin käytön riskit - kysely (AUDIT)", "related"), ("Nikotiiniriippuvuustesti (Fagerström)", "related")]
    with_mood = prefill.forms_for_purpose("medication_follow_up", CLINICAL)
    assert [f.name_fi for f, _r, _n in with_mood][-1] == "Masennuskysely (PHQ-9)", "an F32 diagnosis adds the PHQ-9"
    assert all(note for _f, role, note in with_mood if role == "related"), "a related form says why it is here"
    official = prefill.forms_for_purpose("mental_health_assessment", [])
    assert [(f.name_fi.split(" (")[-1].rstrip(")"), role) for f, role, _n in official][:2] == [("PHQ-9", "official"), ("GAD-7", "official")]
    assert prefill.forms_for_purpose(None, []) == [] and prefill.forms_for_purpose("unknown", []) == []


def test_without_foundry_every_form_is_listed_open_and_level_one_still_answers(monkeypatch):
    monkeypatch.setattr(llm, "foundry_configured", lambda: False)
    sections, forms = asyncio.run(prefill.build(_scoped(CLINICAL), "medication_follow_up", TODAY))
    assert any(s.status == "answered" for s in sections)
    assert [f.generation_status for f in forms] == ["unavailable"] * 3
    assert all(item.status == "unknown" for f in forms for item in f.items)
    assert forms[0].total_questions == len([q for q in knowledge_forms.find_form("AUDIT").questions if q.type != "heading"])


def _fagerstrom():
    form = knowledge_forms.find_form("Fagerström")
    q1, q2 = form.scored_questions
    return form, q1, q2


def test_level_two_shows_only_verified_answers_scores_a_fully_answered_form_and_drops_out_of_scope_citations(monkeypatch):
    form, q1, q2 = _fagerstrom()
    sent = {}

    async def complete(schema, instructions, user, timeout_s):
        sent["user"] = user
        assert schema is prefill.PrefillAnswerSet and "Älä päättele" in instructions
        items = [
            prefill.PrefillItem(question_id=q1.id, status="answered", answer_text=None, answer_id=q1.answers[1].id,
                                source_record_ids=["note-1"]),
            prefill.PrefillItem(question_id=q2.id, status="answered", answer_text=None, answer_id=q2.answers[0].id,
                                source_record_ids=["note-1"]),
            # Not a question of this form: ignored, never rendered.
            prefill.PrefillItem(question_id="not-a-question", status="answered", answer_text="x", answer_id=None,
                                source_record_ids=["note-1"]),
        ]
        return llm.FoundryResult(output=prefill.PrefillAnswerSet(items=items), model="stub-generator")

    async def verify(scoped, candidate, today, timeout_s):
        verdicts = []
        for sentence in candidate.sentences:
            assert set(sentence.source_ids) <= set(scoped.ids)
            verdicts.append({"sentence_id": sentence.sentence_id, "verdict": "supported",
                             "supporting_record_ids": list(sentence.source_ids), "call_status": "complete",
                             "answered_by": "stub-verifier", "input_tokens": 1, "output_tokens": 1})
        return {"verdicts": verdicts, "model": "stub-verifier", "status": "complete", "input_tokens": 1, "output_tokens": 1}

    monkeypatch.setattr(llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(llm, "_complete_bounded", complete)
    monkeypatch.setattr(decision_model, "verify_narration", verify)
    monkeypatch.setattr(prefill, "FORMS_BY_PURPOSE", {"medication_follow_up": (("Fagerström", "related", "x"),)})
    monkeypatch.setattr(prefill, "MOOD_CODES", ())
    _sections, forms = asyncio.run(prefill.build(_scoped(CLINICAL), "medication_follow_up", TODAY))
    [result] = forms
    assert result.generation_status == "generated" and result.verification_status == "complete"
    assert result.generation_model == "stub-generator"
    by_id = {item.question_id: item for item in result.items}
    assert by_id[q1.id].status == "answered" and by_id[q1.id].verified and by_id[q1.id].answer_text == q1.answers[1].text_fi
    assert by_id[q2.id].answer_id == q2.answers[0].id and by_id[q2.id].source_record_ids == ["note-1"]
    assert "not-a-question" not in by_id and len(result.items) == 2
    assert (result.proposed, result.answered, result.verified) == (2, 2, 2)
    assert result.score is not None and result.score.total == q1.answers[1].points + q2.answers[0].points
    assert result.score.max == form.score_calculations[0].max and "sertifioitu" in result.score.basis
    assert result.source_file and result.source_sha256 and result.relation_note == "x"
    # The payload the generator saw: permitted records only, projected for the model, no identity key.
    source.assert_model_safe(sent["user"])
    assert "note-1" in sent["user"] and '"osiot"' in sent["user"]


def test_a_citation_outside_the_permitted_set_drops_the_item_before_any_verifier_call(monkeypatch):
    form = knowledge_forms.find_form("AUDIT")
    q1 = form.scored_questions[0]
    verifier_calls = []

    async def complete(schema, instructions, user, timeout_s):
        items = [prefill.PrefillItem(question_id=q1.id, status="answered", answer_text=None, answer_id=q1.answers[0].id,
                                     source_record_ids=["note-1", "someone-elses-record"])]
        return llm.FoundryResult(output=prefill.PrefillAnswerSet(items=items), model="stub")

    async def verify(scoped, candidate, today, timeout_s):
        verifier_calls.append(candidate)
        return {"verdicts": [], "model": "v", "status": "complete", "input_tokens": 0, "output_tokens": 0}

    monkeypatch.setattr(llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(llm, "_complete_bounded", complete)
    monkeypatch.setattr(decision_model, "verify_narration", verify)
    monkeypatch.setattr(prefill, "FORMS_BY_PURPOSE", {"medication_follow_up": (("AUDIT", "related", None),)})
    monkeypatch.setattr(prefill, "MOOD_CODES", ())
    _s, [result] = asyncio.run(prefill.build(_scoped(CLINICAL), "medication_follow_up", TODAY))
    assert verifier_calls == [], "nothing reached the verifier: the only proposal cited outside the permitted set"
    assert result.proposed == 0 and result.answered == 0 and all(item.status == "unknown" for item in result.items)
    assert result.verification_status == "not_run"


def test_an_unverified_answer_is_shown_as_open_and_a_historical_source_is_flagged(monkeypatch):
    form, q1, q2 = _fagerstrom()

    async def complete(schema, instructions, user, timeout_s):
        items = [prefill.PrefillItem(question_id=q1.id, status="answered", answer_text=None, answer_id=q1.answers[2].id,
                                     source_record_ids=["med-2"]),
                 prefill.PrefillItem(question_id=q2.id, status="answered", answer_text="kyllä", answer_id=None,
                                     source_record_ids=["note-1"])]
        return llm.FoundryResult(output=prefill.PrefillAnswerSet(items=items), model="stub")

    async def verify(scoped, candidate, today, timeout_s):
        verdicts = [{"sentence_id": s.sentence_id, "verdict": "supported" if s.sentence_id == "s1" else "unsupported",
                     "supporting_record_ids": list(s.source_ids) if s.sentence_id == "s1" else [],
                     "call_status": "complete", "answered_by": "v", "input_tokens": 0, "output_tokens": 0}
                    for s in candidate.sentences]
        return {"verdicts": verdicts, "model": "v", "status": "complete", "input_tokens": 0, "output_tokens": 0}

    monkeypatch.setattr(llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(llm, "_complete_bounded", complete)
    monkeypatch.setattr(decision_model, "verify_narration", verify)
    monkeypatch.setattr(prefill, "FORMS_BY_PURPOSE", {"medication_follow_up": (("Fagerström", "related", None),)})
    monkeypatch.setattr(prefill, "MOOD_CODES", ())
    _s, [result] = asyncio.run(prefill.build(_scoped(CLINICAL), "medication_follow_up", TODAY))
    by_id = {item.question_id: item for item in result.items}
    assert by_id[q1.id].verified and by_id[q1.id].historical, "cited only an ended medication: shown with Tarkista"
    assert by_id[q2.id].status == "unknown" and by_id[q2.id].answer_text is None, "unsupported is not shown"
    assert (result.proposed, result.answered, result.verified) == (2, 1, 1) and result.score is None, "one scored answer short: no total"


def test_a_failed_or_slow_generator_yields_an_open_form_not_a_failed_briefing(monkeypatch):
    async def boom(schema, instructions, user, timeout_s):
        raise RuntimeError("provider down")

    monkeypatch.setattr(llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(llm, "_complete_bounded", boom)
    monkeypatch.setattr(prefill, "FORMS_BY_PURPOSE", {"medication_follow_up": (("AUDIT", "related", None),)})
    monkeypatch.setattr(prefill, "MOOD_CODES", ())
    _s, [result] = asyncio.run(prefill.build(_scoped(CLINICAL), "medication_follow_up", TODAY))
    assert result.generation_status == "failed" and result.answered == 0
    import time

    _s, [late] = asyncio.run(prefill.build(_scoped(CLINICAL), "medication_follow_up", TODAY,
                                           deadline=time.monotonic() + 1.0))
    assert late.generation_status == "timed_out"


def test_the_pipeline_carries_both_levels_on_the_picture(monkeypatch):
    from app.fixtures import load_personas
    from app.pipeline import build_picture_for_encounter
    from app.store import Store

    monkeypatch.setattr(llm, "foundry_configured", lambda: False)
    store = Store(personas=load_personas(), path=None, encounters=ENCOUNTERS, example_labels=EXAMPLE_LABELS)
    picture = asyncio.run(build_picture_for_encounter("bound-encounter-002-laakitys-sairaanhoitaja",
                                                      personas=store.persona_index(),
                                                      encounters=store.encounter_index(), foundry_enabled=False))
    assert isinstance(picture, Picture)
    assert [s.key for s in picture.esitiedot] == [k for k, _h, _s in prefill.CLINICAL_HEADINGS]
    assert any(s.status == "answered" for s in picture.esitiedot)
    assert [f.generation_status for f in picture.prefilled_forms] and all(
        f.generation_status == "unavailable" for f in picture.prefilled_forms)
    assert any(stage.stage == "prefill.total" for stage in picture.pipeline_trace)


def test_the_calculator_is_the_forms_own_arithmetic_and_never_partial():
    form = knowledge_forms.find_form("PHQ-9")
    full = {q.id: q.answers[3].id for q in form.scored_questions}
    result = assess.score_from_form(form, full)
    # Nine symptom items at 3 points plus the form's tenth, unweighted question: the form's own maximum.
    assert result["total"] == result["max"] == 27 and len(form.scored_questions) == 10
    partial = dict(list(full.items())[:-1])
    assert assess.score_from_form(form, partial) is None
    unscored = knowledge_forms.find_form("Äitiysneuvola")
    assert not unscored.is_scored and assess.score_from_form(unscored, {}) is None


def test_an_oversized_model_answer_degrades_to_a_bounded_answer_never_an_exception(monkeypatch):
    """Review finding 1: `PrefillItem.answer_text` is unbounded and the verifier sentence is not; the briefing must
    not fail on a model that quotes a long passage."""
    form, q1, q2 = _fagerstrom()
    seen = {}

    async def complete(schema, instructions, user, timeout_s):
        items = [prefill.PrefillItem(question_id=q1.id, status="answered", answer_text="x" * 1200, answer_id=None,
                                     source_record_ids=["note-1"])]
        return llm.FoundryResult(output=prefill.PrefillAnswerSet(items=items), model="stub")

    async def verify(scoped, candidate, today, timeout_s):
        seen["texts"] = [s.text for s in candidate.sentences]
        return {"verdicts": [{"sentence_id": s.sentence_id, "verdict": "supported",
                              "supporting_record_ids": list(s.source_ids), "call_status": "complete",
                              "answered_by": "v", "input_tokens": 0, "output_tokens": 0} for s in candidate.sentences],
                "model": "v", "status": "complete", "input_tokens": 0, "output_tokens": 0}

    monkeypatch.setattr(llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(llm, "_complete_bounded", complete)
    monkeypatch.setattr(decision_model, "verify_narration", verify)
    monkeypatch.setattr(prefill, "FORMS_BY_PURPOSE", {"medication_follow_up": (("Fagerström", "related", None),)})
    monkeypatch.setattr(prefill, "MOOD_CODES", ())
    _s, [result] = asyncio.run(prefill.build(_scoped(CLINICAL), "medication_follow_up", TODAY))
    assert result.generation_status == "generated"
    item = next(i for i in result.items if i.question_id == q1.id)
    assert item.status == "answered" and len(item.answer_text) == prefill.MAX_ANSWER_CHARS
    assert all(len(text) <= llm.MAX_SENTENCE_CHARS for text in seen["texts"]), "the verifier saw bounded sentences"
    assert seen["texts"][0].endswith(item.answer_text), "the sentence verified carries exactly the text shown"


def test_a_verifier_sentence_the_schema_refuses_is_an_unverified_batch_not_a_crash(monkeypatch):
    form, q1, q2 = _fagerstrom()

    async def complete(schema, instructions, user, timeout_s):
        items = [prefill.PrefillItem(question_id=q1.id, status="answered", answer_text="ok", answer_id=None,
                                     source_record_ids=["note-1"])]
        return llm.FoundryResult(output=prefill.PrefillAnswerSet(items=items), model="stub")

    monkeypatch.setattr(llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(llm, "_complete_bounded", complete)
    monkeypatch.setattr(prefill, "FORMS_BY_PURPOSE", {"medication_follow_up": (("Fagerström", "related", None),)})
    monkeypatch.setattr(prefill, "MOOD_CODES", ())
    # Force the sentence builder to produce something the schema refuses; the guard must turn it into "unverified".
    monkeypatch.setattr(prefill, "_verifier_sentence", lambda question, item: "")
    _s, [result] = asyncio.run(prefill.build(_scoped(CLINICAL), "medication_follow_up", TODAY))
    assert result.generation_status == "generated" and result.verification_status == "partial"
    assert all(item.status == "unknown" for item in result.items)


def test_a_form_whose_fill_raises_is_an_open_form_not_a_failed_briefing(monkeypatch):
    async def explode(*_args, **_kwargs):
        raise RuntimeError("anything")

    monkeypatch.setattr(llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(prefill, "_prefill_form", explode)
    monkeypatch.setattr(prefill, "FORMS_BY_PURPOSE", {"medication_follow_up": (("AUDIT", "related", None),)})
    monkeypatch.setattr(prefill, "MOOD_CODES", ())
    _s, [result] = asyncio.run(prefill.build(_scoped(CLINICAL), "medication_follow_up", TODAY))
    assert result.generation_status == "failed" and result.answered == 0


def test_the_published_citations_are_the_verifiers_not_the_models(monkeypatch):
    """Review finding 2: a record the verifier scored below the floor is not shown as a source, and `historical` is
    decided over the verifier's set, as narration's citations are."""
    form, q1, q2 = _fagerstrom()

    async def complete(schema, instructions, user, timeout_s):
        items = [prefill.PrefillItem(question_id=q1.id, status="answered", answer_text=None, answer_id=q1.answers[1].id,
                                     source_record_ids=["note-1", "med-2"]),
                 prefill.PrefillItem(question_id=q2.id, status="answered", answer_text=None, answer_id=q2.answers[0].id,
                                     source_record_ids=["note-1"])]
        return llm.FoundryResult(output=prefill.PrefillAnswerSet(items=items), model="stub")

    async def verify(scoped, candidate, today, timeout_s):
        verdicts = []
        for s in candidate.sentences:
            # s1: only the ended medication supports it; s2: supported but with no supporting record at all.
            supporting = ["med-2"] if s.sentence_id == "s1" else []
            verdicts.append({"sentence_id": s.sentence_id, "verdict": "supported", "supporting_record_ids": supporting,
                             "call_status": "complete", "answered_by": "v", "input_tokens": 0, "output_tokens": 0})
        return {"verdicts": verdicts, "model": "v", "status": "complete", "input_tokens": 0, "output_tokens": 0}

    monkeypatch.setattr(llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(llm, "_complete_bounded", complete)
    monkeypatch.setattr(decision_model, "verify_narration", verify)
    monkeypatch.setattr(prefill, "FORMS_BY_PURPOSE", {"medication_follow_up": (("Fagerström", "related", None),)})
    monkeypatch.setattr(prefill, "MOOD_CODES", ())
    _s, [result] = asyncio.run(prefill.build(_scoped(CLINICAL), "medication_follow_up", TODAY))
    by_id = {item.question_id: item for item in result.items}
    assert by_id[q1.id].status == "answered" and by_id[q1.id].source_record_ids == ["med-2"], (
        "the model cited two records; only the one the verifier confirmed is shown")
    assert by_id[q1.id].historical is True, "decided over the verifier's set: the one confirmed record is history"
    assert by_id[q2.id].status == "unknown", "a `supported` verdict with no supporting record is not a verified answer"
    assert (result.proposed, result.answered, result.verified) == (2, 1, 1)


def test_a_form_with_several_score_calculations_reports_the_total_against_the_questions_own_maximum():
    """ADHD-RS declares per-subscale ranges; summing all scored questions against the first subscale's max would
    report a total above its own ceiling. The whole-form maximum is derived from the questions instead."""
    form = knowledge_forms.find_form("ADHD")
    assert len(form.score_calculations) >= 1
    answers = {q.id: max(q.answers, key=lambda a: a.points or 0).id for q in form.scored_questions}
    result = assess.score_from_form(form, answers)
    derived = sum(max((a.points for a in q.answers if a.points is not None), default=0) for q in form.scored_questions)
    assert result["total"] == result["max"] == derived
    assert result["total"] <= result["max"], "a total can never exceed the range it is reported against"
    phq = knowledge_forms.find_form("PHQ-9")
    full = {q.id: q.answers[3].id for q in phq.scored_questions}
    assert assess.score_from_form(phq, full)["title"] == phq.score_calculations[0].title_fi, (
        "a form whose one calculation covers the whole keeps the publisher's own title")


def test_level_one_folds_repeated_diagnoses_and_prescriptions_into_one_line_citing_every_record():
    records = [
        _record("dx-a", "diagnoosi", {"nimi": "Tyypin 2 diabetes", "icd10": "E11.9", "tila": "aktiivinen"},
                when="2025-01-01T00:00:00+00:00"),
        _record("dx-b", "diagnoosi", {"nimi": "Tyypin 2 diabetes", "icd10": "E11.9", "tila": "aktiivinen"},
                when="2026-03-01T00:00:00+00:00"),
        _record("med-a", "lääkitys", {"lääke": "metformiini", "annos": "säännöllinen", "tila": "aktiivinen"}, topic="lääkitys",
                when="2025-05-01T00:00:00+00:00"),
        _record("med-b", "lääkitys", {"lääke": "metformiini", "annos": "säännöllinen", "tila": "aktiivinen"}, topic="lääkitys",
                when="2026-05-01T00:00:00+00:00"),
    ]
    by_key = {s.key: s for s in prefill.esitieto_sections(_scoped(records), "medication_follow_up", TODAY)}
    [diagnosis] = by_key["aikaisempi_hoito"].items
    assert diagnosis.text == "Tyypin 2 diabetes (E11.9) · pitkäaikainen" and sorted(diagnosis.source_record_ids) == ["dx-a", "dx-b"]
    assert diagnosis.recorded_at.year == 2026, "the line is dated with the latest of its records"
    [medication] = by_key["laakehoito"].items
    assert medication.text == "metformiini · säännöllinen · käytössä" and sorted(medication.source_record_ids) == ["med-a", "med-b"]


def test_a_markdown_decision_document_reads_as_one_plain_line_and_a_long_one_is_cut():
    text = "# PÄÄTÖSLUONNOS\n\n## PERUSTIEDOT\n- Päätöspäivämäärä: 4.2.2026\n- Palvelu: Säännöllinen kotihoito\n\n## RATKAISU\nMyönteinen."
    assert prefill._plain(text) == "PÄÄTÖSLUONNOS · PERUSTIEDOT · Päätöspäivämäärä: 4.2.2026 · Palvelu: Säännöllinen kotihoito · RATKAISU · Myönteinen."
    assert prefill._plain("Tavallinen lause.") == "Tavallinen lause."
    long = prefill._plain("x" * 1000)
    assert len(long) == prefill.MAX_ITEM_CHARS + 1 and long.endswith("…")


def test_a_record_dated_after_today_is_future_not_history_at_both_levels(monkeypatch):
    """C1: the release dates some records after the day they are read (a corpus property, not a fault). An ended
    diagnosis takes `history` from its `tila`, so without a date check it was shown as `aiempi` beside a
    `kirjattu` date still to come. Level 1 says `future`; Level 2 does not flag such an answer `Tarkista`."""
    future = "2026-11-29T11:29:00+00:00"
    records = [
        _record("dx-future", "diagnoosi", {"nimi": "Nivelrikko", "icd10": "M19.9", "tila": "ei_aktiivinen"}, when=future),
        _record("med-future", "lääkitys", {"lääke": "ibuprofeeni", "annos": "tarvittaessa", "tila": "päättynyt"},
                topic="lääkitys", when=future),
        _record("note-future", "vapaateksti", {"otsikko": "Käynti", "teksti": NOTE_TEXT, "osiot": SECTIONS}, when=future),
    ]
    assert TODAY < datetime.fromisoformat(future).date()
    by_key = {s.key: s for s in prefill.esitieto_sections(_scoped(records), "medication_follow_up", TODAY)}
    assert by_key["aikaisempi_hoito"].items[0].temporal_status == "future"
    assert by_key["laakehoito"].items[0].temporal_status == "future"
    assert by_key["tulosyy"].items[0].temporal_status == "future"
    # The same records a day after they were recorded read as they always did.
    later = datetime(2026, 12, 1, tzinfo=timezone.utc).date()
    by_key = {s.key: s for s in prefill.esitieto_sections(_scoped(records), "medication_follow_up", later)}
    assert by_key["aikaisempi_hoito"].items[0].temporal_status == "history"
    assert by_key["tulosyy"].items[0].temporal_status == "current"

    form, q1, q2 = _fagerstrom()

    async def complete(schema, instructions, user, timeout_s):
        items = [prefill.PrefillItem(question_id=q1.id, status="answered", answer_text=None, answer_id=q1.answers[0].id,
                                     source_record_ids=["med-future"])]
        return llm.FoundryResult(output=prefill.PrefillAnswerSet(items=items), model="stub")

    async def verify(scoped, candidate, today, timeout_s):
        return {"verdicts": [{"sentence_id": s.sentence_id, "verdict": "supported",
                              "supporting_record_ids": list(s.source_ids), "call_status": "complete",
                              "answered_by": "v", "input_tokens": 0, "output_tokens": 0} for s in candidate.sentences],
                "model": "v", "status": "complete", "input_tokens": 0, "output_tokens": 0}

    monkeypatch.setattr(llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(llm, "_complete_bounded", complete)
    monkeypatch.setattr(decision_model, "verify_narration", verify)
    monkeypatch.setattr(prefill, "FORMS_BY_PURPOSE", {"medication_follow_up": (("Fagerström", "related", None),)})
    monkeypatch.setattr(prefill, "MOOD_CODES", ())
    _s, [result] = asyncio.run(prefill.build(_scoped(records), "medication_follow_up", TODAY))
    item = next(i for i in result.items if i.question_id == q1.id)
    assert item.status == "answered" and item.historical is False, "an ended medication dated in the future is not history"


def _physio():
    form = knowledge_forms.find_form("Fysioterapia")
    by_text = {q.text_fi.strip(): q for q in form.questions if q.type != "heading"}
    weight = by_text["Mikä on painosi?"]
    height = by_text["Mikä on pituutesi?"]
    conditions = next(q for q in form.questions if q.type == "multiselect"
                      and any(a.text_fi.strip() == "Korkea verenpaine" for a in q.answers))
    return form, weight, height, conditions


PHYSIO_RECORDS = [
    _record("meas-old", "mittaus", {"paino_kg": 80.0, "pituus_cm": 176}, when="2025-05-01T10:00:00+00:00"),
    _record("meas-new", "mittaus", {"paino_kg": 78.5, "verenpaine": "138/84"}, when="2026-09-20T10:00:00+00:00"),
    _record("dx-bp", "diagnoosi", {"nimi": "korkea verenpaine", "icd10": "I10", "tila": "aktiivinen"}),
    _record("note-1", "vapaateksti", {"otsikko": "Käynti", "teksti": NOTE_TEXT, "osiot": SECTIONS}),
]


def test_the_deterministic_step_answers_a_measurement_question_from_the_newest_record_and_an_option_that_is_a_diagnosis():
    """D1: the physio form asks weight and height as free text and lists conditions as options. Weight comes from the
    newest `mittaus` that carries it, height from the only one that does, and the option `Korkea verenpaine` from
    the diagnosis of that name, case-insensitively; each cites its record. Nothing else is answered by rule."""
    form, weight, height, conditions = _physio()
    answers = prefill._deterministic_answers(form, PHYSIO_RECORDS)
    assert answers[weight.id].answer_text == "78,5 kg" and answers[weight.id].source_record_ids == ["meas-new"]
    assert answers[height.id].answer_text == "176 cm" and answers[height.id].source_record_ids == ["meas-old"]
    option = next(a for a in conditions.answers if a.text_fi.strip() == "Korkea verenpaine")
    assert answers[conditions.id].answer_id == option.id and answers[conditions.id].source_record_ids == ["dx-bp"]
    assert set(answers) == {weight.id, height.id, conditions.id}, "no other question of the form is answered by rule"
    # No measurement, no diagnosis: nothing is answered -- a rule never guesses.
    assert prefill._deterministic_answers(form, [r for r in PHYSIO_RECORDS if r.record_type == "vapaateksti"]) == {}
    # Two options that are both recorded diagnoses: one answer_id cannot carry both, so the question is left to the model.
    two = PHYSIO_RECORDS + [_record("dx-op", "diagnoosi", {"nimi": "Osteoporoosi", "icd10": "M81.9", "tila": "aktiivinen"})]
    assert conditions.id not in prefill._deterministic_answers(form, two)


def test_deterministic_answers_go_through_the_verifier_and_take_precedence_over_the_models_proposal(monkeypatch):
    form, weight, height, conditions = _physio()
    seen = {}

    async def complete(schema, instructions, user, timeout_s):
        # The model proposes a different weight from a note; the rule's answer, citing the measurement, wins.
        items = [prefill.PrefillItem(question_id=weight.id, status="answered", answer_text="noin 90 kg", answer_id=None,
                                     source_record_ids=["note-1"])]
        return llm.FoundryResult(output=prefill.PrefillAnswerSet(items=items), model="stub")

    async def verify(scoped, candidate, today, timeout_s):
        seen.setdefault("sentences", []).extend((s.text, list(s.source_ids)) for s in candidate.sentences)
        return {"verdicts": [{"sentence_id": s.sentence_id,
                              "verdict": "supported" if "pituu" not in s.text else "unsupported",
                              "supporting_record_ids": list(s.source_ids) if "pituu" not in s.text else [],
                              "call_status": "complete", "answered_by": "v", "input_tokens": 0, "output_tokens": 0}
                             for s in candidate.sentences],
                "model": "v", "status": "complete", "input_tokens": 0, "output_tokens": 0}

    monkeypatch.setattr(llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(llm, "_complete_bounded", complete)
    monkeypatch.setattr(decision_model, "verify_narration", verify)
    monkeypatch.setattr(prefill, "FORMS_BY_PURPOSE", {"medication_follow_up": (("Fysioterapia", "related", None),)})
    monkeypatch.setattr(prefill, "MOOD_CODES", ())
    _s, [result] = asyncio.run(prefill.build(_scoped(PHYSIO_RECORDS), "medication_follow_up", TODAY))
    by_id = {item.question_id: item for item in result.items}
    assert by_id[weight.id].answer_text == "78,5 kg" and by_id[weight.id].source_record_ids == ["meas-new"]
    assert by_id[height.id].status == "unknown", "a rule's answer the verifier does not support is not shown either"
    assert by_id[conditions.id].status == "answered" and by_id[conditions.id].answer_text == "Korkea verenpaine "
    assert (result.proposed, result.answered, result.verified) == (3, 2, 2)
    assert ("Mikä on painosi? — vastaus: 78,5 kg", ["meas-new"]) in seen["sentences"], "verified as shown, citing the record"
    assert all(set(ids) <= {"meas-new", "meas-old", "dx-bp"} for _t, ids in seen["sentences"])
