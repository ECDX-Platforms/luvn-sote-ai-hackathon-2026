"""The VEIL corpus builder (`scripts/build_veil_corpus.py`) against a fixture in the release's own shape.

Every test here builds from a small fixture written under `tmp_path`, so none needs the 687 MB release. The one
test that reads the real release skips with a stated reason when it is absent.

What the fixture pins, by the numbers it is built from:

* the bounded subset: the first `people` ids with a contact row, not the lexicographically first ids overall;
* per-table counts: records written, useful records, source rows in scope, rows skipped for an unparseable
  date, and empty payloads, so a row cannot vanish or double-count without a number moving;
* the exact record ids, so a renamed or duplicated id fails by name;
* the translations (copied strings, the fixed sex/language/flag wordings) and the omissions (a blank cell or an
  unknown code is left out, never guessed);
* the lens admission rule, against the role's own visibility table;
* the dry run and the strict import, which must accept the corpus as the importer defines acceptance.
* WORK-ORDER-VEIL-2: the medication reading against `REFERENCE_DATE` (an end on the date itself is running), the authored
  ICD labels, the `henkilotunnus` join for the two social tables, the home-care cap with its per-person drop count,
  ascending emission after the cap, and that `mittaus` records come only from note lines that carry a value.
* WORK-ORDER-VEIL-3: the home-care `aihe` label, selected by the real `selection.baseline` for the functioning lens; the
  cap over rows dated on or before `REFERENCE_DATE` only, with the rows after it counted apart as `future_excluded`.

The dry run is pinned to `warning` with exactly seven render keys missing from the mapped payload, not to `ready`:
`ready` requires every render key of every present type. Four of the seven are absent from the work order's payloads;
the three lab keys (`tulkinta`, `viitearvo`, `yksikkö`) appear once the lab rows are dated and written. `yksikkö` is in
the release only as free text (`unit=` inside `TulokseenLiittyvaInfokentta`), and mapping that is a decision not made here.
Making it `ready` would mean inventing values or changing the reconciler, which this change does not do.
"""

from __future__ import annotations

import csv
import filecmp
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import get_args

import pytest

from app import delivery, policy, purpose, selection, source, upm
from app.encounters import resolve_encounter
from app.policy import cut_for_encounter
from scripts import build_veil_corpus as builder

ROOT = Path(__file__).resolve().parents[2]
REAL_RELEASE = builder.DEFAULT_SOURCE

ASIAKAS_HEADER = ["asiakas_id", "henkilotunnus", "kotikunta", "sukupuoli", "syntymavuosi", "syntymapaiva",
                  "kieli", "onko_testiasiakas"]
KONTAKTI_HEADER = ["kontakti_id", "asiakas_id", "tapahtuma_pvm", "tapahtuma_aikaleima", "kesto_minuutit_kpl",
                   "yhteystapa_koodi_hilmo", "erikoisala_kansallinen_id", "hoidon_kiireellisyys_koodi_hilmo",
                   "hoidon_kiireellisyys_selite_hilmo", "palvelumuoto_id"]
DIAGNOOSI_HEADER = ["diagnoosi_id", "icd10_id", "icd10_koodi_lahdejarjestelma", "kontakti_id", "asiakas_id",
                    "diagnoosi_aikaleima", "diagnoosi_ensisijaisuus", "onko_pitkaaikaisdiagnoosi"]
LAAKITYS_HEADER = ["laakitys_avain", "laakitys_aloituspvm", "laakitys_paattymispvm", "asiakas_id", "ika",
                   "ikaluokka", "henkilotunnus", "sukupuoli", "on_resepti", "vaikuttava_aine", "atc_koodi",
                   "laakkeen_kayttotapa"]
LAB_LINK_HEADER = ["Avain", "kontakti_id"]
VASTAUS_HEADER = ["Avain", "asiakas_id", "TapahtumatyyppiKoodi", "TapahtumatyyppiSelite", "KayntienLukumaara",
                  "TutkimusNumerotunnusAvain", "SanallinenTutkimustulos", "NumeerinenTutkimustulos",
                  "ViitearvonYlitys", "TulokseenLiittyvaInfokentta", "LiittyyLausuntoKoodi",
                  "LiittyyLausuntoSelite", "TutkimuksenKiireellisyysSelite", "EfficanKayntityyppi",
                  "HoidollinenErikoisalaLahetteeltaAvain", "PaatutkimuksenNro", "PaatutkimuksenLyhenne",
                  "NäytteenottoaikajaotteluTyoaikaMuuaika", "NaytteenottoaikaPaiva"]
NOTES_HEADER = ["subject_id", "visit_id", "visit_date", "clinician_role", "phase", "contact_type", "urgency",
                "primary_diagnosis_code", "note_text"]
AJANVARAUS_HEADER = ["ajanvaraus_id", "kontakti_id", "asiakas_id", "ajanvaraus_pvm", "ajanvaraus_tila",
                     "erikoisala_kansallinen_id"]
# Appointments. APT-1 is on a written contact with an urgency; APT-2 is before REFERENCE_DATE (history, not written);
# APT-3 is ON the reference date for a person with no medication lens; APT-4 is outside the bound; APT-5 has no
# calendar date; APT-6 names a contact the release does not carry, so it has no urgency and no contact record.
AJANVARAUS = [
    {"ajanvaraus_id": "APT-1", "kontakti_id": "CON-01a", "asiakas_id": "SYN-T-01", "ajanvaraus_pvm": "2026-10-20",
     "ajanvaraus_tila": "Toteutunut", "erikoisala_kansallinen_id": "98"},
    {"ajanvaraus_id": "APT-2", "kontakti_id": "CON-02a", "asiakas_id": "SYN-T-02", "ajanvaraus_pvm": "2025-01-05",
     "ajanvaraus_tila": "Toteutunut", "erikoisala_kansallinen_id": "98"},
    {"ajanvaraus_id": "APT-3", "kontakti_id": "CON-04a", "asiakas_id": "SYN-T-04", "ajanvaraus_pvm": "2026-10-13",
     "ajanvaraus_tila": "Peruttu", "erikoisala_kansallinen_id": "98"},
    {"ajanvaraus_id": "APT-4", "kontakti_id": "CON-05a", "asiakas_id": "SYN-T-05", "ajanvaraus_pvm": "2026-10-21",
     "ajanvaraus_tila": "Toteutunut", "erikoisala_kansallinen_id": "98"},
    {"ajanvaraus_id": "APT-5", "kontakti_id": "CON-01b", "asiakas_id": "SYN-T-01", "ajanvaraus_pvm": "2026-13-01",
     "ajanvaraus_tila": "Toteutunut", "erikoisala_kansallinen_id": "98"},
    {"ajanvaraus_id": "APT-6", "kontakti_id": "CON-X", "asiakas_id": "SYN-T-02", "ajanvaraus_pvm": "2026-11-02",
     "ajanvaraus_tila": "Toteutunut", "erikoisala_kansallinen_id": "98"},
]
PAATOS_HEADER = ["paatos_id", "asiakas_id", "paatos_pvm", "paatos_alku_pvm", "paatos_loppu_pvm",
                 "onko_toistaiseksi_voimassa", "palveluluokitus_nimi", "palvelutehtava_nimi", "ratkaisu_laji",
                 "selvitys_id"]
SOCIAL_NOTES_HEADER = ["subject_id", "service", "service_task", "decision_id", "decision_outcome",
                       "decision_date", "decision_text", "plan_date", "plan_text"]

