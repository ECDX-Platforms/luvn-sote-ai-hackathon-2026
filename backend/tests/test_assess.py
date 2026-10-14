"""Assessment scoring: deterministic, never fabricates, policy-respecting.

Pins the two instrument definitions (totals, bands, incompleteness) and the
policy property: an arviointi on a record the role cannot see yields no
assessment for that role.
"""

from datetime import datetime

from app import source
from app.assess import score
from app.fixtures import load_personas
from app.policy import cut
from app.upm import Record


def _assessment_record(payload: dict, record_id: str = "arviointi-test") -> Record:
    return Record(
        record_id=record_id,
        source="test",
        record_type="arviointi",
        authored_by_role="lääkäri",
        recorded_at=datetime.fromisoformat("2026-09-01T08:00:00+03:00"),
        topic="mielenterveys",  # a declared topic: `Record` now refuses an undeclared one (D6)
        sensitivity_tag="sensitive",
        consent_scope="treatment",
        payload=payload,
    )


def test_phq9_total_and_band():
    rec = _assessment_record(
        {"instrumentti": "PHQ-9", "vastaukset": {"q1": 2, "q2": 3, "q3": 1, "q4": 2, "q5": 1, "q6": 2, "q7": 2, "q8": 2, "q9": 1}}
    )
    a = score(rec)
    assert a.valmis is True
    assert a.total == 16 and a.max_total == 27
    assert a.tulkinta == "keskivaikea–vaikea"
    assert len(a.osiot) == 9
    assert a.lahteet == ["arviointi-test"]


def test_iadl_total_and_band():
    rec = _assessment_record(
        {"instrumentti": "IADL", "vastaukset": {"i1": 1, "i2": 1, "i3": 0, "i4": 1, "i5": 1, "i6": 0, "i7": 0, "i8": 1}}
    )
    a = score(rec)
    assert a.valmis is True
    assert a.total == 5 and a.max_total == 8
    assert a.tulkinta == "lievä avun tarve"
    assert len(a.osiot) == 8


def test_incomplete_instrument_is_not_completed():
    # one declared item missing, one out of range (PHQ-9 values are 0-3)
    rec = _assessment_record(
        {"instrumentti": "PHQ-9", "vastaukset": {"q1": 1, "q3": 1, "q4": 2, "q5": 1, "q6": 2, "q7": 1, "q8": 5, "q9": 0}}
    )
    a = score(rec)
    assert a.valmis is False
    assert a.total is None and a.tulkinta is None
    assert a.max_total == 27
    assert {o.osio for o in a.osiot if o.pisteet is None} == {"q2", "q8"}


def test_unknown_instrument_is_not_completed():
    a = score(_assessment_record({"instrumentti": "GDS-15", "vastaukset": {"g1": 1}}))
    assert a.valmis is False
    assert a.instrumentti == "GDS-15"  # echoed, never replaced with a guess
    assert a.total is None and a.osiot == []


def test_fixtures_carry_the_instruments():
    personas = {p.persona_id: p for p in load_personas()}
    p3 = [r for r in personas["bound-persona-003-mielenterveys-sosiaali"].records if r.record_type == "arviointi"]
    assert len(p3) == 1
    assert score(p3[0]).total == 16
    p4 = [r for r in personas["bound-persona-004-sosiaalipalvelut"].records if r.record_type == "arviointi"]
    # Two records now: the incomplete IADL and the supplied RAI result, which is never scored.
    assert len(p4) == 2
    assert score(p4[0]).valmis is False and score(p4[0]).total is None
    rai = next(r for r in p4 if source.assessment_instrument(r) == "RAI")
    assert score(rai).total is None and score(rai).osiot == []


def test_role_that_cannot_see_the_record_gets_no_assessment():
    """Policy-respecting for free: assessments are built only from the cut set."""
    personas = {p.persona_id: p for p in load_personas()}
    persona = personas["bound-persona-003-mielenterveys-sosiaali"]
    # the sosiaalityöntekija role has only social_care consent; the PHQ-9 record is treatment-scoped
    scoped = cut(persona, "sosiaalityontekija")
    assert [score(r) for r in scoped.records if r.record_type == "arviointi"] == []
    scoped_laakari = cut(persona, "laakari")
    assert [score(r) for r in scoped_laakari.records if r.record_type == "arviointi"]
