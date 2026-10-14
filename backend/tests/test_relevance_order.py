"""L3D: the narration input is ordered by validated relevance, and the order only ever sorts.

1. the order is relevance, then urgency, then the incoming order, and it is deterministic;
2. the order function only orders: the same records in, the same records out;
3. visible selection retains every baseline record while narration may deliberately omit an irrelevant one;
4. pipeline hands the generator the relevance order (urgency alone would put the other record first);
5. urgency_order still orders by urgency alone, whatever the relevance says;
6. the ranking metadata on the picture carries no clinical text and no field beyond the permitted set.
"""

import asyncio
import json
from dataclasses import replace
from datetime import date, datetime, timezone

import app.pipeline as pipeline
from app import decision_model, selection
from app.fixtures import load_personas
from app.picture import DecisionNoteOutcome
from app.policy import cut
from app.upm import Persona, Record
from tests.fixtures.bound_encounters import ENCOUNTERS

TODAY = date(2026, 9, 30)
MARKER = "CLINICAL-MARKER-7d3e"


def _record(record_id, *, text=MARKER, topic="hoito"):
    return Record(
        record_id=record_id, source="synthetic", record_type="vapaateksti", authored_by_role="sairaanhoitaja",
        recorded_at=datetime(2026, 9, 15, 8, tzinfo=timezone.utc), topic=topic, sensitivity_tag="sensitive",
        consent_scope="treatment", payload={"otsikko": "Keskustelu", "teksti": text},
    )


def _verdict(record_id, relevant=None, urgency=None, *, status="unclear", gate_refused=True):
    """A note row shaped like a validated Decision-1 answer. `relevant` is P(relevant); None means never assessed."""
    row = {"record_id": record_id, "attempted": True, "status": status, "gate_refused": gate_refused}
    if relevant is not None:
        rest = (1.0 - relevant) / 2
        row["relevance"] = {"choice": "relevant", "confidence": 0.9,
                            "probabilities": {"relevant": relevant, "irrelevant": rest, "unclear": rest}}
    if urgency is not None:
        row["urgency"] = {"score": urgency, "probabilities": {}, "legend": {}, "confidence": 0.9}
    return row


def _selection(rows, *, ids=()):
    records = [_record(record_id) for record_id in ids]
    scoped = cut(Persona(persona_id="p", label="P", records=records), "sairaanhoitaja", "mental_health_assessment")
    return selection.apply_decision(scoped, [], {}, rows, TODAY)


# 1. relevance, then urgency, then the incoming order; deterministic ------------------------------------------

def test_relevance_order_is_relevance_then_urgency_then_incoming_order_and_deterministic():
    rows = [
        _verdict("a", relevant=0.90, urgency=1.0),
        _verdict("b", relevant=0.90, urgency=3.0),
        _verdict("c", relevant=0.95, urgency=0.0),   # highest relevance wins whatever its urgency
        _verdict("d", urgency=4.0),                  # never assessed for relevance: after every assessed one
        _verdict("e", relevant=0.90),                # same relevance, no urgency score: after scored ones
    ]
    sel = _selection(rows)
    incoming = ["a", "b", "c", "d", "e"]

    assert sel.relevance_order(incoming) == ["c", "b", "a", "e", "d"]
    assert sel.relevance_order(incoming) == sel.relevance_order(incoming), "the same inputs must give the same order"

    # Full tie on relevance and urgency: the incoming order decides, in both directions.
    tied = _selection([_verdict("x", relevant=0.8, urgency=2.0), _verdict("y", relevant=0.8, urgency=2.0)])
    assert tied.relevance_order(["x", "y"]) == ["x", "y"]
    assert tied.relevance_order(["y", "x"]) == ["y", "x"]


# 2. the order function only orders --------------------------------------------------------------------------

def test_relevance_order_only_orders_and_never_adds_or_removes():
    sel = _selection([
        _verdict("kept", relevant=0.9, urgency=1.0),
        _verdict("refused", relevant=0.99, urgency=5.0),  # refused by the gate: scored, but not in the input
    ])
    eligible = ["kept", "unscored-eligible"]

    ordered = sel.relevance_order(eligible)

    assert sorted(ordered) == sorted(eligible), "the same records in must come out, no more and no fewer"
    assert "refused" not in ordered, "a refused candidate has relevance on the selection but must never be added"
    assert ordered == ["kept", "unscored-eligible"]


