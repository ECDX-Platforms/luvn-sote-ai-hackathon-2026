"""The full-throttle candidate pool — every permitted record connected to the encounter.

The owner's directive removed the free-text-only candidate filter and the shortlist cap, and stopped
`selection.apply_decision` discarding a verdict by record type. Four tests were deleted with that behaviour
(they asserted the old budget), and the replacement tests are here, because the rework is only real if a
verdict on a **structured** record actually changes what is selected.

What these pin, in the order the risk runs:

1. a verdict on a structured record **adds** it, naming its type — the test that proves the rework is not inert;
2. a verdict on a structured record that is already baseline **emphasises** it without duplicating it;
3. the pool covers **every** permitted record type, not free text alone;
4. **nothing is truncated** above the old shortlist limit;
5. **nothing withheld reaches the model**, asserted on the exact text that would be sent, together with the
   identity invariant closed in the model-audience seam;
6. the throttle retry budget is a **parameter of the call**, not shared state.
"""

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app import decision_model, selection, source
from app.policy import cut
from app.upm import Persona, Record

TODAY = date(2026, 9, 30)
RECORDED = datetime(2026, 9, 15, tzinfo=timezone.utc)

# Distinctive payloads so a leak can be asserted by content rather than by id: the request carries the
# rendered text, not the record id, so a marker is the only thing that proves what actually crossed the wire.
PERMITTED_MARKER = "PERMITTED-MARKER-4f21"
WITHHELD_MARKER = "WITHHELD-MARKER-9c07"


def record(record_id, record_type, topic="hoito", payload=None, *, days_ago=5,
           scope="treatment", sensitivity="none"):
    return Record(
        record_id=record_id, source="synthetic", record_type=record_type, authored_by_role="laakari",
        recorded_at=RECORDED - timedelta(days=days_ago), topic=topic,
        sensitivity_tag=sensitivity, consent_scope=scope,
        payload=payload if payload is not None else {"teksti": PERMITTED_MARKER},
    )


def medication(record_id="med-1", *, days_ago=5, payload=None):
    return record(record_id, source.MEDICATION,
                  payload=payload or {"lääke": "Metformiini", "tila": source.MEDICATION_ACTIVE,
                                      "annos": "500 mg", "teksti": PERMITTED_MARKER},
                  days_ago=days_ago)


# --- 3. the pool is every permitted type, not free text ------------------------------------------------------

def test_every_permitted_record_type_is_a_candidate():
    """Every permitted type in the read order, each current, must be offered to the model. perustieto is the one
    exception, excluded by design (its model projection is empty), and that exclusion is asserted separately."""
    records = [
        medication("med-1"),
        record("dx-1", source.DIAGNOSIS, payload={"nimi": "Tyypin 2 diabetes", "icd10": "E11.9",
                                                  "tila": "aktiivinen"}),
        record("lab-1", source.LAB_RESULT, payload={"tutkimus": "HbA1c", "arvo": "7.1"}),
        record("note-1", source.FREE_TEXT, payload={"otsikko": "Soitto", "teksti": PERMITTED_MARKER}),
        record("visit-1", source.APPOINTMENT, payload={"kanava": "puhelu"}),
        record("besl-1", source.DECISION, payload={"paatos": "Jatketaan"}),
        record("basic-1", source.BASIC_INFO, topic="demografia",
               payload={"näyttötunniste": "SYN-X", "syntymävuosi": 1950}),
    ]
    scoped = cut(Persona(persona_id="p", label="P", records=records), "laakari", "medication_follow_up")
    candidates = decision_model._candidates(scoped, set(), TODAY)

    offered = {item.record.record_id for item in candidates}
    assert offered == {item.record_id for item in records if item.record_type != source.BASIC_INFO}, (
        "the pool must be every permitted record in the read order, not free text alone")


def test_a_current_structured_record_is_marked_missed_until_the_model_finds_it():
    """The group label is what makes a model-found structured record distinguishable from the baseline."""
    scoped = cut(Persona(persona_id="p", label="P", records=[medication("med-1")]),
                 "sairaanhoitaja", "medication_follow_up")
    assert [c.group for c in decision_model._candidates(scoped, set(), TODAY)] == ["missed"]
    assert [c.group for c in decision_model._candidates(scoped, {"med-1"}, TODAY)] == ["baseline"]


# --- 4. nothing is truncated ---------------------------------------------------------------------------------

