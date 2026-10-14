"""The assistant (`app/assistant.py`) and its streaming route."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import assistant, chat, decision_model, llm, main
from app.decide import decide
from app.fixtures import load_personas
from app.pipeline import build_picture_for_encounter
from app.policy import cut_for_encounter
from app.store import Store, get_store
from tests.fixtures.bound_encounters import ENCOUNTERS, EXAMPLE_LABELS

PRINCIPAL = {"X-MS-CLIENT-PRINCIPAL-NAME": "presenter@example.test",
             "X-MS-CLIENT-PRINCIPAL-ID": "00000000-0000-0000-0000-000000000001"}
ENCOUNTER = "bound-encounter-002-laakitys-sairaanhoitaja"


@pytest.fixture(scope="module")
def fixture_picture():
    store = Store(personas=load_personas(), path=None, encounters=ENCOUNTERS, example_labels=EXAMPLE_LABELS)
    picture = asyncio.run(build_picture_for_encounter(ENCOUNTER, personas=store.persona_index(),
                                                      encounters=store.encounter_index(), foundry_enabled=False))
    encounter = store.encounter_index()[ENCOUNTER]
    decision = decide(cut_for_encounter(store.persona(encounter.persona_id), encounter))
    return store, picture, decision


def test_the_tools_reach_exactly_the_picture_and_project_for_the_model(fixture_picture):
    _store, picture, decision = fixture_picture
    tools, handlers = assistant.build_tools(picture, decision)
    assert {t["function"]["name"] for t in tools} == set(handlers) == {
        "list_records", "get_record", "get_esitiedot", "get_prefilled_forms", "get_engine_assessment", "search_knowledge"}
    listed = handlers["list_records"]({})
    ids = {row["record_id"] for row in listed["records"]}
    assert ids == {view.record_id for view in chat.picture_records(picture)}, "the picture's records, no more"
    one = handlers["get_record"]({"record_id": sorted(ids)[0]})
    assert set(one) == {"record_id", "record_type", "topic", "recorded_at", "temporal_status", "payload"}
    assert handlers["get_record"]({"record_id": "not-in-this-picture"}) == {"error": "no such record in this briefing"}
    assert handlers["list_records"]({"record_type": "lääkitys"})["records"] and all(
        row["record_type"] == "lääkitys" for row in handlers["list_records"]({"record_type": "lääkitys"})["records"])
    engine = handlers["get_engine_assessment"]({})
    assert engine["risk"]["risk_level"] == picture.risk.risk_level and engine["basis"] == chat.ACTIONS_BASIS_NOTE
    assert [s["heading"] for s in handlers["get_esitiedot"]({})["sections"]] == [s.heading for s in picture.esitiedot]
    assert "forms" in handlers["get_prefilled_forms"]({})
    knowledge = handlers["search_knowledge"]({"query": "PHQ-9"})
    assert knowledge["items"] and all(item["ref"].startswith(("form:", "guideline:")) and item["kind"] in ("form", "guideline")
                                      for item in knowledge["items"]), "offline: the snapshot fallback, refs only"
    assert any(item["kind"] == "form" and "PHQ-9" in item["form_name"] for item in knowledge["items"])
    assert set(knowledge) == {"items"}, "the model sees resolved refs, not the rail's guideline_links"
    assert set(knowledge["items"][0]) == {"ref", "kind", "title", "form_name", "form_version", "category_name",
                                          "question_text", "source"}
    assert handlers["search_knowledge"]({"query": ""}) == {"items": []}
    # Every tool result is model-safe: no identity key anywhere in what the model can read.
    from app import source

    for name, handler in handlers.items():
        source.assert_model_safe(json.dumps(handler({"record_id": sorted(ids)[0], "query": "lääkitys"}),
                                            ensure_ascii=False))


def _stub_loop(answer: assistant.AssistantAnswer, calls=(("list_records", {}), ("get_record", {"record_id": "x"}))):
    async def loop(schema, instructions, user, tools, handlers, timeout_s, *, max_rounds=6, on_tool=None):
        assert schema is assistant.AssistantAnswer and "Älä tee diagnoosia" in instructions
        for name, arguments in calls:
            handlers[name](arguments)
            if on_tool is not None:
                await on_tool(name, arguments)
        return llm.ToolLoopResult(output=answer, model="stub-agent", tool_calls=tuple(calls))
    return loop


def _stub_verify(verdict_for):
    async def verify(scoped, candidate, today, timeout_s):
        verdicts = [{"sentence_id": s.sentence_id, "verdict": verdict_for(s),
                     "supporting_record_ids": list(s.source_ids) if verdict_for(s) == "supported" else [],
                     "call_status": "complete", "answered_by": "v", "input_tokens": 0, "output_tokens": 0}
                    for s in candidate.sentences]
        return {"verdicts": verdicts, "model": "v", "status": "complete", "input_tokens": 0, "output_tokens": 0}
    return verify


def test_only_verified_sentences_and_traceable_steps_are_published(fixture_picture, monkeypatch):
    _store, picture, decision = fixture_picture
    records = chat.picture_records(picture)
    medication = next(v for v in records if v.record_type == "lääkitys")
    answer = assistant.AssistantAnswer(
        sentences=[assistant.AssistantSentence(text="Käytössä on levotyroksiini.", source_ids=[medication.record_id]),
                   assistant.AssistantSentence(text="Tämä lause ei pidä paikkaansa.", source_ids=[medication.record_id]),
                   assistant.AssistantSentence(text="Lause ilman lähdettä väittää jotain.", source_ids=[]),
                   assistant.AssistantSentence(text="Viittaa vieraaseen kirjaukseen.", source_ids=["not-here"])],
        steps=[assistant.AssistantStep(text="Kysy, onko lääke otettu ohjeen mukaan.", source_ids=[medication.record_id]),
               assistant.AssistantStep(text="Ota 3 uutta kokeenottoa.", source_ids=[]),
               assistant.AssistantStep(text="Askel vieraalla lähteellä.", source_ids=["not-here"])],
        followups=["Mitä laboratoriotuloksia on?", "Onko lääkitys muuttunut?"])
    monkeypatch.setattr(llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(llm, "_complete_tool_loop", _stub_loop(answer))
    monkeypatch.setattr(decision_model, "verify_narration",
                        _stub_verify(lambda s: "supported" if "levotyroksiini" in s.text else "unsupported"))
    seen_tools, seen_sentences = [], []

    async def on_tool(name, arguments): seen_tools.append(name)

    async def on_sentence(sentence): seen_sentences.append(sentence.text)

    result = asyncio.run(assistant.ask("Mitä lääkkeitä on käytössä?", picture, decision, on_tool=on_tool,
                                       on_sentence=on_sentence))
    assert result.status == "answered" and result.model == "stub-agent"
    assert [s.text for s in result.sentences] == ["Käytössä on levotyroksiini."] == seen_sentences
    assert result.sentences[0].source_ids == (medication.record_id,)
    assert result.proposed_sentences == 4, "the model wrote four; one survived verification and membership"
    assert [s.text for s in result.steps] == ["Kysy, onko lääke otettu ohjeen mukaan."], (
        "a figure with no record behind it and a step citing outside the picture are dropped")
    assert result.followups == ["Mitä laboratoriotuloksia on?", "Onko lääkitys muuttunut?"]
    assert seen_tools == ["list_records", "get_record"]

    digest = chat.seal_digest(picture, policy_version="v", persona_id="p")
    turn = assistant.compose_turn("Mitä lääkkeitä on käytössä?", picture, decision, result, digest=digest, seq=1,
                                  turn_id="t1")
    classes = [s.statement_class for s in turn.suggestions]
    assert turn.outcome == chat.OUTCOME_ANSWER and classes[0] == chat.STATEMENT_ANSWER_LEAD
    assert classes[1] == chat.STATEMENT_MODEL_SUMMARY and turn.suggestions[1].text == "Käytössä on levotyroksiini."
    model_steps = [s for s in turn.suggestions if s.label == assistant.MODEL_STEP_LABEL]
    assert len(model_steps) == 1 and model_steps[0].basis_note == assistant.MODEL_STEP_BASIS
    assert turn.generator_state == chat.GENERATOR_PUBLISHED and turn.followups == result.followups
    assert [s.suggestion_id for s in turn.suggestions] == [f"s{i}" for i in range(len(turn.suggestions))]
    assert turn.source_count["cited"] >= 1


def test_without_a_model_the_turn_is_the_engines_own_or_the_one_refusal(fixture_picture, monkeypatch):
    _store, picture, decision = fixture_picture
    monkeypatch.setattr(llm, "foundry_configured", lambda: False)
    result = asyncio.run(assistant.ask("Mitä toimenpiteitä suosittelet?", picture, decision))
    assert result.status == "not_configured" and not result.generator_called
    digest = chat.seal_digest(picture, policy_version="v", persona_id="p")
    turn = assistant.compose_turn("Mitä toimenpiteitä suosittelet?", picture, decision, result, digest=digest, seq=1,
                                  turn_id="t2")
    steps = [s for s in turn.suggestions if s.statement_class == chat.STATEMENT_RULE_OUTPUT]
    if picture.actions:
        assert turn.outcome == chat.OUTCOME_ANSWER and steps, "the engine's own next steps are an answer"
    assert turn.generator_state == chat.GENERATOR_NOT_CONFIGURED
    empty = assistant.AssistantResult(generator_called=True, status="empty")
    blank = picture.model_copy(update={"actions": []})
    refused = assistant.compose_turn("Mikä on sää?", blank, decision, empty, digest=digest, seq=2, turn_id="t3")
    if not [s for s in refused.suggestions if s.statement_class != chat.STATEMENT_ANSWER_LEAD]:
        assert refused.outcome == chat.OUTCOME_REFUSAL and refused.abstention == chat.ABSTENTION
        assert refused.generator_state == chat.GENERATOR_WITHHELD


def _events(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = [line for line in block.splitlines() if line]
        event = next(line[len("event: "):] for line in lines if line.startswith("event: "))
        data = next(line[len("data: "):] for line in lines if line.startswith("data: "))
        out.append((event, json.loads(data)))
    return out


def test_the_stream_route_emits_tools_then_verified_sentences_then_one_stored_turn(monkeypatch):
    store = Store(personas=load_personas(), path=None, encounters=ENCOUNTERS, example_labels=EXAMPLE_LABELS)
    main.app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(main.app) as client:
            first = client.get(f"/api/encounters/{ENCOUNTER}/picture?mode=foundry-off", headers=PRINCIPAL).json()
            medication = next(v for v in [*first["records"]["priority"], *first["records"]["accessible_other"]]
                              if v["record_type"] == "lääkitys")
            answer = assistant.AssistantAnswer(
                sentences=[assistant.AssistantSentence(text="Käytössä on levotyroksiini.", source_ids=[medication["record_id"]])],
                steps=[], followups=["Onko annos muuttunut?"])
            monkeypatch.setattr(llm, "foundry_configured", lambda: True)
            monkeypatch.setattr(llm, "_complete_tool_loop", _stub_loop(answer))
            monkeypatch.setattr(decision_model, "verify_narration", _stub_verify(lambda s: "supported"))
            body = {"turn_id": "stream-1", "question": "Mitä lääkkeitä on käytössä?",
                    "history": [{"question": "Hei", "answer": ["Hei."]}]}
            response = client.post(f"/api/encounters/{ENCOUNTER}/chat/stream", json=body, headers=PRINCIPAL)
            assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
            events = _events(response.text)
            names = [name for name, _ in events]
            assert names[0] == "progress" and events[0][1]["state"] == "reading"
            assert names[1:3] == ["tool", "tool"] and names[3] == "sentence" and names[-1] == "turn"
            assert events[3][1] == {"text": "Käytössä on levotyroksiini.", "source_ids": [medication["record_id"]]}
            turn = events[-1][1]
            assert turn["turn"]["outcome"] == chat.OUTCOME_ANSWER and turn["turn"]["followups"] == ["Onko annos muuttunut?"]
            assert turn["assistant"] == {"status": "answered", "model": "stub-agent",
                                         "tool_calls": ["list_records", "get_record"],
                                         "proposed_sentences": 1, "verified_sentences": 1}
            assert turn["digest"]["digest_id"] and "prompts" not in turn and turn["answer_notice"]
            stored = store.chat_turn("stream-1")
            assert stored["tool_calls"] == ["list_records", "get_record"] and stored["outcome"] == chat.OUTCOME_ANSWER
            assert all(row["text"] is None for row in stored["suggestions"]
                       if row["statement_class"] == chat.STATEMENT_MODEL_SUMMARY), "no statement text is stored"
            # A retry under the stored id replays the stored turn without a model call.
            async def refuse(*_a, **_k):
                raise AssertionError("the model must not be called on a retry")

            monkeypatch.setattr(llm, "_complete_tool_loop", refuse)
            again = client.post(f"/api/encounters/{ENCOUNTER}/chat/stream", json=body, headers=PRINCIPAL)
            replay = _events(again.text)
            assert replay[0][1]["state"] == "replaying" and replay[-1][1]["turn"]["turn_id"] == "stream-1"
            assert replay[-1][1]["turn"]["digest_id"] == turn["turn"]["digest_id"]
            # A stale digest is refused before the stream opens, as the chat route refuses it.
            stale = client.post(f"/api/encounters/{ENCOUNTER}/chat/stream", headers=PRINCIPAL,
                                json={"turn_id": "stream-2", "question": "x", "digest_id": "0" * 32})
            assert stale.status_code == 409 and stale.json()["reason"] == "encounter_changed"
            assert client.post(f"/api/encounters/{ENCOUNTER}/chat/stream", json=body).status_code == 401
    finally:
        main.app.dependency_overrides.clear()


def test_a_guideline_question_publishes_the_guideline_citation_beside_the_verified_sentence(fixture_picture,
                                                                                             monkeypatch):
    """N6: the live failure. The model answered a Käypä hoito question by citing the `guideline:hoi04010` ref its
    `search_knowledge` hit carried -- and every sentence carrying that ref was dropped before verification, because
    membership was tested over the whole `source_ids` list against record ids alone. The turn fell to the engine
    steps and the "ei julkaistu" caption.

    Now a knowledge ref beside at least one picture record is kept: the record part is verified exactly as before,
    the ref rides along, and the `external_evidence` statement names the recorded guideline (title, date, URL). A
    sentence citing the guideline alone is still dropped: a claim about guideline content has no record to verify
    against, and no content is stored (`guidance.py`).
    """
    _store, picture, decision = fixture_picture
    question = "Mitä Käypä hoito -suositus sanoo kohonneen verenpaineen seurannasta?"
    records = chat.picture_records(picture)
    medication = next(v for v in records if v.record_type == "lääkitys" and v.topic == "verenpaine")
    diagnosis = next(v for v in records if v.record_type == "diagnoosi" and v.topic == "verenpaine")
    hit = assistant.build_tools(picture, decision)[1]["search_knowledge"]({"query": question})["items"][0]
    assert hit["ref"] == "guideline:hoi04010", "offline fallback: the question names the hypertension guideline"
    answer = assistant.AssistantAnswer(
        sentences=[assistant.AssistantSentence(text="Suositus käsittelee kohonneen verenpaineen seurantaa.",
                                               source_ids=[hit["ref"]]),
                   assistant.AssistantSentence(text="Käytössä on amlodipiini kohonneeseen verenpaineeseen.",
                                               source_ids=[medication.record_id, hit["ref"]]),
                   assistant.AssistantSentence(text="Essentiaalinen hypertensio on kirjattu aktiiviseksi.",
                                               source_ids=[diagnosis.record_id])],
        steps=[assistant.AssistantStep(text="Tarkista verenpaineen seurantaväli suosituksen mukaan.",
                                       source_ids=[hit["ref"], diagnosis.record_id]),
               assistant.AssistantStep(text="Askel tuntemattomalla viitteellä.", source_ids=["guideline:hoi99999"])],
        followups=[])
    monkeypatch.setattr(llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(llm, "_complete_tool_loop", _stub_loop(answer, calls=(("search_knowledge", {"query": question}),)))
    monkeypatch.setattr(decision_model, "verify_narration", _stub_verify(lambda s: "supported"))
    result = asyncio.run(assistant.ask(question, picture, decision))
    assert result.status == "answered"
    assert [s.text for s in result.sentences] == ["Käytössä on amlodipiini kohonneeseen verenpaineeseen.",
                                                  "Essentiaalinen hypertensio on kirjattu aktiiviseksi."]
    assert result.sentences[0].source_ids == (medication.record_id,), "the record part is what was verified"
    assert result.sentences[0].knowledge_refs == ("guideline:hoi04010",)
    assert result.sentences[1].knowledge_refs == ()
    assert [(s.text, s.source_ids, s.knowledge_refs) for s in result.steps] == [
        ("Tarkista verenpaineen seurantaväli suosituksen mukaan.", (diagnosis.record_id,), ("guideline:hoi04010",))], \
        "a ref the snapshots do not resolve is as foreign as a record outside the picture"

    digest = chat.seal_digest(picture, policy_version="v", persona_id="p")
    turn = assistant.compose_turn(question, picture, decision, result, digest=digest, seq=1, turn_id="t-n6")
    assert turn.outcome == chat.OUTCOME_ANSWER and turn.generator_state == chat.GENERATOR_PUBLISHED
    [guideline] = [s for s in turn.suggestions if s.statement_class == chat.STATEMENT_EXTERNAL_EVIDENCE]
    assert guideline.text == "Käypä hoito -suositus tähän tilanteeseen: Kohonnut verenpaine (julkaistu 21.09.2026)."
    assert guideline.url == "https://www.kaypahoito.fi/hoi04010"
    assert guideline.refs == ["guideline:hoi04010"]
    assert set(guideline.source_record_ids) <= {v.record_id for v in records}
    summaries = [s for s in turn.suggestions if s.statement_class == chat.STATEMENT_MODEL_SUMMARY]
    assert summaries[0].refs == ["guideline:hoi04010"] and summaries[1].refs == []
    [step] = [s for s in turn.suggestions if s.label == assistant.MODEL_STEP_LABEL]
    assert step.refs == ["guideline:hoi04010"] and step.source_record_ids == [diagnosis.record_id]
    assert turn.source_count["knowledge_cited"] == 1
    # The statement follows the sentence that earned it and precedes the engine's own findings and steps.
    classes = [s.statement_class for s in turn.suggestions]
    assert classes.index(chat.STATEMENT_EXTERNAL_EVIDENCE) > classes.index(chat.STATEMENT_MODEL_SUMMARY)


def test_a_guideline_question_cites_the_guideline_from_the_question_alone_when_a_sentence_verified(fixture_picture):
    """The topic may be named by the question rather than by the cited record's text: a verified sentence about
    the demographic record, asked under a verenpaine question, still earns the picture-supported citation. With no
    verified sentence and no model step there is nothing to stand beside, and the engine arm cites none."""
    _store, picture, decision = fixture_picture
    question = "Mitä Käypä hoito -suositus sanoo kohonneen verenpaineen seurannasta?"
    demographic = next(v for v in chat.picture_records(picture) if v.record_type == "perustieto")
    result = assistant.AssistantResult(sentences=[assistant.VerifiedSentence(
        text="Asiakas asuu Helsingissä.", source_ids=(demographic.record_id,))], generator_called=True,
        status="answered")
    digest = chat.seal_digest(picture, policy_version="v", persona_id="p")
    turn = assistant.compose_turn(question, picture, decision, result, digest=digest, seq=1, turn_id="t-q")
    [guideline] = [s for s in turn.suggestions if s.statement_class == chat.STATEMENT_EXTERNAL_EVIDENCE]
    assert guideline.url == "https://www.kaypahoito.fi/hoi04010"
    assert guideline.source_record_ids and set(guideline.source_record_ids) <= {
        v.record_id for v in chat.picture_records(picture)}, "it cites the picture records that support it"
    assert all(s.url != "https://www.kaypahoito.fi/hoi50056" for s in turn.suggestions), \
        "the diabetes guideline is attached to the picture but not asked about, and no sentence cites its record"
    unrelated = assistant.compose_turn("Mitä lääkkeitä on käytössä?", picture, decision, result, digest=digest,
                                       seq=2, turn_id="t-u")
    assert all(s.statement_class != chat.STATEMENT_EXTERNAL_EVIDENCE for s in unrelated.suggestions), \
        "a question that names no topic, beside a sentence whose record names none, cites nothing"


def test_a_guideline_question_cites_the_guideline_even_when_the_model_published_nothing(fixture_picture):
    """The live failure on --night1: the model wrote only guideline-only sentences, every one was dropped, and the
    turn carried the engine steps alone with no Käypä hoito row. The question names the topic and the post-cut
    picture holds the records that support the guideline, so the recorded reference is cited beside the engine's
    steps; a question naming no recorded topic still cites nothing."""
    _store, picture, decision = fixture_picture
    question = "Mitä Käypä hoito -suositus sanoo kohonneen verenpaineen seurannasta?"
    empty = assistant.AssistantResult(sentences=[], generator_called=True, status="empty")
    digest = chat.seal_digest(picture, policy_version="v", persona_id="p")
    turn = assistant.compose_turn(question, picture, decision, empty, digest=digest, seq=1, turn_id="t-e")
    [guideline] = [s for s in turn.suggestions if s.statement_class == chat.STATEMENT_EXTERNAL_EVIDENCE]
    assert guideline.url == "https://www.kaypahoito.fi/hoi04010"
    assert guideline.source_record_ids and set(guideline.source_record_ids) <= {
        v.record_id for v in chat.picture_records(picture)}
    unrelated = assistant.compose_turn("Mitä lääkkeitä on käytössä?", picture, decision, empty, digest=digest,
                                       seq=2, turn_id="t-e2")
    assert all(s.statement_class != chat.STATEMENT_EXTERNAL_EVIDENCE for s in unrelated.suggestions)