# 3. what the professional can READ keeps the baseline; what the GENERATOR is given may be narrower ----------

def test_the_visible_selection_keeps_the_baseline_while_narration_may_omit_it(monkeypatch):
    """The honest version of an invariant an earlier test asserted vacuously.

    Two different things, deliberately, and the earlier test collapsed them. **Visible selection retains every
    baseline record** -- nothing is removed from what the professional can read. **Narration eligibility is
    narrower**: a confidently `irrelevant` CURRENT baseline free-text note is omitted from generation
    (ADR-0023), and drafts are excluded.

    The earlier test returned a verdict only for a MISSED note, so the omission gate never ran and its
    assertion could not fail. This one drives the omission gate itself and asserts BOTH halves, so neither a
    lost baseline record nor a silently narrowed narration can pass. It deliberately does not assert "narration
    is never fewer than baseline": that claim is false by design, and the remedy for a false test is a true
    test, not a change to the behaviour it measures.
    """
    encounter, _ = pipeline.resolve_encounter("bound-encounter-003-mielenterveys-sairaanhoitaja", load_personas(), ENCOUNTERS)
    encounter = replace(encounter, persona_id="synthetic-order")
    persona = Persona(persona_id="synthetic-order", label="S", records=[
        _record("baseline-note", text="Mieliala vaihtelee.", topic="mielenterveys"),
        _record("refused-note", text="Muu kirjaus."),
    ])
    seen = []
    real_narrate = pipeline.narrate

    async def spy(scoped, ids, **kwargs):
        seen.append(list(ids))
        return await real_narrate(scoped, ids, **kwargs)

    async def one_irrelevant_verdict_and_one_refusal(scoped, baseline_ids, today):
        # A confident `irrelevant` on a CURRENT baseline note drives the omission gate; the other candidate is
        # scored relevant and the gate refuses it. Both are needed: the first exercises omission, the second
        # exercises refusal, and neither can stand in for the other.
        return [
            _verdict("baseline-note", relevant=0.05, status="irrelevant", gate_refused=False),
            _verdict("refused-note", relevant=0.9, urgency=1.0),
        ]

    monkeypatch.setattr(pipeline, "narrate", spy)
    monkeypatch.setattr(pipeline, "classify_decision_notes", one_irrelevant_verdict_and_one_refusal)
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: False)
    picture = asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))

    # Non-vacuity: the generator was actually given a set, so the assertions below mean something.
    assert seen and seen[-1] is not None, "the narration spy never ran"

    visible = {record.record_id for record in
               [*picture.records.priority, *picture.records.accessible_other]}
    assert "baseline-note" in visible, (
        "a confidently irrelevant baseline note must remain READABLE -- omission is a generation gate, never a "
        "display gate")
    assert "baseline-note" not in seen[-1], (
        "the omission gate did not run: a confidently irrelevant current baseline note should be withheld from "
        "GENERATION, and this assertion is the one the earlier test could not fail")
    assert "refused-note" not in seen[-1], "a refused candidate must not reach narration"


# 4. pipeline hands the generator the relevance order -------------------------------------------------------

def test_pipeline_gives_the_generator_the_relevance_order_not_the_urgency_order(monkeypatch):
    encounter, _ = pipeline.resolve_encounter("bound-encounter-003-mielenterveys-sairaanhoitaja", load_personas(), ENCOUNTERS)
    encounter = replace(encounter, persona_id="synthetic-order")
    persona = Persona(persona_id="synthetic-order", label="S", records=[
        _record("baseline-note", text="Mieliala vaihtelee.", topic="mielenterveys"),
        _record("candidate-note"),
    ])
    # baseline-note is the more urgent one; candidate-note is the more relevant one. The two orders disagree.
    verdicts = [_verdict("baseline-note", relevant=0.6, urgency=4.0, status="relevant", gate_refused=False),
                _verdict("candidate-note", relevant=0.99, urgency=0.0, status="relevant", gate_refused=False)]
    seen = []
    real_narrate = pipeline.narrate

    async def spy(scoped, ids, **kwargs):
        seen.append(list(ids))
        return await real_narrate(scoped, ids, **kwargs)

    async def classify(scoped, baseline_ids, today):
        return verdicts

    monkeypatch.setattr(pipeline, "narrate", spy)
    monkeypatch.setattr(pipeline, "classify_decision_notes", classify)
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: False)
    picture = asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))

    assert seen[-1] == ["candidate-note", "baseline-note"], "the generator must read the most relevant record first"
    urgency_only = selection.Selection(
        records=(), baseline_record_ids=(), additional_record_ids=(), emphasized_record_ids=(), reasons={},
        decision_notes=tuple(verdicts))
    assert urgency_only.urgency_order(["candidate-note", "baseline-note"]) == ["baseline-note", "candidate-note"], (
        "the fixture must separate the two orders, or this test proves nothing")
    assert picture.selection is not None