# Fixture people: T-01, T-02 and T-04 are inside `people=3`; T-03 has no contact row, so it is never a candidate
# even though it sorts between them; T-05 sorts after the bound and must not appear anywhere.
ASIAKAS = [
    {"asiakas_id": "SYN-T-01", "henkilotunnus": "HEN-T-01", "kotikunta": "Espoo", "sukupuoli": "MIES", "syntymavuosi": "1967",
     "syntymapaiva": "1967-04-03", "kieli": "fi"},
    # A blank birth date: the perustieto row is skipped and counted.
    {"asiakas_id": "SYN-T-02", "henkilotunnus": "HEN-T-02", "kotikunta": "Kirkkonummi", "sukupuoli": "NAINEN", "syntymavuosi": "1989",
     "syntymapaiva": "", "kieli": "sv"},
    {"asiakas_id": "SYN-T-03", "henkilotunnus": "HEN-T-03", "kotikunta": "Espoo", "sukupuoli": "NAINEN", "syntymavuosi": "1990",
     "syntymapaiva": "1990-06-17", "kieli": "fi"},
    # An unknown language code: äidinkieli is left out, not guessed.
    {"asiakas_id": "SYN-T-04", "henkilotunnus": "HEN-T-04", "kotikunta": "Espoo", "sukupuoli": "NAINEN", "syntymavuosi": "2004",
     "syntymapaiva": "2004-03-16", "kieli": "xx"},
    {"asiakas_id": "SYN-T-05", "henkilotunnus": "HEN-T-05", "kotikunta": "Espoo", "sukupuoli": "MIES", "syntymavuosi": "1980",
     "syntymapaiva": "1980-01-01", "kieli": "en"},
]
KONTAKTI = [
    {"kontakti_id": "CON-01a", "asiakas_id": "SYN-T-01", "tapahtuma_aikaleima": "2025-03-01T09:30:00.000000",
     "yhteystapa_koodi_hilmo": "R10", "erikoisala_kansallinen_id": "98",
     "hoidon_kiireellisyys_selite_hilmo": "Kiireetön hoito"},
    {"kontakti_id": "CON-01b", "asiakas_id": "SYN-T-01", "tapahtuma_aikaleima": "2025-04-01T10:00:00.000000",
     "yhteystapa_koodi_hilmo": "R52", "erikoisala_kansallinen_id": "98"},
    # A blank contact timestamp: skipped and counted.
    {"kontakti_id": "CON-02a", "asiakas_id": "SYN-T-02", "tapahtuma_aikaleima": "",
     "yhteystapa_koodi_hilmo": "R10", "erikoisala_kansallinen_id": "98"},
    {"kontakti_id": "CON-04a", "asiakas_id": "SYN-T-04", "tapahtuma_aikaleima": "2025-06-01T08:00:00.000000",
     "yhteystapa_koodi_hilmo": "R10", "erikoisala_kansallinen_id": "98"},
    {"kontakti_id": "CON-02b", "asiakas_id": "SYN-T-02", "tapahtuma_aikaleima": "2025-07-01T08:00:00.000000",
     "yhteystapa_koodi_hilmo": "R10", "erikoisala_kansallinen_id": "98"},
    {"kontakti_id": "CON-05a", "asiakas_id": "SYN-T-05", "tapahtuma_aikaleima": "2025-06-01T08:00:00.000000",
     "yhteystapa_koodi_hilmo": "R10", "erikoisala_kansallinen_id": "98"},
]
DIAGNOOSI = [
    {"diagnoosi_id": "DX-1", "icd10_id": "R10.4", "kontakti_id": "CON-01a", "asiakas_id": "SYN-T-01",
     "diagnoosi_aikaleima": "2024-06-23T12:29:00.000000", "onko_pitkaaikaisdiagnoosi": "false"},
    {"diagnoosi_id": "DX-2", "icd10_id": "J06.9", "kontakti_id": "CON-01b", "asiakas_id": "SYN-T-01",
     "diagnoosi_aikaleima": "2025-03-15T10:55:00.000000", "onko_pitkaaikaisdiagnoosi": "true"},
    # An unparseable timestamp: skipped and counted.
    {"diagnoosi_id": "DX-3", "icd10_id": "E03.9", "kontakti_id": "CON-02b", "asiakas_id": "SYN-T-02",
     "diagnoosi_aikaleima": "not-a-date", "onko_pitkaaikaisdiagnoosi": "true"},
    # A blank long-term flag: tila is left out, the diagnosis itself is kept.
    {"diagnoosi_id": "DX-4", "icd10_id": "I10", "kontakti_id": "CON-04a", "asiakas_id": "SYN-T-04",
     "diagnoosi_aikaleima": "2025-06-01T08:00:00.000000", "onko_pitkaaikaisdiagnoosi": ""},
    {"diagnoosi_id": "DX-5", "icd10_id": "Z99", "kontakti_id": "CON-05a", "asiakas_id": "SYN-T-05",
     "diagnoosi_aikaleima": "2025-06-01T08:00:00.000000", "onko_pitkaaikaisdiagnoosi": "true"},
    # A chapter-F diagnosis that is NOT long-term: it does not open the medication lens, but it opens the doctor lens.
    {"diagnoosi_id": "DX-6", "icd10_id": "F41.9", "kontakti_id": "CON-02b", "asiakas_id": "SYN-T-02",
     "diagnoosi_aikaleima": "2025-07-01T08:00:00.000000", "onko_pitkaaikaisdiagnoosi": "false"},
]
LAAKITYS = [
    {"laakitys_avain": "MED-1", "laakitys_aloituspvm": "2025-06-14", "laakitys_paattymispvm": "2025-06-20",
     "asiakas_id": "SYN-T-01", "vaikuttava_aine": "parasetamoli", "laakkeen_kayttotapa": "TARVITTAESSA"},
    # No end date: still active.
    {"laakitys_avain": "MED-2", "laakitys_aloituspvm": "2025-11-14", "laakitys_paattymispvm": "",
     "asiakas_id": "SYN-T-02", "vaikuttava_aine": "diklofenaakki", "laakkeen_kayttotapa": "SÄÄNNÖLLINEN"},
    # 2025-02-30 is not a calendar date: skipped and counted.
    {"laakitys_avain": "MED-3", "laakitys_aloituspvm": "2025-02-30", "laakitys_paattymispvm": "",
     "asiakas_id": "SYN-T-04", "vaikuttava_aine": "metformiini", "laakkeen_kayttotapa": "SÄÄNNÖLLINEN"},
    {"laakitys_avain": "MED-4", "laakitys_aloituspvm": "2025-06-01", "laakitys_paattymispvm": "",
     "asiakas_id": "SYN-T-05", "vaikuttava_aine": "ramipriili", "laakkeen_kayttotapa": "SÄÄNNÖLLINEN"},
    # Ends after the reference date: running. A medication ending exactly on it is running too (on or after).
    {"laakitys_avain": "MED-5", "laakitys_aloituspvm": "2026-09-01", "laakitys_paattymispvm": "2027-03-31",
     "asiakas_id": "SYN-T-01", "vaikuttava_aine": "amlodipiini", "laakkeen_kayttotapa": "SÄÄNNÖLLINEN"},
    {"laakitys_avain": "MED-6", "laakitys_aloituspvm": "2026-01-10", "laakitys_paattymispvm": "2026-10-13",
     "asiakas_id": "SYN-T-02", "vaikuttava_aine": "simvastatiini", "laakkeen_kayttotapa": "SÄÄNNÖLLINEN"},
    # A finished antidepressant course (N06AB10): not running, so T-04 still gets no medication lens, but the substance
    # opens the doctor lens on its own.
    {"laakitys_avain": "MED-7", "laakitys_aloituspvm": "2025-01-10", "laakitys_paattymispvm": "2025-07-10",
     "asiakas_id": "SYN-T-04", "vaikuttava_aine": "essitalopraami", "atc_koodi": "N06AB10",
     "laakkeen_kayttotapa": "SÄÄNNÖLLINEN"},
]
# The lab rows carry a day index in NaytteenottoaikaPaiva, which is never read as a date. Each row's time is its
# linked contact's. Outcomes: 1000001 and 1000002 link cleanly; 1000003 is outside the bound; 1000004 links to
# another person's contact; 1000005 has no link row; 1000006 has two links; 1000007 links to a contact with a blank
# timestamp (skipped and counted as unparseable).
VASTAUS = [
    {"Avain": "1000001", "asiakas_id": "SYN-T-01", "NumeerinenTutkimustulos": "89.863",
     "PaatutkimuksenLyhenne": "P-Krea", "NaytteenottoaikaPaiva": "4"},
    {"Avain": "1000002", "asiakas_id": "SYN-T-02", "NumeerinenTutkimustulos": "3.50",
     "PaatutkimuksenLyhenne": "P-Gluk", "NaytteenottoaikaPaiva": "1"},
    {"Avain": "1000003", "asiakas_id": "SYN-T-05", "NumeerinenTutkimustulos": "7.0",
     "PaatutkimuksenLyhenne": "P-TSH", "NaytteenottoaikaPaiva": "2"},
    {"Avain": "1000004", "asiakas_id": "SYN-T-01", "NumeerinenTutkimustulos": "5.1",
     "PaatutkimuksenLyhenne": "B-HbA1c", "NaytteenottoaikaPaiva": "3"},
    {"Avain": "1000005", "asiakas_id": "SYN-T-04", "NumeerinenTutkimustulos": "4.4",
     "PaatutkimuksenLyhenne": "P-Krea", "NaytteenottoaikaPaiva": "5"},
    {"Avain": "1000006", "asiakas_id": "SYN-T-02", "NumeerinenTutkimustulos": "2.2",
     "PaatutkimuksenLyhenne": "P-TSH", "NaytteenottoaikaPaiva": "6"},
    {"Avain": "1000007", "asiakas_id": "SYN-T-02", "NumeerinenTutkimustulos": "1.1",
     "PaatutkimuksenLyhenne": "P-Gluk", "NaytteenottoaikaPaiva": "7"},
]
LAB_LINKS = [
    # 1000001 links to the later of T-01's two contacts: a nearest-by-date or first-match join would pick CON-01a.
    {"Avain": "1000001", "kontakti_id": "CON-01b"}, {"Avain": "1000002", "kontakti_id": "CON-02b"},
    {"Avain": "1000003", "kontakti_id": "CON-05a"}, {"Avain": "1000004", "kontakti_id": "CON-04a"},
    {"Avain": "1000006", "kontakti_id": "CON-02a"}, {"Avain": "1000006", "kontakti_id": "CON-02b"},
    {"Avain": "1000007", "kontakti_id": "CON-02a"},
]
# A note in the release's own markdown shape: every section is read into `osiot`, the measurements section yields one
# `mittaus` record per line that carries a value (the two lines below; the third carries none), and the risk and
# functioning sections become records of their own. The quote and the blank line test verbatim copying.
SECTIONED_NOTE = (
    '## KÄYNNIN TIEDOT\nLyhyt teksti, "lainaus"\nrivi kolme\n\n'
    "## TULOSYY JA ESITIEDOT\nPotilaan kertoman mukaan olkapään kipu.\n\n"
    "## RISKITIEDOT\nLääkeaineallergioista ei ole tietoa.\n\n"
    "## FYSIOLOGISET MITTAUKSET\nLämpö 37,2 °C.\nVerenpaine vastaanotolla istuen levon jälkeen 158/92 mmHg.\n"
    "Ei muita mittauksia.\n\n"
    "## TOIMINTAKYKY\nYläraajan käyttö hartiatason yläpuolella on vaikeutunut.\n\n"
    "## SUUNNITELMA\nUusi arvio oireiden pitkittyessä."
)
SECTIONS = {
    "KÄYNNIN TIEDOT": 'Lyhyt teksti, "lainaus"\nrivi kolme',
    "TULOSYY JA ESITIEDOT": "Potilaan kertoman mukaan olkapään kipu.",
    "RISKITIEDOT": "Lääkeaineallergioista ei ole tietoa.",
    "FYSIOLOGISET MITTAUKSET": "Lämpö 37,2 °C.\nVerenpaine vastaanotolla istuen levon jälkeen 158/92 mmHg.\nEi muita mittauksia.",
    "TOIMINTAKYKY": "Yläraajan käyttö hartiatason yläpuolella on vaikeutunut.",
    "SUUNNITELMA": "Uusi arvio oireiden pitkittyessä.",
}
NOTES = [
    # Two notes on one visit: the ordinal makes the ids distinct, in file order.
    {"subject_id": "SYN-T-01", "visit_id": "CON-01a", "visit_date": "2025-03-01", "clinician_role": "lääkäri",
     "phase": "Ensikäynti", "contact_type": "Asiakkaan käynti vastaanotolla",
     "note_text": SECTIONED_NOTE},
    {"subject_id": "SYN-T-01", "visit_id": "CON-01a", "visit_date": "2025-03-01", "clinician_role": "sairaanhoitaja",
     "phase": "Seuranta", "contact_type": "Puhelu", "note_text": "toinen merkintä"},
    # A blank clinician role: falls back to sairaanhoitaja.
    {"subject_id": "SYN-T-02", "visit_id": "CON-02b", "visit_date": "2025-07-01", "clinician_role": "",
     "phase": "Seuranta", "contact_type": "Etäasiointi", "note_text": "Hoitajan muistiinpano"},
    {"subject_id": "SYN-T-05", "visit_id": "CON-05a", "visit_date": "2025-06-01", "clinician_role": "lääkäri",
     "phase": "Ensikäynti", "contact_type": "Vastaanotto", "note_text": "ulkopuolinen"},
]
PAATOS = [
    {"paatos_id": "DEC-1", "asiakas_id": "SYN-T-01", "paatos_pvm": "2026-01-15",
     "onko_toistaiseksi_voimassa": "true", "palveluluokitus_nimi": "Aikuissosiaalityö ja sosiaaliohjaus"},
    # An empty validity flag: voimassa is left out, not defaulted to määräaikainen.
    {"paatos_id": "DEC-2", "asiakas_id": "SYN-T-04", "paatos_pvm": "2026-02-01",
     "onko_toistaiseksi_voimassa": "", "palveluluokitus_nimi": "Säännöllinen kotihoito"},
    {"paatos_id": "DEC-3", "asiakas_id": "SYN-T-05", "paatos_pvm": "2026-02-01",
     "onko_toistaiseksi_voimassa": "true", "palveluluokitus_nimi": "Säännöllinen kotihoito"},
]
SOCIAL_NOTES = [
    # Has a plan: a second record, dated with the decision (the table's declared recorded_at).
    {"subject_id": "SYN-T-01", "decision_id": "DEC-1", "decision_outcome": "Myönteinen",
     "decision_date": "2026-01-15", "decision_text": "# PÄÄTÖSLUONNOS\nTeksti", "plan_date": "2026-02-01",
     "plan_text": "Suunnitelma"},
    {"subject_id": "SYN-T-04", "decision_id": "DEC-2", "decision_outcome": "Kielteinen",
     "decision_date": "2026-02-01", "decision_text": "Päätösteksti", "plan_date": "", "plan_text": ""},
    {"subject_id": "SYN-T-05", "decision_id": "DEC-3", "decision_outcome": "Myönteinen",
     "decision_date": "2026-02-01", "decision_text": "ulkopuolinen", "plan_date": "", "plan_text": ""},
]