def test_nothing_is_truncated_above_the_old_shortlist_limit():
    """The old cap was 80. 120 permitted records must all be candidates, because the data's own maximum is 103
    and the owner removed the budget. A reintroduced cap fails here."""
    records = [record(f"note-{index:03d}", source.FREE_TEXT, payload={"teksti": f"n{index}"})
               for index in range(120)]
    scoped = cut(Persona(persona_id="p", label="P", records=records), "sairaanhoitaja", "medication_follow_up")
    candidates = decision_model._candidates(scoped, set(), TODAY)

    assert len(candidates) == 120, f"expected every one of 120 candidates, got {len(candidates)}"
    assert len(candidates) <= decision_model.SHORTLIST_LIMIT, "the constant must not truncate this fixture"


# --- 5. nothing withheld, and no identity, in the text that would be sent ------------------------------------

def test_no_withheld_record_or_identity_value_can_reach_the_model():
    """The strongest available form of the permission test: the request carries the rendered text rather than
    the record id, so this asserts on the exact text `classify_notes` would put on the wire.

    The premise is asserted too -- if the cut withheld nothing, the test proves nothing and must fail.
    """
    records = [
        record("note-treatment", source.FREE_TEXT, payload={"otsikko": "Hoito", "teksti": PERMITTED_MARKER}),
        # social_care is outside the nurse lens, which admits `treatment` only.
        record("note-social", source.FREE_TEXT, topic="sosiaalipalvelut", scope="social_care",
               payload={"otsikko": "Sosiaali", "teksti": WITHHELD_MARKER}, sensitivity="sensitive"),
        record("basic-1", source.BASIC_INFO, topic="demografia",
               payload={"näyttötunniste": "SYN-IDENTITY-1234", "syntymävuosi": 1950}),
    ]
    persona = Persona(persona_id="p", label="P", records=records)
    scoped = cut(persona, "sairaanhoitaja", "medication_follow_up")

    withheld = {item.record_id for item in records} - set(scoped.ids)
    assert withheld == {"note-social"}, f"the cut must withhold the social record, withheld={withheld}"

    candidates = decision_model._candidates(scoped, set(), TODAY)
    assert "note-social" not in {c.record.record_id for c in candidates}

    texts = [decision_model._candidate_text(c.record, c.temporal_status) for c in candidates]
    assert texts, "the spy must not be vacuous"
    assert any(PERMITTED_MARKER in text for text in texts), "a permitted record must be offered"
    assert all(WITHHELD_MARKER not in text for text in texts), "a withheld record reached the model"
    assert all("SYN-IDENTITY-1234" not in text for text in texts), "an identity value reached the model"


# --- 1 and 2. the crux: a structured verdict changes what is selected ----------------------------------------

def test_a_verdict_on_a_structured_record_adds_it_and_names_its_type():
    """Before the rework `apply_decision` discarded any verdict whose record was not free text, so a model
    finding on a medication, a diagnosis or a lab could never change the outcome. This is that test."""
    scoped = cut(Persona(persona_id="p", label="P", records=[medication("med-1")]),
                 "sairaanhoitaja", "medication_follow_up")
    result = selection.apply_decision(
        scoped, [], {},
        [{"record_id": "med-1", "status": "relevant", "attempted": True}], TODAY)

    assert "med-1" in result.additional_record_ids, "a relevant structured record must be added"
    assert result.reasons["med-1"] == (f"decision:medication_follow_up:relevant:{source.MEDICATION}:current",), (
        "the reason must name the record's type and its temporal status")


def test_a_verdict_on_a_structured_baseline_record_emphasises_it_without_adding_it():
    scoped = cut(Persona(persona_id="p", label="P", records=[medication("med-1")]),
                 "sairaanhoitaja", "medication_follow_up")
    baseline = [item for item in scoped.records if item.record_id == "med-1"]
    result = selection.apply_decision(
        scoped, baseline, {},
        [{"record_id": "med-1", "status": "relevant", "attempted": True}], TODAY)

    assert result.additional_record_ids == (), "a baseline record is emphasised, never added twice"
    assert "med-1" in result.emphasized_record_ids
    assert result.reasons["med-1"] == (f"decision:medication_follow_up:relevant:{source.MEDICATION}:current",)


