"""Deterministic feature extraction: keyword triage + conflict detection.

This is the feature-level proof behind 'tekoälyn hyödyntäminen': unstructured
Finnish free-text becomes flags the decision tables can fire on.

Keyword map is the day-1 implementation (PLAN Locked decision #5). The LLM
(von) is a production footnote only -- it has no published Finnish clinical
fine-tune.

Source field names are NOT read here; `source.py` owns them.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from . import assess, source
from .upm import Persona

# Lowercased substrings. Order does not matter; each maps to a boolean flag.
KEYWORDS: dict[str, tuple[str, ...]] = {
    # Stems, not surface forms: "akuutt" hits akuutti/akuutteja, "hengitysvaike"
    # hits the -utta/-uksien cases. "äkilli" is the common prefix of every form
    # (äkillinen, äkillisen, äkillistä, äkillisiä, äkillisesti) -- "äkillis" would
    # miss the nominative/genitive, which end in -nen/-sen, not -s.
    "acute": ("akuutt", "kuume", "päivystys", "infektio", "hengitysvaike", "äkilli"),
    # ponytail: "itsetuho" omitted -- negation ("ei itsetuhoisuutta") makes it a
    # false-positive trap a substring map cannot handle; add with a real NLP pass.
    # "mielentervey" stems: the genitive "mielenterveyden" drops the -s ending.
    "mental_health": ("masennus", "mieliala", "psykiatr", "uupum", "mielentervey", "ahdistus"),
    "social": ("toimeentulo", "sosiaali", "velkaneuvonta", "palvelutarve", "täydentävä"),
    "elder_care": ("kotihoid", "ikäänty", "muistisairau", "kotikäynti"),
    "chronic": ("diabetes", "hypertensio", "astma", "krooninen", "tyypin 2"),
}

# Phrases that mark a free-text note as a discontinuation statement.
STOP_WORDS = ("lopettan", "lopetta", "keskeyt")

# Record types that describe a moment. An old one must not drive today's picture --
# a 2019 note cannot make the situation acute today. Master/state data
# (perustieto, diagnoosi, lääkitys, päätös) carries its own `tila` and is never
# dropped by date; a 2019 diagnosis marked aktiivinen is still a current fact.
TIME_BOUND_TYPES = {source.FREE_TEXT, source.MEASUREMENT, source.LAB_RESULT, source.APPOINTMENT}
RECENT_DAYS = 365


# Finnish negation: the plain "ei", the plural "eivät", the conjunction "eikä",
# plus the copula forms "ei ole" / "eivät ole" (the copula is the one gap word
# the (\s+\w+){0,2} tail admits). Word-bounded, so it never fires on a word
# ending in 'ei'. Shared with the conflict/stop path (_has_stop_statement).
_NEGATION = re.compile(r"\b(?:eikä|eivät|ei)\b(?:\s+\w+){0,2}\s*$")


# How far back a negation is looked for, in characters. Finnish often puts two or
# three words between the negation and the symptom -- "ei ole viime aikoina valittanut
# masennuksesta" is about 30 characters -- so this window is a *ceiling on silence*,
# not a linguistic boundary: a phrase whose negation sits further back than this does
# not fire the flag. 24 is empirical, not derived, and the tradeoff is one-sided: too
# small under-reports a negated symptom, too large lets a distant negation suppress a
# genuine one. Tests pin the behaviour on both sides of the boundary.
NEGATION_WINDOW = 24


def _negated(text: str, idx: int) -> bool:
    """True if the phrase at idx is preceded by a Finnish negation ("ei", "ei ole",
    "eivät", "eivät ole", ...). Word-bounded, so it never fires on a word ending in 'ei'."""
    return bool(_NEGATION.search(text[max(0, idx - NEGATION_WINDOW):idx]))


def _has_stop_statement(text: str) -> bool:
    """A discontinuation statement that is not negated ("ei lopettanut")."""
    for word in STOP_WORDS:
        start = 0
        while (idx := text.find(word, start)) != -1:
            if not _negated(text, idx):
                return True
            start = idx + len(word)
    return False


_BOUNDARY = ".,;\n"


def _clause_bounds(text: str, idx: int) -> tuple[int, int]:
    """The (start, end) of the clause around idx. Binds a stop statement to the
    drug's own clause, so one stopped drug does not mark every other active drug
    as a conflict."""
    start = max(text.rfind(ch, 0, idx) for ch in _BOUNDARY) + 1
    ends = [text.find(ch, idx) for ch in _BOUNDARY]
    end = min((e for e in ends if e != -1), default=len(text))
    return start, end


def _clause(text: str, idx: int) -> str:
    start, end = _clause_bounds(text, idx)
    return text[start:end]


# Master/state record types. They carry a status, not a symptom statement, so a
# negated keyword in one ("ei ole masennusta" as a diagnoosi line) still fires:
# master data is never negation-suppressed. Each master record's record_type
# token sits in the same haystack clause as its values (_searchable_text), and
# the clause check reads it back.
# Each record is scanned independently below, so a later master record cannot
# make a negated free-text clause inherit the master's never-suppress rule.
_MASTER = (source.BASIC_INFO, source.DIAGNOSIS, source.MEDICATION, source.DECISION)


def _master(clause: str) -> bool:
    """A master/state clause, from its whole-word record_type token."""
    for token in _MASTER:
        start = 0
        while (i := clause.find(token, start)) != -1:
            start = i + 1
            before = clause[i - 1] if i else " "
            after = clause[i + len(token)] if i + len(token) < len(clause) else " "
            if not before.isalpha() and not after.isalpha():
                return True
    return False


def _flag_fires(text: str, keyword: str) -> bool:
    """True if the keyword is present in a form that should count.

    Each occurrence must start a word (a stem-prefix hit: "kuumetta" counts,
    but "satekuumeetta" is a substring artifact) and is checked against the
    negation in its *own* clause: "ei kuumetta" does not fire, while
    "ei kuumetta, mutta akuutti vatsakipu" still fires from the second clause.
    Master/state clauses (perustieto/diagnoosi/lääkitys/päätös) are never
    suppressed; a negated symptom in a vapaateksti clause is.
    """
    start = 0
    while (idx := text.find(keyword, start)) != -1:
        start = idx + len(keyword)
        if idx and text[idx - 1].isalpha():
            continue
        clause_start, clause_end = _clause_bounds(text, idx)
        clause = text[clause_start:clause_end]
        if _master(clause):
            return True
        # idx - clause_start: this occurrence's own offset in the clause, not the
        # first occurrence's -- a later, un-negated hit must still fire.
        if not _negated(clause, idx - clause_start):
            return True
    return False


def _is_current(record, today: date) -> bool:
    return source.temporal_status(record, today) == "current"


def _current_records(persona: Persona, today: date) -> list:
    """The records that may describe today: master data plus recent time-bound ones."""
    return [r for r in persona.records if _is_current(r, today)]


def _searchable_text(records: list) -> list[str]:
    """Build one lowercase keyword haystack per record, never across record boundaries."""
    texts: list[str] = []
    for r in records:
        # Structured instruments (arviointi) stay out of the keyword haystack:
        # their item text (e.g. PHQ-9 "uupumus") would pollute the flags and move
        # golden decisions. Their data is rendered via assess.py, not keyword triage.
        if r.record_type == source.ARVIOINTI:
            continue
        parts = [r.source, r.topic, r.record_type, *source.string_values(r)]
        texts.append(" ".join(parts).lower())
    return texts


def _assessment_features(records: list, today: date) -> dict:
    """Current latest completed assessment only; ties/incompletes stay unknown."""
    out: dict[str, Any] = {"phq9_total": None, "phq9_tulkinta": None, "iadl_total": None, "iadl_tulkinta": None}
    for assessment in assess.select_for_picture(records, today):
        if not assessment.valmis or assessment.temporal_status != "current":
            continue
        if assessment.instrumentti == "PHQ-9":
            out["phq9_total"] = assessment.total
            out["phq9_tulkinta"] = assessment.tulkinta
        elif assessment.instrumentti == "IADL":
            out["iadl_total"] = assessment.total
            out["iadl_tulkinta"] = assessment.tulkinta
    return out


def _stem_position(text: str, stem: str) -> int:
    """First occurrence of `stem` that starts a word, or -1.

    The keyword matcher already refuses mid-word hits (`_flag_fires`), and this path needs the
    same rule for the same reason: `find` alone lets a stem match inside a longer word, so a note
    containing "parasetamolidura" would flag plain "parasetamoli" too. A word-start hit is still a
    prefix hit, which is deliberate -- the stem is truncated to survive Finnish case endings.
    """
    start = 0
    while (idx := text.find(stem, start)) != -1:
        if idx == 0 or not text[idx - 1].isalpha():
            return idx
        start = idx + 1
    return -1


def _find_conflicts(records: list) -> list[dict]:
    """An active medication that a recent free-text note says was stopped.

    The one hardcoded-style conflicts link the plan keeps (innovatiivisuus).
    """
    active = [r for r in records if source.is_active_medication(r)]
    notes = [r for r in records if r.record_type == source.FREE_TEXT]
    conflicts: list[dict] = []
    for med in active:
        drug = (source.drug_name(med) or "").lower()
        stem = drug.split("/")[0][:8]  # enough to survive Finnish case endings
        if not stem:
            continue
        for note in notes:
            text = source.note_text(note).lower()
            idx = _stem_position(text, stem)
            if idx != -1 and _has_stop_statement(_clause(text, idx)):
                conflicts.append(
                    {
                        "drug": source.drug_name(med),
                        "medication_record_id": med.record_id,
                        "note_record_id": note.record_id,
                    }
                )
    return conflicts


def extract_features(persona: Persona, today: date | None = None) -> dict:
    today = today or date.today()
    current = _current_records(persona, today)
    texts = _searchable_text(current)

    flags = {name: any(_flag_fires(text, keyword) for text in texts for keyword in keywords)
             for name, keywords in KEYWORDS.items()}

    age = None
    for r in current:
        if r.record_type == source.BASIC_INFO:
            year = source.birth_year(r)
            if year is not None:
                age = today.year - year
                break

    temps = [t for r in current if (t := source.temperature(r)) is not None]
    spo2s = [s for r in current if (s := source.spo2(r)) is not None]
    pressures = [bp for r in current if (bp := source.blood_pressure(r)) is not None]

    missed = sum(d for r in current if (d := source.missed_doses(r)) is not None)

    record_types: dict[str, int] = {}
    for r in current:
        record_types[r.record_type] = record_types.get(r.record_type, 0) + 1

    feats = {
        "persona_id": persona.persona_id,
        "label": persona.label,
        "age": age,
        # how many records this role may see at all. Zero is a legitimate, common
        # outcome of the policy cut, and the decision tables must be able to tell
        # "no elevated risk" apart from "no evidence".
        "visible_record_count": len(persona.records),
        # ... and how many of those can describe today (master data always, notes
        # and measurements only when recent). Zero here means "nothing current to
        # assess", which is also not the same as "no risk".
        "current_record_count": len(current),
        "flags": flags,
        "conflicts": _find_conflicts(current),
        "missed_doses_total": missed,
        "active_medications": sum(1 for r in current if source.is_active_medication(r)),
        "max_temp_c": max(temps) if temps else None,
        "min_spo2": min(spo2s) if spo2s else None,
        "systolic_max": max(s for s, _d in pressures) if pressures else None,
        "diastolic_max": max(d for _s, d in pressures) if pressures else None,
        "record_types": record_types,
    }
    # Assessments are not time-bound in this model, so the already-cut persona
    # records (not the recent-window `current`) are the right source.
    feats.update(_assessment_features(persona.records, today))
    return feats
