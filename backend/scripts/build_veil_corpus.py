#!/usr/bin/env python
"""Build the VEIL canonical corpus: a bounded subset of the challenge's own synthetic release, in the
delivery's canonical shape (`profiles.csv`, `encounters.csv`, `records.jsonl`), for `reconcile_delivery.py`.

    backend/.venv/bin/python backend/scripts/build_veil_corpus.py \\
        --source /tmp/luvn-hackathon-data/LUVN_synteettinenData_VEIL/2024-2026 --out /tmp/luvn-veil-corpus

Deterministic file IO, no network, no model, no `az`. The output directory must lie outside the repository:
the release, its extraction and every derived corpus are deleted after the event, and a corpus under the
repository would be a copy the deletion runbook cannot reach.

The subset: the `--people` (default 24) lexicographically smallest `asiakas_id` that **some source row names**
-- a table's `asiakas_id`, a `henkilotunnus` resolved through either domain's registry, or a note's
`subject_id` (`_people`). This covers both release cohorts: 2024-2026 registers one person set for clinical
and social alike, while 2025 registers separate sets, so the clinical registry alone is not the population.
No sampling, and a registered person the release says nothing else about is not padded in.

Ten source tables, each mapped to one record type (TABLES). Only the render keys are written. Every value is the
release's own string, copied byte-for-byte, except the fixed translations declared in this file (SEX, LANGUAGE,
the flag wordings). A value outside a translation table is LEFT OUT of the payload, never guessed.

Stated limits, all of which the report repeats:

* `nimi` on `diagnoosi` is an AUTHORED Finnish label (`ICD10_FI`, written by WORK-ORDER-VEIL-2) for each of the 29
  codes the chosen people carry; `icd10` stays the release's code. The release carries no Finnish diagnosis label, so
  these are the builder's own words, not the release's. A code outside the map is written as its own code, never guessed.
  `I10` ("Kohonnut verenpaine") contains `verenpaine`, a baseline term of `medication_follow_up`, so those diagnoses
  become eligible for that purpose. That is a side effect of the label, named here, not a goal.
* `lääkitys` `tila` is the builder's declared reading of the source's end date, not a clinical judgement: a
  prescription with no end date, or one on or after `REFERENCE_DATE` (2026-10-13, the day the corpus was loaded), is
  `aktiivinen`; an earlier end date is `päättynyt`. An end date that is present but not a calendar date leaves `tila` out.
* `palvelutarpeen_arviointi` (social) is joined to a person through `henkilotunnus`, since the table carries no
  `asiakas_id`. A row whose `henkilotunnus` matches no `asiakas.csv` row is skipped and counted (`unlinked`). A row that
  names another bounded-out person is out of scope and counted nowhere, like every other table. Its `teksti` is the
  source's `tuen_tarve_selite`, unedited, and is left out when that cell is blank.
* `kotihoito_kontakti` (social) is joined by `henkilotunnus` and CAPPED at the 10 most recent rows per person
  (`HOMECARE_CAP`), counted over the rows dated on or before `REFERENCE_DATE` only. A row dated after `REFERENCE_DATE`
  is not history yet: it is excluded before the cap and counted per person as `future_excluded`, apart from the rows the
  cap drops (`capped`). Both are printed. Rows are ranked by their parsed date, then by record id, both descending, so a
  tie at the cut is decided the same way on every run. A row with an unparseable date is not ranked and takes no slot.
  The kept rows are written in ascending date order. Uncapped, one chosen person alone carries 1 273 rows. Its `aihe` is
  the authored label `Kotihoito` for a home-care visit (see `HOMECARE_AIHE`); `kanava` is the source's `yhteystapa`.
* A clinical note is ALSO read by its own sections. The release writes every clinical note as markdown with fixed
  `## ` headings (`KÄYNNIN TIEDOT`, `TULOSYY JA ESITIEDOT`, `RISKITIEDOT`, `TOIMINTAKYKY`, `FYSIOLOGISET MITTAUKSET`, ...).
  The note record keeps the whole text unedited and ALSO carries `osiot`, the same sections keyed by their own heading,
  so a reader can cite one section. Three sections are additionally written as records of their own, each naming the
  note it came from in `lähde_record_id` and dated with the note:
    - `## FYSIOLOGISET MITTAUKSET` -> one `mittaus` record per line that carries a value the product reads
      (`MEASUREMENT_PATTERNS`: a temperature in °C, a blood pressure in mmHg, a pulse, an SpO2, a weight, a height).
      The value is the line's own number; a line matching no pattern writes nothing. The release carries temperatures
      and blood pressures only, so those are the only two that occur.
    - `## RISKITIEDOT` -> a `vapaateksti` record headed `Riskitiedot` (the allergy statement, verbatim, negations kept).
    - `## TOIMINTAKYKY` -> a `vapaateksti` record headed `Toimintakyky`, topic `toimintakyky`, consent scope
      `social_care`, with the national heading `Toimintakyky`: the clinician's functional-capacity statement is the
      one clinical section the service-needs lens may see. The parent note stays `treatment`-scoped.
  Nothing is invented: every emitted value is a substring of the note.
* `vastausarkistohistoria_view` carries NO date: `NaytteenottoaikaPaiva` is an integer 1-7 on every row. A lab row's
  `recorded_at` is the timestamp of the one contact that the release's own `laboratorio_kontakti` table names for it
  (`Avain` -> `kontakti_id`), and that contact must belong to the same person. A row whose link is absent, repeated,
  pointing at another person's contact, or whose contact has no timestamp is not dated: it is skipped and counted
  (`unlinked`, or `skipped_unparseable_dates` for a blank or unparseable timestamp), never given a day index or an
  invented time. The join never falls back to the nearest contact by date.
* `recorded_at` is parsed strictly (ISO-8601 only). `app.mapping.parse_date` is NOT used: it reads a bare integer
  as epoch seconds, so `NaytteenottoaikaPaiva=4` would silently become 1970-01-01. A naive timestamp is read as UTC,
  the convention `app/mapping.py` declares for the delivery path.
* `perustieto.recorded_at` is the birth date (`syntymapaiva`), as declared. It orders the demographics record at
  birth in any timeline.
* `paatos.voimassa` is left out when `onko_toistaiseksi_voimassa` is empty: an empty cell is not a flag value.
* The plan record of a social note takes the decision's date (`decision_date`), the date this table declares.
* `asiointitapahtuma` is authored by `sairaanhoitaja`, as the owner ruled; the work order's axis table names it.
  The home-care rows are `asiointitapahtuma` too, with topic `kotihoidon palvelu`. Neither that topic nor the source
  spelling contains the baseline term `kotihoito`, so their `aihe` is the authored label `Kotihoito` (`HOMECARE_AIHE`);
  without that label the baseline's plain substring match selects none of them (measured on the built corpus: 0 of 10).
* No clinician has reviewed the subset. The corpus is synthetic.
* The dry run over the real release is `warning`, not `ready`, with zero refusals and exactly seven render keys
  missing. Four have no field in the release that this builder's mapping reads: `asiointitapahtuma` `suunta` and
  `yhteenveto`, `lääkitys` `puuttuneet_annokset_30d`, `perustieto` `asumismuoto`. Three are `tutkimustulos` keys:
  `tulkinta` and `viitearvo` have no value (`ViitearvonYlitys` is empty on every row), and `yksikkö` is not mapped
  from the release's free-text `TulokseenLiittyvaInfokentta` (`unit=` text that this builder does not parse).
  None of the seven is invented.

Lens encounters: for each person, in order, a `medication_follow_up` lens for `sairaanhoitaja`, a
`functioning_service_needs` lens for `sosiaalityontekija` and a `mental_health_assessment` lens for `laakari`. The
functioning lens is emitted only when the person has at least one purpose-filtered record that the role's own visibility
table admits (`admits`). The medication lens is emitted only when the person has a running medication or a long-term
diagnosis (`_medication_lens_eligible`). The mental-health lens (the doctor lens) is emitted only when the person has a
diagnosis in ICD-10 chapter F or a medication in the antidepressant/anxiolytic classes the release carries
(`_mental_lens_eligible`): the record itself is the evidence, running or not, since a past depression diagnosis is what
a mental-health assessment reads. A person who qualifies for no lens gets no encounter. The identity record is excluded
from the `admits` gate on purpose: it is admitted to every purpose by an exception, so counting it would make every
person qualify for every lens. A clinical appointment opens the doctor lens when the person has it and either has
no nurse lens or the appointment's contact carries a doctor-authored note (`_clinical_appointment_encounter`);
every other clinical appointment opens the nurse lens, as the owner ruled.

Appointments (`appointments.csv`): the day view's axis, read from the release's own scheduling tables and never
authored. A clinical `ajanvaraus` row dated on or after `REFERENCE_DATE` becomes a `treatment` appointment carrying
the source's own `ajanvaraus_tila` and the linked contact's `hoidon_kiireellisyys_selite_hilmo` verbatim, the contact
record it names (when that record was written) and the person's medication lens (when it was emitted). A home-care
visit (`kotihoito_kontakti`) dated on or after `REFERENCE_DATE` becomes one `social_care` appointment per person and
day -- the release schedules several visits a day, and the day view shows the person once -- carrying the functioning
lens when it was emitted. Rows before `REFERENCE_DATE` are history, which is Challenge 1.2's domain, and are not
written. A row whose date does not parse is skipped and counted. The release carries dates, not clock times, so no
time is written.

The permission axes are declared below, once, and printed by `main`. The release carries none of them.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# nosec B108 -- the work order fixes these to /tmp: outside the repository, deleted after the event.
DEFAULT_SOURCE = Path("/tmp/luvn-hackathon-data/LUVN_synteettinenData_VEIL/2024-2026")  # nosec B108  # noqa: S108
DEFAULT_OUT = Path("/tmp/luvn-veil-corpus")  # nosec B108  # noqa: S108
DEFAULT_PEOPLE = 24
VISIBILITY = ROOT / "backend" / "app" / "tables" / "visibility.json"

PROFILE_COLUMNS = ("source_person_id", "persona_id", "label")
# Restated from `app/delivery.py` (APPOINTMENT_COLUMNS); pinned by the test.
APPOINTMENT_COLUMNS = ("appointment_id", "source_person_id", "domain", "scheduled_on", "status_text",
                       "urgency_text", "specialty", "contact_record_id", "encounter_id")
AJANVARAUS_COLUMNS = ("ajanvaraus_id", "kontakti_id", "asiakas_id", "ajanvaraus_pvm", "ajanvaraus_tila",
                      "erikoisala_kansallinen_id")
ENCOUNTER_COLUMNS = ("encounter_id", "source_person_id", "role", "purpose", "example_label", "demo_name",
                     "consent_holders", "life_stage")
SENSITIVITY_TAG = "none"  # the demo's own value; every row carries onko_testiasiakas=true
# The work order's reference date (the day the corpus was loaded). A medication ending on or after it is running: the
# builder's declared reading, not a clinical judgement.
REFERENCE_DATE = date(2026, 10, 13)
HOMECARE_CAP = 10  # the most recent home-care contacts kept per person; the rest are counted, never written
# AUTHORED by WORK-ORDER-VEIL-3, like ICD10_FI, not taken from the release. A home-care visit's source value
# (`SIM-Kotihoidon käynti`) is written as this label. The spelling is needed: `selection.baseline()` matches a purpose's
# baseline terms by a plain substring test, and `functioning_service_needs` has the term `kotihoito`, which neither the
# source spelling nor the topic `kotihoidon palvelu` contains (`kotihoidon` has a `d` where `kotihoito` has a `t`).
# It is authored from the source's own spelling, not invented content.
HOMECARE_AIHE = "Kotihoito"
# AUTHORED by the lens spec, from the source's own classification, not invented content. The source's
# `SIM-LASTENSUOJELUN-PALVELUTARPEEN-ARVIOINTI` is read as this Finnish heading so a child-protection
# service-needs assessment is not presented as plain "Palvelutarpeen arviointi".
CHILD_PROTECTION_HEADING = "Lastensuojelun palvelutarpeen arviointi"
TREATMENT, SOCIAL_CARE = "treatment", "social_care"

# Restated from `app/policy.py` (PURPOSE_FILTERED_TYPES) and `app/delivery.py` (USEFUL_PAYLOAD_KEYS). Importing
# either module pulls in pydantic and the app's model layer, which this file does not need. `tests/test_veil_corpus.py`
# pins each restatement to its source, so a change there fails the test rather than drifting silently.
PURPOSE_FILTERED_TYPES = frozenset({"diagnoosi", "lääkitys", "vapaateksti", "arviointi", "mittaus",
                                    "tutkimustulos", "asiointitapahtuma", "päätös"})
USEFUL = {
    "perustieto": frozenset({"syntymävuosi"}), "diagnoosi": frozenset({"nimi"}),
    "lääkitys": frozenset({"lääke"}), "asiointitapahtuma": frozenset({"kanava"}),
    "tutkimustulos": frozenset({"tutkimus"}), "vapaateksti": frozenset({"teksti"}),
    "päätös": frozenset({"päätös"}),
    "mittaus": frozenset({"paino_kg", "pituus_cm", "verenpaine", "syke", "lämpö_c", "spo2"}),
}

# Restated from `app/purpose.py` for the three purposes this corpus writes lenses for. Importing that module runs
# `app/__init__.py`, which imports httpx, so the builder cannot import it. `tests/test_veil_corpus.py` pins both
# tables to the source.
PURPOSE_TOPICS = {
    "medication_follow_up": frozenset({"lääkitys", "verenpaine", "diabetes", "astma", "kolesteroli", "hoito"}),
    "functioning_service_needs": frozenset({"toimintakyky", "palvelutarve", "toimeentulo", "perhetyö",
                                           "sosiaalipalvelut", "vammaispalvelut", "kotihoidon palvelu", "hoito"}),
    "mental_health_assessment": frozenset({"mielenterveys", "hoito", "psykiatria",
                                           "lääkitys", "verenpaine", "diabetes", "astma", "kolesteroli"}),
}
PURPOSE_DEMO_NAMES = {"medication_follow_up": "Lääkityksen seuranta",
                      "functioning_service_needs": "Palvelutarpeen arviointi",
                      "mental_health_assessment": "Mielenterveyden arvio"}
# The doctor lens gate. ICD-10 chapter F is "Mielenterveyden ja käyttäytymisen häiriöt"; the code prefix is the
# release's own value (`icd10`), so no label is involved. The medication names are AUTHORED from the release's own
# ATC column (`atc_koodi`, which the builder neither requires nor writes): every active substance the release files
# under N06A (antidepressants) or N05B (anxiolytics). The 2024-2026 release carries exactly one, `essitalopraami`
# (N06AB10); it carries no N05B substance, and `melatoniini` (N05CH01) is a hypnotic, deliberately not listed. A
# release that adds a substance in either class extends this set, never the rule.
MENTAL_HEALTH_ICD10_PREFIX = "F"
MENTAL_HEALTH_MEDICATIONS = frozenset({"essitalopraami"})

# AUTHORED by WORK-ORDER-VEIL-2, not taken from the release, which carries ICD-10 codes only. Exactly the 29 codes the
# 24 chosen people carry. A code outside this map is written as its own code (see `_diagnoosi`), never guessed.
ICD10_FI = {
    "A09": "Ripuli ja gastroenteriitti, oletettavasti infektioperäinen",
    "E03.9": "Kilpirauhasen vajaatoiminta, määrittämätön",
    "E11.9": "Tyypin 2 diabetes ilman komplikaatioita",
    "F32.9": "Masennusjakso, määrittämätön",
    "F41.9": "Ahdistuneisuushäiriö, määrittämätön",
    "G47.0": "Nukahtamis- ja unessa pysymisen häiriöt",
    "H10.3": "Määrittämätön akuutti sidekalvotulehdus",
    "H61.2": "Korvavahan tukkeuma",
    "H66.0": "Akuutti märkäinen välikorvatulehdus",
    "I10": "Kohonnut verenpaine",
    "J06.9": "Äkillinen ylähengitystieinfektio, määrittämätön",
    "J30.4": "Allerginen nuha, määrittämätön",
    "J45.9": "Astma, määrittämätön",
    "K21.9": "Refluksiesofagiitti",
    "K59.0": "Ummetus",
    "L08.9": "Paikallinen ihon ja ihonalaiskudoksen infektio",
    "L30.9": "Ihotulehdus, määrittämätön",
    "M17.9": "Polven nivelrikko, määrittämätön",
    "M25.5": "Nivelsärky",
    "M54.2": "Niskakipu",
    "M54.5": "Alaselkäkipu",
    "N30.0": "Akuutti kystiitti",
    "R10.4": "Muu ja määrittämätön vatsakipu",
    "R42": "Huimaus ja heitehuimaus",
    "R51": "Päänsärky",
    "S52.5": "Värttinäluun alaosan murtuma",
    "S61.9": "Avoin haava ranteen ja käden alueella, määrittämätön",
    "S69.9": "Ranteen ja käden vamma, määrittämätön",
    "S93.4": "Nilkan nyrjähdys ja venähdys",
}
SEX = {"NAINEN": "nainen", "MIES": "mies"}
LANGUAGE = {"fi": "suomi", "sv": "ruotsi", "en": "englanti"}
ACTIVE_FLAG = {"true": "aktiivinen", "false": "ei_aktiivinen"}
TERM_FLAG = {"true": "toistaiseksi", "false": "määräaikainen"}
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?)?")
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
YEAR = re.compile(r"\d{4}")

# The header columns each source must carry. A missing column is refused by name before any row is read.
ASIAKAS_COLUMNS = ("asiakas_id", "henkilotunnus", "sukupuoli", "syntymavuosi", "syntymapaiva", "kieli",
                   "kotikunta")
KONTAKTI_COLUMNS = ("kontakti_id", "asiakas_id", "tapahtuma_aikaleima", "yhteystapa_koodi_hilmo",
                    "erikoisala_kansallinen_id", "hoidon_kiireellisyys_selite_hilmo")
LAB_LINK_COLUMNS = ("Avain", "kontakti_id")
NEEDS_COLUMNS = ("palvelutarpeen_arviointi_avain", "henkilotunnus", "alkaen_pvm", "tuen_tarve_selite",
                 "palvelutarpeen_arviointi_luokiteltu")
HOMECARE_COLUMNS = ("kontakti_id", "tapahtuma_pvm", "henkilotunnus", "yhteystapa", "kayntityyppi")
DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class Draft:
    record_id: str
    when: str | None  # the raw source value, parsed strictly; None means the join could not date the row
    role: str  # authored_by_role
    payload: dict
    # A draft derived from a source row may differ from its table in type, topic, scope or heading (the note
    # sections). None means the table's own declaration applies.
    record_type: str | None = None
    topic: str | None = None
    consent_scope: str | None = None
    heading: str | None = None


# The note's own section headings, as the release writes them, and the product keys their values are read into.
# Each pattern captures ONE number from one line; the value written is that number and nothing else.
SECTION_HEADING = re.compile(r"^## (.+?)\s*$", re.M)
MEASUREMENTS_SECTION = "FYSIOLOGISET MITTAUKSET"
RISKS_SECTION = "RISKITIEDOT"
FUNCTIONING_SECTION = "TOIMINTAKYKY"
MEASUREMENT_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("lämpö_c", re.compile(r"Lämpö\s+(\d+(?:,\d+)?)\s*°C"), "float"),
    ("verenpaine", re.compile(r"(\d{2,3}/\d{2,3})\s*mmHg"), "str"),
    ("syke", re.compile(r"Syke\s+(\d{2,3})\b"), "int"),
    ("spo2", re.compile(r"SpO2\s+(\d{2,3})\s*%"), "int"),
    ("paino_kg", re.compile(r"Paino\s+(\d+(?:,\d+)?)\s*kg"), "float"),
    ("pituus_cm", re.compile(r"Pituus\s+(\d+(?:,\d+)?)\s*cm"), "float"),
)
RISKS_HEADING = "Riskitiedot"  # AUTHORED heading for the section record; the section text itself is verbatim
FUNCTIONING_HEADING = "Toimintakyky"  # the national heading (`upm.HEADING_PAIRS`), also the record's otsikko


def note_sections(text: str) -> dict[str, str]:
    """The note's `## ` sections keyed by their own heading, each value verbatim with surrounding blank lines removed.

    A note with no heading has no sections. A repeated heading keeps the first occurrence: the release writes each
    heading once, and a second would be the same section twice, not new content.
    """
    matches = list(SECTION_HEADING.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end].strip("\n")
        if body.strip() and match.group(1) not in sections:
            sections[match.group(1)] = body
    return sections


def _measurement_lines(section: str) -> list[dict]:
    """One payload per line of the measurements section that carries at least one readable value."""
    payloads = []
    for line in section.splitlines():
        payload: dict = {}
        for key, pattern, kind in MEASUREMENT_PATTERNS:
            match = pattern.search(line)
            if match is None:
                continue
            raw = match.group(1)
            payload[key] = (float(raw.replace(",", ".")) if kind == "float" else int(raw) if kind == "int" else raw)
        if payload:
            payloads.append(payload)
    return payloads


def _put(payload: dict, key: str, value) -> None:
    """Write a render key only when the source supplies a value. A blank cell is absent, never empty."""
    if (isinstance(value, str) and value.strip()) or (
            isinstance(value, (int, float)) and not isinstance(value, bool)):
        payload[key] = value


def _perustieto(row: dict, ctx: dict) -> list[Draft]:
    payload: dict = {"näyttötunniste": row["asiakas_id"]}
    _put(payload, "sukupuoli", SEX.get(row["sukupuoli"]))
    if YEAR.fullmatch(row["syntymavuosi"]):
        payload["syntymävuosi"] = int(row["syntymavuosi"])
    _put(payload, "äidinkieli", LANGUAGE.get(row["kieli"]))
    _put(payload, "kotikunta", row["kotikunta"])
    return [Draft(f"veil-basic-{row['asiakas_id']}", row["syntymapaiva"], "sihteeri", payload)]


def _diagnoosi(row: dict, ctx: dict) -> list[Draft]:
    # `nimi` is the authored label for the code, or the code itself when the map does not carry it (see ICD10_FI).
    payload: dict = {}
    _put(payload, "nimi", ICD10_FI.get(row["icd10_id"], row["icd10_id"]))
    _put(payload, "icd10", row["icd10_id"])
    _put(payload, "tila", ACTIVE_FLAG.get(row["onko_pitkaaikaisdiagnoosi"]))
    return [Draft(f"veil-diag-{row['diagnoosi_id']}", row["diagnoosi_aikaleima"], "laakari", payload)]


def _medication_status(end: str) -> str | None:
    """`aktiivinen` when the release gives no end date or one on or after REFERENCE_DATE, else `päättynyt`.

    None when the end date is present but is not a calendar date: the field is left out, never guessed.
    """
    text = end.strip()
    if not text:
        return "aktiivinen"
    if not DATE_ONLY.fullmatch(text):
        return None
    try:
        ends = date.fromisoformat(text)
    except ValueError:
        return None
    return "aktiivinen" if ends >= REFERENCE_DATE else "päättynyt"


def _laakitys(row: dict, ctx: dict) -> list[Draft]:
    payload: dict = {}
    _put(payload, "lääke", row["vaikuttava_aine"])
    _put(payload, "annos", row["laakkeen_kayttotapa"].lower())
    _put(payload, "tila", _medication_status(row["laakitys_paattymispvm"]))
    return [Draft(f"veil-med-{row['laakitys_avain']}", row["laakitys_aloituspvm"], "laakari", payload)]


def _asiointitapahtuma(row: dict, ctx: dict) -> list[Draft]:
    payload: dict = {}
    _put(payload, "kanava", row["yhteystapa_koodi_hilmo"])
    _put(payload, "aihe", row["erikoisala_kansallinen_id"])
    return [Draft(f"veil-visit-{row['kontakti_id']}", row["tapahtuma_aikaleima"], "sairaanhoitaja", payload)]


def _tutkimustulos(row: dict, ctx: dict) -> list[Draft]:
    payload: dict = {}
    # The release carries only PaatutkimuksenLyhenne; TutkimuksenLyhenne, when a delivery has it, wins.
    _put(payload, "tutkimus", row.get("TutkimuksenLyhenne") or row["PaatutkimuksenLyhenne"])
    # The release's decimal is kept as written: a float would turn "3.50" into 3.5.
    if NUMBER.fullmatch(row["NumeerinenTutkimustulos"]):
        payload["arvo"] = row["NumeerinenTutkimustulos"]
    _put(payload, "tulkinta", row["ViitearvonYlitys"])
    # `NaytteenottoaikaPaiva` is a day index and is never read as a date. The time is the linked contact's. When the
    # join cannot name one (None), or the contact belongs to another person, the row is unlinked and counted.
    link = ctx["lab_contact"].get(row["Avain"])
    when = None if link is None or link[0] != row["asiakas_id"] else link[1]
    return [Draft(f"veil-lab-{row['Avain']}", when, "sairaanhoitaja", payload)]


def _clinical_note(row: dict, ctx: dict) -> list[Draft]:
    # The ordinal counts notes per (subject, visit) in file order, before any date check, so an id never
    # shifts when a neighbouring row is skipped.
    key = (row["subject_id"], row["visit_id"])
    ctx[key] = ctx.get(key, 0) + 1
    payload: dict = {}
    _put(payload, "otsikko", row["contact_type"])
    _put(payload, "teksti", row["note_text"])  # unedited
    _put(payload, "käyntityyppi", row["phase"])
    role = row["clinician_role"].strip() or "sairaanhoitaja"
    note_id = f"veil-note-{row['subject_id']}-{row['visit_id']}-{ctx[key]}"
    sections = note_sections(row["note_text"])
    if sections:
        payload["osiot"] = sections
    drafts = [Draft(note_id, row["visit_date"], role, payload)]
    stem = f"{row['subject_id']}-{row['visit_id']}-{ctx[key]}"
    for index, measurement in enumerate(_measurement_lines(sections.get(MEASUREMENTS_SECTION, "")), start=1):
        drafts.append(Draft(f"veil-meas-{stem}-{index}", row["visit_date"], role,
                            {**measurement, "lähde_record_id": note_id}, record_type="mittaus"))
    if RISKS_SECTION in sections:
        drafts.append(Draft(f"veil-risk-{stem}", row["visit_date"], role,
                            {"otsikko": RISKS_HEADING, "teksti": sections[RISKS_SECTION], "lähde_record_id": note_id}))
    if FUNCTIONING_SECTION in sections:
        drafts.append(Draft(f"veil-func-{stem}", row["visit_date"], role,
                            {"otsikko": FUNCTIONING_HEADING, "teksti": sections[FUNCTIONING_SECTION],
                             "lähde_record_id": note_id},
                            topic="toimintakyky", consent_scope=SOCIAL_CARE, heading=FUNCTIONING_HEADING))
    return drafts


def _paatos(row: dict, ctx: dict) -> list[Draft]:
    payload: dict = {}
    _put(payload, "päätös", row["palveluluokitus_nimi"])
    _put(payload, "voimassa", TERM_FLAG.get(row["onko_toistaiseksi_voimassa"]))
    return [Draft(f"veil-dec-{row['paatos_id']}", row["paatos_pvm"], "sosiaalityontekija", payload)]


def _social_note(row: dict, ctx: dict) -> list[Draft]:
    decision = row["decision_id"]
    payload: dict = {}
    _put(payload, "otsikko", row["decision_outcome"])
    _put(payload, "teksti", row["decision_text"])
    drafts = [Draft(f"veil-snote-{decision}", row["decision_date"], "sosiaalityontekija", payload)]
    if row["plan_text"].strip():
        drafts.append(Draft(f"veil-snote-{decision}-plan", row["decision_date"], "sosiaalityontekija",
                            {"teksti": row["plan_text"]}))
    return drafts


def _palvelutarve(row: dict, ctx: dict) -> list[Draft]:
    # The source's own text, unedited. A blank `tuen_tarve_selite` leaves `teksti` out; the heading is the one the work
    # order fixes, which the release also carries as its `palvelutarpeen_arviointi` value.
    luokiteltu = row["palvelutarpeen_arviointi_luokiteltu"].strip()
    otsikko = (CHILD_PROTECTION_HEADING
               if luokiteltu == "SIM-LASTENSUOJELUN-PALVELUTARPEEN-ARVIOINTI" else "Palvelutarpeen arviointi")
    payload: dict = {"otsikko": otsikko}
    _put(payload, "teksti", row["tuen_tarve_selite"])
    return [Draft(f"veil-needs-{row['palvelutarpeen_arviointi_avain']}", row["alkaen_pvm"], "sosiaalityontekija",
                  payload)]


def _kotihoito(row: dict, ctx: dict) -> list[Draft]:
    payload: dict = {}
    _put(payload, "kanava", row["yhteystapa"])
    # A home-care visit is labelled HOMECARE_AIHE; any other visit type keeps the source's value.
    home_care = "kotihoidon" in row["kayntityyppi"].casefold()
    _put(payload, "aihe", HOMECARE_AIHE if home_care else row["kayntityyppi"])
    return [Draft(f"veil-homecare-{row['kontakti_id']}", row["tapahtuma_pvm"], "sairaanhoitaja", payload)]


@dataclass(frozen=True)
class Table:
    key: str
    path: str  # relative to --source
    record_type: str
    consent_scope: str
    topic: str
    author: str  # the declared authored_by_role, as printed
    subject_column: str
    columns: tuple[str, ...]
    map: Callable[[dict, dict], list[Draft]]
    # The subject column names a henkilotunnus, not an asiakas_id: the person is the asiakas that carries it.
    by_henkilotunnus: bool = False
    # The most recent `cap` rows dated on or before REFERENCE_DATE are kept per person; the rest are counted as capped,
    # and rows dated after REFERENCE_DATE as future_excluded. None means uncapped.
    cap: int | None = None


TABLES = (
    Table("perustieto", "clinical/tables/asiakas.csv", "perustieto", TREATMENT, "demografia", "sihteeri",
          "asiakas_id", ASIAKAS_COLUMNS, _perustieto),
    Table("diagnoosi", "clinical/tables/diagnoosi.csv", "diagnoosi", TREATMENT, "hoito", "laakari",
          "asiakas_id", ("diagnoosi_id", "icd10_id", "asiakas_id", "diagnoosi_aikaleima",
                         "onko_pitkaaikaisdiagnoosi"), _diagnoosi),
    Table("lääkitys", "clinical/tables/laakitys.csv", "lääkitys", TREATMENT, "lääkitys", "laakari",
          "asiakas_id", ("laakitys_avain", "laakitys_aloituspvm", "laakitys_paattymispvm", "asiakas_id",
                         "vaikuttava_aine", "laakkeen_kayttotapa"), _laakitys),
    Table("asiointitapahtuma", "clinical/tables/kontakti.csv", "asiointitapahtuma", TREATMENT, "hoito",
          "sairaanhoitaja", "asiakas_id", KONTAKTI_COLUMNS,
          _asiointitapahtuma),
    Table("tutkimustulos", "clinical/tables/vastausarkistohistoria_view.csv", "tutkimustulos", TREATMENT,
          "hoito", "sairaanhoitaja", "asiakas_id", ("Avain", "asiakas_id", "NumeerinenTutkimustulos",
                                                    "ViitearvonYlitys", "PaatutkimuksenLyhenne",
                                                    "NaytteenottoaikaPaiva"), _tutkimustulos),
    Table("vapaateksti (clinical)", "clinical/text/clinical-notes.csv", "vapaateksti", TREATMENT, "hoito",
          "the row's clinician_role (blank: sairaanhoitaja)", "subject_id",
          ("subject_id", "visit_id", "visit_date", "clinician_role", "phase", "contact_type", "note_text"),
          _clinical_note),
    Table("päätös", "social/tables/paatos.csv", "päätös", SOCIAL_CARE, "palvelutarve", "sosiaalityontekija",
          "asiakas_id", ("paatos_id", "asiakas_id", "paatos_pvm", "palveluluokitus_nimi",
                         "onko_toistaiseksi_voimassa"), _paatos),
    Table("vapaateksti (social)", "social/text/social-notes.csv", "vapaateksti", SOCIAL_CARE, "palvelutarve",
          "sosiaalityontekija", "subject_id", ("subject_id", "decision_id", "decision_outcome", "decision_date",
                                               "decision_text", "plan_text"), _social_note),
    Table("vapaateksti (needs)", "social/tables/palvelutarpeen_arviointi.csv", "vapaateksti", SOCIAL_CARE,
          "palvelutarve", "sosiaalityontekija", "henkilotunnus", NEEDS_COLUMNS, _palvelutarve,
          by_henkilotunnus=True),
    Table("kotihoito_kontakti", "social/tables/kotihoito_kontakti.csv", "asiointitapahtuma", SOCIAL_CARE,
          "kotihoidon palvelu", "sairaanhoitaja", "henkilotunnus", HOMECARE_COLUMNS, _kotihoito,
          by_henkilotunnus=True, cap=HOMECARE_CAP),
)

# Each lens: (name, role, purpose). Order is the order the encounters are written in.
LENSES = (("medication", "sairaanhoitaja", "medication_follow_up"),
          ("functioning", "sosiaalityontekija", "functioning_service_needs"),
          ("mental", "laakari", "mental_health_assessment"))


@dataclass
class TableStats:
    rows: int = 0  # source rows of the bounded people
    records: int = 0  # records written (the social plan record counts here)
    useful: int = 0  # written records carrying a USEFUL key
    skipped_dates: int = 0  # source rows whose recorded_at did not parse
    unlinked: int = 0  # lab rows the link table could not date (absent, repeated, another person's, no contact), and
    # henkilotunnus rows that match no asiakas in the release; those are not counted in rows
    empty: int = 0  # drafts whose payload came out empty (never written)
    capped: int = 0  # dated rows the per-person cap dropped (see Table.cap)
    future_excluded: int = 0  # dated after REFERENCE_DATE: excluded before the cap, never counted in capped


@dataclass
class AppointmentStats:
    rows: int = 0  # source rows of the bounded people
    written: int = 0
    past: int = 0  # dated before REFERENCE_DATE: history, not written
    skipped_dates: int = 0
    merged: int = 0  # home-care rows folded into one appointment per person and day


@dataclass
class Summary:
    people: int
    records: int
    encounters: int
    skipped_unparseable_dates: int
    unlinked: int
    bytes: int
    stats: dict[str, TableStats] = field(default_factory=dict)
    capped: int = 0  # home-care rows the cap dropped, all people
    capped_by_person: dict[str, int] = field(default_factory=dict)
    future_excluded: int = 0  # home-care rows dated after REFERENCE_DATE, all people
    future_excluded_by_person: dict[str, int] = field(default_factory=dict)
    appointments: int = 0
    appointment_stats: dict[str, AppointmentStats] = field(default_factory=dict)


def _parse_recorded_at(raw: str) -> datetime | None:
    """Strict ISO-8601 only. None means the row is skipped and counted, never guessed."""
    text = raw.strip()
    if not ISO_DATE.fullmatch(text):
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _rows(path: Path, columns: tuple[str, ...]) -> Iterator[dict]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = [column for column in columns if column not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"build_veil_corpus: {path} is missing columns {missing}")
        yield from reader


def _people(source: Path, count: int) -> list[str]:
    """Every person the release carries at least one row about, clinical or social.

    Two cohorts with different person sets share one canonical shape. 2024-2026 registers **one** person set
    for both domains, so reading `clinical/tables/asiakas.csv` happened to be complete there. 2025 registers
    **separate** clinical and social sets, and of its 15,000 social people only **2,630 appear in any social
    table** -- so neither obvious rule is right. A clinical-only read drops all 2,630 social people, whose
    data is real; the whole registry would add the **12,370** people the release says nothing else about, each
    an empty profile with no encounter.

    The rule is therefore: a person is in the corpus when a source row **the builder maps to a record** names
    them, and their own `asiakas` row supplies the demographics. Scanning every table was not enough: the
    social tables `vireilletulo`, `selvitys`, `palvelu` and `asumisjakso` name people but map to no record, so
    that wider rule added 294 people whose profile would have been empty. `named` is read from `TABLES` -- the
    mapper's own list -- and resolved by `asiakas_id`, or by `henkilotunnus` through the registry.
    """
    registry: dict[str, str] = {}
    hetu_to_id: dict[str, str] = {}
    for name in ("clinical/tables/asiakas.csv", "social/tables/asiakas.csv"):
        path = source / name
        if not path.exists():
            continue
        in_file: set[str] = set()
        for row in _rows(path, ASIAKAS_COLUMNS):
            asiakas_id = row["asiakas_id"]
            if asiakas_id in in_file:
                raise SystemExit(f"build_veil_corpus: duplicate asiakas_id {asiakas_id!r} in {name}")
            in_file.add(asiakas_id)
            registry.setdefault(asiakas_id, row["henkilotunnus"].strip())
            hetu = row["henkilotunnus"].strip()
            if hetu and hetu not in hetu_to_id:
                hetu_to_id[hetu] = asiakas_id
    named: set[str] = set()
    for table in TABLES:
        if Path(table.path).name == "asiakas.csv":
            continue  # the registry is the population, not evidence that the release says anything else
        path = source / table.path
        if not path.exists():
            continue
        with path.open(encoding="utf-8", newline="") as stream:
            header = next(csv.reader(stream), [])
        column = table.subject_column
        if column not in header:
            continue
        for row in _rows(path, (column,)):
            value = row[column]
            if not value:
                continue
            named.add(hetu_to_id.get(value, "") if table.by_henkilotunnus else value)
    return sorted(asiakas_id for asiakas_id in registry if asiakas_id in named)[:count]


def _person_by_henkilotunnus(source: Path) -> dict[str, str]:
    """Each person's henkilotunnus, from both domains' asiakas tables.

    The 2025 cohort's social people are absent from the clinical table, so a clinical-only map would resolve
    every social `henkilotunnus` to None and drop those rows as unlinked. A blank key is never stored, and one
    henkilotunnus under two different asiakas_id is still refused.
    """
    people: dict[str, str] = {}
    for name in ("clinical/tables/asiakas.csv", "social/tables/asiakas.csv"):
        path = source / name
        if not path.exists():
            continue
        for row in _rows(path, ASIAKAS_COLUMNS):
            if not row["henkilotunnus"].strip():
                continue
            known = people.get(row["henkilotunnus"])
            if known is not None and known != row["asiakas_id"]:
                raise SystemExit(f"build_veil_corpus: henkilotunnus shared by asiakas {known!r} "
                                 f"and {row['asiakas_id']!r} in {name}")
            people[row["henkilotunnus"]] = row["asiakas_id"]
    return people


def _subject(row: dict, table: Table, ctx: dict) -> str | None:
    """The person a source row belongs to. None only for a henkilotunnus that matches no asiakas in the release."""
    if table.by_henkilotunnus:
        return ctx["person_by_henkilotunnus"].get(row[table.subject_column])
    return row[table.subject_column]


_EARLIEST = datetime.min.replace(tzinfo=timezone.utc)


def _capped_order(source: Path, table: Table, scope: set[str], ctx: dict, cap: int) -> tuple[list[dict], set[str]]:
    """The table's in-scope rows in ascending (date, record id) order, and the record ids the cap keeps.

    The cap keeps the `cap` most recent rows of each bounded person that are dated on or before REFERENCE_DATE: ranked by
    the parsed time, then by the record id, both descending, so a tie at the cut is decided the same way on every run. A
    row dated after REFERENCE_DATE is not history yet and is not ranked, so it cannot take a slot; a row whose date does
    not parse is not ranked either. The main pass skips and counts the unparseable rows, and counts the future rows as
    `future_excluded`, apart from the rows the cap drops.
    """
    entries: list[tuple[datetime, str, str, dict, bool]] = []  # (when, record id, person, row, rankable)
    for row in _rows(source / table.path, table.columns):
        person = _subject(row, table, ctx)
        if person is not None and person not in scope:
            continue
        if person is None:
            # Matches no asiakas: nothing to rank under. The main pass counts it unlinked.
            entries.append((_EARLIEST, "", "", row, False))
            continue
        draft = table.map(row, ctx)[0]
        parsed = _parse_recorded_at(draft.when or "")
        rankable = parsed is not None and parsed.date() <= REFERENCE_DATE
        entries.append((parsed or _EARLIEST, draft.record_id, person, row, rankable))
    ranked: dict[str, list[tuple[datetime, str]]] = {}
    for when, record_id, person, _, rankable in entries:
        if rankable:
            ranked.setdefault(person, []).append((when, record_id))
    keep: set[str] = set()
    for items in ranked.values():
        items.sort(reverse=True)
        keep.update(record_id for _, record_id in items[:cap])
    entries.sort(key=lambda entry: (entry[0], entry[1]))
    return [entry[3] for entry in entries], keep


def _lab_contact_times(source: Path) -> dict[str, tuple[str, str] | None]:
    """Each lab row's contact, as the release's own link table names it: `Avain` -> (asiakas_id, tapahtuma_aikaleima).

    A lab row maps to None when its link is repeated or points at a contact the release does not carry. Only a single
    link is trusted, and nothing near it in time is used instead. The caller checks the person.
    """
    contacts: dict[str, tuple[str, str]] = {}
    for row in _rows(source / "clinical/tables/kontakti.csv", KONTAKTI_COLUMNS):
        if row["kontakti_id"] in contacts:
            # A repeated id would make the link name two visits: refused, never last-wins.
            raise SystemExit(f"build_veil_corpus: duplicate kontakti_id {row['kontakti_id']!r} in kontakti.csv")
        contacts[row["kontakti_id"]] = (row["asiakas_id"], row["tapahtuma_aikaleima"])
    links: dict[str, list[str]] = {}
    for row in _rows(source / "clinical/tables/laboratorio_kontakti.csv", LAB_LINK_COLUMNS):
        links.setdefault(row["Avain"], []).append(row["kontakti_id"])
    return {avain: contacts.get(ids[0]) if len(ids) == 1 else None for avain, ids in links.items()}


def _contacts(source: Path) -> dict[str, dict]:
    """Each clinical contact by id: its person, timestamp and the source's own urgency wording."""
    contacts: dict[str, dict] = {}
    for row in _rows(source / "clinical/tables/kontakti.csv", KONTAKTI_COLUMNS):
        contacts[row["kontakti_id"]] = {"person": row["asiakas_id"], "when": row["tapahtuma_aikaleima"],
                                        "urgency": row["hoidon_kiireellisyys_selite_hilmo"].strip()}
    return contacts


