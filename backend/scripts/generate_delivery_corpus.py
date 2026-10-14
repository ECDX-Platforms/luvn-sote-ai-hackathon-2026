#!/usr/bin/env python
"""Deterministic synthetic delivery generator + scale dry run. NO model calls, so it costs no tokens.

Emits the assumed delivery shape (`profiles.csv`, `encounters.csv`, `records.jsonl`) at any scale,
then optionally imports it with `app.delivery.import_delivery`, reports wall time / peak RSS / output
size, and resolves one encounter through the resulting index. That is the whole 13.10.2026 risk in one
command: if these numbers are comfortable at 5,000 and 50,000 profiles, arrival is a data swap.

The role/purpose matrix is derived from `app.purpose` rather than hardcoded, so this generator cannot
drift from the policy. Record envelopes are validated by the importer; payloads are plausible shapes,
not a source schema.

Run from `backend/`:

    .venv/bin/python scripts/generate_delivery_corpus.py --profiles 5000
    .venv/bin/python scripts/generate_delivery_corpus.py --profiles 50000 --out /tmp/luvn-50k

Notes:
  * The tracked 50,000-profile measurement is
    the delivery-shape dry-run artifact recorded under `docs/evidence/` (seed `20261013`).
    **It predates the 2026-10-11 vocabulary fix**, which changed the deterministic stream: a fresh run at
    the same seed now produces 700,426 records rather than the artifact's 698,142, so re-run this command
    with `--result` and re-render the decision evidence before citing those timings. Disk is the binding
    constraint, not time — the importer writes ONE Persona FILE PER PROFILE (50,000 files to budget as
    inodes) plus hundreds of MB of source and index.
  * `--result PATH` records the run (wall time, peak RSS, record counts, integrity block) as JSON instead
    of leaving it in a log.
  * The integrity pass re-reads every person file and costs more than the import it verifies
    (reported separately as `integrity_wall_s`).
  * `encounters.csv` is deliberately bounded (MAX_OPERATOR_ENCOUNTERS = 128): the operator picks a
    handful of (person, role, purpose) tuples; it is NOT one encounter per profile.
  * Output defaults to a temp directory so a 50k corpus never lands in the repository.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import resource
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import assess, source  # noqa: E402
from app.delivery import MAX_OPERATOR_ENCOUNTERS, import_delivery  # noqa: E402
from app.purpose import palvelukonteksti, palvelukontekstit  # noqa: E402

# Payload shapes per record type. Values are synthetic; only the envelope is validated on import.
# Every key emitted below is a key `delivery.RENDER_PAYLOAD_KEYS` declares for that type, or one of the
# registered ingress keys (`vastaukset`, `kirjaaja`), so a generated corpus reconciles `ready`.
FIRST_NAMES = ("Aino", "Eero", "Liisa", "Mikko", "Sanna", "Juhani", "Emilia", "Ville", "Helena", "Onni")
# Synthetic Finnish surnames, authored for this generator. They are not taken from any population register.
LAST_NAMES = ("Rautakallio", "Vähäkangas", "Lehtimaa", "Niskakangas", "Salmela", "Kivelä",
              "Peltomäki", "Korhonen", "Haapala", "Mallikas", "Keksitty", "Esimerkki")
MUNICIPALITIES = ("Helsinki", "Espoo", "Raasepori", "Lohja", "Kirkkonummi", "Hanko", "Siuntio")
DRUGS = ("amlodipiini", "hydroklooritiatsidi", "metformiini", "sertraliini", "simvastatiini")
DIAGNOSES = (("I10", "Essentiaalinen hypertensio"), ("E11", "Tyypin 2 diabetes"),
             ("F32", "Masennusjakso"), ("J45", "Astma"), ("M79", "Pehmytkudossärky"))
NOTE_TEMPLATES = (
    ("Kotikäynti, vointi ennallaan", "Asiakas kertoi, että arki sujuu pääosin itsenäisesti. "
     "Hän toivoo keskustelua jatkosta seuraavalla käynnillä."),
    ("Kotikäynti, huoli omasta tilanteesta", "Asiakas kertoi, että säännöllinen tabletti on jäänyt "
     "ottamatta useana päivänä huimauksen ja väsymyksen vuoksi eikä ole ollut yhteydessä vastaanotolle."),
    ("Keskustelu tuen tarpeesta", "Asiakas kertoi tarvitsevansa apua arjen askareissa ja toivoo, että "
     "tukimuotoja mietitään yhdessä."),
    ("Naapuruston meluhaitta", "Asiakas kertoi naapurin koiran haukkuvan öisin ja toivoo isännöitsijän "
     "yhteydenottoa asiaan."),
    ("Voinnin seuranta", "Asiakkaan mukaan oireet ovat vähentyneet ja uni on parantunut viime viikkoina."),
    ("Muistutus jatkokontrollista", "Sovittu, että asia tarkistetaan seuraavalla käynnillä."),
    # The medication-conflict vocabulary: a note that a drug was stopped beside an active medication.
    ("Lääkitys keskeytyi", "Asiakas kertoi jättäneensä säännöllisen lääkkeen oma-aloitteisesti pois "
     "haittavaikutusten vuoksi; asiasta ei ole merkintää määräävältä taholta."),
)
# A measurement row carries the four things the tiles render. One row is one measurement, so every
# generated row carries all four -- a key absent from EVERY row is the one the dry run flags.
MEASUREMENTS = (
    {"paino_kg": 71.2, "pituus_cm": 164, "verenpaine": "146/88", "syke": 72},
    {"paino_kg": 84.1, "pituus_cm": 176, "verenpaine": "152/90", "syke": 78},
    {"paino_kg": 62.4, "pituus_cm": 168, "verenpaine": "138/84", "syke": 84},
    {"paino_kg": 78.5, "pituus_cm": 172, "verenpaine": "158/92", "syke": 88},
)
APPOINTMENT_CHANNELS = (
    {"kanava": "puhelu", "suunta": "lähtevä", "aihe": "Lääkityksen tarkistus",
     "yhteenveto": "Kävimme läpi lääkityksen toteutumista ja unohdettuja annoksia."},
    {"kanava": "käynti", "suunta": "saapuva", "aihe": "Toimintakyvyn arviointi",
     "yhteenveto": "Arki sujuu tuen turvin; apuvälineiden tarve kartoitettiin."},
    {"kanava": "viesti", "suunta": "lähtevä", "aihe": "Ajanvaraus",
     "yhteenveto": "Asiakkaalle lähetettiin muistutus tulevasta tarkastuksesta."},
)
# Derived from `assess.INSTRUMENTS`, so an item added to an instrument is emitted here and cannot drift
# from the scorer. RAI is a *supplied* instrument this code has no specification for (ADR-0008): its
# answers are recorded, never totalled.
ASSESSMENT_INSTRUMENTS = ("PHQ-9", "IADL", "RAI")
# `code_system` is an ADR-0015 intent field on the envelope, not a payload key; it names the code system
# the record's codes are intended to map to, and is never itself a code.
CODE_SYSTEMS = {
    source.DIAGNOSIS: "ICD-10",
    source.MEDICATION: "ATC",
    source.LAB_RESULT: "FinLOINC",
}


def _permitted_pairs() -> list[tuple[str, str]]:
    """Every (purpose, role) the policy actually permits, from the single owner of that vocabulary."""
    pairs: list[tuple[str, str]] = []
    for purpose in palvelukontekstit():
        for role in sorted(palvelukonteksti(purpose).roles):
            pairs.append((purpose, role))
    if not pairs:
        raise SystemExit("purpose.py declares no permitted role/purpose pairs")
    return pairs


def _topics_for(purpose: str) -> list[str]:
    return sorted(palvelukonteksti(purpose).topics)


def _payload(rng: random.Random, record_type: str, topic: str) -> dict:
    if record_type == source.BASIC_INFO:
        return {"syntymävuosi": rng.randint(1930, 2005), "sukupuoli": rng.choice(("nainen", "mies")),
                "kotikunta": rng.choice(MUNICIPALITIES), "äidinkieli": "suomi",
                "asumismuoto": rng.choice(("koti", "koti, kotihoidon tukena", "palvelutalo"))}
    if record_type == source.DIAGNOSIS:
        code, name = rng.choice(DIAGNOSES)
        return {"icd10": code, "nimi": name,
                "tila": rng.choice((source.MEDICATION_ACTIVE, "päättynyt"))}
    if record_type == source.MEDICATION:
        # No `atc` key: it is not a registered payload key, so emitting it made every generated medication
        # an `unmapped_payload_keys` warning. The code system is an envelope property (see `CODE_SYSTEMS`).
        return {"lääke": rng.choice(DRUGS), "annos": f"{rng.choice((5, 10, 25, 50))} mg x 1",
                "tila": source.MEDICATION_ACTIVE, "puuttuneet_annokset_30d": rng.randint(0, 12)}
    if record_type == source.FREE_TEXT:
        otsikko, teksti = rng.choice(NOTE_TEMPLATES)
        return {"otsikko": otsikko, "teksti": teksti, "kirjaaja": "lähihoitaja",
                "käyntityyppi": rng.choice(("kotikäynti", "vastaanotto", "etäkäynti"))}
    if record_type == source.ARVIOINTI:
        instrument = rng.choice(ASSESSMENT_INSTRUMENTS)
        spec = assess.INSTRUMENTS.get(instrument)
        # `vastaukset` is the ingress-only item map `assess.score` sums. The item codes and the per-item
        # range come from the scorer itself; a supplied instrument we have no specification for records its
        # answers and is never totalled (ADR-0008).
        items = list(spec.items) if spec is not None else [f"r{index}" for index in range(1, 3)]
        per_item_max = spec.per_item_max if spec is not None else 1
        return {"instrumentti": instrument, "versio": "gen-v1",
                "vastaukset": {code: rng.randint(0, per_item_max) for code in items}}
    if record_type == source.MEASUREMENT:
        return dict(rng.choice(MEASUREMENTS))
    if record_type == source.LAB_RESULT:
        # PROVISIONAL payload shape (owner decision 2026-10-07): the delivered Veil.AI shape is not
        # known, so ONE vocabulary is fixed here and in data/experimental/delivery-sample/records.jsonl
        # instead of drifting. The value key is `arvo` (as in MEASUREMENT above, not `tulos`), and the
        # `tulkinta` carries an authored source category, never a threshold-derived flag. The delivered
        # schema remains unknown; this is synthetic, provisional vocabulary from c86/4894 (2026-10-07).
        return {"tutkimus": rng.choice(("HbA1c", "LDL", "TSH")), "arvo": rng.choice(("42", "3.1", "2.4")),
                "yksikkö": rng.choice(("mmol/mol", "mmol/l", "mIU/l")),
                "tulkinta": rng.choice(("viitearvo", "poikkeava", "kliinisesti hälyttävä", "kriittisesti hälyttävä")),
                "viitearvo": rng.choice(("< 3.0", "4.0-6.0", "0.5-4.5"))}
    if record_type == source.APPOINTMENT:
        return dict(rng.choice(APPOINTMENT_CHANNELS))
    if record_type == source.DECISION:
        # `päätös` is the registry key (`kuvaus` is an alias for it, but the generator emits the canonical
        # name so the corpus is not exercising an adapter it does not need).
        return {"päätös": rng.choice(("Palvelupäätös", "Maksusitoumus", "Hoitopäätös")),
                "voimassa": rng.choice(("toistaiseksi", "määräaikainen"))}
    return {"arvo": rng.choice(("kyllä", "ei"))}


def generate(out_dir: Path, profiles: int, records_min: int, records_max: int,
             encounters: int, seed: int) -> Path:
    """Write profiles.csv, encounters.csv and records.jsonl; return the source directory."""
    source_dir = out_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    pairs = _permitted_pairs()
    now = datetime(2026, 10, 13, 9, 0, tzinfo=timezone(timedelta(hours=3)))
    record_types = (source.BASIC_INFO, source.DIAGNOSIS, source.MEDICATION, source.FREE_TEXT,
                    source.ARVIOINTI, source.MEASUREMENT, source.LAB_RESULT, source.APPOINTMENT,
                    source.DECISION)

    with (source_dir / "profiles.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("source_person_id", "persona_id", "label"))
        for index in range(profiles):
            writer.writerow((f"gen-source-{index:06d}", f"gen-persona-{index:06d}",
                             f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"))

    written = 0
    with (source_dir / "records.jsonl").open("w", encoding="utf-8") as stream:
        for index in range(profiles):
            person_key = f"gen-source-{index:06d}"
            purpose, _role = rng.choice(pairs)
            topics = _topics_for(purpose)
            for offset in range(rng.randint(records_min, records_max)):
                record_type = record_types[0] if offset == 0 else rng.choice(record_types[1:])
                topic = "demografia" if record_type == source.BASIC_INFO else rng.choice(topics)
                # ~60% current (within a year), ~40% historical, so temporal_status varies.
                age_days = rng.randint(0, 330) if rng.random() < 0.6 else rng.randint(400, 2500)
                row = {
                    "record_id": f"gen-{index:06d}-{offset:03d}",
                    "source_person_id": person_key,
                    "record_type": record_type,
                    "authored_by_role": rng.choice(("laakari", "sairaanhoitaja", "sosiaalityontekija",
                                                    "lähihoitaja", "rekisterisihteeri")),
                    "recorded_at": (now - timedelta(days=age_days, hours=rng.randint(0, 23))).isoformat(),
                    "topic": topic,
                    "sensitivity_tag": rng.choice(("none", "none", "none", "sensitive", "highly_sensitive")),
                    "consent_scope": rng.choice(("treatment", "treatment", "social_care")),
                    "payload": _payload(rng, record_type, topic),
                }
                if record_type in CODE_SYSTEMS:
                    row["code_system"] = CODE_SYSTEMS[record_type]
                if record_type == source.DECISION and rng.random() < 0.34:
                    # A catalogue row whose declared due date has already passed: the one form state a
                    # list of forms cannot otherwise show. Synthetic and deliberately dated before `now`.
                    row["form"] = {"form_type": "IADL", "service": "toimintakyky", "version": "gen-v1",
                                   "due_at": (now - timedelta(days=rng.randint(1, 30))).isoformat()}
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1

    bounded = max(1, min(encounters, MAX_OPERATOR_ENCOUNTERS))
    with (source_dir / "encounters.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("encounter_id", "source_person_id", "role", "purpose",
                         "example_label", "demo_name", "consent_holders", "life_stage"))
        seen: set[str] = set()
        made = 0
        while made < bounded:
            index = rng.randrange(profiles)
            purpose, role = rng.choice(pairs)
            encounter_id = f"gen-encounter-{made:03d}"
            if encounter_id in seen:
                continue
            seen.add(encounter_id)
            writer.writerow((encounter_id, f"gen-source-{index:06d}", role, purpose,
                             f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}", f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
                             "", ""))
            made += 1

    print(f"generated  profiles={profiles}  records={written}  encounters={made}")
    print(f"  pairs in use: {sorted({f'{p}/{r}' for p, r in pairs})}")
    return source_dir


def _peak_rss_mb() -> float:
    """ru_maxrss is bytes on macOS and kilobytes on Linux; normalise to MB."""
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / (1024 * 1024) if sys.platform == "darwin" else raw / 1024


def _verify(index_dir: Path, persona_files: int, expected_personas: int, expected_records: int) -> dict:
    """Count what actually landed. At 50k scale a truncated write would otherwise pass unnoticed."""
    on_disk = 0
    for path in (index_dir / "people").glob("*.jsonl"):
        with path.open(encoding="utf-8") as stream:
            on_disk += sum(1 for _ in stream)
    indexes = list(index_dir.glob("*.sqlite"))
    if len(indexes) != 1:
        raise SystemExit(f"expected exactly one sqlite index in {index_dir}, found {len(indexes)}")
    connection = sqlite3.connect(f"file:{indexes[0]}?mode=ro", uri=True)
    try:
        indexed = connection.execute("SELECT count(*) FROM seen_records").fetchone()[0]
        duplicates = connection.execute("SELECT count(*) FROM (SELECT record_id FROM seen_records "
                                        "GROUP BY record_id HAVING count(*) > 1)").fetchone()[0]
    finally:
        connection.close()
    result = {"persona_files": persona_files, "records_on_disk": on_disk,
              "indexed_records": indexed, "duplicate_record_ids": duplicates}
    if persona_files != expected_personas:
        raise SystemExit(f"integrity: {persona_files} persona files for {expected_personas} profiles")
    if on_disk != expected_records or indexed != expected_records:
        raise SystemExit(f"integrity: {on_disk} records on disk / {indexed} indexed, "
                         f"expected {expected_records}")
    if duplicates:
        raise SystemExit(f"integrity: {duplicates} duplicate record IDs")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=int, default=50000)
    parser.add_argument("--records-min", type=int, default=4)
    parser.add_argument("--records-max", type=int, default=24)
    parser.add_argument("--encounters", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20261013)
    parser.add_argument("--out", type=Path, default=Path("/tmp/luvn-delivery-corpus"))
    parser.add_argument("--skip-import", action="store_true")
    parser.add_argument("--result", type=Path, default=None,
                        help="write the measured run to this JSON path (machine-written, no typing)")
    args = parser.parse_args()

    try:
        if args.out.exists():
            shutil.rmtree(args.out)
        args.out.mkdir(parents=True)
    except OSError as exc:
        raise SystemExit(f"cannot prepare output directory {args.out}: {exc}") from exc

    started = time.monotonic()
    source_dir = generate(args.out, args.profiles, args.records_min, args.records_max,
                          args.encounters, args.seed)
    generate_wall = time.monotonic() - started
    print(f"  generate wall={generate_wall:.1f}s  disk="
          f"{sum(f.stat().st_size for f in source_dir.iterdir()) / 1e6:.0f} MB")
    if args.skip_import:
        return

    print("\nimporting (this is the 13.10 risk)...")
    index_started = time.monotonic()
    report = import_delivery(source_dir, args.out / "index")
    import_wall = time.monotonic() - index_started
    people = args.out / "index" / "people"
    people_count = sum(1 for _ in people.iterdir()) if people.is_dir() else 0

    print(f"  import wall={import_wall:.1f}s  personas={people_count}  "
          f"peak_rss={_peak_rss_mb():.0f} MB  report={report}")

    # Resolve one encounter through the index: one encounter must touch one person, not the corpus.
    from app.delivery import DeliveryIndex  # noqa: E402
    from app.encounters import resolve_encounter  # noqa: E402

    with (source_dir / "encounters.csv").open(encoding="utf-8", newline="") as stream:
        first = next(csv.DictReader(stream))
    index = DeliveryIndex(args.out / "index")
    try:
        resolve_started = time.monotonic()
        encounter, persona = resolve_encounter(first["encounter_id"], index, index.encounter_index())
        resolve_ms = (time.monotonic() - resolve_started) * 1000
        resolved_records = len(persona.records)
    finally:
        index.close()
    print(f"  resolved {encounter.encounter_id} -> {persona.persona_id} with "
          f"{resolved_records} records in {resolve_ms:.0f} ms")

    # Measured after the pipeline timings so the integrity pass cannot flatter them, and reported
    # separately: counting lines across 50k files costs more than the import it verifies.
    total_wall = time.monotonic() - started
    verify_started = time.monotonic()
    integrity = _verify(args.out / "index", people_count, report.personas, report.records)
    integrity_wall = time.monotonic() - verify_started
    print(f"  integrity: {integrity['records_on_disk']} records on disk, "
          f"{integrity['indexed_records']} indexed, {integrity['duplicate_record_ids']} duplicates "
          f"({integrity_wall:.1f}s to verify)")
    print(f"\npipeline wall={total_wall:.1f}s  peak_rss={_peak_rss_mb():.0f} MB  out={args.out}")

    if args.result:
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(json.dumps({
            "kind": "delivery-scale-dry-run",
            "date": time.strftime("%Y-%m-%d"),
            "profiles": args.profiles,
            "records": report.records,
            "encounters": report.encounters,
            "skipped": report.skipped,
            "seed": args.seed,
            "records_per_profile": [args.records_min, args.records_max],
            "generate_wall_s": round(generate_wall, 1),
            "import_wall_s": round(import_wall, 1),
            "pipeline_wall_s": round(total_wall, 1),
            "integrity_wall_s": round(integrity_wall, 1),
            "peak_rss_mb": round(_peak_rss_mb()),
            "resolve_one_encounter_ms": round(resolve_ms),
            "resolved_records": resolved_records,
            "source_mb": round(sum(f.stat().st_size for f in source_dir.iterdir()) / 1e6),
            "index_mb": round(sum(f.stat().st_size for f in (args.out / "index").rglob("*")
                                  if f.is_file()) / 1e6),
            "integrity": integrity,
            "limits": [
                "synthetic deterministic corpus; it exercises import scale and index shape, not the "
                "real 13.10.2026 schema",
                "one machine; wall time is I/O-bound and not a throughput guarantee for another host",
            ],
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"  wrote {args.result}")


if __name__ == "__main__":
    main()
