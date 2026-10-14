"""The conversational surface's deterministic half: the turn's identity, the engine arm and the audit replay.

The assistant (`assistant.py`) is the conversational surface: a tool-using agent over the built picture, whose
verified sentences and proposed steps are composed into a `Turn` by `assistant.compose_turn`. This module is what
that composition and the audit trail stand on, and three rules decide everything below:

**The engine arm explains; it never advises, and it needs no model.** `engine_parts` composes the framing lead and
restates the decision's own rule outputs — the risk and situation findings, and the actions table's steps — with
each step's basis visible. With no model configured it is the whole turn, so the surface works, and is
demonstrable, offline. A Käypä hoito citation (`_external_evidence_suggestions`) is attached only beside a verified
sentence that cites the record supporting it, and the guideline's recorded metadata never reaches a model.

**Every turn terminates in a citation, a labelled abstention, or a human review decision.** The refusal is one
byte-identical, cause-neutral string: a refusal that varied with what is withheld would be a differential
oracle over the hidden set.

**A stored turn is replayed, never trusted.** The audit row keeps each statement's class, label, evidence ids and
the digest, and statement text for one class only: a proposed step (`knowledge_advice`). That text is kept so an
idempotent retry can show the same step, and it is replayed only through `replay_advice`, which re-checks every
step's record membership and figures against the picture as it is now; a row that no longer verifies is withheld.

Two things this module deliberately does NOT do:

* It never persists a transcript. A turn's record keeps the question the professional typed, the *ids* its
  statements cited, each statement's class and label, the digest, the actor and the outcome; the verified
  sentence text is never written to the store.
* It never lets the client supply history as evidence. The assistant takes the client's own transcript as model
  context only (`assistant._history_payload`); the thread itself is server-held and addressed by digest.

The record renderers (`_sentence_*`) are kept as the deterministic one-sentence reading of each record type, in the
record's own words and with no payload key name on it. They are pinned by test; the assistant's `get_record` tool
hands the model the record's model projection (`_model_payload`), not these sentences.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence, cast

from pydantic import BaseModel, Field

from . import guidance, source
from .picture import Picture, RecordView
from .text import overlap as _overlap, tokens as _tokens
from .upm import Record

log = logging.getLogger(__name__)

# --- statement classes -------------------------------------------------------------------------------
# The plan's three classes, plus one addition recorded here rather than smuggled in: generated prose is not a
# verbatim source fact, so calling it one would be false. It is `model_summary`, carries the machine-drafted
# label, and is published only when the verifier found it supported by its cited records (ADR-0017's marking
# rule is the same discipline).
STATEMENT_SOURCE_FACT = "source_fact"
STATEMENT_RULE_OUTPUT = "rule_output"
STATEMENT_EXTERNAL_EVIDENCE = "external_evidence"
STATEMENT_MODEL_SUMMARY = "model_summary"
# The framing sentence that makes an answer read as an answer (defect 3). It is its own class and NOT an
# evidence class, because it cites nothing: it names the question and says what follows, and presenting
# framing as evidence is exactly the "statement without evidence" failure `rule_output` already avoids by
# carrying no `source_record_ids` and staying out of `EVIDENCE_CLASSES`. It is not part of `SOURCE_FACT`
# for the same reason a rule output is not: it is composed by this module, not restated from a record.
STATEMENT_ANSWER_LEAD = "answer_lead"
# A proposed step: the assistant's own suggestion of what to ask, check or record (`assistant.compose_turn`), kept as
# a proposal for the professional to accept, edit or dismiss. It cites a record only when it rests on one, so it is NOT
# in `EVIDENCE_CLASSES`: that tuple promises every member cites a record, which a general step legitimately does not.
STATEMENT_KNOWLEDGE_ADVICE = "knowledge_advice"
STATEMENT_CLASSES = (STATEMENT_ANSWER_LEAD, STATEMENT_SOURCE_FACT, STATEMENT_RULE_OUTPUT,
                     STATEMENT_EXTERNAL_EVIDENCE, STATEMENT_MODEL_SUMMARY, STATEMENT_KNOWLEDGE_ADVICE)
EVIDENCE_CLASSES = (STATEMENT_SOURCE_FACT, STATEMENT_EXTERNAL_EVIDENCE, STATEMENT_MODEL_SUMMARY)
KNOWLEDGE_ADVICE_LABEL = "Tietopohjainen ehdotus"

OUTCOME_ANSWER = "answer"
OUTCOME_REFUSAL = "procedural_refusal"

# The three generator states a turn can be in, carried on the wire as `Turn.generator_state`. Before this
# field existed, "no model was configured" and "a model was called and nothing it produced was published"
# both arrived as `generator: None`, so the rail described a called-but-unpublished model arm as an answer
# assembled without a model. State (ii) is deliberately cause-neutral — it covers a verifier rejection, an
# empty model answer, a failed call and a verified sentence the deterministic arm's slots displaced —
# and the wording for each state is the frontend's, not a machine string on the wire.
GENERATOR_NOT_CONFIGURED = "not_configured"
GENERATOR_WITHHELD = "withheld"
GENERATOR_PUBLISHED = "published"
GENERATOR_STATES = (GENERATOR_NOT_CONFIGURED, GENERATOR_WITHHELD, GENERATOR_PUBLISHED)

# One string, one module constant, byte-pinned by test. Cause-neutral: it does not say "not shown", which
# would imply there is something in the picture that is being withheld.
ABSTENTION = "Ei tietoa tässä aineistossa."

REVIEW_ACCEPT = "accept"
REVIEW_EDIT = "edit"
REVIEW_DISMISS = "dismiss"
REVIEW_ACTIONS = (REVIEW_ACCEPT, REVIEW_EDIT, REVIEW_DISMISS)

DIGEST_VERSION = "chat-digest-v1"
MAX_QUESTION_CHARS = 2000
# Five, not four: the deterministic arm now composes a framing lead and can carry a guideline statement
# beside up to three records. At four the guideline statement was displaced by the records whenever a
# question matched three of them, and a designed-but-unreachable class is the same as no class at all. The
# generated summary is still appended last and still truncated away first, which is unchanged.
MAX_SUGGESTIONS = 5
MAX_RESTATEMENT_CHARS = 400
# The lead quotes the professional's own question, so it is bounded separately: the point is one short
# sentence, not a second copy of the question.
MAX_ANSWER_LEAD_CHARS = 160
# The actions table's own status, carried on every published step. It states a claim about `tables/actions.json`'s
# `_meta` ("ILLUSTRATIVE -- synthetic demo policy, pending clinical/legal sign-off"), so it is true of the table
# rather than a caveat invented here. Pinned by a test, so a future row cannot ship without it.
ACTIONS_BASIS_NOTE = "Havainnollistava sääntö, ei kliinisesti kalibroitu eikä hyväksytty käyttöön."
# The table's general row. It is detected by this id in the trace, never by matching the step's text.
ACTIONS_FALLBACK_RULE_ID = "act-yleinen-fallback"
# The label that marks an engine step: a `rule_output` the actions table proposed. The label is the step's identity,
# not its `basis_note`: a turn stored before the note existed has the label and no note, and the rail must still
# recognise it as a step (so it can withhold it). The label is kept by the audit row and `turn_from_record`. The
# risk and situation findings are `rule_output` too, with the plain label, so the statement class alone cannot
# tell a step from a finding.
STEP_LABEL = "Sääntöpohjainen arvio (ehdotus)"
DIGITS = re.compile(r"\d+(?:[.,]\d+)?")
SPACE = re.compile(r"\s+")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def picture_records(picture: Picture) -> list[RecordView]:
    """The built picture's own records, priority first: the only record set this surface may read.

    This is the boundary `CONTRACT` rule 7 draws for a downstream module. A `Scoped` is the whole
    *permitted* set **before** relevance selection narrowed it, and taking the records from one made the
    chat cite records the briefing said were not relevant to this view — the two surfaces disagreed about
    the same encounter while every cited record was still lawful. The picture *is* the built output, and
    these two lists are how it carries the records.
    """
    return [*picture.records.priority, *picture.records.accessible_other]


def _model_projection(record: Record | RecordView) -> dict[str, Any]:
    """The **hash**-facing projection of one picture record -- used only by `content_hash`, below.

    This is deliberately NOT what a model may see; `_model_payload`, defined next, is that. `RecordView.payload`
    is the **display** projection (`RecordView.from_record`), which for a basic-info record carries the name
    and year fields that `source.basics_payload(record, "model")` deliberately returns `{}` for. Hashing the
    display payload would change every basic-info record's `content_hash` and silently redefine what a sealed
    digest means (every stored thread would stop matching), so this reproduces the *old* model projection
    explicitly, frozen, rather than tracking whatever `source.basics_payload(record, "model")` does today.
    `source.IDENTITY_KEYS` stripping landed in `basics_payload` after this hash was first sealed; widening
    what this hashes to match would reseal every stored thread, which is exactly what must not happen (work
    order L2B). An internal digest carrying more than the model actually sees is acceptable; a digest that
    silently changes is not.
    """
    return {} if record.record_type == source.BASIC_INFO else dict(record.payload)


def _model_payload(record: Record | RecordView) -> dict[str, Any]:
    """The one definition of what a model may see, for every record type (work order L2B).

    A thin call to `source.basics_payload(record, "model")` -- the seam that strips `source.IDENTITY_KEYS`
    and returns `{}` for basic-info. Every path that puts a record's payload in front of a model must call
    this (or `basics_payload` directly), never `_model_projection` above and never `record.payload`/
    `RecordView.payload` (the display projection) directly.
    """
    return source.basics_payload(cast(Record, record), "model")


def content_hash(record: Record | RecordView) -> str:
    """The identity of one record's *content*, as it was hashed before the model projection was corrected.

    Hashed over the frozen hash projection (`_model_projection`), not the internal object and not today's
    model projection, so a change that only affects internal fields does not falsely seal a thread, a change
    the model would see does, and the identity-key fix in `source.basics_payload` does not retroactively
    reseal every stored thread.
    """
    projection = {"record_id": record.record_id, "record_type": record.record_type, "topic": record.topic,
                  "recorded_at": record.recorded_at.isoformat(),
                  "payload": _model_projection(record)}
    return hashlib.sha256(json.dumps(projection, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class Digest(BaseModel):
    """A frozen subject. A thread binds to this, never to a live encounter."""

    digest_id: str
    digest_version: str = DIGEST_VERSION
    encounter_id: str | None = None
    persona_id: str
    role: str
    purpose: str | None = None
    policy_version: str
    members: dict[str, str] = Field(default_factory=dict)
    sealed_at: datetime


class Suggestion(BaseModel):
    """One statement on the surface, with its class, its evidence and its label.

    `url` exists only for an `external_evidence` statement, where the reference *is* the link: the guideline's
    recorded URL, the same one the rail renders. It is `None` everywhere else, and the frontend applies its
    own host check before rendering it rather than trusting this field. No other statement carries one,
    because no other statement is a link.
    """

    suggestion_id: str
    turn_id: str
    ordinal: int
    statement_class: str
    text: str
    label: str
    source_record_ids: list[str] = Field(default_factory=list)
    basis: str | None = None
    # A short label taken from the statement's own words, never invented: the cited record's own subject for a
    # `source_fact` (a stored row from the record-restatement era), the engine's own leading clause for a
    # `rule_output` (`_rule_heading`).
    heading: str | None = None
    # The engine's own reason a published step applies, with the table's status appended. Set on every step from
    # the actions module and on no other statement; see `_actions_basis_note`. A step without it is withheld by
    # the rail rather than shown unqualified.
    basis_note: str | None = None
    url: str | None = None
    # The knowledge refs a `knowledge_advice` statement cites, each resolvable through `knowledge_forms`. Empty on
    # every other statement; a retry that dropped it would publish advice with no citation on the second answer.
    refs: list[str] = Field(default_factory=list)
    # The category of the knowledge item a `knowledge_advice` statement cites, for display. Set on that statement only.
    category: str | None = None


class Turn(BaseModel):
    turn_id: str
    digest_id: str
    seq: int
    asked_at: datetime
    outcome: str
    abstention: str | None = None
    suggestions: list[Suggestion] = Field(default_factory=list)
    # `"foundry"` only when a generated statement actually survived into `suggestions`; `generator_state`
    # is the three-way truth the rail needs (see the GENERATOR_* constants above).
    generator: str | None = None
    generator_state: str = GENERATOR_NOT_CONFIGURED
    # `available` is the sealed digest's member count, which is the picture's *selected* records, not the wider
    # permitted set; `cited` is the distinct records this answer's statements cite. They are kept apart on purpose:
    # the rail must not let the larger number read as support for the answer.
    source_count: dict | None = None
    # The assistant's offered follow-up questions (`app/assistant.py`): the professional's to ask, never claims.
    followups: list[str] = Field(default_factory=list)


def seal_digest(picture: Picture, *, policy_version: str, persona_id: str) -> Digest:
    """Seal the built picture's record set into a digest.

    The digest is the subject of the conversation. Because the encounter is recomputed per request, a thread
    without a digest has no stable subject and can silently change person mid-conversation; a digest that no
    longer matches **seals** the thread instead.

    `DIGEST_VERSION` is part of the hashed material, not decoration: a change to the digest scheme must
    change the digest, or two different schemes would be indistinguishable by id.

    `persona_id` is passed in rather than read from the picture, and that is the one thing the picture does
    not carry: it is the audit join key, deliberately kept off the wire, and the digest has to keep binding
    the thread to the person or two people with an identical record set would share a subject. It arrives as
    a primitive string from the composition root — not as a handle — and it cannot widen the record set,
    which is the picture's `records` and nothing else. The hashed material is otherwise unchanged from the
    `Scoped`-based seal, so an already-stored digest still matches the subject it was sealed from.
    """
    members = {record.record_id: content_hash(record) for record in picture_records(picture)}
    material = json.dumps({"digest_version": DIGEST_VERSION, "encounter": picture.encounter_id,
                           "persona": persona_id,
                           "role": picture.role, "purpose": picture.purpose,
                           "policy_version": policy_version, "members": members},
                          ensure_ascii=False, sort_keys=True)
    return Digest(digest_id=hashlib.sha256(material.encode()).hexdigest()[:32],
                  encounter_id=picture.encounter_id,
                  digest_version=DIGEST_VERSION, persona_id=persona_id, role=picture.role,
                  purpose=picture.purpose, policy_version=policy_version, members=members, sealed_at=_now())


def digest_is_current(digest: Digest, picture: Picture, *, policy_version: str, persona_id: str) -> bool:
    """A stale digest seals the thread: the picture's records, the role, the purpose or the rules moved."""
    current = seal_digest(picture, policy_version=policy_version, persona_id=persona_id)
    return (digest.digest_version == DIGEST_VERSION and digest.policy_version == policy_version
            and digest.persona_id == persona_id
            and digest.role == picture.role and digest.purpose == picture.purpose
            and digest.members == current.members)