def _clinical_appointment_encounter(person: str, contact_id: str, emitted: set[str], doctor_contacts: set[str]) -> str:
    """Which lens a clinical appointment opens: the doctor's when the person has that lens and either has no
    nurse lens or the appointment's contact carries a doctor-authored note; otherwise the nurse's. The scheduling
    table names no specialty, so this is the only reading of the release that can tell the two apart. A person
    with neither lens gets no encounter link, as before."""
    medication = f"veil-enc-{person}-medication"
    mental = f"veil-enc-{person}-mental"
    if mental in emitted and (medication not in emitted or contact_id in doctor_contacts):
        return mental
    return medication if medication in emitted else ""


def _doctor_contacts(source: Path) -> set[str]:
    """The contact ids with a clinical note the source itself attributes to a doctor (`clinician_role`)."""
    notes = source / "clinical/text/clinical-notes.csv"
    if not notes.is_file():
        return set()
    with notes.open(encoding="utf-8", newline="") as stream:
        return {row["visit_id"] for row in csv.DictReader(stream)
                if (row.get("clinician_role") or "").strip() == "lääkäri"}


def _appointments(source: Path, scope: set[str], contacts: dict[str, dict], person_by_hetu: dict[str, str],
                  emitted: set[str], written: set[str]) -> tuple[list[dict], dict[str, AppointmentStats]]:
    """The bounded people's appointments on or after REFERENCE_DATE, from both domains (see the module docstring)."""
    stats = {"ajanvaraus": AppointmentStats(), "kotihoito_kontakti": AppointmentStats()}
    rows: list[dict] = []
    doctor_contacts = _doctor_contacts(source)
    clinical = source / "clinical/tables/ajanvaraus.csv"
    if clinical.is_file():
        st = stats["ajanvaraus"]
        for row in _rows(clinical, AJANVARAUS_COLUMNS):
            person = row["asiakas_id"]
            if person not in scope:
                continue
            st.rows += 1
            text = row["ajanvaraus_pvm"].strip()[:10]
            try:
                when = date.fromisoformat(text) if DATE_ONLY.fullmatch(text) else None
            except ValueError:
                when = None
            if when is None:
                st.skipped_dates += 1
                continue
            if when < REFERENCE_DATE:
                st.past += 1
                continue
            contact = contacts.get(row["kontakti_id"])
            same_person = contact is not None and contact["person"] == person
            contact_id = f"veil-visit-{row['kontakti_id']}"
            encounter_id = _clinical_appointment_encounter(person, row["kontakti_id"], emitted, doctor_contacts)
            rows.append({"appointment_id": f"veil-appt-{row['ajanvaraus_id']}", "source_person_id": person,
                         "domain": TREATMENT, "scheduled_on": when.isoformat(),
                         "status_text": row["ajanvaraus_tila"].strip(),
                         "urgency_text": contact["urgency"] if same_person else "",
                         "specialty": row["erikoisala_kansallinen_id"].strip(),
                         "contact_record_id": contact_id if contact_id in written else "",
                         "encounter_id": encounter_id if encounter_id in emitted else ""})
            st.written += 1
    social = source / "social/tables/kotihoito_kontakti.csv"
    if social.is_file():
        st = stats["kotihoito_kontakti"]
        days: dict[tuple[str, date], int] = {}
        for row in _rows(social, HOMECARE_COLUMNS):
            person = person_by_hetu.get(row["henkilotunnus"])
            if person is None or person not in scope:
                continue
            st.rows += 1
            text = row["tapahtuma_pvm"].strip()[:10]
            try:
                when = date.fromisoformat(text) if DATE_ONLY.fullmatch(text) else None
            except ValueError:
                when = None
            if when is None:
                st.skipped_dates += 1
                continue
            if when < REFERENCE_DATE:
                st.past += 1
                continue
            if (person, when) in days:
                st.merged += 1
            days[(person, when)] = days.get((person, when), 0) + 1
        for (person, when), _count in sorted(days.items()):
            encounter_id = f"veil-enc-{person}-functioning"
            rows.append({"appointment_id": f"veil-homeappt-{person}-{when.strftime('%Y%m%d')}",
                         "source_person_id": person, "domain": SOCIAL_CARE, "scheduled_on": when.isoformat(),
                         "status_text": "", "urgency_text": "", "specialty": "", "contact_record_id": "",
                         "encounter_id": encounter_id if encounter_id in emitted else ""})
            st.written += 1
    rows.sort(key=lambda row: (row["scheduled_on"], row["appointment_id"]))
    return rows, stats