PALVELUTARPEEN_HEADER = ["palvelutarpeen_arviointi_avain", "henkilotunnus", "kunta", "ika", "alkaen_pvm", "saakka_pvm",
                         "vireilletulo_pvm", "vireilletulo_palvelutarpeen_arviointi_kasittelyaika",
                         "vireilletulo_palvelu_kasittelyaika_viikko_luokittelu",
                         "vireilletulo_palvelutarpeen_arviointi_kasittelyaika_tyopaivia",
                         "vireilletulo_palvelu_kasittelyaika_tyopaivia_viikko_luokittelu", "palveluprosessi",
                         "palvelutarpeen_arvionti_tila", "vireilletulo_avain", "palvelutarpeen_arviointi",
                         "tuen_tarve_selite", "suorituspaikan_nimi", "tukilajin_nimi", "asiakkuuden_nimi",
                         "palvelutarpeen_arviointi_luokiteltu", "onko_testiasiakas"]
KOTIHOITO_HEADER = ["kontakti_id", "tapahtuma_pvm", "kontaktin_kesto_yhteensa", "kestoluokka", "ika", "henkilotunnus",
                    "kotikunta", "kayntityyppi", "yhteystapa", "yhteystapa_ylataso", "yhteystapa_koodi_kansallinen",
                    "yhteystapa_selite", "kotihoito_palvelu_tunnus"]
# Service-needs assessments, joined by henkilotunnus. SEL-T3 matches nobody; SEL-T4 is another person's; SEL-T5 has no date.
NEEDS = [
    {"palvelutarpeen_arviointi_avain": "SEL-T1", "henkilotunnus": "HEN-T-01", "alkaen_pvm": "2026-03-02",
     "tuen_tarve_selite": 'Kotona selviytymisen tuki; "lisäapu" 2 kertaa viikossa',
     "palvelutarpeen_arviointi": "Palvelutarpeen arviointi", "onko_testiasiakas": "true"},
    # A blank selite: the record is written, teksti is left out.
    {"palvelutarpeen_arviointi_avain": "SEL-T2", "henkilotunnus": "HEN-T-02", "alkaen_pvm": "2026-04-01",
     "tuen_tarve_selite": "", "palvelutarpeen_arviointi": "Palvelutarpeen arviointi", "onko_testiasiakas": "true"},
    {"palvelutarpeen_arviointi_avain": "SEL-T3", "henkilotunnus": "HEN-T-99", "alkaen_pvm": "2026-05-01",
     "tuen_tarve_selite": "Arjen hallinnan tuki", "onko_testiasiakas": "true"},
    {"palvelutarpeen_arviointi_avain": "SEL-T4", "henkilotunnus": "HEN-T-05", "alkaen_pvm": "2026-05-01",
     "tuen_tarve_selite": "Arjen tuki", "onko_testiasiakas": "true"},
    {"palvelutarpeen_arviointi_avain": "SEL-T5", "henkilotunnus": "HEN-T-04", "alkaen_pvm": "",
     "tuen_tarve_selite": "Perhetyön tuki", "onko_testiasiakas": "true"},
]


def _home(kontakti_id: str, day: str, henkilotunnus: str) -> dict:
    return {"kontakti_id": kontakti_id, "tapahtuma_pvm": day, "henkilotunnus": henkilotunnus,
            "kayntityyppi": "SIM-Kotihoidon käynti", "yhteystapa": "SIM-Kotikäynti"}


# Home-care contacts. T-01 has 12 dated past rows and one undated: the cap keeps the 10 most recent, and the two
# 2025-01-03 rows tie at the cut, so the lower id ("03a") is the one dropped. T-01 also has two rows dated AFTER
# REFERENCE_DATE, the newest of all: a cap that ranked them would keep them and drop 2025 rows, so they are excluded
# before the cap. The file lists T-01 DESCENDING, so emission order is proven by sorting, not by file order. HOME-T02-R
# is dated on REFERENCE_DATE itself (history, kept); HOME-T02-F is after it. HOME-T99 matches nobody; HOME-T05 is
# another person's.
KOTIHOITO = [
    *(_home(f"HOME-T01-{day:02d}", f"2025-01-{day:02d}", "HEN-T-01") for day in range(12, 3, -1)),
    _home("HOME-T01-03b", "2025-01-03", "HEN-T-01"),
    _home("HOME-T01-03a", "2025-01-03", "HEN-T-01"),
    _home("HOME-T01-01", "2025-01-01", "HEN-T-01"),
    _home("HOME-T01-X", "not-a-date", "HEN-T-01"),
    _home("HOME-T01-F1", "2026-12-01", "HEN-T-01"),
    _home("HOME-T01-F2", "2026-10-14", "HEN-T-01"),
    _home("HOME-T02-1", "2025-02-01", "HEN-T-02"),
    _home("HOME-T02-2", "2025-02-02", "HEN-T-02"),
    _home("HOME-T02-3", "2025-02-03", "HEN-T-02"),
    _home("HOME-T02-R", "2026-10-13", "HEN-T-02"),
    _home("HOME-T02-F", "2026-10-14", "HEN-T-02"),
    _home("HOME-T99-1", "2025-02-01", "HEN-T-99"),
    _home("HOME-T05-1", "2025-02-01", "HEN-T-05"),
]
HOMECARE_KEPT_T01 = ["03b", *(f"{day:02d}" for day in range(4, 13))]
HOMECARE_KEPT_T02 = ["1", "2", "3", "R"]
HOMECARE_KEPT = [f"HOME-T01-{suffix}" for suffix in HOMECARE_KEPT_T01] + [
    f"HOME-T02-{suffix}" for suffix in HOMECARE_KEPT_T02]

EXPECTED_IDS = {
    "veil-basic-SYN-T-01", "veil-basic-SYN-T-04",
    "veil-diag-DX-1", "veil-diag-DX-2", "veil-diag-DX-4", "veil-diag-DX-6",
    "veil-med-MED-1", "veil-med-MED-2", "veil-med-MED-7",
    "veil-visit-CON-01a", "veil-visit-CON-01b", "veil-visit-CON-04a", "veil-visit-CON-02b",
    "veil-note-SYN-T-01-CON-01a-1", "veil-note-SYN-T-01-CON-01a-2", "veil-note-SYN-T-02-CON-02b-1",
    "veil-meas-SYN-T-01-CON-01a-1-1", "veil-meas-SYN-T-01-CON-01a-1-2", "veil-risk-SYN-T-01-CON-01a-1",
    "veil-func-SYN-T-01-CON-01a-1",
    "veil-lab-1000001", "veil-lab-1000002",
    "veil-dec-DEC-1", "veil-dec-DEC-2",
    "veil-snote-DEC-1", "veil-snote-DEC-1-plan", "veil-snote-DEC-2",
    "veil-med-MED-5", "veil-med-MED-6", "veil-needs-SEL-T1", "veil-needs-SEL-T2",
    *(f"veil-homecare-{kontakti_id}" for kontakti_id in HOMECARE_KEPT),
}
# (records, useful, source rows in scope, skipped for an unparseable date, unlinked, empty payload)
EXPECTED_STATS = {
    "perustieto": (2, 2, 3, 1, 0, 0), "diagnoosi": (4, 4, 5, 1, 0, 0), "lääkitys": (5, 5, 6, 1, 0, 0),
    "asiointitapahtuma": (4, 4, 5, 1, 0, 0), "tutkimustulos": (2, 2, 6, 1, 3, 0),
    "vapaateksti (clinical)": (7, 7, 3, 0, 0, 0), "päätös": (2, 2, 2, 0, 0, 0),
    "vapaateksti (social)": (3, 3, 2, 0, 0, 0),
    "vapaateksti (needs)": (2, 1, 3, 1, 1, 0), "kotihoito_kontakti": (14, 14, 20, 1, 1, 0),
}
# The seven render keys missing from the mapped payload. The fixture and the real
# release report the same set.
DECLARED_GAPS = {"asiointitapahtuma": {"suunta", "yhteenveto"}, "lääkitys": {"puuttuneet_annokset_30d"},
                 "perustieto": {"asumismuoto"}, "tutkimustulos": {"tulkinta", "viitearvo", "yksikkö"},
                 # The notes carry temperatures and blood pressures; the other measurement keys have no source line.
                 "mittaus": {"paino_kg", "pituus_cm", "syke"}}
# Strings the builder itself writes (translations and flags). Every other payload string must be the release's
# own text, copied verbatim (case-insensitively for the lowercased annos).
DECLARED_OUTPUTS = {"nainen", "mies", "suomi", "ruotsi", "englanti", "aktiivinen", "ei_aktiivinen",
                    "päättynyt", "toistaiseksi", "määräaikainen",
                    # The heading the work order fixes; the release carries the same text in its own column.
                    "Palvelutarpeen arviointi",
                    # The child-protection heading the builder authors from the source classification (CHILD_PROTECTION_HEADING).
                    "Lastensuojelun palvelutarpeen arviointi",
                    # The home-care label the builder authors (HOMECARE_AIHE); the release carries the source spelling.
                    "Kotihoito",
                    # The headings of the two note-section records (RISKS_HEADING, FUNCTIONING_HEADING).
                    "Riskitiedot", "Toimintakyky"}


def _write_csv(path: Path, header: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=header, restval="")
        writer.writeheader()
        writer.writerows(rows)


def _write_release(root: Path) -> Path:
    """A fixture in the release's own layout: the same file names, the same headers, the same column order."""
    source_dir = root / "2024-2026"
    _write_csv(source_dir / "clinical/tables/asiakas.csv", ASIAKAS_HEADER, ASIAKAS)
    _write_csv(source_dir / "clinical/tables/kontakti.csv", KONTAKTI_HEADER, KONTAKTI)
    _write_csv(source_dir / "clinical/tables/diagnoosi.csv", DIAGNOOSI_HEADER, DIAGNOOSI)
    _write_csv(source_dir / "clinical/tables/laakitys.csv", LAAKITYS_HEADER, LAAKITYS)
    _write_csv(source_dir / "clinical/tables/vastausarkistohistoria_view.csv", VASTAUS_HEADER, VASTAUS)
    _write_csv(source_dir / "clinical/tables/laboratorio_kontakti.csv", LAB_LINK_HEADER, LAB_LINKS)
    _write_csv(source_dir / "clinical/text/clinical-notes.csv", NOTES_HEADER, NOTES)
    _write_csv(source_dir / "clinical/tables/ajanvaraus.csv", AJANVARAUS_HEADER, AJANVARAUS)
    _write_csv(source_dir / "social/tables/paatos.csv", PAATOS_HEADER, PAATOS)
    _write_csv(source_dir / "social/text/social-notes.csv", SOCIAL_NOTES_HEADER, SOCIAL_NOTES)
    _write_csv(source_dir / "social/tables/palvelutarpeen_arviointi.csv", PALVELUTARPEEN_HEADER, NEEDS)
    _write_csv(source_dir / "social/tables/kotihoito_kontakti.csv", KOTIHOITO_HEADER, KOTIHOITO)
    return source_dir


