"""Structured assessments (PHQ-9, IADL): deterministic scoring of record payloads.

The challenge invites structured instruments; this is the read-only layer that
renders whatever the source records already contain. Pure functions, no model:
The shadow reader and the LLM never touch assessment values, and nothing here decides --
the professional decides.

Policy-respecting for free: the pipeline calls `score` only on `scoped.records`
(the already-cut set), so a role that cannot see a record cannot see its
assessment. This module never imports policy.

Band ranges are illustrative, like the tables/_meta convention:
"havainnollistava tulkinta, ei kliininen raja-arvo".

PHQ-9 band cut-points (0–4, 5–9, 10–14, 15–19, 20–27) source:
Kroenke K, Spitzer RL, Williams JB. The PHQ-9: validity of a brief depression severity measure.
J Gen Intern Med 2001;16(9):606-13. PMID 11556941.
Caveat: these published thresholds are not calibrated on Finnish data.

IADL band cut-points (0–2, 3–4, 5–7, 8):
The band source is not recorded and the bands are illustrative.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from . import source
from .picture import Assessment, AssessmentItem
from .upm import Record


@dataclass(frozen=True)
class InstrumentSpec:
    """One instrument: its declared items, per-item range, and band labels."""

    name: str
    # Ordered item code -> Finnish item text. Order is the display order.
    items: dict[str, str]
    per_item_max: int
    # (lo, hi, label) ascending; the first band whose range contains the total wins.
    bands: tuple[tuple[int, int, str], ...]

    @property
    def max_total(self) -> int:
        return self.per_item_max * len(self.items)


INSTRUMENTS: dict[str, InstrumentSpec] = {
    "PHQ-9": InstrumentSpec(
        name="PHQ-9",
        items={
            "q1": "Vähäinen kiinnostus tai mielihyvä asioihin, joista tavallisesti nauttii",
            "q2": "Masentunut, alakuloinen tai toivoton olo",
            "q3": "Nukahtamisvaikeudet, katkonainen uni tai liikaunisuus",
            "q4": "Väsymys tai vähäinen energia",
            "q5": "Ruokahalun heikkeneminen tai liiallinen syöminen",
            "q6": "Huono itsetunto, tunne epäonnistumisesta tai pettymyksestä itseensä",
            "q7": "Keskittymisvaikeudet",
            "q8": "Hidasliikkeisyys tai levottomuus, jonka muutkin ovat huomanneet",
            "q9": "Ajatukset kuolemasta tai itsensä vahingoittamisesta",
        },
        per_item_max=3,
        bands=(
            (0, 4, "minimaalinen"),
            (5, 9, "lievä"),
            (10, 14, "keskivaikea"),
            (15, 19, "keskivaikea–vaikea"),
            (20, 27, "vaikea"),
        ),
    ),
    "IADL": InstrumentSpec(
        name="IADL",
        items={
            "i1": "Ostosten hoitaminen",
            "i2": "Puhelimen käyttö",
            "i3": "Laskujen ja talouden hoitaminen",
            "i4": "Ajanvarausten ja asiointien hoitaminen",
            "i5": "Liikkumisen järjestäminen (kuljetukset)",
            "i6": "Lääkkeiden oikominen ja ottaminen",
            "i7": "Pieni kodin ylläpito (pyykki, siivous, tiskaus)",
            "i8": "Aterioiden valmistaminen",
        },
        # 0 = tarvitsee apua, 1 = itsenäinen; higher total = more independent
        per_item_max=1,
        bands=(
            (0, 2, "merkittävä avun tarve"),
            (3, 4, "kohtalainen avun tarve"),
            (5, 7, "lievä avun tarve"),
            (8, 8, "itsenäinen"),
        ),
    ),
}


def score_from_form(form, answers: dict[str, str]) -> dict | None:
    """Sum a form's own points over the given answers (question id -> answer id). Never invents an answer.

    Returns None unless EVERY scored question of the form has an answer that the form itself gives points
    for, and the form declares a score calculation. The result carries the total, the form's declared range,
    and the publisher's own summary for that total when the form carries one -- an instrument fact in the
    form's words, not a severity authored here. The computation is this system's own and uncertified.
    """
    if not getattr(form, "is_scored", False):
        return None
    total = 0
    for question in form.scored_questions:
        answer_id = answers.get(question.id)
        option = next((a for a in question.answers if a.id == answer_id), None) if answer_id else None
        if option is None or option.points is None:
            return None
        total += option.points
    # The total is the sum over EVERY scored question, so the calculation it is reported against must be the one
    # whose declared range covers that sum. A form with several calculations (ADHD-RS declares per-subscale ranges)
    # has no single calculation for the whole; then the maximum is derived from the questions' own points and no
    # publisher title or summary is borrowed from a subscale.
    derived_max = sum(max((a.points for a in q.answers if a.points is not None), default=0)
                      for q in form.scored_questions)
    calculation = next((c for c in form.score_calculations if c.max == derived_max), None)
    if calculation is None:
        return {"total": total, "min": 0, "max": derived_max, "title": "Yhteispisteet", "summary": None,
                "function": "sum",
                "basis": "Lomakkeen omista pisteistä laskettu summa; lomake ei määrittele yhtä kokonaisasteikkoa. "
                         "Laskenta on järjestelmän oma eikä sertifioitu."}
    summary = next((s.get("text_fi") for s in calculation.summaries
                    if isinstance(s.get("min"), int) and isinstance(s.get("max"), int)
                    and s["min"] <= total <= s["max"] and s.get("text_fi")), None)
    return {"total": total, "min": calculation.min if calculation.min is not None else 0, "max": derived_max,
            "title": calculation.title_fi, "summary": summary, "function": calculation.function,
            "basis": "Lomakkeen oma pistelasku; laskenta on järjestelmän oma eikä sertifioitu."}


def _band(spec: InstrumentSpec, total: int) -> str:
    for lo, hi, label in spec.bands:
        if lo <= total <= hi:
            return label
    # Bands are authored to cover the full range; this only guards a typo.
    return "tuntematon"


def score(record: Record) -> Assessment:
    """Sum the items one record already contains. Never invents an answer.

    `valmis` is True only when every declared item is present and in range;
    otherwise total/tulkinta are None -- an incomplete instrument is rendered
    as not completed, never guessed.

    An instrument this module has **no specification** for is presented, not implemented: the
    instrument name, the version and the count of answers the record itself supplied are reported,
    and `valmis` stays False with `total`, `max_total`, `tulkinta` and `osiot` all absent. That is how
    a supplied RAI result reaches the briefing under ADR-0008 / `issues/05`, which forbid implementing
    instruments. The count is deliberately a count and not a labelling of the answers: this record's
    answer keys are our own fixture's codes, so naming them after RAI domains would state a fact about
    the instrument that this repository does not have -- and an absent value beats an invented one.
    """
    instrument_id = source.assessment_instrument(record)
    raw_version = record.payload.get("versio")
    instrument_version = raw_version if isinstance(raw_version, str) and raw_version else None
    lahteet = [record.record_id]
    # Read before the specification lookup: what the source supplied is a property of the record, not of
    # whether we happen to have an instrument spec for it.
    answers = source.assessment_answers(record)
    spec = INSTRUMENTS.get(instrument_id or "")
    if spec is None:
        return Assessment(
            instrumentti=instrument_id or "tuntematon",
            instrument_version=instrument_version,
            valmis=False,
            total=None,
            max_total=None,
            tulkinta=None,
            osiot=[],
            source_answers=len(answers),
            lahteet=lahteet,
            # The record's own date travels with the presented result exactly as it does on the
            # specification branch below. A presented assessment is identified by instrument, version,
            # source *and date*; dropping the date here made a supplied RAI result the one row that could
            # not be placed in time, while the IADL row beside it carried its date.
            recorded_at=record.recorded_at,
        )

    osiot: list[AssessmentItem] = []
    total = 0
    valmis = True
    for code, kysymys in spec.items.items():
        raw: Any = answers.get(code)
        if raw is not None and 0 <= raw <= spec.per_item_max:
            total += int(raw)
        else:
            valmis = False
            raw = None
        osiot.append(AssessmentItem(osio=code, kysymys=kysymys, pisteet=raw))

    return Assessment(
        instrumentti=spec.name,
        instrument_version=instrument_version,
        valmis=valmis,
        total=total if valmis else None,
        max_total=spec.max_total,
        tulkinta=_band(spec, total) if valmis else None,
        osiot=osiot,
        source_answers=len(answers),
        lahteet=lahteet,
        recorded_at=record.recorded_at,
    )


def select_for_picture(records: list[Record], today: date) -> list[Assessment]:
    """Keep the latest current completed result, newer incomplete result, or history.

    Exact-timestamp ties with different completed totals are marked ambiguous;
    they never contribute a decision feature.
    """
    groups: dict[str, list[Record]] = {}
    for record in records:
        if source.is_assessment(record):
            groups.setdefault(source.assessment_instrument(record) or "tuntematon", []).append(record)

    selected: list[Assessment] = []
    for group in groups.values():
        current = [r for r in group if source.temporal_status(r, today) == "current"]
        history = [r for r in group if source.temporal_status(r, today) == "history"]
        completed = [r for r in current if score(r).valmis]
        incomplete = [r for r in current if not score(r).valmis]
        latest_complete_at = max((r.recorded_at for r in completed), default=None)
        if latest_complete_at is not None:
            ties = [r for r in completed if r.recorded_at == latest_complete_at]
            signatures = {(score(r).total, tuple(i.pisteet for i in score(r).osiot)) for r in ties}
            for record in ties:
                result = score(record)
                if len(signatures) > 1:
                    result = result.model_copy(update={"valmis": False, "total": None, "tulkinta": None, "temporal_status": "ambiguous"})
                else:
                    result = result.model_copy(update={"temporal_status": "current"})
                selected.append(result)
        newer_incomplete = [r for r in incomplete if latest_complete_at is None or r.recorded_at > latest_complete_at]
        if newer_incomplete:
            latest = max(newer_incomplete, key=lambda r: (r.recorded_at, r.record_id))
            selected.append(score(latest).model_copy(update={"temporal_status": "incomplete"}))
        if not current and history:
            latest = max(history, key=lambda r: (r.recorded_at, r.record_id))
            selected.append(score(latest).model_copy(update={"temporal_status": "history"}))
    return selected