def admits(record: dict, visibility: dict, role: str, purpose: str) -> bool:
    """The purpose filter `app/policy.py` applies (types, topics, consent scope, excluded topics, sensitivity),
    restated over a written record so the builder can decide which lenses a person qualifies for."""
    cfg = visibility["roles"][role]
    rank = visibility["sensitivity_rank"]
    return (record["record_type"] in PURPOSE_FILTERED_TYPES
            and record["topic"] in PURPOSE_TOPICS[purpose]
            and record["consent_scope"] in cfg["consent_scopes"]
            and record["topic"] not in cfg["exclude_topics"]
            and rank[record["sensitivity_tag"]] <= rank[cfg["max_sensitivity"]])


def _medication_lens_eligible(records: list[dict]) -> bool:
    """A person gets the medication lens when they have a running medication OR a long-term diagnosis.

    `tila == "aktiivinen"` is the builder's own reading of the source (see `_medication_status` and `_diagnoosi`). A
    `lääkitys`/`diagnoosi` whose `tila` was left out (blank/unparseable source value) is not `aktiivinen` and does not
    qualify.
    """
    return any(r["record_type"] == "lääkitys" and r["payload"].get("tila") == "aktiivinen" for r in records) or any(
        r["record_type"] == "diagnoosi" and r["payload"].get("tila") == "aktiivinen" for r in records)