@pytest.fixture
def release(tmp_path: Path) -> Path:
    return _write_release(tmp_path / "release")


@pytest.fixture
def built(release: Path, tmp_path: Path):
    out = tmp_path / "corpus"
    return builder.build(release, out, people=3), out


def _records(out: Path) -> list[dict]:
    return [json.loads(line) for line in (out / "records.jsonl").read_text(encoding="utf-8").splitlines()]


def _baseline_for(corpus: Path, import_dir: Path, encounter_id: str):
    """The real `selection.baseline` over an encounter's lens: the real import, the real resolve and the real cut."""
    delivery.import_delivery(corpus, import_dir)
    index = delivery.DeliveryIndex(import_dir)
    try:
        encounters = index.encounter_index()
        persona = index[encounters[encounter_id].persona_id]
        resolved, bound = resolve_encounter(encounter_id, {persona.persona_id: persona}, encounters)
        return selection.baseline(cut_for_encounter(bound, resolved), builder.REFERENCE_DATE)
    finally:
        index.close()


def test_the_subset_is_the_first_people_with_contacts_and_nothing_else(built):
    summary, out = built
    assert summary.people == 3
    profiles = list(csv.DictReader((out / "profiles.csv").open(encoding="utf-8")))
    assert [row["source_person_id"] for row in profiles] == ["SYN-T-01", "SYN-T-02", "SYN-T-04"]
    people = {record["source_person_id"] for record in _records(out)}
    assert people == {"SYN-T-01", "SYN-T-02", "SYN-T-04"}, "T-03 has no contact; T-05 is outside the bound"


def test_per_table_counts_reconcile_row_for_row(built):
    summary, _ = built
    observed = {key: (st.records, st.useful, st.rows, st.skipped_dates, st.unlinked, st.empty)
                for key, st in summary.stats.items()}
    assert observed == EXPECTED_STATS
    assert summary.records == 45 and summary.skipped_unparseable_dates == 7 and summary.unlinked == 5
    assert summary.encounters == 7


def test_the_exact_record_ids_are_written_once_each(built):
    _, out = built
    ids = [record["record_id"] for record in _records(out)]
    assert len(ids) == len(set(ids)), "a duplicate id would be refused by the importer"
    assert set(ids) == EXPECTED_IDS


def test_translations_are_the_fixed_wordings_and_omissions_leave_the_field_out(built):
    _, out = built
    by_id = {record["record_id"]: record for record in _records(out)}
    assert by_id["veil-basic-SYN-T-01"]["payload"] == {
        "näyttötunniste": "SYN-T-01", "sukupuoli": "mies", "syntymävuosi": 1967, "kotikunta": "Espoo",
        "äidinkieli": "suomi"}
    assert "äidinkieli" not in by_id["veil-basic-SYN-T-04"]["payload"], "an unknown language is left out"
    assert by_id["veil-basic-SYN-T-04"]["payload"]["sukupuoli"] == "nainen"
    assert by_id["veil-diag-DX-1"]["payload"]["tila"] == "ei_aktiivinen"
    assert by_id["veil-diag-DX-2"]["payload"]["tila"] == "aktiivinen"
    assert "tila" not in by_id["veil-diag-DX-4"]["payload"], "a blank flag is left out"
    assert by_id["veil-med-MED-1"]["payload"]["tila"] == "päättynyt"
    assert by_id["veil-med-MED-2"]["payload"]["tila"] == "aktiivinen"
    assert by_id["veil-med-MED-2"]["payload"]["annos"] == "säännöllinen"
    assert "voimassa" not in by_id["veil-dec-DEC-2"]["payload"], "an empty validity flag is never defaulted"
    assert by_id["veil-dec-DEC-1"]["payload"]["voimassa"] == "toistaiseksi"


def test_icd10_codes_are_copied_and_each_label_is_the_authored_one(built):
    _, out = built
    records = _records(out)
    assert {r["payload"]["icd10"] for r in records if r["record_type"] == "diagnoosi"} == {
        "R10.4", "J06.9", "I10", "F41.9"}
    assert {r["payload"]["nimi"] for r in records if r["record_type"] == "diagnoosi"} == {
        "Muu ja määrittämätön vatsakipu", "Äkillinen ylähengitystieinfektio, määrittämätön", "Kohonnut verenpaine",
        "Ahdistuneisuushäiriö, määrittämätön"}
    assert {r["payload"]["kanava"] for r in records if r["record_type"] == "asiointitapahtuma"
            and r["record_id"].startswith("veil-visit-")} == {"R10", "R52"}


def test_every_payload_string_is_a_declared_output_or_the_release_text_verbatim(release, built):
    _, out = built
    # Parsed cell values, not raw bytes: the CSV writer doubles embedded quotes, so a raw substring check would miss them.
    cells = set()
    for path in release.rglob("*.csv"):
        with path.open(encoding="utf-8", newline="") as stream:
            cells.update(cell for row in csv.reader(stream) for cell in row)
    lowered = {cell.lower() for cell in cells}
    declared = DECLARED_OUTPUTS | set(builder.ICD10_FI.values())
    notes = {record["record_id"]: record["payload"]["teksti"] for record in _records(out)
             if record["record_id"].startswith("veil-note-")}
    for record in _records(out):
        parent = notes.get(record["payload"].get("lähde_record_id", ""), "")
        for key, value in record["payload"].items():
            if key == "lähde_record_id" or not isinstance(value, str) or value in declared:
                continue
            # A section record's text and a measurement's value are read out of one note: a substring of that note,
            # never a cell of their own, and never text from any other note.
            assert value in cells or value.lower() in lowered or (parent and value in parent), (
                f"{record['record_id']}.{key}={value!r} is neither a declared output nor a cell of the release")


# Every copied-text payload pinned in full, with the person whose row it came from. A swap of any field between
# people or rows (heading, body, phase, decision name) changes a payload and fails by name.
# Every record the fixture writes, pinned in full: the person and every payload value. An exact match covers every
# copied text field of every record, so a text or person swapped between any two records fails here.
OWN_PAYLOAD = {
    "veil-basic-SYN-T-01": ("SYN-T-01", {"näyttötunniste": "SYN-T-01", "sukupuoli": "mies",
                                         "syntymävuosi": 1967, "äidinkieli": "suomi", "kotikunta": "Espoo"}),
    "veil-basic-SYN-T-04": ("SYN-T-04", {"näyttötunniste": "SYN-T-04", "sukupuoli": "nainen",
                                         "syntymävuosi": 2004, "kotikunta": "Espoo"}),
    "veil-diag-DX-1": ("SYN-T-01", {"nimi": "Muu ja määrittämätön vatsakipu", "icd10": "R10.4",
                                     "tila": "ei_aktiivinen"}),
    "veil-diag-DX-2": ("SYN-T-01", {"nimi": "Äkillinen ylähengitystieinfektio, määrittämätön", "icd10": "J06.9",
                                     "tila": "aktiivinen"}),
    "veil-diag-DX-4": ("SYN-T-04", {"nimi": "Kohonnut verenpaine", "icd10": "I10"}),
    "veil-diag-DX-6": ("SYN-T-02", {"nimi": "Ahdistuneisuushäiriö, määrittämätön", "icd10": "F41.9",
                                    "tila": "ei_aktiivinen"}),
    "veil-med-MED-1": ("SYN-T-01", {"lääke": "parasetamoli", "annos": "tarvittaessa", "tila": "päättynyt"}),
    "veil-med-MED-2": ("SYN-T-02", {"lääke": "diklofenaakki", "annos": "säännöllinen", "tila": "aktiivinen"}),
    "veil-med-MED-5": ("SYN-T-01", {"lääke": "amlodipiini", "annos": "säännöllinen", "tila": "aktiivinen"}),
    "veil-med-MED-6": ("SYN-T-02", {"lääke": "simvastatiini", "annos": "säännöllinen", "tila": "aktiivinen"}),
    "veil-med-MED-7": ("SYN-T-04", {"lääke": "essitalopraami", "annos": "säännöllinen", "tila": "päättynyt"}),
    "veil-needs-SEL-T1": ("SYN-T-01", {"otsikko": "Palvelutarpeen arviointi",
                                        "teksti": 'Kotona selviytymisen tuki; "lisäapu" 2 kertaa viikossa'}),
    "veil-needs-SEL-T2": ("SYN-T-02", {"otsikko": "Palvelutarpeen arviointi"}),
    "veil-visit-CON-01a": ("SYN-T-01", {"kanava": "R10", "aihe": "98"}),
    "veil-visit-CON-01b": ("SYN-T-01", {"kanava": "R52", "aihe": "98"}),
    "veil-visit-CON-04a": ("SYN-T-04", {"kanava": "R10", "aihe": "98"}),
    "veil-visit-CON-02b": ("SYN-T-02", {"kanava": "R10", "aihe": "98"}),
    "veil-lab-1000001": ("SYN-T-01", {"tutkimus": "P-Krea", "arvo": "89.863"}),
    "veil-lab-1000002": ("SYN-T-02", {"tutkimus": "P-Gluk", "arvo": "3.50"}),
    "veil-note-SYN-T-01-CON-01a-1": ("SYN-T-01", {"otsikko": "Asiakkaan käynti vastaanotolla",
                                                  "teksti": NOTES[0]["note_text"], "käyntityyppi": "Ensikäynti",
                                                  "osiot": SECTIONS}),
    "veil-meas-SYN-T-01-CON-01a-1-1": ("SYN-T-01", {"lämpö_c": 37.2, "lähde_record_id": "veil-note-SYN-T-01-CON-01a-1"}),
    "veil-meas-SYN-T-01-CON-01a-1-2": ("SYN-T-01", {"verenpaine": "158/92",
                                                    "lähde_record_id": "veil-note-SYN-T-01-CON-01a-1"}),
    "veil-risk-SYN-T-01-CON-01a-1": ("SYN-T-01", {"otsikko": "Riskitiedot", "teksti": "Lääkeaineallergioista ei ole tietoa.",
                                                  "lähde_record_id": "veil-note-SYN-T-01-CON-01a-1"}),
    "veil-func-SYN-T-01-CON-01a-1": ("SYN-T-01", {"otsikko": "Toimintakyky",
                                                  "teksti": "Yläraajan käyttö hartiatason yläpuolella on vaikeutunut.",
                                                  "lähde_record_id": "veil-note-SYN-T-01-CON-01a-1"}),
    "veil-note-SYN-T-01-CON-01a-2": ("SYN-T-01", {"otsikko": "Puhelu", "teksti": "toinen merkintä",
                                                  "käyntityyppi": "Seuranta"}),
    "veil-note-SYN-T-02-CON-02b-1": ("SYN-T-02", {"otsikko": "Etäasiointi", "teksti": "Hoitajan muistiinpano",
                                                  "käyntityyppi": "Seuranta"}),
    "veil-dec-DEC-1": ("SYN-T-01", {"päätös": "Aikuissosiaalityö ja sosiaaliohjaus", "voimassa": "toistaiseksi"}),
    "veil-dec-DEC-2": ("SYN-T-04", {"päätös": "Säännöllinen kotihoito"}),
    "veil-snote-DEC-1": ("SYN-T-01", {"otsikko": "Myönteinen", "teksti": SOCIAL_NOTES[0]["decision_text"]}),
    "veil-snote-DEC-1-plan": ("SYN-T-01", {"teksti": "Suunnitelma"}),
    "veil-snote-DEC-2": ("SYN-T-04", {"otsikko": "Kielteinen", "teksti": "Päätösteksti"}),
}