# --- the record renderer ------------------------------------------------------------------------------
# One record, one Finnish sentence, composed from that record's own values and nothing else.
#
# What this replaced, and why it was a defect rather than a style choice: `_restatement` joined
# `f"{key}: {value}"` pairs with `"; "`, so a professional asking what medications a patient is on read
# `lääkitys: lääke: levotyroksiini; annos: 25 mikrog…` — a database row with the payload's own field names on
# it — and a lab result that merely mentioned `lääkitys` in its free-text interpretation was restated as
# evidence about medications. The sentence now names the thing and states the fact, and no payload key name
# ever reaches the reader.
#
# The renderer is a **pure function of the record**: no `Scoped`, no decision, no wall clock beyond the
# record's own `recorded_at`, which is what makes it testable in isolation and what keeps it on the
# deterministic side of the surface. It returns `None` when the record carries nothing sentence-shaped; the
# caller omits the suggestion rather than emitting a placeholder, because a placeholder is a dump with a
# different shape.

# The one field name that changed spelling under the reader's nose: the screenshot's corpus carried
# `viiteArvo` and the shipped corpus carries `viitearvo`. Lookup is case-insensitive rather than keyed on one
# spelling, so the same source field is one fact on both corpora.
_LOWER_KEYS = ("tutkimus", "arvo", "yksikkö", "viitearvo", "tulkinta", "lääke", "annos", "tila", "nimi",
               "icd10", "teksti", "kirjaaja", "otsikko", "aihe", "kanava", "suunta", "yhteenveto", "päätös",
               "voimassa", "instrumentti", "versio")