def _mental_lens_eligible(records: list[dict]) -> bool:
    """A person gets the doctor lens when they have a chapter-F diagnosis OR an antidepressant/anxiolytic medication.

    `icd10` is the release's own code (see `_diagnoosi`) and `lääke` its own substance name (see `_laakitys`); the
    gate reads those, not the authored label. Neither `tila` matters here: a finished course of escitalopram or a
    depression diagnosis that is not flagged long-term is still what a mental-health assessment reads. A diagnosis
    whose code was left out, or a medication whose name was, does not qualify.
    """
    return any(r["record_type"] == "diagnoosi"
               and str(r["payload"].get("icd10", "")).upper().startswith(MENTAL_HEALTH_ICD10_PREFIX)
               for r in records) or any(
        r["record_type"] == "lääkitys" and str(r["payload"].get("lääke", "")).casefold() in MENTAL_HEALTH_MEDICATIONS
        for r in records)


def build(source: Path, out: Path, people: int = DEFAULT_PEOPLE) -> Summary:
    if people < 1:
        raise SystemExit("build_veil_corpus: --people must be at least 1")
    if not source.is_dir():
        raise SystemExit(f"build_veil_corpus: --source is not a directory: {source}")
    if out.resolve().is_relative_to(ROOT):
        raise SystemExit("build_veil_corpus: --out must lie outside the repository (the corpus is deleted after the event)")
    ids = _people(source, people)
    if len(ids) < people:
        raise SystemExit(f"build_veil_corpus: the release registers only {len(ids)} people; --people={people}")
    scope = set(ids)
    lab_contacts = _lab_contact_times(source)
    person_by_hetu = _person_by_henkilotunnus(source)
    stats = {table.key: TableStats() for table in TABLES}
    records: list[dict] = []
    record_ids: set[str] = set()
    by_person: dict[str, list[dict]] = {person: [] for person in ids}
    capped_by_person: dict[str, int] = {}
    future_by_person: dict[str, int] = {}
    for table in TABLES:
        st = stats[table.key]
        ctx = {"lab_contact": lab_contacts, "person_by_henkilotunnus": person_by_hetu}
        if table.cap is not None:
            rows, keep = _capped_order(source, table, scope, ctx, table.cap)
        else:
            rows, keep = _rows(source / table.path, table.columns), None
        for row in rows:
            person = _subject(row, table, ctx)
            if person is None:
                st.unlinked += 1
                continue
            if person not in scope:
                continue
            st.rows += 1
            drafts = table.map(row, ctx)
            if drafts[0].when is None:
                st.unlinked += 1
                continue
            parsed = _parse_recorded_at(drafts[0].when)
            if parsed is None:
                st.skipped_dates += 1
                continue
            if keep is not None and parsed.date() > REFERENCE_DATE:
                # Not history yet: excluded before the cap, so it never takes a slot, and counted apart from `capped`.
                st.future_excluded += 1
                future_by_person[person] = future_by_person.get(person, 0) + 1
                continue
            if keep is not None and drafts[0].record_id not in keep:
                st.capped += 1
                capped_by_person[person] = capped_by_person.get(person, 0) + 1
                continue
            for draft in drafts:
                if not draft.payload:
                    st.empty += 1
                    continue
                record_type = draft.record_type or table.record_type
                record = {"record_id": draft.record_id, "source_person_id": person,
                          "record_type": record_type, "authored_by_role": draft.role,
                          "recorded_at": parsed.isoformat(), "topic": draft.topic or table.topic,
                          "sensitivity_tag": SENSITIVITY_TAG,
                          "consent_scope": draft.consent_scope or table.consent_scope,
                          "payload": draft.payload}
                if draft.heading is not None:
                    record["heading"] = draft.heading
                if record["record_id"] in record_ids:
                    # A repeated source key would emit two records under one id: refused, never deduplicated.
                    raise SystemExit(f"build_veil_corpus: duplicate record_id {record['record_id']!r}; "
                                     "a source key repeats in the release")
                record_ids.add(record["record_id"])
                records.append(record)
                by_person[person].append(record)
                st.records += 1
                st.useful += bool(USEFUL[record_type] & draft.payload.keys())

    try:
        visibility = json.loads(VISIBILITY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"build_veil_corpus: cannot read {VISIBILITY}: {exc}") from exc
    encounters = []
    for person in ids:
        for name, role, purpose in LENSES:
            if name == "medication":
                eligible = _medication_lens_eligible(by_person[person])
            elif name == "mental":
                eligible = _mental_lens_eligible(by_person[person])
            else:
                eligible = any(admits(record, visibility, role, purpose) for record in by_person[person])
            if eligible:
                encounters.append({
                    "encounter_id": f"veil-enc-{person}-{name}", "source_person_id": person, "role": role,
                    "purpose": purpose, "example_label": f"Synteettinen asiakas {person}",
                    # The purpose's own label: the text before the colon in the codebase's description.
                    "demo_name": PURPOSE_DEMO_NAMES[purpose],
                    "consent_holders": "", "life_stage": ""})

    appointments, appointment_stats = _appointments(
        source, scope, _contacts(source), person_by_hetu,
        {row["encounter_id"] for row in encounters}, record_ids)

    out.mkdir(parents=True, exist_ok=True)
    profiles_path, encounters_path, records_path = (out / "profiles.csv", out / "encounters.csv",
                                                    out / "records.jsonl")
    appointments_path = out / "appointments.csv"
    with appointments_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=APPOINTMENT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(appointments)
    with profiles_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(PROFILE_COLUMNS)
        for person in ids:
            writer.writerow((person, f"veil-{person}", f"Synteettinen asiakas {person}"))
    with encounters_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=ENCOUNTER_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(encounters)
    with records_path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    written = sum(path.stat().st_size for path in (profiles_path, encounters_path, records_path, appointments_path))
    return Summary(people=len(ids), records=len(records), encounters=len(encounters),
                   appointments=len(appointments), appointment_stats=appointment_stats,
                   skipped_unparseable_dates=sum(st.skipped_dates for st in stats.values()),
                   unlinked=sum(st.unlinked for st in stats.values()), bytes=written, stats=stats,
                   capped=sum(st.capped for st in stats.values()), capped_by_person=capped_by_person,
                   future_excluded=sum(st.future_excluded for st in stats.values()),
                   future_excluded_by_person=future_by_person)


