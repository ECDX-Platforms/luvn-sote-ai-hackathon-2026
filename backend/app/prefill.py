"""The pre-filled esitiedot: the flagship of the briefing.

Two levels, both from the permitted records only and both read after `policy.cut`.

**Level 1 -- the county's own esitieto structure.** The release writes every clinical note under fixed `## `
headings (`TULOSYY JA ESITIEDOT`, `TERVEYTEEN VAIKUTTAVAT TEKIJÄT`, `RISKITIEDOT`, `AIKAISEMPI HOITO`,
`LÄÄKEHOITO`, `TOIMINTAKYKY`, `FYSIOLOGISET MITTAUKSET`), and the corpus builder carries those sections on the
note (`payload.osiot`) and as records of their own. So the structure a professional expects is filled
deterministically: a heading's items are the note sections in the source's own words, plus the structured
records that belong under it (running medications under `Lääkehoito`, diagnoses under `Aikaisempi hoito`,
measurements under `Fysiologiset mittaukset`), each item citing the record it came from. A heading nothing
permitted answers is `unknown` -- rendered as *Kysy vastaanotolla*, never guessed. No model runs here.

**Level 2 -- the official forms.** From the 14 forms the release publishes, the ones declared for the
encounter's purpose (`FORMS_BY_PURPOSE`; stated honestly: medication follow-up has no official form, so it
gets the related instruments and says so). A deterministic step runs first (`_deterministic_answers`): a
question that names a measurement the newest `mittaus` record carries, or an option that is a recorded
diagnosis by name, is answered by rule, citing its record. Then one strict-schema `gpt-6.1-sol` call per form proposes an answer
per question from the permitted records -- extraction only; the instructions forbid inference -- and every
answered item is then verified by Decision-1 against exactly the records it cites, through the same
per-sentence verifier narration uses (`decision_model.verify_narration`). An item the verifier does not
mark `supported` is downgraded to `unknown`: unverified answers are not shown. When every scored question of
a scored form is answered and verified, the form's own arithmetic (`assess.score_from_form`) gives the total.

Permission is untouched: the model and the verifier see `source.basics_payload(record, "model")` of permitted
records, as narration does, and a proposed answer that cites an id outside the permitted set is dropped before
any verifier call (the rest of the form is unaffected). What is shown for a verified answer is what the VERIFIER
confirmed: its citations are the verifier's `supporting_record_ids`, as narration's are, never the model's claim.
"""

from __future__ import annotations

import json
import logging
import re
import os
import time
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import assess, decision_model, llm, source
from .llm import FreshNarration, NarrationSentence
from .picture import EsitietoItem, EsitietoSection, FormScore, PrefillAnswerView, PrefilledForm
from .policy import Scoped
from .telemetry import record as telemetry_record
from .upm import Record

log = logging.getLogger(__name__)

# Part of the briefings cache key: a cached picture built under another pre-fill version is not this one.
PREFILL_VERSION = "prefill-1"
PREFILL_BUDGET_S = float(os.environ.get("LUVN_PREFILL_BUDGET", "45"))
FORM_GENERATION_BUDGET_S = 20.0
FORM_VERIFY_RESERVE_S = 10.0
MAX_RECORDS_FOR_MODEL = 60
MAX_ITEMS_PER_HEADING = 6
VERIFY_BATCH = 4  # `FreshNarration` carries at most four sentences; the verifier is reused as it is
# An answer is an extraction, not a passage. The verifier sentence is "<question> — vastaus: <answer>" and
# `NarrationSentence.text` is bounded (`llm.MAX_SENTENCE_CHARS`), so the answer is bounded here, below that, with
# room for the longest question; a longer model answer is cut, never allowed to raise inside the pipeline.
MAX_ANSWER_CHARS = 300
MAX_QUESTION_CHARS_IN_SENTENCE = 300