def test_an_irrelevant_verdict_on_a_structured_record_adds_nothing():
    """The widening must not become a way for an irrelevant read to add a record."""
    scoped = cut(Persona(persona_id="p", label="P", records=[medication("med-1")]),
                 "sairaanhoitaja", "medication_follow_up")
    result = selection.apply_decision(
        scoped, [], {},
        [{"record_id": "med-1", "status": "irrelevant", "attempted": True}], TODAY)

    assert result.additional_record_ids == ()


# --- 6. the retry budget is per call -------------------------------------------------------------------------

def test_the_retry_budget_is_a_parameter_of_the_call_not_shared_state():
    """`_with_throttle_retry.started` used to be a function attribute: with the concurrency this rework needs,
    concurrent calls shared one retry budget. Each call must now carry its own deadline."""
    assert not hasattr(decision_model._with_throttle_retry, "started"), (
        "the module-level retry budget must not come back")

    calls = {"throttled": 0}

    async def flaky():
        calls["throttled"] += 1
        if calls["throttled"] <= 2:
            raise httpx.HTTPStatusError("throttled", request=httpx.Request("POST", "https://x"),
                                        response=httpx.Response(429))
        return {"status": "ok"}

    async def run():
        # Three concurrent calls, each with its own generous deadline: all must succeed independently.
        return await asyncio.gather(*(decision_model._with_throttle_retry(
            flaky, deadline=asyncio.get_running_loop().time() + 5) for _ in range(3)))

    results = asyncio.run(run())
    assert results == [{"status": "ok"}] * 3


def test_a_call_past_its_own_deadline_does_not_retry():
    """A call whose deadline has passed must raise rather than sleep into another call's budget."""
    async def always_throttled():
        raise httpx.HTTPStatusError("throttled", request=httpx.Request("POST", "https://x"),
                                    response=httpx.Response(429))

    async def run():
        return await decision_model._with_throttle_retry(
            always_throttled, deadline=asyncio.get_running_loop().time() - 1)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(run())


# --- L3C: the whole permitted history is a candidate ---------------------------------------------------------
# The candidate carries its temporal status as data; a historical record is never filtered out; a verdict on a
# historical record adds it under a reason that names the status; `no_input` means nothing was assessable.

from app import decision_model as _dm  # noqa: E402  (grouped with the L3C tests it serves)

PURPOSE = "medication_follow_up"


def ended_medication(record_id="med-ended", *, days_ago=400, scope="treatment"):
    return record(record_id, source.MEDICATION, days_ago=days_ago, scope=scope,
                  payload={"lääke": "Simvastatiini", "tila": "lopetettu", "teksti": PERMITTED_MARKER})


def old_note(record_id="note-old", *, days_ago=500, scope="treatment", payload=None):
    return record(record_id, source.FREE_TEXT, days_ago=days_ago, scope=scope,
                  payload=payload or {"otsikko": "Vanha", "teksti": PERMITTED_MARKER})


def judgment(status="relevant", time="current"):
    """A valid Decision-1 envelope. `time` drives the currentness answer the deterministic gate reads."""
    def choice(value, labels):
        return {"type": "choice", "choice": value, "confidence": 0.8,
                "probabilities": {label: (0.8 if label == value else 0.2 / (len(labels) - 1)) for label in labels}}
    urgency = {"type": "score", "score": 0.0, "confidence": 0.8,
               "probabilities": {str(i): (1.0 if i == 0 else 0.0) for i in range(len(_dm.URGENCY_LEVELS))},
               "legend": {str(i): level for i, level in enumerate(_dm.URGENCY_LEVELS)}}
    return json.dumps({"model": _dm.MODEL, "answers": {
        "relevance": choice(status, ("relevant", "irrelevant", "unclear")),
        "time_sensitivity": choice(time, ("current", "historical", "unclear")),
        "subject_binding": choice("person", ("person", "other_person", "unclear")),
        "urgency": urgency}}).encode()


# 1. a historical record is a candidate and carries its status as data
def test_a_historical_record_is_a_candidate_and_carries_its_status():
    scoped = cut(Persona(persona_id="p", label="P", records=[ended_medication(), old_note()]), "laakari", PURPOSE)
    candidates = decision_model._candidates(scoped, set(), TODAY)

    assert [(c.record.record_id, c.group, c.temporal_status) for c in candidates] == [
        ("med-ended", "missed", "history"), ("note-old", "missed", "history")]
    medication_text = json.loads(decision_model._candidate_text(candidates[0].record, candidates[0].temporal_status))
    assert medication_text["ajallisuus"] == "history", "the model must be able to read that the medication ended"
    assert decision_model._candidate_text(candidates[1].record, "history").startswith("ajallisuus: history\n")