# 5. urgency_order is unchanged: urgency alone ----------------------------------------------------------------

def test_urgency_order_still_orders_by_urgency_alone_whatever_relevance_says():
    sel = _selection([
        _verdict("high-relevance", relevant=0.99, urgency=0.0),
        _verdict("low-relevance", relevant=0.10, urgency=3.0),
        _verdict("unscored", relevant=0.95),
    ])
    assert sel.urgency_order(["high-relevance", "low-relevance", "unscored"]) == [
        "low-relevance", "high-relevance", "unscored"]


# 6. the ranking metadata carries no clinical text and no field beyond the permitted set --------------------

PERMITTED_DECISION_NOTE_FIELDS = {
    "record_id", "status", "model", "elapsed_ms", "input_tokens", "output_tokens", "relevance_probabilities",
    "gate_probabilities", "relevance_confidence", "time_sensitivity", "negation_probability", "subject_binding",
    "gate_refused",
}


def test_ranking_metadata_on_the_picture_carries_no_clinical_text_and_no_new_field(monkeypatch):
    encounter, _ = pipeline.resolve_encounter("bound-encounter-003-mielenterveys-sairaanhoitaja", load_personas(), ENCOUNTERS)
    encounter = replace(encounter, persona_id="synthetic-order")
    persona = Persona(persona_id="synthetic-order", label="S", records=[
        _record("baseline-note", text=MARKER, topic="mielenterveys"),
        _record("candidate-note", text=MARKER),
    ])
    verdicts = [_verdict("baseline-note", relevant=0.6, urgency=4.0, status="relevant", gate_refused=False),
                _verdict("candidate-note", relevant=0.99, urgency=0.0, status="relevant", gate_refused=False)]

    async def classify(scoped, baseline_ids, today):
        return verdicts

    async def generate(scoped, today, timeout_s):
        sentence = pipeline.llm.NarrationSentence(sentence_id="s1", text="Mieliala vaihtelee.",
                                                  source_ids=[scoped.ids[0]])
        return pipeline.llm.FoundryResult(output=pipeline.llm.FreshNarration(sentences=[sentence]),
                                          model="gpt-5.4-mini-test")

    async def verify(scoped, candidate, today, timeout_s):
        sentence = candidate.sentences[0]
        return {"verdicts": [{"sentence_id": sentence.sentence_id, "verdict": "supported",
                              "supporting_record_ids": sentence.source_ids}], "model": "decision"}

    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    monkeypatch.setattr(pipeline, "classify_decision_notes", classify)
    monkeypatch.setattr(pipeline.llm, "foundry_configured", lambda: True)
    monkeypatch.setattr(pipeline.llm, "generate_fresh_narration", generate)
    monkeypatch.setattr(decision_model, "verify_narration", verify)
    picture = asyncio.run(pipeline._build_resolved_picture(encounter, persona, TODAY))

    # The ranking the SPA reads: the generator's order, and each note's relevance and urgency.
    assert picture.selection is not None
    assert picture.narration_input_record_ids == ["candidate-note", "baseline-note"]
    ranking = [note for note in picture.selection.decision_notes if note.record_id in picture.narration_input_record_ids]
    assert {note.record_id for note in ranking} == {"baseline-note", "candidate-note"}
    assert all(note.relevance_probabilities and "urgency" in note.gate_probabilities for note in ranking)

    # No field beyond the permitted set, and no clinical text in any of the ranking metadata.
    assert set(DecisionNoteOutcome.model_fields) == PERMITTED_DECISION_NOTE_FIELDS
    metadata = json.dumps({
        "narration_input_record_ids": picture.narration_input_record_ids,
        "decision_notes": [note.model_dump(mode="json") for note in picture.selection.decision_notes],
    }, ensure_ascii=False)
    assert MARKER not in metadata, "clinical text reached the ranking metadata"