OWN_PAYLOAD.update({f"veil-homecare-HOME-T01-{suffix}": ("SYN-T-01", {"kanava": "SIM-Kotikäynti",
                                                                    "aihe": "Kotihoito"})
                    for suffix in HOMECARE_KEPT_T01})
OWN_PAYLOAD.update({f"veil-homecare-HOME-T02-{suffix}": ("SYN-T-02", {"kanava": "SIM-Kotikäynti",
                                                                    "aihe": "Kotihoito"})
                    for suffix in HOMECARE_KEPT_T02})


def test_each_copied_text_payload_is_its_own_rows_text_under_its_own_person(built):
    _, out = built
    by_id = {record["record_id"]: record for record in _records(out)}
    assert set(by_id) == set(OWN_PAYLOAD), "every written record is pinned here"
    for record_id, (person, payload) in OWN_PAYLOAD.items():
        assert by_id[record_id]["source_person_id"] == person, record_id
        assert by_id[record_id]["payload"] == payload, record_id


def test_a_repeated_contact_id_is_refused_not_last_wins(release, tmp_path):
    # The same person, the same id, a different visit time: the link would name two visits, so the build refuses.
    repeated = dict(KONTAKTI[0], tapahtuma_aikaleima="2025-02-02T09:00:00.000000")
    _write_csv(release / "clinical/tables/kontakti.csv", KONTAKTI_HEADER, KONTAKTI + [repeated])
    with pytest.raises(SystemExit, match="duplicate kontakti_id 'CON-01a'"):
        builder.build(release, tmp_path / "out", people=3)


def test_a_repeated_lab_key_is_refused_not_emitted_twice(release, tmp_path):
    _write_csv(release / "clinical/tables/vastausarkistohistoria_view.csv", VASTAUS_HEADER, VASTAUS + [VASTAUS[0]])
    with pytest.raises(SystemExit, match="duplicate record_id 'veil-lab-1000001'"):
        builder.build(release, tmp_path / "out", people=3)


def test_the_notes_are_unedited_and_their_ids_are_per_visit_in_file_order(built):
    _, out = built
    by_id = {record["record_id"]: record for record in _records(out)}
    assert by_id["veil-note-SYN-T-01-CON-01a-1"]["payload"]["teksti"] == NOTES[0]["note_text"]
    assert by_id["veil-note-SYN-T-01-CON-01a-2"]["payload"]["teksti"] == "toinen merkintä"
    assert by_id["veil-note-SYN-T-02-CON-02b-1"]["authored_by_role"] == "sairaanhoitaja", "blank role falls back"
    assert by_id["veil-note-SYN-T-01-CON-01a-1"]["authored_by_role"] == "lääkäri", "the release's own role"


def test_the_social_plan_is_a_second_record_dated_with_its_decision(built):
    _, out = built
    by_id = {record["record_id"]: record for record in _records(out)}
    plan = by_id["veil-snote-DEC-1-plan"]
    assert plan["payload"] == {"teksti": "Suunnitelma"}
    assert plan["recorded_at"] == by_id["veil-snote-DEC-1"]["recorded_at"] == "2026-01-15T00:00:00+00:00"
    assert "veil-snote-DEC-2-plan" not in by_id, "an empty plan writes no record"


def test_unparseable_dates_are_skipped_and_counted_not_epoch_dated(built):
    _, out = built
    records = _records(out)
    assert all(record["recorded_at"].startswith(("19", "20")) for record in records)
    assert not any(record["recorded_at"].startswith("1970") for record in records), (
        "a bare integer NaytteenottoaikaPaiva must not be read as epoch seconds")
    assert {record["record_id"] for record in records if record["record_type"] == "tutkimustulos"} == {
        "veil-lab-1000001", "veil-lab-1000002"}, "the unlinked lab rows are counted, never written"


def test_a_lab_row_is_dated_by_its_linked_contact_never_by_its_day_index(built):
    _, out = built
    by_id = {record["record_id"]: record for record in _records(out)}
    assert by_id["veil-lab-1000001"]["recorded_at"] == "2025-04-01T10:00:00+00:00", "CON-01b, not CON-01a"
    assert by_id["veil-lab-1000002"]["recorded_at"] == "2025-07-01T08:00:00+00:00"


def test_the_lens_gate_emits_each_lens_only_for_its_qualifying_people(built):
    """T-04 has no running medication and no long-term diagnosis, so it gets no medication lens; it keeps the functioning
    lens. T-01 carries only somatic records, so it gets no doctor lens; T-02 (an F-diagnosis) and T-04 (a finished
    escitalopram course) do."""
    _, out = built
    rows = list(csv.DictReader((out / "encounters.csv").open(encoding="utf-8")))
    assert [(row["source_person_id"], row["purpose"]) for row in rows] == [
        ("SYN-T-01", "medication_follow_up"), ("SYN-T-01", "functioning_service_needs"),
        ("SYN-T-02", "medication_follow_up"), ("SYN-T-02", "functioning_service_needs"),
        ("SYN-T-02", "mental_health_assessment"),
        ("SYN-T-04", "functioning_service_needs"), ("SYN-T-04", "mental_health_assessment")]
    assert list(rows[0].keys()) == list(builder.ENCOUNTER_COLUMNS)
    assert {row["consent_holders"] for row in rows} == {""} and {row["life_stage"] for row in rows} == {""}


def test_a_chapter_f_diagnosis_opens_the_doctor_lens_with_its_role_purpose_and_id(built):
    """The doctor lens: role `laakari`, purpose `mental_health_assessment`, id `veil-enc-<person>-mental`, the
    purpose's own demo name. T-02's F41.9 is not long-term and its running medications are somatic, so the F code alone
    is what opens it."""
    _, out = built
    rows = {row["encounter_id"]: row for row in csv.DictReader((out / "encounters.csv").open(encoding="utf-8"))}
    doctor = rows["veil-enc-SYN-T-02-mental"]
    assert (doctor["source_person_id"], doctor["role"], doctor["purpose"]) == (
        "SYN-T-02", "laakari", "mental_health_assessment")
    assert doctor["demo_name"] == "Mielenterveyden arvio"
    assert doctor["role"] in purpose.palvelukonteksti("mental_health_assessment").roles
    assert {row["encounter_id"] for row in rows.values() if row["purpose"] == "mental_health_assessment"} == {
        "veil-enc-SYN-T-02-mental", "veil-enc-SYN-T-04-mental"}
    assert all(row["encounter_id"] == f"veil-enc-{row['source_person_id']}-{name}"
               for row in rows.values() for name, _role, purpose_ in builder.LENSES if row["purpose"] == purpose_)


def test_a_person_with_only_somatic_records_gets_no_doctor_lens(built):
    """T-01 has R10.4, J06.9, paracetamol and amlodipine: no F code, no antidepressant, no doctor lens."""
    _, out = built
    rows = list(csv.DictReader((out / "encounters.csv").open(encoding="utf-8")))
    assert "veil-enc-SYN-T-01-mental" not in {row["encounter_id"] for row in rows}
    records = [r for r in _records(out) if r["source_person_id"] == "SYN-T-01"]
    assert not builder._mental_lens_eligible(records)
    assert not any(r["payload"].get("icd10", "").startswith("F") for r in records if r["record_type"] == "diagnoosi")


def test_the_doctor_lens_gate_reads_the_code_prefix_and_the_substance_not_the_status():
    """A finished antidepressant course and a non-long-term F diagnosis both qualify; a hypnotic and a somatic code do not."""
    f_dx = {"record_type": "diagnoosi", "payload": {"nimi": "F32.9", "icd10": "F32.9", "tila": "ei_aktiivinen"}}
    somatic = {"record_type": "diagnoosi", "payload": {"nimi": "Kohonnut verenpaine", "icd10": "I10", "tila": "aktiivinen"}}
    ssri_done = {"record_type": "lääkitys", "payload": {"lääke": "essitalopraami", "tila": "päättynyt"}}
    melatonin = {"record_type": "lääkitys", "payload": {"lääke": "melatoniini", "tila": "aktiivinen"}}
    unlabelled = {"record_type": "diagnoosi", "payload": {"nimi": "F99"}}  # no code written: does not qualify
    assert builder._mental_lens_eligible([somatic, f_dx])
    assert builder._mental_lens_eligible([melatonin, ssri_done])
    assert not builder._mental_lens_eligible([somatic, melatonin, unlabelled])
    assert not builder._mental_lens_eligible([])
    assert builder.MENTAL_HEALTH_MEDICATIONS == {"essitalopraami"}, "the one N06A/N05B substance the release carries"


def test_a_child_protection_assessment_is_headed_as_such():
    base = {"palvelutarpeen_arviointi_avain": "SEL-CP", "henkilotunnus": "HEN-T-01",
            "alkaen_pvm": "2026-01-01", "tuen_tarve_selite": "Lastensuojelun tarve"}
    cp = builder._palvelutarve(dict(base, palvelutarpeen_arviointi_luokiteltu="SIM-LASTENSUOJELUN-PALVELUTARPEEN-ARVIOINTI"), {})
    assert cp[0].payload["otsikko"] == "Lastensuojelun palvelutarpeen arviointi"
    plain = builder._palvelutarve(dict(base, palvelutarpeen_arviointi_luokiteltu="SIM-PALVELUTARPEEN-ARVIOINTI"), {})
    assert plain[0].payload["otsikko"] == "Palvelutarpeen arviointi"
    blank = builder._palvelutarve(dict(base, palvelutarpeen_arviointi_luokiteltu=""), {})
    assert blank[0].payload["otsikko"] == "Palvelutarpeen arviointi"


