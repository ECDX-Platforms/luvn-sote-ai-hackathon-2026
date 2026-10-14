"""Assessment totals drive the decision.

Completed PHQ-9/IADL scores escalate risk; an incomplete instrument does not;
a role that cannot see the record cannot use it (policy before rank); and an
IADL at the band edge that no rule covers does not escalate.
"""

from datetime import date, datetime

from app.actions import suggest_actions
from app.decide import decide
from app.fixtures import load_personas
from app.policy import cut
from app.triage import extract_features
from app.upm import ConsentScope, Persona, Record

TODAY = date(2026, 9, 29)
PERSONAS = {p.persona_id: p for p in load_personas()}

# persona-003's PHQ-9 vector: total 16
PHQ9_VAIKEA = {"q1": 2, "q2": 3, "q3": 1, "q4": 2, "q5": 1, "q6": 2, "q7": 2, "q8": 2, "q9": 1}
PHQ9_KESKIVAIKEA = {"q1": 2, "q2": 2, "q3": 1, "q4": 2, "q5": 1, "q6": 1, "q7": 2, "q8": 1, "q9": 0}  # total 12
IADL_MERKITTAVA = {"i1": 1, "i2": 1, "i3": 0, "i4": 0, "i5": 0, "i6": 0, "i7": 0, "i8": 0}  # total 2
IADL_KOHTALAINEN = {"i1": 1, "i2": 1, "i3": 1, "i4": 1, "i5": 0, "i6": 0, "i7": 0, "i8": 0}  # total 4


def _rec(rid: str, rtype: str, payload: dict, scope: ConsentScope = "treatment",
         topic: str = "mielenterveys") -> Record:
    return Record(
        record_id=rid,
        source="test",
        record_type=rtype,
        authored_by_role="lääkäri",
        recorded_at=datetime.fromisoformat("2026-09-15T08:00:00+03:00"),
        topic=topic,
        sensitivity_tag="sensitive",
        consent_scope=scope,
        payload=payload,
    )


def _persona(records: list) -> Persona:
    return Persona(persona_id="t", label="t", records=records)


def _rule_id(decision: dict, table_name: str) -> str:
    return [t for t in decision["trace"] if t["table"] == table_name][0]["rule_id"]


def test_completed_phq9_16_escalates_risk_and_actions():
    p = _persona(
        [
            _rec("n", "vapaateksti", {"otsikko": "Seuranta", "teksti": "Masennusoireet jatkuvat"}, topic="mielenterveys"),
            _rec("a", "arviointi", {"instrumentti": "PHQ-9", "vastaukset": PHQ9_VAIKEA}, topic="mielenterveys"),
        ]
    )
    d = decide(cut(p, "laakari"))
    assert d["features"]["phq9_total"] == 16
    assert _rule_id(d, "risk") == "risk-phq9-vaikea"
    assert d["risk"]["risk_level"] == "korkea"
    assert suggest_actions("mielenterveys", "korkea")[1]["rule_id"] == "act-mielenterveys-korkea"


def test_completed_iadl_2_escalates_risk():
    p = _persona([_rec("a", "arviointi", {"instrumentti": "IADL", "vastaukset": IADL_MERKITTAVA})])
    d = decide(cut(p, "laakari"))
    assert d["features"]["iadl_total"] == 2
    assert _rule_id(d, "risk") == "risk-iadl-merkittava"
    assert d["risk"]["risk_level"] == "korkea"


def test_midband_scores_give_kohtalainen_not_korkea():
    p = _persona(
        [
            _rec("a1", "arviointi", {"instrumentti": "PHQ-9", "vastaukset": PHQ9_KESKIVAIKEA}),
            _rec("a2", "arviointi", {"instrumentti": "IADL", "vastaukset": IADL_KOHTALAINEN}),
        ]
    )
    d = decide(cut(p, "laakari"))
    assert d["features"]["phq9_total"] == 12
    assert d["features"]["iadl_total"] == 4
    assert d["risk"]["risk_level"] == "kohtalainen"


def test_incomplete_instrument_does_not_fire():
    # q2 missing and q8 out of range -> valmis is False
    answers = {c: v for c, v in PHQ9_VAIKEA.items() if c != "q2"}
    answers["q8"] = 7
    p = _persona(
        [
            _rec("demo", "perustieto", {"syntymävuosi": 1990}, scope="treatment", topic="demografia"),
            _rec("a", "arviointi", {"instrumentti": "PHQ-9", "vastaukset": answers}, topic="mielenterveys"),
        ]
    )
    d = decide(cut(p, "laakari"))
    assert d["features"]["phq9_total"] is None
    assert d["risk"]["risk_level"] == "matala"


def test_role_that_cannot_see_the_assessment_does_not_escalate():
    """Policy before rank: the sosiaalityöntekija cut drops the treatment-scoped
    PHQ-9 record, so the feature is None and the risk level does not move."""
    d = decide(cut(PERSONAS["bound-persona-003-mielenterveys-sosiaali"], "sosiaalityontekija"))
    assert d["features"]["phq9_total"] is None
    assert d["risk"]["risk_level"] == "matala"


def test_laakari_sees_the_completed_phq9_feature():
    f = extract_features(cut(PERSONAS["bound-persona-003-mielenterveys-sosiaali"], "laakari").persona, TODAY)
    assert f["phq9_total"] == 16
    assert f["iadl_total"] is None  # persona-003 has no IADL record