# 2. a verdict on a historical record adds it, and the reason names both type and status
def test_a_verdict_on_a_historical_record_adds_it_and_its_reason_names_the_status():
    scoped = cut(Persona(persona_id="p", label="P", records=[ended_medication(), old_note()]), "laakari", PURPOSE)
    result = selection.apply_decision(scoped, [], {}, [
        {"record_id": "med-ended", "status": "relevant", "attempted": True},
        {"record_id": "note-old", "status": "relevant", "attempted": True}], TODAY)

    assert result.additional_record_ids == ("med-ended", "note-old")
    assert result.reasons["med-ended"] == (f"decision:{PURPOSE}:relevant:{source.MEDICATION}:history",)
    assert result.reasons["note-old"] == (f"decision:{PURPOSE}:relevant:{source.FREE_TEXT}:history",)
    assert all(reason.rsplit(":", 1)[-1] == "history" for reasons in result.reasons.values() for reason in reasons), (
        "a historical addition must never be presented as a current fact")


# 3. an encounter whose permitted records are all historical is assessed, not reported no_input
def test_an_encounter_with_only_historical_records_is_assessed_and_not_no_input(monkeypatch):
    scoped = cut(Persona(persona_id="p", label="P", records=[ended_medication(), old_note()]), "laakari", PURPOSE)
    real = httpx.AsyncClient
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    monkeypatch.setattr(decision_model.httpx, "AsyncClient", lambda **kw: real(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=judgment(
            status="relevant", time="historical"), request=request)), **kw))
    rows = asyncio.run(decision_model.classify_notes(scoped, set(), TODAY))

    assert [row["record_id"] for row in rows] == ["med-ended", "note-old"]
    # The model finds the record relevant but reads its content as historical: the deterministic gate, unchanged,
    # refuses an addition on that, so the row is `unclear` and gate-refused. It is assessed, which is the point.
    assert all(row["attempted"] and row["status"] == "unclear" and row["gate_refused"] for row in rows)
    result = selection.apply_decision(scoped, [], {}, rows, TODAY)
    assert result.decision_status != "no_input", "historical records were assessable, so this is not no_input"
    assert result.decision_status == "complete"


# 4. no_input still occurs for an encounter with no assessable permitted record, and only for that
def test_no_input_is_reported_only_when_no_assessable_permitted_record_exists(monkeypatch):
    empty = cut(Persona(persona_id="p", label="P", records=[]), "laakari", PURPOSE)
    basics_only = cut(Persona(persona_id="p", label="P", records=[record(
        "basic-1", source.BASIC_INFO, topic="demografia", payload={"näyttötunniste": "SYN-X", "syntymävuosi": 1950})]),
        "laakari", PURPOSE)
    withheld_only = cut(Persona(persona_id="p", label="P", records=[
        old_note("note-social", scope="social_care")]), "sairaanhoitaja", PURPOSE)
    for scoped in (empty, basics_only, withheld_only):
        assert asyncio.run(decision_model.classify_notes(scoped, set(), TODAY)) == []
        assert selection.apply_decision(scoped, [], {}, [], TODAY).decision_status == "no_input"

    # The contrast that matters: a pool that exists but cannot be assessed is `unavailable`, never `no_input`.
    monkeypatch.delenv("LUNA_FOUNDRY_KEY", raising=False)
    pooled = cut(Persona(persona_id="p", label="P", records=[ended_medication()]), "laakari", PURPOSE)
    rows = asyncio.run(decision_model.classify_notes(pooled, set(), TODAY))
    assert selection.apply_decision(pooled, [], {}, rows, TODAY).decision_status == "unavailable"