def test_the_axes_are_declared_vocabulary_and_the_restated_constants_match_the_app(built):
    _, out = built
    assert builder.PURPOSE_FILTERED_TYPES == policy.PURPOSE_FILTERED_TYPES, "restatement drifted from policy.py"
    for name, topics_ in builder.PURPOSE_TOPICS.items():
        assert topics_ == purpose.palvelukonteksti(name).topics, f"{name} topics drifted from app/purpose.py"
        assert builder.PURPOSE_DEMO_NAMES[name] == purpose.palvelukonteksti(name).description.split(":")[0]
    assert {table.record_type for table in builder.TABLES} <= set(source.RECORD_TYPES)
    expected_useful = {key: value for key, value in delivery.USEFUL_PAYLOAD_KEYS.items() if key in builder.USEFUL}
    assert expected_useful == builder.USEFUL, "restatement drifted from delivery.py"
    assert builder.APPOINTMENT_COLUMNS == delivery.APPOINTMENT_COLUMNS, "restatement drifted from delivery.py"
    assert builder.SENSITIVITY_TAG in get_args(upm.SensitivityTag)
    assert {builder.TREATMENT, builder.SOCIAL_CARE} <= set(get_args(upm.ConsentScope))
    topics = set(purpose.topics())
    for record in _records(out):
        assert record["topic"] in topics and record["consent_scope"] in get_args(upm.ConsentScope)
        assert record["sensitivity_tag"] == "none" and record["authored_by_role"].strip()