def axis_lines() -> list[str]:
    """The declared permission axes, printed as one table. The release carries none of them."""
    lines = [f"axes (declared in build_veil_corpus.py; the release carries none): sensitivity_tag={SENSITIVITY_TAG}",
             f"  {'source table':<24}{'record_type':<20}{'consent_scope':<15}{'topic':<20}authored_by_role"]
    for table in TABLES:
        lines.append(f"  {table.key:<24}{table.record_type:<20}{table.consent_scope:<15}{table.topic:<20}"
                     f"{table.author}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the VEIL canonical corpus (see the module docstring).")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--people", type=int, default=DEFAULT_PEOPLE)
    args = parser.parse_args(argv)
    summary = build(args.source, args.out, args.people)
    print(f"people={summary.people} records={summary.records} encounters={summary.encounters} "
          f"appointments={summary.appointments} "
          f"skipped_unparseable_dates={summary.skipped_unparseable_dates} unlinked={summary.unlinked} "
          f"capped={summary.capped} future_excluded={summary.future_excluded} bytes={summary.bytes}")
    for table in TABLES:
        st = summary.stats[table.key]
        print(f"  {table.key:<24}rows={st.records} useful={st.useful} source_rows={st.rows} "
              f"skipped_unparseable_dates={st.skipped_dates} unlinked={st.unlinked} capped={st.capped} "
              f"future_excluded={st.future_excluded} "
              f"empty_payload={st.empty}")
    for key, st in summary.appointment_stats.items():
        print(f"  appointments from {key:<18}written={st.written} source_rows={st.rows} past={st.past} "
              f"merged_same_day={st.merged} skipped_unparseable_dates={st.skipped_dates}")
    if summary.capped_by_person:
        drops = " ".join(f"{person}={count}" for person, count in sorted(summary.capped_by_person.items()))
        print(f"  home-care cap dropped per person (cap {HOMECARE_CAP}): {drops}")
    if summary.future_excluded_by_person:
        later = " ".join(f"{person}={count}" for person, count in sorted(summary.future_excluded_by_person.items()))
        print(f"  home-care rows dated after {REFERENCE_DATE} excluded per person: {later}")
    print(f"written: {args.out}")
    for line in axis_lines():
        print(line)
    print(f"icd10 labels: {len(ICD10_FI)} authored in ICD10_FI (WORK-ORDER-VEIL-2); the release carries none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
