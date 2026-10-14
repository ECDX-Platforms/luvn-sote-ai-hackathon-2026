"""Triage time-axis: stale time-bound records must not drive today's picture.

Master/state data (perustieto, diagnoosi, lääkitys) keeps its own `tila` and is
never dropped by date; notes, measurements, labs and contact events are.
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.actions import suggest_actions
from app.decide import decide
from app.policy import cut, table
from app.triage import RECENT_DAYS, extract_features
from app.upm import ConsentScope, Persona, Record

TODAY = date(2026, 9, 29)

def _matched_risk_rule_id(decision: dict) -> str:
    """The risk rule the decision matched, as the actions module now receives it.

    Some step sets are keyed on the core's own conclusion rather than on the two scalars alone, so a test that
    compares the picture's actions against the module has to pass the same rule id the pipeline does. It is read
    from the decision's trace, which is where the core records what it matched.
    """
    return next(entry["rule_id"] for entry in decision["trace"] if entry.get("table") == "risk")



def _rec(rid: str, rtype: str, when: datetime, payload: dict, topic: str = "hoito", scope: ConsentScope = "treatment") -> Record:
    return Record(
        record_id=rid,
        source="test",
        record_type=rtype,
        authored_by_role="physician",
        recorded_at=when,
        topic=topic,
        sensitivity_tag="none",
        consent_scope=scope,
        payload=payload,
    )


def test_stale_free_text_does_not_fire_a_flag():
    old = datetime(2019, 1, 1, tzinfo=timezone.utc)
    recent = datetime(2026, 9, 20, tzinfo=timezone.utc)
    p = Persona(
        persona_id="t",
        label="t",
        records=[
            _rec("n-old", "vapaateksti", old, {"teksti": "akuutti kuume päivystys hengitysvaikeus"}),
            _rec("n-new", "vapaateksti", recent, {"teksti": "potilas voi hyvin"}),
        ],
    )
    f = extract_features(p, TODAY)
    assert f["flags"]["acute"] is False, "a 2019 note must not make today acute"
    assert f["current_record_count"] == 1 and f["visible_record_count"] == 2


def test_old_active_diagnosis_still_counts():
    old = datetime(2019, 4, 14, tzinfo=timezone.utc)
    p = Persona(
        persona_id="t",
        label="t",
        records=[_rec("dx", "diagnoosi", old, {"nimi": "Tyypin 2 diabetes", "tila": "aktiivinen"})],
    )
    f = extract_features(p, TODAY)
    assert f["flags"]["chronic"] is True, "an active diagnosis does not expire by date"
    assert f["current_record_count"] == 1


def test_stale_note_does_not_create_a_conflict():
    active_med = datetime(2026, 9, 1, tzinfo=timezone.utc)
    stale_note = datetime(2020, 1, 1, tzinfo=timezone.utc)
    p = Persona(
        persona_id="t",
        label="t",
        records=[
            _rec("med", "lääkitys", active_med, {"lääke": "amlodipiini", "tila": "aktiivinen"}),
            _rec("n", "vapaateksti", stale_note, {"teksti": "Asiakas lopetti amlodipiinin."}),
        ],
    )
    assert extract_features(p, TODAY)["conflicts"] == []


def test_only_stale_evidence_is_unassessable_not_low_risk():
    """A role whose only visible records are stale gets 'arvioimaton', never
    'matala' -- the same false-reassurance guard as the empty-policy case."""
    stale_note = datetime(2019, 1, 1, tzinfo=timezone.utc)
    p = Persona(
        persona_id="t",
        label="t",
        records=[_rec("n", "vapaateksti", stale_note, {"teksti": "akuutti kuume"})],
    )
    d = decide(cut(p, "laakari"))
    assert d["situation"]["situation_type"] == "ei_arvioitavissa"
    assert d["risk"]["risk_level"] == "arvioimaton"
    assert "ajantasaisia" in d["risk"]["reason"]
    # ADR-0003's consequence: "A test pins the wording (no `matala`, no *"ei kohonneen riskin
    # tunnusmerkkejä"*) at the API seam, so the false reassurance cannot return by enum refactor alone."
    # The `matala` half was pinned; the wording half was not, so this is the pin the record claims. It
    # compares against the catch-all rule's own reason rather than a literal, so capitalising or rewording
    # the table cannot silently retire it — which is exactly how a wording pin rots.
    low_risk_reason = next(rule["output"]["reason"] for rule in table("risk")["rules"]
                           if rule["rule_id"] == "risk-yleinen-matala")
    assert low_risk_reason not in d["risk"]["reason"]


def test_assessments_do_not_feed_the_keyword_haystack():
    """Structured instrument item text would pollute the flags (PHQ-9 "uupumus"),
    so arviointi records are skipped in _searchable_text; flags stay unmoved."""
    recent = datetime(2026, 9, 20, tzinfo=timezone.utc)
    p = Persona(
        persona_id="t",
        label="t",
        records=[
            _rec(
                "a",
                "arviointi",
                recent,
                {"instrumentti": "PHQ-9", "vastaukset": {"q1": 3, "q4": 3}},
                topic="mielenterveys",
            )
        ],
    )
    f = extract_features(p, TODAY)
    assert f["flags"] == {k: False for k in f["flags"]}


def _note_flags(text: str) -> dict:
    recent = datetime(2026, 9, 28, tzinfo=timezone.utc)
    p = Persona(
        persona_id="t",
        label="t",
        records=[_rec("n", "vapaateksti", recent, {"teksti": text})],
    )
    return extract_features(p, TODAY)["flags"]


def test_negated_keyword_does_not_fire_a_flag():
    assert _note_flags("ei kuumetta")["acute"] is False


def test_keyword_match_must_start_a_word():
    # A mid-word occurrence is a substring artifact; the stem-prefix partitive
    # ("kuume" inside "kuumetta") still fires, so the boundary is leading-only.
    assert _note_flags("satekuumetta")["acute"] is False
    assert _note_flags("kuumetta")["acute"] is True


def test_negation_with_copula_does_not_fire_a_flag():
    assert _note_flags("ei ole masennusta")["mental_health"] is False


def test_adverbial_stem_matches_its_inflection():
    # "äkillinen" as a full surface form missed "äkillisesti"/"äkillisiä"; the stem
    # "äkilli" hits the adverb/plural AND keeps the nominative and genitive.
    assert _note_flags("äkillisesti")["acute"] is True
    assert _note_flags("vatsa on äkillisesti kipeytynyt")["acute"] is True
    assert _note_flags("äkillinen hengityksen vinkuminen ja yskänkohtaus")["acute"] is True
    assert _note_flags("äkillisen rintakivun vuoksi")["acute"] is True
    assert _note_flags("oireet eivät ole äkillisiä")["acute"] is False


def test_plural_negation_does_not_fire_a_flag():
    # notes_hard_fi:27: "oireet eivät ole äkillisiä" -- plural copula negation.
    assert _note_flags("eivät ole äkillisiä")["acute"] is False


def test_second_occurrence_uses_its_own_offset():
    # Two occurrences in one clause: the first is negated, the second sits more
    # than two words from the negator, so it fires on its own offset.
    assert _note_flags("ei kuumetta aamulla ja iltapäivällä kuume")["acute"] is True


def test_negation_binds_to_its_own_clause():
    # Clause scope, not sentence: the negation must not bleed across the comma.
    assert _note_flags("ei kuumetta, mutta akuutti vatsakipu")["acute"] is True


def test_master_record_does_not_suppress_note_negation_across_records():
    """The pre-fix commit captured the wrong `acute=True`; each record now scopes its own scan."""
    fixture = json.loads((Path(__file__).parent / "fixtures" /
                          "triage-cross-record-negation.json").read_text(encoding="utf-8"))
    records = [
        _rec(row["record_id"], row["record_type"], datetime.fromisoformat(row["recorded_at"]),
             row["payload"], topic=row["topic"])
        for row in fixture["records"]
    ]
    persona = Persona(persona_id="cross-negation", label="Cross-record negation", records=records)
    today = date.fromisoformat(fixture["today"])
    flags = extract_features(persona, today)["flags"]
    decision = decide(cut(persona))
    expected = fixture["expected_after_fix"]
    assert flags["acute"] is expected["acute"]
    assert decision["situation"]["situation_type"] == expected["situation_type"]
    assert decision["risk"]["risk_level"] == expected["risk_level"]
    # `decide` no longer carries actions; the optional module reproduces them from the
    # core's two outputs, and the fixture's expectation is checked there.
    assert suggest_actions(decision["situation"]["situation_type"],
                           decision["risk"]["risk_level"],
                           _matched_risk_rule_id(decision))[0] == expected["actions"]


def test_master_data_negation_still_fires():
    # Documented exception: a diagnosis line is a status, not a symptom
    # statement, so its negated keyword keeps firing.
    recent = datetime(2026, 9, 28, tzinfo=timezone.utc)
    p = Persona(
        persona_id="t",
        label="t",
        records=[_rec("dx", "diagnoosi", recent, {"nimi": "ei ole masennusta", "tila": "aktiivinen"})],
    )
    assert extract_features(p, TODAY)["flags"]["mental_health"] is True


def test_recent_window_boundary():
    just_inside = datetime(2026, 9, 29, tzinfo=timezone.utc).replace(year=2025)
    p = Persona(
        persona_id="t",
        label="t",
        records=[_rec("n", "vapaateksti", just_inside, {"teksti": "kuume"})],
    )
    f = extract_features(p, TODAY)
    assert f["current_record_count"] == 1
    assert RECENT_DAYS == 365


def test_negation_window_is_bounded_and_named():
    """The negation window is a ceiling on silence, and its edge is pinned on both sides.

    `_negated` looks back a fixed number of characters (`NEGATION_WINDOW`), because Finnish puts
    words between the negation and the symptom. The window is empirical, not derived, so the risk is
    that a future change or a longer phrasing silently flips a flag: a negation just inside the
    window suppresses the flag, a negation just outside it does not. Both sides are asserted here, so
    the boundary is a decision rather than an accident.
    """
    from app.triage import NEGATION_WINDOW, _negated

    assert NEGATION_WINDOW == 24, "the window moved; re-check the phrasing it was chosen against"
    symptom = "masennus"
    # `_negated` reads text[idx - NEGATION_WINDOW : idx], so the negation must end within that slice.
    inside = "ei " + "x" * (NEGATION_WINDOW - 3) + symptom
    outside = "ei " + "x" * (NEGATION_WINDOW + 5) + symptom
    assert _negated(inside, inside.index(symptom)) is True, "a negation inside the window must suppress"
    assert _negated(outside, outside.index(symptom)) is False, (
        "a negation beyond the window must not suppress; if this changed, the ceiling moved"
    )
    # The reviewed counterexample sits outside the window and is therefore NOT suppressed.
    long_phrasing = "ei ole viime aikoina valittanut masennuksesta"
    assert _negated(long_phrasing, long_phrasing.index("masennuksesta")) is False


def test_conflict_matcher_requires_a_word_start_like_the_keyword_matcher():
    """The conflict path must refuse mid-word hits, as `_flag_fires` already does.

    `find` alone matched a truncated stem anywhere, so a longer drug name containing the stem flagged
    the plain drug too -- "parasetamolidura" firing for "parasetamoli". The two matchers in this module
    now apply the same leading-boundary rule.
    """
    from app.triage import _stem_position

    assert _stem_position("lopetin parasetamolin", "paraset") == 8
    assert _stem_position("lopetin parasetamoliduran", "paraset") == 8  # prefix hit is deliberate
    assert _stem_position("aktaniparasetamolia", "paraset") == -1, "a mid-word hit must not match"
    assert _stem_position("ei mainintaa", "paraset") == -1


def _pressure(rid: str, payload: dict, when: datetime = datetime(2026, 9, 20, tzinfo=timezone.utc)) -> Record:
    return _rec(rid, "mittaus", when, payload, topic="verenpaine")


def test_blood_pressure_maxima_are_taken_over_current_records_only():
    """The maxima are per field over the current records; a stale 190/110 must not count."""
    stale = datetime(2019, 1, 1, tzinfo=timezone.utc)
    p = Persona(persona_id="t", label="t", records=[
        _pressure("bp-a", {"verenpaine": "120/95"}),
        _pressure("bp-b", {"verenpaine": "158/80"}),
        _pressure("bp-old", {"verenpaine": "190/110"}, when=stale),
    ])
    feats = extract_features(p, TODAY)
    assert (feats["systolic_max"], feats["diastolic_max"]) == (158, 95), "per-field maxima, current records only"


@pytest.mark.parametrize("raw", ["korkea", "158/", "", "1a8/92", "158/92/1", "158", 158])
def test_a_malformed_blood_pressure_is_absent_and_does_not_raise(raw):
    """A value the system cannot parse is not evidence: no guess, and feature extraction still completes."""
    p = Persona(persona_id="t", label="t", records=[_pressure("bp", {"verenpaine": raw})])
    feats = extract_features(p, TODAY)
    assert feats["systolic_max"] is None and feats["diastolic_max"] is None


def test_a_missing_blood_pressure_is_absent_and_a_valid_one_beside_it_still_counts():
    p = Persona(persona_id="t", label="t", records=[
        _pressure("no-bp", {"syke": 78}),
        _pressure("bad", {"verenpaine": "korkea"}),
        _pressure("ok", {"verenpaine": "120/80"}),
    ])
    feats = extract_features(p, TODAY)
    assert (feats["systolic_max"], feats["diastolic_max"]) == (120, 80)