# The county's own structure, as the notes write it. (key, heading, note section) -- the clinical purposes.
CLINICAL_HEADINGS: tuple[tuple[str, str, str | None], ...] = (
    ("tulosyy", "Tulosyy ja esitiedot", "TULOSYY JA ESITIEDOT"),
    ("tekijat", "Terveyteen vaikuttavat tekijät", "TERVEYTEEN VAIKUTTAVAT TEKIJÄT"),
    ("riskitiedot", "Riskitiedot", "RISKITIEDOT"),
    ("aikaisempi_hoito", "Aikaisempi hoito", "AIKAISEMPI HOITO"),
    ("laakehoito", "Lääkehoito", "LÄÄKEHOITO"),
    ("mittaukset", "Fysiologiset mittaukset", "FYSIOLOGISET MITTAUKSET"),
    ("toimintakyky", "Toimintakyky", "TOIMINTAKYKY"),
)
# The service-needs lens sees social records and the functioning section only; its structure is the social one.
SOCIAL_HEADINGS: tuple[tuple[str, str, str | None], ...] = (
    ("palvelutarve", "Palvelutarve", None),
    ("toimintakyky", "Toimintakyky", "TOIMINTAKYKY"),
    ("paatokset", "Päätökset ja palvelut", None),
    ("kotihoito", "Kotihoito", None),
)

# (form name fragment, role, note). `official` is a form the purpose is for; `related` is an instrument the release
# carries that bears on the purpose, with the honest note that no official form exists for it.
NO_MEDICATION_FORM = ("Lääkityksen seurannalle ei ole virallista esitietolomaketta aineistossa; tämä lomake liittyy "
                      "lääkehoidon seurantaan.")
NO_FUNCTIONING_FORM = ("Toimintakyvyn ja palvelutarpeen arviointiin ei ole virallista lomaketta aineistossa (IADL ja RAI "
                       "puuttuvat); mieliala on osa toimintakykyä.")
FORMS_BY_PURPOSE: dict[str, tuple[tuple[str, str, str | None], ...]] = {
    "medication_follow_up": (("AUDIT", "related", NO_MEDICATION_FORM),
                             ("Fagerström", "related", NO_MEDICATION_FORM)),
    "mental_health_assessment": (("PHQ-9", "official", None), ("GAD-7", "official", None),
                                 ("AUDIT", "related", "Alkoholin käyttö liittyy mielialan arviointiin.")),
    "functioning_service_needs": (("PHQ-9", "related", NO_FUNCTIONING_FORM),
                                  ("GAD-7", "related", NO_FUNCTIONING_FORM)),
    "child_health_checkup": (("2v. ikäisen", "official", None), ("5v. ikäisen", "official", None)),
}
# A mood instrument joins the medication lens when a permitted diagnosis is a mood or anxiety code.
MOOD_CODES = ("F32", "F33", "F41")

PREFILL_INSTRUCTIONS = """Olet esitietojen esitäyttäjä. Saat yhden lomakkeen kysymykset ja asiakkaan sallitut kirjaukset.
Tehtäväsi on poimia kuhunkin kysymykseen vastaus VAIN, jos jokin kirjaus sanoo sen suoraan.

Säännöt:
1. Vastaa vain, kun kirjauksen teksti ilmaisee vastauksen sellaisenaan. Älä päättele, älä yleistä, älä arvaa.
   "noin 5 savuketta päivässä" -> 5 on poiminta. "tupakoi" -> määrä ei ole tiedossa: status "unknown".
2. Monivalintakysymyksessä valitse answer_id vain, jos kirjauksen sanamuoto vastaa selvästi juuri sitä vaihtoehtoa.
   Muuten status "unknown" ja answer_id null.
3. Jokainen vastattu kohta nimeää source_record_ids: täsmälleen ne kirjaukset, joista vastaus luettiin (1-4 kpl).
4. answer_text on vastaus asiakkaan omin kirjatuin sanoin tai valitun vaihtoehdon teksti; ei selityksiä.
5. Palauta jokaiselle kysymykselle yksi kohta. Kysymys, jota kirjaukset eivät vastaa, on status "unknown",
   answer_text null, answer_id null, source_record_ids [].
6. Älä koskaan vastaa henkilön nimeä, tunnistetta tai mitään, mikä ei ole kirjauksissa."""


class PrefillItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    question_id: str
    status: Literal["answered", "unknown"]
    answer_text: str | None
    answer_id: str | None
    source_record_ids: list[str] = Field(max_length=4)


class PrefillAnswerSet(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[PrefillItem]


# --- Level 1 -------------------------------------------------------------------------------------------------


MAX_ITEM_CHARS = 600
_MARKDOWN_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_MARKDOWN_BULLET = re.compile(r"^\s*[-*]\s+", re.M)


def _plain(text: str) -> str:
    """A note or decision written as markdown, read as one plain line: headings and bullets become separators,
    nothing is reworded, and a long document is cut with an ellipsis (the record itself is one link away)."""
    stripped = text.strip()
    if not stripped.startswith("#") and "\n- " not in stripped and "\n## " not in stripped:
        return stripped[:MAX_ITEM_CHARS] + ("…" if len(stripped) > MAX_ITEM_CHARS else "")
    lines = []
    for raw in _MARKDOWN_BULLET.sub("", _MARKDOWN_HEADING.sub("", stripped)).splitlines():
        line = raw.strip()
        if line:
            lines.append(line)
    joined = " · ".join(lines)
    return joined[:MAX_ITEM_CHARS] + ("…" if len(joined) > MAX_ITEM_CHARS else "")


def _temporal_status(record: Record, today: date) -> str:
    """`source.temporal_status`, except that a record *recorded* after `today` is `future`, whatever its type.

    The release dates some records after the day they are read (a corpus property, not a fault), and a
    medication or diagnosis takes its status from `tila`, not from its date -- so without this an ended
    diagnosis recorded next month was shown as `aiempi` beside a `kirjattu` date still to come.
    """
    if record.recorded_at.date() > today:
        return "future"
    return source.temporal_status(record, today)


def _item(text: str, records: list[Record], today: date, kind: str = "section") -> EsitietoItem:
    latest = max(records, key=lambda r: r.recorded_at)
    return EsitietoItem(text=_plain(text), source_record_ids=[r.record_id for r in records],
                        recorded_at=latest.recorded_at, temporal_status=_temporal_status(latest, today),
                        kind=kind)  # type: ignore[arg-type]


def _section_items(records: list[Record], section: str | None, today: date) -> list[EsitietoItem]:
    """The note sections under one heading, newest note first, in the source's own words."""
    if section is None:
        return []
    items: list[EsitietoItem] = []
    notes = sorted((r for r in records if r.record_type == source.FREE_TEXT
                    and isinstance(r.payload.get("osiot"), dict) and r.payload["osiot"].get(section)),
                   key=lambda r: r.recorded_at, reverse=True)
    seen: set[str] = set()
    for note in notes:
        text = str(note.payload["osiot"][section]).strip()
        if text and text not in seen:
            seen.add(text)
            items.append(_item(text, [note], today))
    return items


def _medication_items(records: list[Record], today: date) -> list[EsitietoItem]:
    """One line per medication as the source states it; the same drug, dose and status recorded on several
    prescriptions (renewals) is one line citing every record, running medications first."""
    grouped: dict[str, list[Record]] = {}
    for record in sorted((r for r in records if source.is_medication(r)),
                         key=lambda r: (not source.is_active_medication(r), -r.recorded_at.timestamp())):
        payload = record.payload
        if not payload.get("lääke"):
            continue
        parts = [str(payload["lääke"])]
        if payload.get("annos"):
            parts.append(str(payload["annos"]))
        status = payload.get("tila")
        if status:
            parts.append("käytössä" if status == source.MEDICATION_ACTIVE else "päättynyt")
        grouped.setdefault(" · ".join(parts), []).append(record)
    return [_item(text, group, today, kind="medication") for text, group in grouped.items()]


def _diagnosis_items(records: list[Record], today: date) -> list[EsitietoItem]:
    """One line per diagnosis as the source names it; the same code recorded on several contacts is one line
    citing every record, long-term diagnoses first."""
    grouped: dict[str, list[Record]] = {}
    for record in sorted((r for r in records if r.record_type == source.DIAGNOSIS),
                         key=lambda r: (r.payload.get("tila") != "aktiivinen", -r.recorded_at.timestamp())):
        payload = record.payload
        if not payload.get("nimi"):
            continue
        text = str(payload["nimi"]) + (f" ({payload['icd10']})" if payload.get("icd10") else "")
        if payload.get("tila") == "aktiivinen":
            text += " · pitkäaikainen"
        grouped.setdefault(text, []).append(record)
    return [_item(text, group, today, kind="diagnosis") for text, group in grouped.items()]


def _measurement_items(records: list[Record], today: date) -> list[EsitietoItem]:
    labels = (("verenpaine", "Verenpaine", "mmHg"), ("syke", "Syke", "/min"), ("lämpö_c", "Lämpö", "°C"),
              ("spo2", "SpO2", "%"), ("paino_kg", "Paino", "kg"), ("pituus_cm", "Pituus", "cm"))
    items = []
    for record in sorted((r for r in records if r.record_type == source.MEASUREMENT),
                         key=lambda r: r.recorded_at, reverse=True):
        parts = [f"{label} {record.payload[key]} {unit}".replace(".", ",") if isinstance(record.payload.get(key), float)
                 else f"{label} {record.payload[key]} {unit}"
                 for key, label, unit in labels if record.payload.get(key) is not None]
        if parts:
            items.append(_item(", ".join(parts), [record], today, kind="measurement"))
    return items


def _free_text_items(records: list[Record], today: date, *, heading: str | None = None,
                     otsikko: str | None = None, topic: str | None = None, record_type: str | None = None,
                     key: str = "teksti") -> list[EsitietoItem]:
    chosen = []
    for record in records:
        if record_type and record.record_type != record_type:
            continue
        if heading and not (record.heading and record.heading.national == heading):
            continue
        if otsikko and record.payload.get("otsikko") != otsikko:
            continue
        if topic and record.topic != topic:
            continue
        text = record.payload.get(key)
        if isinstance(text, str) and text.strip():
            chosen.append(record)
    chosen.sort(key=lambda r: r.recorded_at, reverse=True)
    seen: set[str] = set()
    items = []
    for record in chosen:
        text = str(record.payload[key]).strip()
        if text not in seen:
            seen.add(text)
            items.append(_item(text, [record], today))
    return items


def _homecare_items(records: list[Record], today: date) -> list[EsitietoItem]:
    visits = sorted((r for r in records if r.record_type == source.APPOINTMENT and r.topic == "kotihoidon palvelu"),
                    key=lambda r: r.recorded_at, reverse=True)
    if not visits:
        return []
    latest = visits[0]
    text = f"{len(visits)} kotihoidon käyntiä, viimeisin {latest.recorded_at.date().strftime('%-d.%-m.%Y')}"
    return [_item(text, visits[:8], today)]


def esitieto_sections(scoped: Scoped, purpose: str | None, today: date) -> list[EsitietoSection]:
    """Level 1: the county's own heading structure, filled from the permitted records, deterministically."""
    records = list(scoped.records)
    sections: list[EsitietoSection] = []
    structure = SOCIAL_HEADINGS if purpose == "functioning_service_needs" else CLINICAL_HEADINGS
    for key, heading, section in structure:
        items = _section_items(records, section, today)
        if key == "laakehoito":
            items = items + _medication_items(records, today)
        elif key == "aikaisempi_hoito":
            items = items + _diagnosis_items(records, today)
        elif key == "mittaukset":
            items = items + _measurement_items(records, today)
        elif key == "riskitiedot":
            items = items + [i for i in _free_text_items(records, today, otsikko="Riskitiedot")
                             if i.text not in {x.text for x in items}]
        elif key == "toimintakyky":
            items = items + [i for i in _free_text_items(records, today, heading="Toimintakyky")
                             if i.text not in {x.text for x in items}]
        elif key == "palvelutarve":
            items = _free_text_items(records, today, record_type=source.FREE_TEXT, topic="palvelutarve")
            items = [i for i in items if "arviointi" in (i.text.casefold() + " ")] or items
        elif key == "paatokset":
            items = _free_text_items(records, today, record_type=source.DECISION, key="päätös")
        elif key == "kotihoito":
            items = _homecare_items(records, today)
        items = items[:MAX_ITEMS_PER_HEADING]
        sections.append(EsitietoSection(key=key, heading=heading, status="answered" if items else "unknown",
                                        items=items))
    return sections


# --- Level 2 -------------------------------------------------------------------------------------------------


def forms_for_purpose(purpose: str | None, records: list[Record]) -> list[tuple[Any, str, str | None]]:
    """The release forms declared for the purpose, resolved against the snapshot; absent forms are skipped."""
    from . import knowledge_forms

    declared = list(FORMS_BY_PURPOSE.get(purpose or "", ()))
    if purpose == "medication_follow_up" and any(
            r.record_type == source.DIAGNOSIS and str(r.payload.get("icd10", "")).startswith(MOOD_CODES)
            for r in records):
        declared.append(("PHQ-9", "related", "Sallituissa kirjauksissa on mieliala- tai ahdistuneisuusdiagnoosi."))
    out = []
    for fragment, role, note in declared:
        form = knowledge_forms.find_form(fragment)
        if form is not None and form.form_id not in {f.form_id for f, _r, _n in out}:
            out.append((form, role, note))
    return out


def _model_records(records: list[Record], today: date) -> list[dict]:
    chosen = sorted(records, key=lambda r: r.recorded_at, reverse=True)[:MAX_RECORDS_FOR_MODEL]
    return [{"record_id": r.record_id, "record_type": r.record_type, "topic": r.topic,
             "recorded_at": r.recorded_at.isoformat(), "temporal_status": source.temporal_status(r, today),
             "payload": source.basics_payload(r, "model")} for r in chosen]


def _form_payload(form) -> dict:
    return {"form_id": form.form_id, "name": form.name_fi,
            "questions": [{"id": q.id, "text": q.text_fi, "type": q.type,
                           "answers": [{"id": a.id, "text": a.text_fi} for a in q.answers]}
                          for q in form.questions if q.type != "heading"]}


# (stem in the question, measurement key, unit): a question that names the measurement is answered from the newest
# `mittaus` record that carries it. Matching is by the stem alone (`pituu` because `pituus` inflects to `pituutesi`);
# no code or synonym mapping.
DETERMINISTIC_MEASUREMENTS: tuple[tuple[str, str, str], ...] = (
    ("paino", "paino_kg", "kg"), ("pituu", "pituus_cm", "cm"), ("verenpaine", "verenpaine", "mmHg"))


def _measurement_text(value: Any, unit: str) -> str:
    text = f"{value} {unit}"
    return text.replace(".", ",") if isinstance(value, float) else text


def _deterministic_answers(form, records: list[Record]) -> dict[str, PrefillItem]:
    """Answers that need no model: a number the records carry as a number, an option that is a recorded diagnosis.

    Two rules only. A `freetext`/`number` question whose text names a measurement (`DETERMINISTIC_MEASUREMENTS`)
    is answered from the newest permitted `mittaus` record that carries that key. A `select`/`multiselect`
    question is answered when exactly one of its options equals, case-insensitively, the `nimi` of a permitted
    diagnosis -- exact text only, no code mapping, and a question with two matching options is left to the
    model, because one `answer_id` cannot carry two choices. Every answer cites the record(s) it was read from
    and then goes through the same verifier batch as a model answer; nothing here is shown unverified.
    """
    measurements = sorted((r for r in records if r.record_type == source.MEASUREMENT),
                          key=lambda r: r.recorded_at, reverse=True)
    diagnoses: dict[str, list[Record]] = {}
    for record in sorted((r for r in records if r.record_type == source.DIAGNOSIS),
                         key=lambda r: r.recorded_at, reverse=True):
        name = record.payload.get("nimi")
        if isinstance(name, str) and name.strip():
            diagnoses.setdefault(name.strip().casefold(), []).append(record)
    out: dict[str, PrefillItem] = {}
    for question in form.questions:
        if question.type in {"freetext", "number"}:
            text = question.text_fi.casefold()
            for stem, key, unit in DETERMINISTIC_MEASUREMENTS:
                if stem not in text:
                    continue
                newest = next((r for r in measurements if r.payload.get(key) is not None), None)
                if newest is not None:
                    out[question.id] = PrefillItem(
                        question_id=question.id, status="answered", answer_id=None,
                        answer_text=_measurement_text(newest.payload[key], unit)[:MAX_ANSWER_CHARS],
                        source_record_ids=[newest.record_id])
                break
        elif question.type in {"select", "multiselect"}:
            matches = [(answer, diagnoses[answer.text_fi.strip().casefold()]) for answer in question.answers
                       if answer.text_fi.strip().casefold() in diagnoses]
            if len(matches) == 1:
                answer, cited = matches[0]
                out[question.id] = PrefillItem(question_id=question.id, status="answered", answer_text=None,
                                               answer_id=answer.id, source_record_ids=[r.record_id for r in cited][:4])
    return out


def _empty_form(form, role: str, note: str | None, status: str, *, model: str | None = None) -> PrefilledForm:
    questions = [q for q in form.questions if q.type != "heading"]
    return PrefilledForm(
        form_id=form.form_id, revision_id=form.revision_id, name=form.name_fi, form_role=role,  # type: ignore[arg-type]
        relation_note=note, source=form.source, source_file=form.source_file, source_sha256=form.source_sha256,
        generation_status=status, generation_model=model, verification_status="not_run",  # type: ignore[arg-type]
        answered=0, verified=0, total_questions=len(questions),
        items=[PrefillAnswerView(question_id=q.id, question_text=q.text_fi, category_name=q.category_name,
                                 question_type=q.type, status="unknown") for q in questions])


async def _prefill_form(form, role: str, note: str | None, scoped: Scoped, today: date,
                        deadline: float) -> PrefilledForm:
    questions = {q.id: q for q in form.questions if q.type != "heading"}
    remaining = deadline - time.monotonic()
    if remaining <= FORM_VERIFY_RESERVE_S + 1.0:
        return _empty_form(form, role, note, "timed_out")
    # The deterministic step first: what the records carry as a number or a named diagnosis needs no model, and
    # a model proposal for the same question is set aside in favour of the one that cites its record by rule.
    deterministic = _deterministic_answers(form, list(scoped.records))
    user = json.dumps({"form": _form_payload(form), "records": _model_records(list(scoped.records), today)},
                      ensure_ascii=False)
    generation_started = time.monotonic()
    try:
        result = await llm._complete_bounded(PrefillAnswerSet, PREFILL_INSTRUCTIONS, user,
                                             min(FORM_GENERATION_BUDGET_S, remaining - FORM_VERIFY_RESERVE_S))
    except TimeoutError:
        telemetry_record("prefill.generation", generation_started, status="timed_out")
        return _empty_form(form, role, note, "timed_out")
    except Exception as exc:  # noqa: BLE001 -- a failed form is an empty form, never a failed briefing
        log.warning("prefill generation failed for %s: %s", form.name_fi, type(exc).__name__)
        telemetry_record("prefill.generation", generation_started, status="failed")
        return _empty_form(form, role, note, "failed")
    if not isinstance(result.output, PrefillAnswerSet):
        # A generator that answered with another shape (a stubbed or misrouted call): nothing is read from it.
        telemetry_record("prefill.generation", generation_started, model=result.model, status="failed")
        return _empty_form(form, role, note, "failed", model=result.model)
    telemetry_record("prefill.generation", generation_started, model=result.model, status="ok",
                     input_tokens=result.input_tokens, output_tokens=result.output_tokens)
    permitted = set(scoped.ids)
    proposed: dict[str, PrefillItem] = dict(deterministic)
    for item in result.output.items:
        question = questions.get(item.question_id)
        if question is None or item.question_id in proposed:
            continue
        if item.status != "answered" or not item.source_record_ids or not set(item.source_record_ids) <= permitted:
            continue
        if item.answer_id is not None and item.answer_id not in {a.id for a in question.answers}:
            continue
        if not item.answer_text and item.answer_id is None:
            continue
        # Bounded at acceptance, so the sentence built for the verifier and the text shown are the same string.
        text = (item.answer_text or "").strip()[:MAX_ANSWER_CHARS] or None
        proposed[item.question_id] = item.model_copy(update={"answer_text": text})

    # Verification: each proposed answer as one sentence against exactly its cited records, in batches of four.
    by_id = {record.record_id: record for record in scoped.records}
    verdicts: dict[str, dict] = {}
    statuses: set[str] = set()
    verify_started = time.monotonic()
    proposals = list(proposed.values())
    for start in range(0, len(proposals), VERIFY_BATCH):
        batch = proposals[start:start + VERIFY_BATCH]
        budget = deadline - time.monotonic()
        if budget <= 0:
            statuses.add("timeout")
            break
        try:
            # Built inside the guard: a sentence the schema refuses (however it came about) is an unverified
            # batch, never an exception that takes the briefing down.
            candidate = FreshNarration(sentences=[
                NarrationSentence(sentence_id=f"s{index + 1}",
                                  text=_verifier_sentence(questions[item.question_id], item),
                                  source_ids=list(dict.fromkeys(item.source_record_ids))[:8])
                for index, item in enumerate(batch)])
            review = await decision_model.verify_narration(scoped, candidate, today, min(budget, FORM_VERIFY_RESERVE_S))
        except Exception as exc:  # noqa: BLE001 -- an unverifiable batch is unverified, never a crash
            log.warning("prefill verification failed for %s: %s", form.name_fi, type(exc).__name__)
            statuses.add("invalid_response")
            continue
        statuses.add(review.get("status", "unknown"))
        for index, item in enumerate(batch):
            verdict = next((v for v in review.get("verdicts", []) if v.get("sentence_id") == f"s{index + 1}"), None)
            if verdict is not None:
                verdicts[item.question_id] = verdict
    if proposals:
        telemetry_record("prefill.verification", verify_started,
                         status="unavailable" if statuses == {"unavailable"} else
                         "complete" if statuses <= {"complete"} else "partial")
    verification_status = ("not_run" if not proposals else "unavailable" if statuses == {"unavailable"}
                           else "complete" if statuses <= {"complete"} else "partial")

    views: list[PrefillAnswerView] = []
    answers_for_score: dict[str, str] = {}
    for question_id, question in questions.items():
        item = proposed.get(question_id)
        verdict = verdicts.get(question_id)
        # What is published is what the verifier confirmed: the citations are its `supporting_record_ids`, drawn
        # from the sentence's own sources (the rule `grounding.accept_supported_candidate` applies to narration).
        # A `supported` verdict with no supporting record is not a verified answer.
        supporting = [r for r in (verdict or {}).get("supporting_record_ids", [])
                      if item is not None and r in item.source_record_ids and r in by_id] if verdict else []
        supported = verdict is not None and verdict.get("verdict") == "supported" and bool(supporting)
        if item is not None and supported:
            cited = [by_id[r] for r in supporting]
            # A future-dated record is not history: `Tarkista` is for an answer read only from old records.
            historical = all(_temporal_status(r, today) == "history" for r in cited)
            text = item.answer_text or _answer_text(question, item.answer_id)
            views.append(PrefillAnswerView(
                question_id=question_id, question_text=question.text_fi, category_name=question.category_name,
                question_type=question.type, status="answered", verified=True, answer_text=text,
                answer_id=item.answer_id, source_record_ids=list(supporting), historical=historical))
            if item.answer_id is not None:
                answers_for_score[question_id] = item.answer_id
        else:
            # Proposed but not verified, or never proposed: the same open question either way, never shown as an answer.
            views.append(PrefillAnswerView(question_id=question_id, question_text=question.text_fi,
                                           category_name=question.category_name, question_type=question.type,
                                           status="unknown"))
    score = assess.score_from_form(form, answers_for_score) if form.is_scored else None
    return PrefilledForm(
        form_id=form.form_id, revision_id=form.revision_id, name=form.name_fi, form_role=role,  # type: ignore[arg-type]
        relation_note=note, source=form.source, source_file=form.source_file, source_sha256=form.source_sha256,
        generation_status="generated", generation_model=result.model,
        verification_status=verification_status,  # type: ignore[arg-type]
        proposed=len(proposals), answered=sum(1 for v in views if v.status == "answered"),
        verified=sum(1 for v in views if v.verified), total_questions=len(questions),
        items=views, score=FormScore(**score) if score else None)


def _answer_text(question, answer_id: str | None) -> str:
    option = next((a for a in question.answers if a.id == answer_id), None) if answer_id else None
    return option.text_fi if option else ""


def _verifier_sentence(question, item: PrefillItem) -> str:
    """The one sentence the verifier judges: the question and the answer, both bounded, as shown."""
    answer = (item.answer_text or _answer_text(question, item.answer_id))[:MAX_ANSWER_CHARS]
    return f"{question.text_fi.strip()[:MAX_QUESTION_CHARS_IN_SENTENCE]} — vastaus: {answer}"


async def prefilled_forms(scoped: Scoped, purpose: str | None, today: date, deadline: float,
                          foundry_enabled: bool = True) -> list[PrefilledForm]:
    """Level 2: every declared form for the purpose, pre-filled and verified, within the budget."""
    forms = forms_for_purpose(purpose, list(scoped.records))
    if not forms:
        return []
    if not foundry_enabled or not llm.foundry_configured():
        return [_empty_form(form, role, note, "unavailable") for form, role, note in forms]
    out = []
    for form, role, note in forms:
        try:
            out.append(await _prefill_form(form, role, note, scoped, today, deadline))
        except Exception as exc:  # noqa: BLE001 -- the last guard: a form that fails for any reason is an open form
            log.warning("prefill failed for %s: %s", form.name_fi, type(exc).__name__)
            out.append(_empty_form(form, role, note, "failed"))
    return out


async def build(scoped: Scoped, purpose: str | None, today: date, *, deadline: float | None = None,
                foundry_enabled: bool = True) -> tuple[list[EsitietoSection], list[PrefilledForm]]:
    """Both levels for one permitted scope. Level 1 never fails; level 2 degrades per form."""
    sections = esitieto_sections(scoped, purpose, today)
    limit = min(deadline, time.monotonic() + PREFILL_BUDGET_S) if deadline else time.monotonic() + PREFILL_BUDGET_S
    forms = await prefilled_forms(scoped, purpose, today, limit, foundry_enabled=foundry_enabled)
    return sections, forms