def test_the_builder_is_deterministic_byte_for_byte(release, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    builder.build(release, first, people=3)
    builder.build(release, second, people=3)
    for name in ("profiles.csv", "encounters.csv", "records.jsonl"):
        assert filecmp.cmp(first / name, second / name, shallow=False), name


def test_a_source_missing_a_declared_column_is_refused_by_name(release, tmp_path):
    path = release / "clinical/tables/diagnoosi.csv"
    text = path.read_text(encoding="utf-8").replace(",onko_pitkaaikaisdiagnoosi", "")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(SystemExit, match="onko_pitkaaikaisdiagnoosi"):
        builder.build(release, tmp_path / "out", people=3)


def test_the_corpus_is_refused_inside_the_repository_and_a_short_subset_is_refused(release, tmp_path):
    with pytest.raises(SystemExit, match="outside the repository"):
        builder.build(release, ROOT / "data" / "experimental" / "veil-should-never-exist", people=3)
    # The fixture has four people with a contact row (T-01, T-02, T-04, T-05); T-03 has none.
    with pytest.raises(SystemExit, match="only 4 people"):
        builder.build(release, tmp_path / "never-written", people=5)


def test_the_fixture_dry_run_is_warning_on_exactly_the_declared_render_gaps(built, tmp_path):
    summary, out = built
    report = delivery.dry_run_delivery(out, tmp_path / "dry")
    body = json.loads(report.report_path.read_text(encoding="utf-8"))
    assert body["status"] == "warning", "ready needs every render key; seven are missing from the mapped payload"
    assert body["refusals"] == [] and body["unmapped_record_types"] == {}
    assert body["records_seen"] == summary.records
    gaps = {record_type: set(item["missing_render_keys"]) for record_type, item in body["types"].items()
            if item["missing_render_keys"]}
    assert gaps == DECLARED_GAPS
    # The one assessment with a blank selite is written with its heading only, as the work order requires; the dry run
    # counts that row as carrying no useful key. Pinned to exactly that one row, not waved through.
    assert {record_type: item["useful_set_miss_rows"] for record_type, item in body["types"].items()
            if item["useful_set_miss_rows"]} == {"vapaateksti": 1}
    assert all(item["content_maps_to_nothing"] == 0 for item in body["types"].values())


def test_the_fixture_imports_every_record_and_every_lens_resolves_and_renders(built, tmp_path):
    summary, out = built
    report = delivery.import_delivery(out, tmp_path / "import")
    assert (report.personas, report.records, report.skipped, report.encounters) == (3, 45, 0, 7)
    index = delivery.DeliveryIndex(tmp_path / "import")
    try:
        encounters = index.encounter_index()
        for encounter_id, encounter in encounters.items():
            persona = index[encounter.persona_id]
            resolved, bound = resolve_encounter(encounter_id, {persona.persona_id: persona}, encounters)
            scoped = cut_for_encounter(bound, resolved)
            assert scoped.records, f"{encounter_id} cut to nothing"
            assert any(bool(builder.USEFUL[record.record_type] & set(record.payload))
                       for record in scoped.records if record.record_type in builder.USEFUL), (
                f"{encounter_id} renders no useful content under its own lens")
    finally:
        index.close()


def test_the_active_medication_count_is_exact(built):
    _, out = built
    active = sorted(r["record_id"] for r in _records(out)
                    if r["record_type"] == "lääkitys" and r["payload"].get("tila") == "aktiivinen")
    # MED-1 ended before the reference date; MED-2 has no end date; MED-5 ends after it; MED-6 ends on it.
    assert active == ["veil-med-MED-2", "veil-med-MED-5", "veil-med-MED-6"]


def test_a_medication_ending_before_the_reference_date_is_finished_and_one_on_or_after_it_is_running(built):
    _, out = built
    by_id = {record["record_id"]: record for record in _records(out)}
    assert by_id["veil-med-MED-1"]["payload"]["tila"] == "päättynyt", "ended 2025-06-20, before 2026-10-13"
    assert by_id["veil-med-MED-5"]["payload"]["tila"] == "aktiivinen", "ends 2027-03-31, after the reference date"
    assert by_id["veil-med-MED-6"]["payload"]["tila"] == "aktiivinen", "ends on the reference date: on or after is running"


def test_an_end_date_that_is_not_a_calendar_date_leaves_the_status_out():
    assert builder._medication_status("2025-02-30") is None, "not a calendar date: left out, never guessed"
    assert builder._medication_status("2026-10-12") == "päättynyt"
    assert builder._medication_status("2026-10-13") == "aktiivinen"
    assert builder._medication_status("   ") == "aktiivinen", "a blank end date is no end date"


def test_every_emitted_diagnosis_name_is_an_authored_label_or_its_own_code(built):
    _, out = built
    labels = set(builder.ICD10_FI.values())
    diagnoses = [r for r in _records(out) if r["record_type"] == "diagnoosi"]
    assert diagnoses
    for record in diagnoses:
        nimi, code = record["payload"]["nimi"], record["payload"]["icd10"]
        assert nimi in labels or nimi == code, record["record_id"]


def test_a_code_outside_the_label_map_is_written_as_its_own_code():
    row = {"diagnoosi_id": "DX-9", "icd10_id": "Z99", "diagnoosi_aikaleima": "2025-06-01T08:00:00.000000",
           "onko_pitkaaikaisdiagnoosi": "true"}
    assert builder._diagnoosi(row, {})[0].payload == {"nimi": "Z99", "icd10": "Z99", "tila": "aktiivinen"}


def test_the_label_map_holds_exactly_the_29_authored_codes():
    assert len(builder.ICD10_FI) == 29
    assert "Z99" not in builder.ICD10_FI
    assert "verenpaine" in builder.ICD10_FI["I10"], "the side effect the docstring names"


def test_the_assessment_text_is_the_source_cell_unedited_and_a_blank_one_leaves_teksti_out(built):
    _, out = built
    by_id = {record["record_id"]: record for record in _records(out)}
    assert by_id["veil-needs-SEL-T1"]["payload"] == {
        "otsikko": "Palvelutarpeen arviointi", "teksti": 'Kotona selviytymisen tuki; "lisäapu" 2 kertaa viikossa'}
    assert by_id["veil-needs-SEL-T1"]["authored_by_role"] == "sosiaalityontekija"
    assert by_id["veil-needs-SEL-T2"]["payload"] == {"otsikko": "Palvelutarpeen arviointi"}


def test_the_home_care_cap_keeps_exactly_the_ten_most_recent_and_drops_the_rest(built):
    _, out = built
    records = _records(out)
    kept = {r["record_id"] for r in records if r["record_id"].startswith("veil-homecare-HOME-T01-")}
    assert kept == {f"veil-homecare-HOME-T01-{suffix}" for suffix in HOMECARE_KEPT_T01}
    assert len(kept) == builder.HOMECARE_CAP == 10
    written = {r["record_id"] for r in records}
    assert "veil-homecare-HOME-T01-01" not in written, "the oldest is dropped"
    assert "veil-homecare-HOME-T01-03a" not in written, "the tie at the cut goes to the higher id"
    assert "veil-homecare-HOME-T01-X" not in written, "an undated row is skipped, not ranked"
    dates = [r["recorded_at"] for r in records if r["record_id"].startswith("veil-homecare-HOME-T01-")]
    assert dates == sorted(dates), "kept rows are written in ascending date order"


def test_the_cap_drop_count_is_reported_per_person_and_per_table(built):
    summary, _ = built
    assert summary.capped == 2 and summary.capped_by_person == {"SYN-T-01": 2}
    assert summary.stats["kotihoito_kontakti"].capped == 2
    assert all(st.capped == 0 for key, st in summary.stats.items() if key != "kotihoito_kontakti")


def test_a_home_care_visit_is_labelled_kotihoito_and_the_real_baseline_selects_it(built, tmp_path):
    _, out = built
    # The source spelling holds neither baseline term, which is why the label is written (see the builder's HOMECARE_AIHE).
    assert "kotihoito" not in "sim-kotihoidon käynti" and "kotihoito" not in "kotihoidon palvelu"
    by_id = {record["record_id"]: record for record in _records(out)}
    home_care = [record_id for record_id in by_id if record_id.startswith("veil-homecare-")]
    assert home_care and all(by_id[record_id]["payload"]["aihe"] == "Kotihoito" for record_id in home_care)
    expected_by_person = {
        "SYN-T-01": {f"veil-homecare-HOME-T01-{suffix}" for suffix in HOMECARE_KEPT_T01},
        "SYN-T-02": {f"veil-homecare-HOME-T02-{suffix}" for suffix in HOMECARE_KEPT_T02},
    }
    assert len(expected_by_person["SYN-T-01"]) == builder.HOMECARE_CAP
    for person, expected in expected_by_person.items():
        chosen, reasons = _baseline_for(out, tmp_path / f"import-{person}", f"veil-enc-{person}-functioning")
        selected = {record.record_id for record in chosen if record.record_id.startswith("veil-homecare-")}
        assert selected == expected, person
        assert all(reasons[record_id] == ["keyword:kotihoito"] for record_id in expected), person


def test_the_cap_counts_only_past_rows_and_reports_future_rows_apart(built):
    summary, out = built
    written = {record["record_id"] for record in _records(out)}
    # Three future rows (two for T-01, one for T-02) are excluded before the cap, so they take no slot and are not written.
    assert not {"veil-homecare-HOME-T01-F1", "veil-homecare-HOME-T01-F2", "veil-homecare-HOME-T02-F"} & written
    assert "veil-homecare-HOME-T02-R" in written, "a row dated on REFERENCE_DATE itself is history, not future"
    assert summary.future_excluded == 3 and summary.future_excluded_by_person == {"SYN-T-01": 2, "SYN-T-02": 1}
    assert summary.stats["kotihoito_kontakti"].future_excluded == 3
    # The cap drops past rows only: its count is the same with or without the future rows, and holds none of them.
    assert summary.capped == 2 and summary.capped_by_person == {"SYN-T-01": 2}
    assert all(st.future_excluded == 0 for key, st in summary.stats.items() if key != "kotihoito_kontakti")
    assert all(record["recorded_at"][:10] <= builder.REFERENCE_DATE.isoformat() for record in _records(out)
               if record["record_id"].startswith("veil-homecare-"))


def test_a_visit_that_is_not_home_care_keeps_its_source_visit_type():
    row = {"kontakti_id": "HOME-X", "tapahtuma_pvm": "2025-02-01", "henkilotunnus": "HEN-T-01",
           "yhteystapa": "SIM-Kotikäynti", "kayntityyppi": "SIM-Puhelu"}
    assert builder._kotihoito(row, {})[0].payload == {"kanava": "SIM-Kotikäynti", "aihe": "SIM-Puhelu"}
    assert builder._kotihoito(dict(row, kayntityyppi="sim-KOTIHOIDON käynti"), {})[0].payload["aihe"] == "Kotihoito"


def test_every_row_of_the_two_new_tables_is_written_skipped_capped_or_empty(built):
    summary, _ = built
    for key in ("vapaateksti (needs)", "kotihoito_kontakti"):
        st = summary.stats[key]
        assert st.rows == st.records + st.skipped_dates + st.capped + st.future_excluded + st.empty, key


def test_a_row_whose_henkilotunnus_matches_nobody_is_skipped_and_counted(built):
    summary, out = built
    written = {r["record_id"] for r in _records(out)}
    assert "veil-needs-SEL-T3" not in written and "veil-homecare-HOME-T99-1" not in written
    assert summary.stats["vapaateksti (needs)"].unlinked == 1
    assert summary.stats["kotihoito_kontakti"].unlinked == 1
    assert "veil-needs-SEL-T4" not in written and "veil-homecare-HOME-T05-1" not in written, "another person's row"
    assert "veil-needs-SEL-T5" not in written and summary.stats["vapaateksti (needs)"].skipped_dates == 1


def test_a_note_is_read_by_its_sections_and_only_the_measurement_lines_with_a_value_become_records(built):
    _, out = built
    by_id = {record["record_id"]: record for record in _records(out)}
    assert all(table.record_type != "mittaus" for table in builder.TABLES), "mittaus is derived from notes, not a table"
    assert by_id["veil-note-SYN-T-01-CON-01a-1"]["payload"]["osiot"] == SECTIONS
    assert "osiot" not in by_id["veil-note-SYN-T-01-CON-01a-2"]["payload"], "a note with no heading has no sections"
    measurements = sorted(r["record_id"] for r in _records(out) if r["record_type"] == "mittaus")
    assert measurements == ["veil-meas-SYN-T-01-CON-01a-1-1", "veil-meas-SYN-T-01-CON-01a-1-2"], (
        "two of the three lines carry a value; `Ei muita mittauksia.` writes nothing")
    temperature, pressure = by_id[measurements[0]], by_id[measurements[1]]
    assert temperature["payload"]["lämpö_c"] == 37.2 and pressure["payload"]["verenpaine"] == "158/92"
    for derived in (temperature, pressure):
        assert derived["topic"] == "hoito" and derived["consent_scope"] == "treatment"
        assert derived["recorded_at"] == by_id["veil-note-SYN-T-01-CON-01a-1"]["recorded_at"]
        assert derived["authored_by_role"] == "lääkäri", "the note's own author"
    risk = by_id["veil-risk-SYN-T-01-CON-01a-1"]
    assert risk["record_type"] == "vapaateksti" and risk["consent_scope"] == "treatment" and "heading" not in risk
    functioning = by_id["veil-func-SYN-T-01-CON-01a-1"]
    assert functioning["record_type"] == "vapaateksti" and functioning["topic"] == "toimintakyky"
    assert functioning["consent_scope"] == "social_care" and functioning["heading"] == "Toimintakyky"
    assert functioning["payload"]["teksti"] in NOTES[0]["note_text"], "a substring of its own note, never edited"


def test_the_functioning_section_is_admitted_to_the_social_lens_and_the_note_itself_is_not(built, tmp_path):
    """The service-needs lens may see the clinician's functional-capacity statement and nothing else of the note."""
    _, out = built
    visibility = json.loads(builder.VISIBILITY.read_text(encoding="utf-8"))
    by_id = {record["record_id"]: record for record in _records(out)}
    social = ("sosiaalityontekija", "functioning_service_needs")
    assert builder.admits(by_id["veil-func-SYN-T-01-CON-01a-1"], visibility, *social)
    assert not builder.admits(by_id["veil-note-SYN-T-01-CON-01a-1"], visibility, *social)
    assert not builder.admits(by_id["veil-risk-SYN-T-01-CON-01a-1"], visibility, *social)
    nurse = ("sairaanhoitaja", "medication_follow_up")
    assert builder.admits(by_id["veil-note-SYN-T-01-CON-01a-1"], visibility, *nurse)
    assert not builder.admits(by_id["veil-func-SYN-T-01-CON-01a-1"], visibility, *nurse), (
        "the social-scoped section is not the nurse's; the nurse reads it inside the note")
    # The real cut agrees with the restated rule.
    delivery.import_delivery(out, tmp_path / "import")
    index = delivery.DeliveryIndex(tmp_path / "import")
    try:
        encounters = index.encounter_index()
        persona = index[encounters["veil-enc-SYN-T-01-functioning"].persona_id]
        resolved, bound = resolve_encounter("veil-enc-SYN-T-01-functioning", {persona.persona_id: persona}, encounters)
        visible = {record.record_id for record in cut_for_encounter(bound, resolved).records}
    finally:
        index.close()
    assert "veil-func-SYN-T-01-CON-01a-1" in visible and "veil-note-SYN-T-01-CON-01a-1" not in visible


# The appointments the fixture yields, in the written order (date, then id). A home-care appointment is one per
# person and day; a clinical one carries the source's status, the contact's urgency and the contact record when
# the release carries them, and the lens when it was emitted.
EXPECTED_APPOINTMENTS = [
    {"appointment_id": "veil-appt-APT-3", "source_person_id": "SYN-T-04", "domain": "treatment",
     "scheduled_on": "2026-10-13", "status_text": "Peruttu", "urgency_text": "", "specialty": "98",
     "contact_record_id": "veil-visit-CON-04a", "encounter_id": "veil-enc-SYN-T-04-mental"},
    {"appointment_id": "veil-homeappt-SYN-T-02-20261013", "source_person_id": "SYN-T-02", "domain": "social_care",
     "scheduled_on": "2026-10-13", "status_text": "", "urgency_text": "", "specialty": "", "contact_record_id": "",
     "encounter_id": "veil-enc-SYN-T-02-functioning"},
    {"appointment_id": "veil-homeappt-SYN-T-01-20261014", "source_person_id": "SYN-T-01", "domain": "social_care",
     "scheduled_on": "2026-10-14", "status_text": "", "urgency_text": "", "specialty": "", "contact_record_id": "",
     "encounter_id": "veil-enc-SYN-T-01-functioning"},
    {"appointment_id": "veil-homeappt-SYN-T-02-20261014", "source_person_id": "SYN-T-02", "domain": "social_care",
     "scheduled_on": "2026-10-14", "status_text": "", "urgency_text": "", "specialty": "", "contact_record_id": "",
     "encounter_id": "veil-enc-SYN-T-02-functioning"},
    {"appointment_id": "veil-appt-APT-1", "source_person_id": "SYN-T-01", "domain": "treatment",
     "scheduled_on": "2026-10-20", "status_text": "Toteutunut", "urgency_text": "Kiireetön hoito", "specialty": "98",
     "contact_record_id": "veil-visit-CON-01a", "encounter_id": "veil-enc-SYN-T-01-medication"},
    {"appointment_id": "veil-appt-APT-6", "source_person_id": "SYN-T-02", "domain": "treatment",
     "scheduled_on": "2026-11-02", "status_text": "Toteutunut", "urgency_text": "", "specialty": "98",
     "contact_record_id": "", "encounter_id": "veil-enc-SYN-T-02-medication"},
    {"appointment_id": "veil-homeappt-SYN-T-01-20261201", "source_person_id": "SYN-T-01", "domain": "social_care",
     "scheduled_on": "2026-12-01", "status_text": "", "urgency_text": "", "specialty": "", "contact_record_id": "",
     "encounter_id": "veil-enc-SYN-T-01-functioning"},
]


def test_appointments_come_from_both_scheduling_tables_on_or_after_the_reference_date(built):
    summary, out = built
    rows = list(csv.DictReader((out / "appointments.csv").open(encoding="utf-8")))
    assert list(rows[0].keys()) == list(builder.APPOINTMENT_COLUMNS)
    assert rows == EXPECTED_APPOINTMENTS
    assert summary.appointments == 7
    clinical = summary.appointment_stats["ajanvaraus"]
    assert (clinical.rows, clinical.written, clinical.past, clinical.skipped_dates, clinical.merged) == (5, 3, 1, 1, 0)
    social = summary.appointment_stats["kotihoito_kontakti"]
    # 20 bounded rows; 4 on or after the reference date (one of them ON it), none sharing a day; 1 unparseable.
    assert (social.rows, social.written, social.past, social.skipped_dates, social.merged) == (20, 4, 15, 1, 0)


def test_the_imported_index_answers_a_days_appointments_joined_to_their_lens(built, tmp_path):
    from datetime import date

    _, out = built
    delivery.import_delivery(out, tmp_path / "import")
    index = delivery.DeliveryIndex(tmp_path / "import")
    try:
        day = index.appointments_for_day(date(2026, 10, 13))
        assert [a.appointment_id for a in day] == ["veil-appt-APT-3", "veil-homeappt-SYN-T-02-20261013"]
        clinical, social = day
        assert clinical.encounter_id == "veil-enc-SYN-T-04-mental" and clinical.role == "laakari", \
            "T-04 has the doctor lens and no nurse lens, so its clinical appointment opens the doctor's briefing"
        assert clinical.status_text == "Peruttu" and clinical.contact_record_id == "veil-visit-CON-04a"
        assert social.encounter_id == "veil-enc-SYN-T-02-functioning" and social.role == "sosiaalityontekija"
        assert social.purpose == "functioning_service_needs" and social.example_label == "Synteettinen asiakas SYN-T-02"
        assert index.appointment_days(date(2026, 10, 13), date(2026, 10, 31)) == {"2026-10-13": 2, "2026-10-14": 2,
                                                                                 "2026-10-20": 1}
        assert index.appointments_for_day(date(2026, 10, 15)) == []
    finally:
        index.close()


def test_section_parsing_keeps_the_first_of_a_repeated_heading_and_drops_empty_sections():
    text = "## A\nyksi\n\n## B\n\n## A\nkaksi\n\n## C\nkolme"
    assert builder.note_sections(text) == {"A": "yksi", "C": "kolme"}
    assert builder.note_sections("ei otsikoita") == {}
    assert builder._measurement_lines("Lämpö 38,5 °C.\nSpO2 93 %\nSyke 101/min\nPaino 71,2 kg ja Pituus 164 cm\n") == [
        {"lämpö_c": 38.5}, {"spo2": 93}, {"syke": 101}, {"paino_kg": 71.2, "pituus_cm": 164.0}]


def _release_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


@pytest.mark.skipif(not REAL_RELEASE.is_dir(),
                    reason=f"the challenge release is not extracted at {REAL_RELEASE}; the fixture tests cover the builder")
def test_the_real_release_builds_24_people_and_reconciles_against_an_independent_count(tmp_path):
    summary = builder.build(REAL_RELEASE, tmp_path / "real", people=24)
    # Independent of the builder: the bounded people and each table's rows for them, read straight from the CSVs.
    contacts = _release_rows(REAL_RELEASE / "clinical/tables/kontakti.csv")
    with_contacts = {row["asiakas_id"] for row in contacts}
    people = sorted(row["asiakas_id"] for row in _release_rows(REAL_RELEASE / "clinical/tables/asiakas.csv")
                    if row["asiakas_id"] in with_contacts)[:24]
    inside = set(people)
    # The social tables carry no asiakas_id: they join to a person through the clinical asiakas table's henkilotunnus.
    hetu_to_person = {row["henkilotunnus"]: row["asiakas_id"]
                      for row in _release_rows(REAL_RELEASE / "clinical/tables/asiakas.csv")}
    assert summary.people == len(people) == 24
    expected_rows = {
        "perustieto": len(people),
        "diagnoosi": sum(r["asiakas_id"] in inside for r in _release_rows(REAL_RELEASE / "clinical/tables/diagnoosi.csv")),
        "lääkitys": sum(r["asiakas_id"] in inside for r in _release_rows(REAL_RELEASE / "clinical/tables/laakitys.csv")),
        "asiointitapahtuma": sum(r["asiakas_id"] in inside for r in contacts),
        "tutkimustulos": sum(r["asiakas_id"] in inside for r in _release_rows(
            REAL_RELEASE / "clinical/tables/vastausarkistohistoria_view.csv")),
        "vapaateksti (clinical)": sum(r["subject_id"] in inside for r in _release_rows(
            REAL_RELEASE / "clinical/text/clinical-notes.csv")),
        "päätös": sum(r["asiakas_id"] in inside for r in _release_rows(REAL_RELEASE / "social/tables/paatos.csv")),
        "vapaateksti (social)": sum(r["subject_id"] in inside for r in _release_rows(
            REAL_RELEASE / "social/text/social-notes.csv")),
        "vapaateksti (needs)": sum(hetu_to_person.get(r["henkilotunnus"]) in inside for r in _release_rows(
            REAL_RELEASE / "social/tables/palvelutarpeen_arviointi.csv")),
        "kotihoito_kontakti": sum(hetu_to_person.get(r["henkilotunnus"]) in inside for r in _release_rows(
            REAL_RELEASE / "social/tables/kotihoito_kontakti.csv")),
    }
    assert {key: st.rows for key, st in summary.stats.items()} == expected_rows
    assert summary.stats["tutkimustulos"].records == expected_rows["tutkimustulos"] == 29
    assert summary.stats["tutkimustulos"].skipped_dates == summary.stats["tutkimustulos"].unlinked == 0
    # Independent of the builder: each bounded lab row's link names a contact of the same person.
    links = {row["Avain"]: row["kontakti_id"] for row in _release_rows(REAL_RELEASE / "clinical/tables/laboratorio_kontakti.csv")}
    person_of = {row["kontakti_id"]: row["asiakas_id"] for row in contacts}
    lab_rows = [row for row in _release_rows(REAL_RELEASE / "clinical/tables/vastausarkistohistoria_view.csv")
                if row["asiakas_id"] in inside]
    assert len(lab_rows) == 29 and all(person_of.get(links.get(row["Avain"])) == row["asiakas_id"] for row in lab_rows)
    # Every source row is written, skipped or empty; the one social plan adds a record.
    written_or_counted = sum(st.records + st.skipped_dates + st.unlinked + st.empty + st.capped + st.future_excluded
                             for st in summary.stats.values())
    records = _records(tmp_path / "real")
    derived = sum(1 for r in records if r["payload"].get("lähde_record_id"))
    assert written_or_counted == sum(expected_rows.values()) + 1 + derived
    # Independent of the builder: a clinical appointment on or after the reference date, and one home-care
    # appointment per person and day on or after it, for the bounded people.
    clinical_appointments = sum(r["asiakas_id"] in inside and r["ajanvaraus_pvm"][:10] >= "2026-10-13"
                                for r in _release_rows(REAL_RELEASE / "clinical/tables/ajanvaraus.csv"))
    social_days = {(hetu_to_person.get(r["henkilotunnus"]), r["tapahtuma_pvm"][:10])
                   for r in _release_rows(REAL_RELEASE / "social/tables/kotihoito_kontakti.csv")
                   if hetu_to_person.get(r["henkilotunnus"]) in inside and r["tapahtuma_pvm"][:10] >= "2026-10-13"}
    assert summary.appointments == clinical_appointments + len(social_days)
    # Independent of the builder: every note has a risk and a functioning section; the measurement lines are counted.
    notes = [r for r in _release_rows(REAL_RELEASE / "clinical/text/clinical-notes.csv") if r["subject_id"] in inside]
    assert sum(1 for r in records if r["record_id"].startswith("veil-risk-")) == sum(
        "## RISKITIEDOT" in n["note_text"] for n in notes) == len(notes)
    assert sum(1 for r in records if r["record_id"].startswith("veil-func-")) == sum(
        "## TOIMINTAKYKY" in n["note_text"] for n in notes) == len(notes)
    assert sum(1 for r in records if r["record_type"] == "mittaus") == sum(
        len(builder._measurement_lines(builder.note_sections(n["note_text"]).get("FYSIOLOGISET MITTAUKSET", "")))
        for n in notes)
    # Independent of the builder: the active medications are the open-ended ones and those ending on or after 2026-10-13.
    medications = _release_rows(REAL_RELEASE / "clinical/tables/laakitys.csv")
    running = [r for r in medications if r["asiakas_id"] in inside
               and (not r["laakitys_paattymispvm"] or r["laakitys_paattymispvm"] >= "2026-10-13")]
    assert len(running) == 28
    assert sum(1 for r in records if r["record_type"] == "lääkitys" and r["payload"].get("tila") == "aktiivinen") == 28
    # Independent of the builder: the gate is a running medication or a long-term diagnosis, read from the source CSVs.
    run_people = {r["asiakas_id"] for r in medications if r["asiakas_id"] in inside
                  and (not r["laakitys_paattymispvm"] or r["laakitys_paattymispvm"] >= "2026-10-13")}
    longdx_people = {r["asiakas_id"] for r in _release_rows(REAL_RELEASE / "clinical/tables/diagnoosi.csv")
                     if r["asiakas_id"] in inside and r["onko_pitkaaikaisdiagnoosi"] == "true"}
    assert len(run_people) == 15 and len(longdx_people) == 7 and len(run_people | longdx_people) == 16
    med_encounters = [row for row in csv.DictReader((tmp_path / "real" / "encounters.csv").open(encoding="utf-8"))
                      if row["purpose"] == "medication_follow_up"]
    assert len(med_encounters) == 16 and len({row["source_person_id"] for row in med_encounters}) == 16
    # Independent of the builder: the label map is exactly the codes the 24 people carry.
    codes = {r["icd10_id"] for r in _release_rows(REAL_RELEASE / "clinical/tables/diagnoosi.csv")
             if r["asiakas_id"] in inside}
    assert len(codes) == 29 and set(builder.ICD10_FI) == codes
    assert {r["payload"]["nimi"] for r in records if r["record_type"] == "diagnoosi"} <= set(builder.ICD10_FI.values())
    # Independent of the builder: rows dated after REFERENCE_DATE are excluded apart from the cap, and the cap drops every
    # past row beyond the 10 most recent of each person.
    homecare = [r for r in _release_rows(REAL_RELEASE / "social/tables/kotihoito_kontakti.csv")
                if hetu_to_person.get(r["henkilotunnus"]) in inside]
    reference = builder.REFERENCE_DATE.isoformat()
    past = Counter(hetu_to_person[r["henkilotunnus"]] for r in homecare if r["tapahtuma_pvm"] <= reference)
    later = Counter(hetu_to_person[r["henkilotunnus"]] for r in homecare if r["tapahtuma_pvm"] > reference)
    expected_capped = {person: count - builder.HOMECARE_CAP for person, count in past.items()
                       if count > builder.HOMECARE_CAP}
    assert expected_capped == {"SYN-ASI-1000000017": 1168} and dict(later) == {"SYN-ASI-1000000017": 95}
    assert summary.capped == 1168 and summary.capped_by_person == expected_capped
    assert summary.future_excluded == 95 and summary.future_excluded_by_person == dict(later)
    assert summary.stats["kotihoito_kontakti"].records == 10 and summary.stats["vapaateksti (needs)"].records == 8
    assert not [r for r in records if r["record_type"] == "mittaus"], "the release has no vital-sign source"
    # The real `selection.baseline` over the one person's functioning lens: every home-care row written matches the
    # `kotihoito` term, and the social lens folds same-day visits to one per day (owner decision 2026-10-14), naming
    # the folded ids on the kept visit so the written set is accounted for in full: kept plus folded, nothing dropped.
    chosen, reasons = _baseline_for(tmp_path / "real", tmp_path / "imported",
                                    "veil-enc-SYN-ASI-1000000017-functioning")
    selected = {record.record_id for record in chosen if record.record_id.startswith("veil-homecare-")}
    written_homecare = {r["record_id"] for r in records if r["record_id"].startswith("veil-homecare-")}
    assert len(written_homecare) == builder.HOMECARE_CAP and selected <= written_homecare
    folded = {folded_id for record_id in selected for reason in reasons[record_id]
              if reason.startswith(selection.HOME_CARE_FOLD_REASON + ":")
              for folded_id in reason.split(":", 2)[2].split(",")}
    assert selected | folded == written_homecare and not (selected & folded)
    assert all(reasons[record_id][0] == "keyword:kotihoito" for record_id in selected)
    homecare_days = {datetime.fromisoformat(r["recorded_at"]).date() for r in records
                     if r["record_id"] in written_homecare}
    assert len(selected) == len(homecare_days)
    report = delivery.dry_run_delivery(tmp_path / "real", tmp_path / "dry")
    body = json.loads(report.report_path.read_text(encoding="utf-8"))
    # Not `ready`: seven render keys are missing from the mapped payload (see the module docstring).
    assert body["status"] == "warning" and body["refusals"] == []
    gaps = {record_type: set(item["missing_render_keys"]) for record_type, item in body["types"].items()
            if item["missing_render_keys"]}
    # The measurement gap exists only when a bounded note carries a measurement line; the 24 smallest ids need not.
    expected_gaps = {key: value for key, value in DECLARED_GAPS.items()
                     if key != "mittaus" or any(r["record_type"] == "mittaus" for r in records)}
    assert gaps == expected_gaps