def _value(record: Record, key: str) -> Any:
    """One payload value by its name, matched case-insensitively. `None` when absent or empty."""
    payload = record.payload
    if key in payload:
        value = payload[key]
    else:
        wanted = key.lower()
        value = next((item for name, item in payload.items() if name.lower() == wanted), None)
    return value if value not in (None, "", [], {}) else None


def _text(value: Any) -> str:
    """A payload value as plain text. A list reads as a comma-separated phrase, never as JSON."""
    if isinstance(value, list):
        return ", ".join(item for item in (_text(part) for part in value) if item)
    return str(value).strip()


def _capitalised(text: str) -> str:
    for index, character in enumerate(text):
        if character.isalpha():
            return text[:index] + character.upper() + text[index + 1:]
    return text


def _hundredths(value: Any) -> str | None:
    """A 0–100 measurement without a trailing `.0`, so `150.0` reads as `150`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != int(value):
        return str(value)
    return str(int(value))


def _sentence_medication(record: Record) -> str | None:
    drug, dose, status = _text(_value(record, "lääke") or ""), _text(_value(record, "annos") or ""), \
        _text(_value(record, "tila") or "").casefold()
    if not drug and not dose:
        return None
    subject = " ".join(part for part in (drug, dose) if part)
    if status == source.MEDICATION_ACTIVE:
        sentence = f"{subject} on merkitty aktiiviseksi lääkitykseksi."
    elif status:
        sentence = f"{subject} on merkitty lääkitykseksi, jonka tila on {status}."
    else:
        sentence = f"{subject} on merkitty lääkitykseksi."
    # The missed-dose clause only when the record carries a positive count. `puuttuneet_annokset_30d: 0` is
    # not "zero missed doses have been recorded"; saying "annoksia on jäänyt ottamatta 0 kertaa" is noise.
    missed = source.missed_doses(record)
    if missed:
        sentence += f" Annoksia on jäänyt ottamatta {missed} kertaa 30 vuorokauden aikana."
    return _capitalised(sentence)


def _sentence_lab(record: Record) -> str | None:
    name, value, unit = (_text(_value(record, key) or "") for key in ("tutkimus", "arvo", "yksikkö"))
    if not name or not value:
        return None
    reference = _text(_value(record, "viitearvo") or "")
    measured = f"{value} {unit}" if unit else value
    body = f"{name} on {measured}" + (f" (viite {reference})" if reference else "")
    interpretation = _text(_value(record, "tulkinta") or "")
    return f"{_capitalised(body)}" + (f"; kirjattu {interpretation}" if interpretation else "") + "."


def _sentence_diagnosis(record: Record) -> str | None:
    name = _text(_value(record, "nimi") or "")
    if not name:
        return None
    code = _text(_value(record, "icd10") or "")
    status = _text(_value(record, "tila") or "").casefold()
    described = f"{name} ({code})" if code else name
    if status == "aktiivinen":
        return f"{described} on merkitty aktiiviseksi diagnoosiksi."
    return f"{described} on merkitty diagnoosiksi."


def _sentence_note(record: Record) -> str | None:
    """A note is quoted verbatim or not at all. A paraphrase would be this module authoring clinical text."""
    note = _text(_value(record, "teksti") or "")
    if not note:
        return None
    role = _text(_value(record, "kirjaaja") or record.authored_by_role)
    date = record.recorded_at.date()
    quote = '"' + note + '"'
    return _capitalised(f"Kirjauksessa {date.day}.{date.month}.{date.year} ({role}) todetaan: {quote}")


def _sentence_measurement(record: Record) -> str | None:
    """Name only the measurements this record carries, and never a combination it does not."""
    values = {key: _value(record, key) for key in ("verenpaine", "syke", "paino_kg", "pituus_cm")}
    # Compact units when the record is a vitals row; spaced units when it is a height/weight row, where
    # `pituus 148cm` reads like a typo.
    compact = bool(values["verenpaine"] or values["syke"])
    parts: list[str] = []
    # `is not None`, not truthiness: a `verenpaine` of 0 is a value the record carries, and the sentence's own
    # clause for it is emitted on the same test. Checking truthiness here once let a heading (since removed)
    # name a measurement the sentence then omitted.
    if values["verenpaine"] is not None:
        parts.append(f"verenpaine {_text(values['verenpaine'])}")
    if values["syke"] is not None:
        parts.append(f"syke {_text(values['syke'])}")
    if values["paino_kg"] is not None:
        weight = _text(values["paino_kg"])
        parts.append(f"paino {weight}kg" if compact else f"paino {weight} kg")
    if values["pituus_cm"] is not None:
        height = _text(values["pituus_cm"])
        parts.append(f"pituus {height}cm" if compact else f"pituus {height} cm")
    if not parts:
        return None
    described = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " ja " + parts[-1]
    date = record.recorded_at.date()
    return f"{_capitalised(described)} on kirjattu {date.day}.{date.month}.{date.year}."


# Instruments whose own literature publishes a total score, so a total may be stated **when the record
# carries one**. Nothing here is summed: a scale's total that the record does not record is the instrument's
# own figure that this module would be computing, and a figure derived here rather than carried by the record
# is exactly what `_digits_traceable` exists to refuse — so PHQ-9's published total is read from
# `yhteispisteet`/`kokonaispisteet` if it is there, and the sentence is dropped by the ordinary check if the
# item values do not also support it. No total is ever invented from the item sum.
TOTAL_SCORE_INSTRUMENTS = {"phq": 9}
# Every item when the instrument is this small, the highest-scoring ones otherwise, so a long scale does not
# produce a paragraph. Never more than this many item phrases in one sentence.
MAX_ITEM_DETAILS = 6


def _sentence_assessment(record: Record) -> str | None:
    """Name the instrument and its own figures — the item values, and a total only if the record has one."""
    instrument = _text(_value(record, "instrumentti") or "")
    if not instrument:
        return None
    answers = source.assessment_answers(record)
    if not answers:
        return None
    normalised = instrument.casefold().replace("-", "").replace(" ", "")
    expected_items = TOTAL_SCORE_INSTRUMENTS.get(normalised)
    # Deterministic selection: the highest-scoring items first, ties broken by the record's own order, so a
    # long scale's most notable entries survive the cap and truncation is by title, never by chance.
    ordered = sorted(answers.items(), key=lambda item: (-item[1], list(answers).index(item[0])))
    shown = ordered[:MAX_ITEM_DETAILS]
    if len(answers) <= MAX_ITEM_DETAILS:
        shown = list(answers.items())
    items = ", ".join(f"kohta {key} {value}" for key, value in shown)
    if len(shown) < len(answers):
        items += f" (kohteita yhteensä {len(answers)})"
    total = _value(record, "yhteispisteet") or _value(record, "kokonaispisteet")
    if expected_items is not None and len(answers) == expected_items \
            and isinstance(total, int) and not isinstance(total, bool):
        return f"{instrument}-yhteispisteet {total}, ja {items}."
    return f"{instrument}: {items}."
def _sentence_basics(record: Record) -> str | None:
    """Basics are read from the **display** projection, never the model one.

    `source.basics_payload(record, "model")` returns `{}` for basics by design, so a renderer that read the
    model projection would find nothing and fall through to a dump. Which of these fields a role may see is
    not decided here: the record only reaches this function if `policy.cut` already admitted it for this role,
    and the display projection is the same one the briefing's identity header uses.
    """
    fields = source.basics_payload(record, "display")
    parts: list[str] = []
    if fields.get("kotikunta"):
        parts.append(f"kotikunta {fields['kotikunta']}")
    if fields.get("asumismuoto"):
        parts.append(f"asumismuoto {fields['asumismuoto']}")
    return f"Perustiedot: {', '.join(parts)}." if parts else None


def _sentence_appointment(record: Record) -> str | None:
    subject = _text(_value(record, "aihe") or "") or _text(_value(record, "yhteenveto") or "")
    if not subject:
        return None
    context = [part for part in (_text(_value(record, "kanava") or ""),
                                 _text(_value(record, "suunta") or "")) if part]
    date = record.recorded_at.date()
    return (f"Asiointitapahtuma {date.day}.{date.month}.{date.year}"
            f"{f' ({', '.join(context)})' if context else ''}: {subject}.")


def _sentence_decision(record: Record) -> str | None:
    decision = _text(_value(record, "päätös") or "")
    if not decision:
        return None
    validity = _text(_value(record, "voimassa") or "")
    return f"Päätös: {decision}{f' ({validity})' if validity else ''}."


def _sentence_fallback(record: Record) -> str | None:
    """Anything else: a readable clause from the record's own values, never a dump.

    The two most meaningful values, in the record type's own priority order, joined as a phrase. No key name
    is printed, and if the payload carries nothing printable the suggestion is omitted rather than filled.
    """
    candidates: list[str] = []
    for _key, value in record.payload.items():
        if isinstance(value, (dict, list)):
            continue
        rendered = _text(value)
        if rendered and rendered not in candidates:
            candidates.append(rendered)
        if len(candidates) == 2:
            break
    if not candidates:
        return None
    return f"Kirjaus ({record.record_type}): {', '.join(candidates)}."


_RENDERERS = {
    source.MEDICATION: _sentence_medication,
    source.LAB_RESULT: _sentence_lab,
    source.DIAGNOSIS: _sentence_diagnosis,
    source.FREE_TEXT: _sentence_note,
    source.MEASUREMENT: _sentence_measurement,
    source.ARVIOINTI: _sentence_assessment,
    source.BASIC_INFO: _sentence_basics,
    source.APPOINTMENT: _sentence_appointment,
    source.DECISION: _sentence_decision,
}


def _sentence(record: Record) -> str | None:
    """The sentence for one record, or `None` when the record carries nothing sentence-shaped."""
    renderer = _RENDERERS.get(record.record_type, _sentence_fallback)
    try:
        sentence = renderer(record)
    except (TypeError, ValueError):
        # A malformed payload is not a reason to fail a turn, and it is certainly not a reason to emit the
        # payload: an unrenderable record contributes no statement.
        return None
    return sentence[:MAX_RESTATEMENT_CHARS] if sentence else None


def _digits_traceable(text: str, cited: Sequence[Record | RecordView], *, extra: str = "",
                      guideline_metadata: bool = True) -> bool:
    """Every number in a statement must appear in the records it cites.

    This is the deterministic half of the plan's visible-referent rule: a statement may not introduce a figure
    that its own evidence does not carry. It is deliberately crude — it cannot judge meaning — but it cannot be
    prompt-injected either.

    `extra` is further evidence text the caller has already restricted to the statement's own citations: the
    knowledge items an advice sentence cites. It is read exactly like the records, so a figure in a cited item
    is traceable and a figure in neither is not — there is still one digits rule, not a second one.

    The comparison normalises the decimal separator on **both** sides. Normalising only the text dropped a
    sentence that said `1,5` about a record carrying `1.5` (and the reverse), which is a valid answer about a
    decimal record silently killed by number formatting. It fails closed either way — no figure is published
    that its evidence lacks — but a false negative is still a defect.
    """
    haystack = json.dumps([_model_payload(record) for record in cited],
                          ensure_ascii=False).lower()
    # The date in both spellings the surface uses. `recorded_at` serialises as `2026-09-30`, whose year is not
    # a number the extractor can see, while a sentence writes the Finnish `30.9.2026` — and `30.9` extracts as
    # one decimal, not as `30` and `9`. The record carries the day; it carries it in one notation, and both
    # notations are the same fact, so the check is given both rather than the renderer being made to hide the
    # year. This is not a loosening: a date the record does not carry is still absent in both spellings.
    for record in cited:
        day = record.recorded_at.date()
        haystack += f" {day.isoformat()} {day.day}.{day.month}.{day.year}"
    # A guideline statement's figures are its own recorded metadata — the publication date — and they are
    # checked like every other figure: the metadata of a *recorded* guideline that this record supports is
    # added to the haystack, so `julkaistu 21.09.2026` is traceable exactly when a guideline with that
    # publication date is attached to a record the statement cites. An invented publication date has no
    # recorded guideline behind it and is still refused.
    if guideline_metadata:
        haystack += " " + _guideline_metadata_for(cited)
    haystack += " " + extra
    # Extract numbers from the haystack the same way they are extracted from the text, and normalise the
    # separator on both sides. Replacing every comma in the whole haystack would merge a JSON list of
    # separate numbers (`[1, 5]`) into `1.5` and make an invented decimal look traceable.
    available = {value.replace(",", ".") for value in DIGITS.findall(haystack)}
    for value in DIGITS.findall(text or ""):
        if value.replace(",", ".") not in available:
            return False
    return True


def _guideline_metadata_for(cited: Sequence[Record]) -> str:
    """The recorded metadata of every guideline those records support, as a traceability haystack.

    Titles, codes, publication dates and URLs only — the same fields `guidance.links_for` offers the rail and
    the same fields `_external_evidence_suggestions` states. No guideline content is read here, because none
    is stored (see `guidance.py`). The topic test is `guidance.links_for`'s own: a guideline is attached when
    a record mentions one of its topics. It takes records rather than a `Scoped` value because this is a
    property of the citation, and a *withheld* record never reaches here — the caller only ever passes the
    records a statement cites.
    """
    if not cited:
        return ""
    text = " ".join(" ".join([record.topic or "", record.record_type or "", source.note_text(record),
                              " ".join(source.string_values(record))]) for record in cited).casefold()
    return " ".join(f"{item.code} {item.title} {item.published} {item.url}"
                    for item in guidance.GUIDELINES
                    if any(topic in text for topic in item.topics))


def _rule_suggestions(question: str, picture: Picture, decision: Mapping[str, Any], turn_id: str,
                      start: int) -> list[Suggestion]:
    """Restate the decision's own outputs, with the threshold basis visible when it has one.

    The suggested next steps are `picture.actions`: the optional actions module's output, which the core no
    longer produces (`decide` deliberately has no `actions` key). They are the picture's own field, so this
    surface still receives only the built output.

    The findings (the risk and the situation) come first, then the steps, in the table's own order. Nothing is
    capped here: a step truncated in this function would be a step the turn never had.
    """
    tokens = _tokens(question)
    candidates: list[tuple[str, str, str | None, str | None]] = []
    risk = decision.get("risk") or {}
    situation = decision.get("situation") or {}
    actions = picture.actions or []
    asks_about_risk = bool(_overlap(tokens, _tokens("riski risk tilanne tilannekuva arvio")))
    if risk.get("reason") and (asks_about_risk or _overlap(tokens, _tokens(str(risk["reason"])))):
        candidates.append((str(risk["reason"]), "Sääntöpohjainen arvio", risk.get("basis"), None))
    if situation.get("situation_type") and _overlap(tokens, _tokens(str(situation["situation_type"]))):
        candidates.append((str(situation["situation_type"]), "Sääntöpohjainen arvio", None, None))
    # The steps are published whenever the module produced them, whatever the question asks. A keyword gate
    # rewarded the phrasing of a question rather than the situation, and an answer with no steps in it is the
    # dead end this surface exists to avoid. An action is a step only when its rule can be named from the
    # picture's actions trace (O3): otherwise there is no applicability to state, and nothing is published.
    note = _actions_basis_note(picture, decision) if actions else None
    if note:
        candidates.extend((str(action), STEP_LABEL, None, note) for action in actions)
    out: list[Suggestion] = []
    for ordinal, (text, label, basis, basis_note) in enumerate(candidates):
        shown = text[:MAX_RESTATEMENT_CHARS]
        out.append(Suggestion(suggestion_id=f"s{start + ordinal}", turn_id=turn_id, ordinal=start + ordinal,
                              statement_class=STATEMENT_RULE_OUTPUT, text=shown,
                              label=label, basis=basis, heading=_rule_heading(shown), basis_note=basis_note))
    return out


def _actions_entry(picture: Picture, decision: Mapping[str, Any]) -> tuple[str | None, list]:
    """The actions table's `(rule_id, matched)` trace entry, or `(None, [])` when the module left no entry.

    The pipeline appends that entry to the picture's trace, not to the core decision, so the picture is read
    first. The decision's trace is read too, so a caller holding the engine's trace gets the same answer.
    """
    for entry in [*(decision.get("trace") or []), *picture.trace]:
        if isinstance(entry, Mapping):
            table, rule_id, matched = entry.get("table"), entry.get("rule_id"), entry.get("matched")
        else:
            table, rule_id, matched = entry.table, entry.rule_id, entry.matched
        if table == "actions":
            return rule_id, list(matched or [])
    return None, []


def _actions_basis_note(picture: Picture, decision: Mapping[str, Any]) -> str | None:
    """The engine's own reason the steps apply, then the table's status. Every step carries both, or none.

    The reason is worded from the engine's values only: the situation type the picture keyed on, the risk rule's
    own reason when the step is keyed on a risk rule, or the general row's own statement that it is not
    situation-specific. It says which rule applies, never why the step is
    right. `None` when no rule can be named: no actions trace entry, or a situation-keyed entry with no situation.
    A step is an applicable step only with its rule named, so `None` means none of the actions is published.
    """
    rule_id, matched = _actions_entry(picture, decision)
    situation = picture.situation.situation_type
    keyed_on_risk = any(isinstance(item, Mapping) and item.get("field") == "risk_rule_id" for item in matched)
    if rule_id == ACTIONS_FALLBACK_RULE_ID:
        # The general row matched nothing about this person; "Jatka tilanteen seurantaa" beside a question is
        # noise, not a step (owner, 2026-10-14). Nothing is published from it.
        return None
    elif rule_id and keyed_on_risk:
        # The risk rule's own reason, the text the briefing already shows. Nothing is named without it.
        if not picture.risk.reason:
            return None
        reason = f"Riski: {picture.risk.reason}"
    elif rule_id and situation:
        reason = f"Tilanne: {situation}"
    else:
        return None
    return f"{reason}. {ACTIONS_BASIS_NOTE}"


def _is_step(item: Suggestion) -> bool:
    """An engine step, identified by its class and its proposal label, never by its `basis_note`."""
    return item.statement_class == STATEMENT_RULE_OUTPUT and item.label == STEP_LABEL


def _source_count(available: int, suggestions: Sequence[Suggestion], *, knowledge_available: int = 0) -> dict:
    """The selected members (the digest's, i.e. the picture's selected records), beside the records this answer cites.

    `available` is the digest's member count, not the wider permitted set; the rail's caption names that scope.
    The knowledge pair sits beside the record pair and is kept apart from it for the same reason: a count of
    retrieved knowledge items must not read as a count of records. `knowledge_available` is the candidate set this
    turn could advise from; `knowledge_cited` is the distinct refs the answer actually published.
    """
    cited = {record_id for item in suggestions for record_id in item.source_record_ids}
    refs = {ref for item in suggestions for ref in item.refs}
    return {"available": available, "cited": len(cited), "knowledge_available": knowledge_available,
            "knowledge_cited": len(refs)}


def _lead_sentence(question: str) -> str:
    """The framing sentence: name the question asked, and say what follows (defect 3).

    A list of four sentences with no framing does not read as an answer. This is the frame.

    It is **its own statement class and not an evidence class**, following the precedent `rule_output`
    already set for a sentence that cites nothing: it carries no `source_record_ids`, it is not presented as
    a source fact, and the frontend labels it. Attaching the first record's id to it would be a fabricated
    citation — the sentence is not drawn from that record — and calling it a `source_fact` would make the
    same false claim in the class, which is worse. It is deterministic (the question is sanitised and
    truncated by this function, nothing is generated) and it asserts no finding: it names the question and
    says the permitted records follow.

    The question is the professional's own words, which the audit already stores verbatim (`store.add_chat_turn`
    keeps `question`), so quoting it here adds no exposure. Quotes and angle brackets are removed and the
    whole thing is bounded, because this text lands inside a sentence and must not be able to break out of it
    or turn the frame into a paragraph.
    """
    spoken = SPACE.sub(" ", (question or "").replace('"', " ").replace("'", " ").replace("<", " ")
                       .replace(">", " ")).strip()
    if len(spoken) > MAX_ANSWER_LEAD_CHARS:
        spoken = spoken[:MAX_ANSWER_LEAD_CHARS].rstrip() + "…"
    return f'Kysymykseesi "{spoken}" tämän palvelutilanteen sallitut kirjaukset vastaavat näin:'


def _external_evidence_suggestions(question: str, picture: Picture, facts: Sequence[Suggestion], *,
                                   turn_id: str) -> list[Suggestion]:
    """Käypä hoito as a citation: recorded title, publication date and link, and nothing else.

    **Why the guideline arrives as a reference and never as model input.** Duodecim's terms permit linking to
    Käypä hoito in the publisher's prescribed `/hoiNNNNN` form and prohibit using the content to train,
    develop or improve an AI application; `docs/references.md` §6 records both, and `guidance.py` stores codes,
    titles and URLs precisely because of that split. So the recorded metadata is displayed *to the
    professional* and no guideline text ever enters a model payload — the assistant's tools (`assistant.build_tools`)
    hand the model the picture's records and nothing of this metadata, which
    `test_no_guideline_metadata_reaches_the_model_payload` pins. This is a licence boundary, not an oversight:
    do not "improve" the answer by adding guideline content here or by putting a link in the prompt.

    **A guideline is cited by the answer it belongs to.** `guidance.links_for` attaches a guideline when *any*
    record in the picture mentions its topic, which is right for the rail and too loose here: on a medication
    question the hypertension guideline is attached because a blood-pressure measurement is visible, and
    stating it beside two sentences about levotyroksiini is the same irrelevance defect in a new shape. So a
    guideline statement is emitted only beside the answer's own verified sentences (or published model steps),
    and only when the answer belongs to the guideline's topic in one of three ways: a sentence cites the
    visible record that supports the guideline and that record's text names the topic; a sentence cites the
    guideline's own ref (`guideline:hoiNNNNN`, the `search_knowledge` hit the model was handed, carried in
    `Suggestion.refs`); or the question itself names the topic. The first two need a verified fact to hang on;
    the third does not — a professional who asks for the guideline by its subject gets the recorded reference
    even when the model published nothing, because the reference is deterministic metadata cited to the
    post-cut picture's own records, and the engine's steps still stand beside it.

    The statement cites picture records: the cited records that support the guideline when there are any,
    else every picture record that supports it. `guidance.links_for` reads the built picture alone, so a
    guideline whose only supporting record is withheld produces no link, and this statement can never name or
    imply a record the professional does not see.

    The topic test is repeated here over the **cited** records rather than trusting a set of matched record
    ids: it is the identical rule `guidance.links_for` applies (`guidance.GUIDELINES` topics against the
    record's own text), so the same guideline is attached by both paths or by neither.
    """
    supported = {record_id for fact in facts for record_id in fact.source_record_ids}
    cited_refs = {ref for fact in facts for ref in fact.refs}
    records = {record.record_id: record for record in picture_records(picture)}
    asked = (question or "").casefold()
    suggestions: list[Suggestion] = []
    for link in guidance.links_for(picture):
        topics = _guideline_topics(link["code"])
        citing = sorted(set(link["evidence_record_ids"]) & supported)
        # The topic test over the records that actually cite this guideline, not over every record the answer
        # cites: an answer can carry several record sentences and only one of them may mention the guideline's
        # subject.
        mentioning = " ".join(" ".join([record.topic or "", record.record_type or "", source.note_text(record),
                                        " ".join(source.string_values(record))])
                              for record in (records[record_id] for record_id in citing)).casefold()
        by_record = bool(citing) and any(topic in mentioning for topic in topics)
        by_ref = f"{GUIDELINE_REF_PREFIX}{link['code']}" in cited_refs
        by_question = any(topic in asked for topic in topics)
        if not (by_record or by_ref or by_question):
            continue
        citing = citing or sorted(link["evidence_record_ids"])
        text = (f"Käypä hoito -suositus tähän tilanteeseen: {link['title']} "
                f"(julkaistu {link['published']}).")
        # The same traceability discipline as a record sentence: the statement's figures — here the
        # publication date — must be in what it cites. `_digits_traceable` sees the recorded guideline
        # metadata of the cited records, so a guideline whose metadata is recorded passes and an invented
        # publication date does not.
        if not _digits_traceable(text, [records[record_id] for record_id in citing]):
            continue
        suggestions.append(Suggestion(
            suggestion_id="", turn_id=turn_id, ordinal=0, statement_class=STATEMENT_EXTERNAL_EVIDENCE,
            text=text[:MAX_RESTATEMENT_CHARS], label=f"Käypä hoito -suositus · {guidance.CITATION_HOST}",
            source_record_ids=citing, url=link["url"], refs=[f"{GUIDELINE_REF_PREFIX}{link['code']}"]))
    return suggestions


# The ref form a guideline carries in the knowledge index (`knowledge_search.GUIDELINE_PREFIX`); spelled here so
# this module does not import the search seam.
GUIDELINE_REF_PREFIX = "guideline:"


def _guideline_topics(code: str) -> tuple[str, ...]:
    """The recorded topics of one guideline, or `()` for a code that is not in the verified map."""
    return next((item.topics for item in guidance.GUIDELINES if item.code == code), ())


def _renumber(suggestions: Sequence[Suggestion]) -> list[Suggestion]:
    """One contiguous ordinal sequence, ids included, so a caller cannot assemble a gap."""
    for ordinal, suggestion in enumerate(suggestions):
        suggestion.ordinal = ordinal
        suggestion.suggestion_id = f"s{ordinal}"
    return list(suggestions)


def _frame_only(suggestions: Sequence[Suggestion]) -> bool:
    """True when a turn carries nothing but its framing lead, which is a refusal, not an answer.

    The lead is a statement of a class that cites nothing, so it must never be what makes a turn an answer:
    a frame with nothing inside it says nothing, and it is certainly not evidence. A refusal keeps its one
    byte-identical sentence and drops the frame.
    """
    return all(item.statement_class == STATEMENT_ANSWER_LEAD for item in suggestions)


def _rule_heading(text: str) -> str | None:
    """The engine's own leading clause, when it is a non-empty prefix shorter than the whole statement.

    The remainder after the dash must be non-empty too: a reason that is only `"clause — "` would otherwise
    label the row with a clause the row then repeats in full behind a dangling dash.
    """
    clause, separator, rest = text.partition(" — ")
    if separator and clause.strip() and rest.strip() and len(clause) < len(text):
        return clause
    return None


OPEN_QUESTIONS_LABEL = "Esitietojen tila"
OPEN_QUESTIONS_MAX = 6
_OPEN_QUESTIONS_WORDS = _tokens("esitiedot esitieto esitietolomake esitietolomakkeen lomake lomakkeen lomakkeet "
                                "kysymykset kysymystä avoinna avoimet avoin täyttämättä puuttuu puuttuvat")


def _open_question_suggestions(question: str, picture: Picture, turn_id: str, start: int) -> list[Suggestion]:
    """What the pre-fill left open, stated from the picture's own fields when the question asks for it.

    An open heading or an unanswered form question cites no record -- that is what makes it open -- so the
    model's version of this answer can never verify and the question (one the surface itself offers) dead-ended
    in the engine's steps alone. The answer is deterministic: the esitiedot headings whose status is `unknown`
    and, per pre-filled form, the questions still marked open, counted against the form's own total. It is a
    `rule_output` like the engine's findings, carries no record ids, and names nothing withheld: every count is
    of what the briefing renders.
    """
    if not _overlap(_tokens(question), _OPEN_QUESTIONS_WORDS):
        return []
    texts: list[str] = []
    open_headings = [section.heading for section in picture.esitiedot if section.status != "answered"]
    if open_headings:
        texts.append("Esitietojen avoimet otsikot (kysy vastaanotolla): " + "; ".join(open_headings) + ".")
    for form in picture.prefilled_forms:
        pending = [item.question_text for item in form.items if item.status != "answered"]
        if not pending:
            continue
        shown = "; ".join(pending[:OPEN_QUESTIONS_MAX])
        more = f" (ja {len(pending) - OPEN_QUESTIONS_MAX} muuta)" if len(pending) > OPEN_QUESTIONS_MAX else ""
        texts.append(f"{form.name}: {len(pending)} / {len(form.items)} kysymystä avoinna: {shown}{more}.")
    if not texts and (picture.esitiedot or picture.prefilled_forms):
        texts.append("Esitietojen otsikot ja lomakkeiden kysymykset on esitäytetty; avoimia ei ole.")
    return [Suggestion(suggestion_id=f"s{start + i}", turn_id=turn_id, ordinal=start + i,
                       statement_class=STATEMENT_RULE_OUTPUT, text=text[:MAX_RESTATEMENT_CHARS],
                       label=OPEN_QUESTIONS_LABEL, heading=_rule_heading(text))
            for i, text in enumerate(texts)]


def engine_parts(question: str, picture: Picture, decision: Mapping[str, Any], *, turn_id: str):
    """The engine arm of a turn, before any model: the framing lead, the rule findings and the steps.

    Each is its own list so the composer (`assistant.compose_turn`) can place the model's verified sentences
    between the lead and the engine's own output. Nothing is dropped in this function, and nothing here reads
    a record: the lead quotes the question, and the findings and steps are the decision's and the picture's own
    rule outputs (`_rule_suggestions`).
    """
    lead = [Suggestion(suggestion_id="", turn_id=turn_id, ordinal=0, statement_class=STATEMENT_ANSWER_LEAD,
                       text=_lead_sentence(question), label="Kysymys")]
    rules = _rule_suggestions(question, picture, decision, turn_id, 0)
    steps = [item for item in rules if _is_step(item)]
    findings = [*_open_question_suggestions(question, picture, turn_id, len(rules)),
                *(item for item in rules if not _is_step(item))]
    return lead, findings, steps


# --- the picture as verifier evidence ----------------------------------------------------------------
# The assistant (`assistant.py`) is the only model arm. What it needs from here is the adapter below, which hands
# the shared grounding/verifier entry points the built picture's records and nothing wider.


class _PictureEvidence:
    """The picture's own records in the shape the shared grounding/verifier entry points expect.

    `grounding.validate_fresh_candidate` and `decision_model.verify_narration` are shared with the briefing's
    narration path and read only two things from their first argument: `ids` and `records`. This adapter
    carries exactly those, taken from the built picture, and nothing else — no persona, no policy handle, and
    no way to widen the set, because there is no wider set to hand it. It is deliberately not a `Scoped`:
    `chat` does not import that type, and the records it exposes are the picture's, so the chat generator is
    still downstream of selection.
    """

    __slots__ = ("records",)

    def __init__(self, records: Sequence[RecordView]):
        self.records = list(records)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(record.record_id for record in self.records)


@dataclass(frozen=True)
class VerifiedAdvice:
    """One stored proposed step, rebuilt from its audit row and re-checked against the picture (`_reverified`).

    Only `replay_advice` makes one, and only after the check has passed, so `turn_from_record` cannot render a stored
    row that no longer verifies. The proof is a module-private token that only `replay_advice` holds; a hand-built
    instance is refused at construction. This guards honest callers against mistakes, not hostile code running in
    this process.
    """
    sentence_id: str
    text: str
    knowledge_refs: tuple[str, ...]
    source_ids: tuple[str, ...]
    basis_note: str
    # A display label carried through from the stored row; no check reads it.
    category: str | None = None
    _proof: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._proof is not _VERIFIED:
            raise TypeError("VerifiedAdvice is made only by chat.replay_advice, after re-verification")


_VERIFIED = object()


def _reverified(sentence: VerifiedAdvice, picture: Picture) -> bool:
    """Check a stored step against this picture, rather than trusting the proof the sentence carries.

    The same two checks the assistant applies before it publishes a proposed step (`assistant.ask`): every record
    the step cites is in the picture, and every figure in its text is traceable to those records. A step that cites
    nothing passes only when it carries no figure at all.
    """
    permitted = {record.record_id: record for record in picture_records(picture)}
    if any(record_id not in permitted for record_id in sentence.source_ids):
        return False
    cited = [permitted[record_id] for record_id in sentence.source_ids]
    return _digits_traceable(sentence.text, cited)


def _advice_suggestion(sentence: VerifiedAdvice, turn_id: str, *, suggestion_id: str = "", ordinal: int = 0,
                       label: str = KNOWLEDGE_ADVICE_LABEL) -> Suggestion:
    """The statement a re-verified stored step publishes as, under the label and basis note the row carried.

    The label is the stored row's, not a constant: the assistant publishes its steps under its own label
    (`assistant.MODEL_STEP_LABEL`), and a replay that renamed them would show the professional a different
    statement than the one that was stored.
    """
    return Suggestion(suggestion_id=suggestion_id, turn_id=turn_id, ordinal=ordinal,
                      statement_class=STATEMENT_KNOWLEDGE_ADVICE, label=label,
                      text=sentence.text[:MAX_RESTATEMENT_CHARS], basis=None, basis_note=sentence.basis_note,
                      source_record_ids=list(sentence.source_ids), refs=list(sentence.knowledge_refs),
                      category=sentence.category)


def replay_advice(record: Mapping[str, Any], picture: Picture) -> list[VerifiedAdvice]:
    """The stored proposed steps of a turn, rebuilt as `VerifiedAdvice` and re-checked against this picture.

    A stored step row keeps its text, its source record ids, its basis note and its label (see `main._store_turn`).
    That is enough to replay the step, and replaying it is safe only because it is not trusted: every one goes
    through `_reverified`, the same membership and figure checks the assistant applied before it published the step,
    against the picture as it is now. A row whose records are no longer in the picture, or whose figures no longer
    trace to them, fails closed and is withheld. A row stored before the text was kept has nothing to check and is
    withheld too. The module-private proof is minted here only after that check has passed.
    """
    replayed: list[VerifiedAdvice] = []
    for row in record.get("suggestions") or []:
        if row.get("statement_class") != STATEMENT_KNOWLEDGE_ADVICE:
            continue
        text = row.get("text")
        if not isinstance(text, str) or not text.strip():
            log.warning("advice: a stored step has no text to re-verify; withheld on replay")
            continue
        sentence = VerifiedAdvice(
            sentence_id=row["suggestion_id"], text=text, knowledge_refs=tuple(row.get("refs") or ()),
            source_ids=tuple(row.get("source_record_ids") or ()), basis_note=row.get("basis_note") or "",
            category=row.get("category"), _proof=_VERIFIED)
        if _reverified(sentence, picture):
            replayed.append(sentence)
        else:
            log.warning("advice: a stored step failed re-verification on replay; withheld")
    return replayed


def turn_from_record(record: Mapping[str, Any], *, advice: Sequence[VerifiedAdvice] = ()) -> Turn:
    """Rebuild a stored turn's identity for review validation, or as the idempotent response.

    The stored record keeps each statement's id, class, label and evidence ids, and the text of proposed steps only
    (see the module docstring), so the rebuilt suggestions carry empty text, except steps. `advice` is the output of
    `replay_advice`: a stored step renders only when its sentence is in it, so nothing reaches the reply
    that did not re-verify. Only the fields a review needs — the turn
    id, the digest, which suggestion ids exist and each statement's `basis` — are meaningful here. The
    abstention is restored from the outcome, because it is one byte-constant string and a refusal rebuilt
    without it would render as nothing. The `basis` is kept for the same reason: a threshold qualification a
    retry drops would appear on the first answer and silently vanish on the second.
    """
    stored_state = record.get("generator_state")
    if stored_state not in GENERATOR_STATES:
        stored_state = GENERATOR_PUBLISHED if record.get("generator") else GENERATOR_NOT_CONFIGURED
    # A proposed step is rebuilt only from a sentence `replay_advice` has re-verified, under the id and label it was
    # stored with. A stored row with no such sentence is absent: a retry never shows a step that has not passed the check.
    replayed = {sentence.sentence_id: sentence for sentence in advice}
    suggestions: list[Suggestion] = []
    for item in record.get("suggestions") or []:
        if item.get("statement_class") == STATEMENT_KNOWLEDGE_ADVICE:
            sentence = replayed.get(item["suggestion_id"])
            if sentence is not None:
                suggestions.append(_advice_suggestion(sentence, record["turn_id"],
                                                      suggestion_id=item["suggestion_id"], ordinal=item["ordinal"],
                                                      label=item.get("label") or KNOWLEDGE_ADVICE_LABEL))
            continue
        suggestions.append(Suggestion(suggestion_id=item["suggestion_id"], turn_id=record["turn_id"],
                                      ordinal=item["ordinal"], statement_class=item["statement_class"], text="",
                                      label=item["label"], basis=item.get("basis"),
                                      # Restored for the same reason as `basis`: this is the second time the
                                      # retry path has dropped a field the first answer carried, so it is kept
                                      # explicitly rather than assumed to survive.
                                      heading=item.get("heading"),
                                      basis_note=item.get("basis_note"),
                                      source_record_ids=list(item.get("source_record_ids") or []),
                                      url=item.get("url"), refs=list(item.get("refs") or [])))
    # Advice withheld on replay can leave the framing lead alone. That is the refusal the first answer would have been, so
    # the replay says so rather than rendering a frame with nothing inside it.
    outcome, abstention = record["outcome"], ABSTENTION if record["outcome"] == OUTCOME_REFUSAL else None
    if suggestions and _frame_only(suggestions):
        suggestions, outcome, abstention = [], OUTCOME_REFUSAL, ABSTENTION
    # A turn whose generated prose was stored and none of it re-verified now is withheld, not published on the strength of
    # its stored state. A legacy turn with no generated rows keeps the state it always reported.
    stored_generated = any(item.get("statement_class") in (STATEMENT_MODEL_SUMMARY, STATEMENT_KNOWLEDGE_ADVICE)
                           for item in record.get("suggestions") or [])
    survived = any(item.statement_class in (STATEMENT_MODEL_SUMMARY, STATEMENT_KNOWLEDGE_ADVICE) for item in suggestions)
    if stored_state == GENERATOR_PUBLISHED and stored_generated and not survived:
        stored_state = GENERATOR_WITHHELD
    # The stored digest carries the member set, so the available count is recovered the same way the cited count is.
    members = (record.get("digest") or {}).get("members") or {}
    return Turn(
        turn_id=record["turn_id"], digest_id=record["digest_id"], seq=record["seq"],
        asked_at=record["asked_at"], outcome=outcome,
        generator=record.get("generator") if stored_state == GENERATOR_PUBLISHED else None,
        generator_state=stored_state,
        abstention=abstention,
        suggestions=suggestions,
        source_count=_source_count(len(members), suggestions,
                                   knowledge_available=int(record.get("knowledge_available") or 0)))


def review_of(turn: Turn, suggestion_id: str, action: str, actor, *, edited_text: str | None = None) -> dict:
    """Record a decision on one suggestion. Append-only; the actor is required.

    `accept` here means *accept for checking* — the label the surface renders is `Tallennettu palveluun`, never
    "done", because a recorded decision is not a completed clinical action.
    """
    if action not in REVIEW_ACTIONS:
        raise ValueError("unknown review action")
    suggestion = next((item for item in turn.suggestions if item.suggestion_id == suggestion_id), None)
    if suggestion is None:
        raise ValueError("suggestion is not part of this turn")
    if action == REVIEW_EDIT:
        if not edited_text or not edited_text.strip():
            raise ValueError("edited text is required when editing")
    elif edited_text is not None:
        raise ValueError("edited text is only accepted when editing")
    if actor is None or not getattr(actor, "actor_id", None):
        raise ValueError("a review decision requires an authenticated actor")
    return {"turn_id": turn.turn_id, "digest_id": turn.digest_id, "suggestion_id": suggestion_id,
            "action": action, "edited_text": edited_text.strip() if edited_text else None,
            **actor.as_record()}