# 5. the permission invariant holds for a withheld HISTORICAL record too
def test_a_withheld_historical_record_never_reaches_the_model():
    records = [
        old_note("note-permitted-old", payload={"otsikko": "Hoito", "teksti": PERMITTED_MARKER}),
        old_note("note-withheld-old", scope="social_care",
                 payload={"otsikko": "Sosiaali", "teksti": WITHHELD_MARKER}),
        record("med-withheld-old", source.MEDICATION, days_ago=500, scope="social_care",
               payload={"lääke": "Sertraliini", "tila": "lopetettu", "teksti": WITHHELD_MARKER}),
    ]
    scoped = cut(Persona(persona_id="p", label="P", records=records), "sairaanhoitaja", PURPOSE)
    assert {"note-withheld-old", "med-withheld-old"} <= ({item.record_id for item in records} - set(scoped.ids))

    candidates = decision_model._candidates(scoped, set(), TODAY)
    assert {c.record.record_id for c in candidates} == {"note-permitted-old"}
    texts = [decision_model._candidate_text(c.record, c.temporal_status) for c in candidates]
    assert any(PERMITTED_MARKER in text and "ajallisuus: history" in text for text in texts), (
        "the permitted historical record must be offered, with its status")
    assert all(WITHHELD_MARKER not in text for text in texts), "a withheld historical record reached the model"


# 6. the deterministic order is pinned, with current before history
def test_the_candidate_order_is_type_then_status_then_recency_then_id():
    records = [
        record("med-hist-recent", source.MEDICATION, days_ago=1,
               payload={"lääke": "Simvastatiini", "tila": "lopetettu", "teksti": PERMITTED_MARKER}),
        medication("med-cur", days_ago=5),
        record("dx-1", source.DIAGNOSIS, days_ago=3, payload={"nimi": "X", "tila": "aktiivinen"}),
        old_note("note-z", days_ago=10), old_note("note-a", days_ago=10),
        old_note("note-cur-old", days_ago=300),
        old_note("note-hist-recent", days_ago=400),
        old_note("note-hist-older", days_ago=900),
    ]
    scoped = cut(Persona(persona_id="p", label="P", records=records), "laakari", PURPOSE)
    ordered = [(c.record.record_id, c.temporal_status) for c in decision_model._candidates(scoped, set(), TODAY)]

    assert ordered == [
        ("med-cur", "current"),              # medication first; current before history...
        ("med-hist-recent", "history"),      # ...even though this one is more recent
        ("dx-1", "current"),                 # diagnosis next
        ("note-a", "current"),               # free text: same recency, so record id decides
        ("note-z", "current"),
        ("note-cur-old", "current"),         # then the older current note
        ("note-hist-recent", "history"),     # history after all current notes, most recent first
        ("note-hist-older", "history"),
    ]


# 7. the baseline is immutable: no verdict removes a baseline record
@pytest.mark.parametrize("status", ["irrelevant", "unclear", "unavailable", "invalid_response", "timeout", "relevant"])
def test_no_verdict_on_a_historical_baseline_record_removes_it(status):
    baseline_med = ended_medication("base-med")
    baseline_note = old_note("base-note")
    scoped = cut(Persona(persona_id="p", label="P", records=[baseline_med, baseline_note]), "laakari", PURPOSE)
    base = [item for item in scoped.records]
    reasons = {"base-med": ["context:active-medication"], "base-note": ["keyword:x"]}
    result = selection.apply_decision(scoped, base, reasons, [
        {"record_id": "base-med", "status": status, "attempted": True},
        {"record_id": "base-note", "status": status, "attempted": True}], TODAY)

    assert result.baseline_record_ids == ("base-med", "base-note")
    assert {r.record_id for r in result.records} == {"base-med", "base-note"}
    assert result.additional_record_ids == ()


# 8. perustieto is excluded from the pool, and only from the pool
def test_perustieto_is_permitted_and_baseline_but_never_a_candidate():
    basic = record("basic-1", source.BASIC_INFO, topic="demografia",
                   payload={"näyttötunniste": "SYN-X", "syntymävuosi": 1950})
    scoped = cut(Persona(persona_id="p", label="P", records=[basic, old_note("note-1")]), "laakari", PURPOSE)

    assert "basic-1" in scoped.ids, "perustieto is permitted; the exclusion is the pool's, not the cut's"
    assert [c.record.record_id for c in decision_model._candidates(scoped, {"basic-1"}, TODAY)] == ["note-1"]
    baseline_records, reasons = selection.baseline(scoped, TODAY)
    assert "basic-1" in {r.record_id for r in baseline_records}, "the deterministic baseline keeps perustieto"
    only_basics = cut(Persona(persona_id="p", label="P", records=[basic]), "laakari", PURPOSE)
    assert decision_model._candidates(only_basics, {"basic-1"}, TODAY) == []
