#!/usr/bin/env python
"""The arrival rehearsal: land, profile, write a mapping, import -- on shapes we have never imported.

The 2026-10-13 delivery is "50 000 profiles, CSV or JSON, unknown until the day", so arrival day must be
the SECOND run of this pipeline, not the first. This script rehearses the whole path offline, against three
deliberately NON-canonical shapes:

* ``wide``  -- a semicolon-delimited WIDE CSV, Latin-1 ``ä``/``ö``, Finnish decimal commas (``72,5``) and
  ``DD.MM.YYYY`` dates, one row per person, with the payload as individual columns;
* ``long``  -- a semicolon-delimited long/narrow CSV, one row per record, payload as a JSON cell;
* ``jsonl`` -- one JSON object per line with a nested ``payload`` object.

Every variant carries vendor ``record_type`` and ``topic`` spellings that this code does NOT declare, so the
import only succeeds because a ``lookup_valueset`` rule translates them before the envelope is bound. Then
each variant is imported a second and third time with a WRONG mapping and the failure is printed:

* a rule pointing at a source path that exists nowhere;
* a ``lookup_valueset`` value the value set does not declare;
* a mapping with no rule for a required envelope field (``sensitivity_tag``).

Nothing here touches the network, Azure, or the decision model. The work happens under ``--workdir`` (a
temporary directory by default), and the script exits 0 only when every success succeeded AND every failure
failed for the named reason.

    .venv/bin/python scripts/rehearse_delivery.py
    .venv/bin/python scripts/rehearse_delivery.py --workdir /tmp/luvn-rehearsal --keep
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import source  # noqa: E402
from app.delivery import DeliveryShapeError, DeliveryIndex, import_delivery  # noqa: E402
from app.landing import land_delivery, profile_delivery  # noqa: E402
from app.mapping import MappingError  # noqa: E402
from app.purpose import topics  # noqa: E402

# Vendor spellings. NONE of these is a canonical record_type or topic: `source.RECORD_TYPES` and
# `purpose.topics()` do not contain them, which is the point -- only the mapping's value sets make the
# delivery bind. The two assertions below the tables make that non-negotiable: if our own vocabulary ever
# grew one of these spellings, the rehearsal would stop rehearsing translation.
RECORD_TYPE_SET = {
    "VapaaTeksti": "vapaateksti",
    "DiagnoosiKirjaus": "diagnoosi",
    "Laakemaarays": "lääkitys",
    "MittausTulos": "mittaus",
}
TOPIC_SET = {
    "Laakitys": "lääkitys",
    "Mieliala": "mielenterveys",
    "VerenpaineSeuranta": "verenpaine",
    "Yleiskirjaus": "hoito",
}
SENSITIVITY_SET = {"julkinen": "none", "salainen": "sensitive", "erittain_salainen": "highly_sensitive"}
CONSENT_SET = {"hoito": "treatment", "sosiaalihuolto": "social_care"}


def _assert_vendor_spellings_are_not_ours() -> None:
    """If our own vocabulary grew one of these spellings the rehearsal would stop rehearsing translation."""
    if set(RECORD_TYPE_SET) - set(source.RECORD_TYPES) != set(RECORD_TYPE_SET):
        raise AssertionError("rehearsal: a vendor record_type spelling is now canonical; the rehearsal "
                             "would no longer rehearse translation")
    if set(TOPIC_SET) & set(topics()):
        raise AssertionError("rehearsal: a vendor topic spelling is now canonical; the rehearsal would no "
                             "longer rehearse translation")
    if set(RECORD_TYPE_SET.values()) - set(source.RECORD_TYPES):
        raise AssertionError("rehearsal: a value set maps to a record_type this code does not declare")

# Three people, enough rows per type that every present type carries at least one useful key (the
# importer's own gate), and Latin-1 characters in the free text.
PEOPLE = ("H-1001", "H-1002", "H-1003")


def _landing_and_profile(raw: Path, landing: Path) -> dict[str, Any]:
    """Step 0 and 0b: land the bytes verbatim, then profile the landing zone."""
    land_delivery(raw, landing)
    document = profile_delivery(landing, landing)
    return document


def _variant_wide(raw: Path) -> None:
    """Wide CSV: one row per person, payload as columns, Latin-1, decimal comma, DD.MM.YYYY dates."""
    raw.mkdir(parents=True, exist_ok=True)
    rows = [
        ["TietueVersio", "Henkilötunniste", "Kirjaustunniste", "Kirjaustyyppi", "Kirjaaja", "Kirjausaika",
         "Aihe", "Salassapitoluokka", "SuostumuksenLaajuus", "Paino", "Pituus"],
        ["4", "H-1001", "R-W-1", "MittausTulos", "hoitaja", "13.10.2026", "VerenpaineSeuranta", "julkinen",
         "hoito", "72,5", "168,0"],
        ["4", "H-1002", "R-W-2", "MittausTulos", "hoitaja", "13.10.2026", "VerenpaineSeuranta", "julkinen",
         "hoito", "81,0", "175,5"],
        ["4", "H-1003", "R-W-3", "MittausTulos", "hoitaja", "14.10.2026", "VerenpaineSeuranta", "salainen",
         "hoito", "64,25", "160,0"],
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerows(rows)
    # Latin-1 on purpose: a UTF-8 decoder must not be assumed, and `ä`/`ö` must survive to the payload.
    (raw / "mittaukset.csv").write_bytes(buffer.getvalue().encode("latin-1"))


def _variant_long(raw: Path) -> None:
    """Long/narrow CSV: one row per record, the payload in a JSON cell, mixed Finnish date spellings."""
    raw.mkdir(parents=True, exist_ok=True)
    rows = [
        ["TietueVersio", "Henkilötunniste", "Kirjaustunniste", "Kirjaustyyppi", "Kirjaaja", "Kirjausaika",
         "Aihe", "Salassapitoluokka", "SuostumuksenLaajuus", "Sisältö"],
        ["4", "H-1001", "R-L-1", "VapaaTeksti", "laakari", "2026-10-13T08:30:00Z", "Yleiskirjaus",
         "julkinen", "hoito", json.dumps({"teksti": "Yrjö Ärje voi hyvin"}, ensure_ascii=False)],
        ["4", "H-1001", "R-L-2", "DiagnoosiKirjaus", "laakari", "14.10.2026", "Mieliala", "salainen", "hoito",
         json.dumps({"nimi": "F32.1", "icd10": "F32.1"}, ensure_ascii=False)],
        ["4", "H-1002", "R-L-3", "Laakemaarays", "laakari", "15.10.2026 09:15", "Laakitys",
         "erittain_salainen", "hoito", json.dumps({"lääke": "Sertraliini", "annos": "50 mg"},
                                                  ensure_ascii=False)],
        ["4", "H-1003", "R-L-4", "VapaaTeksti", "hoitaja", "16.10.2026", "Yleiskirjaus", "julkinen",
         "sosiaalihuolto", json.dumps({"teksti": "Ääni kuului hyvin"}, ensure_ascii=False)],
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerows(rows)
    (raw / "kirjaukset.csv").write_bytes(buffer.getvalue().encode("latin-1"))


def _variant_jsonl(raw: Path) -> None:
    """JSONL: one JSON object per line, nested payload, epoch-millisecond and ISO timestamps."""
    raw.mkdir(parents=True, exist_ok=True)
    records = [
        {"TietueVersio": 4, "Henkilötunniste": "H-1001", "Kirjaustunniste": "R-J-1",
         "Kirjaustyyppi": "VapaaTeksti", "Kirjaaja": "laakari", "Kirjausaika": 1791900000000,
         "Aihe": "Yleiskirjaus", "Salassapitoluokka": "julkinen", "SuostumuksenLaajuus": "hoito",
         "Sisältö": {"teksti": "Yrjö voi hyvin"}},
        {"TietueVersio": 4, "Henkilötunniste": "H-1002", "Kirjaustunniste": "R-J-2",
         "Kirjaustyyppi": "DiagnoosiKirjaus", "Kirjaaja": "laakari", "Kirjausaika": "2026-10-14T10:00:00+03:00",
         "Aihe": "Mieliala", "Salassapitoluokka": "salainen", "SuostumuksenLaajuus": "hoito",
         "Sisältö": {"nimi": "F41.1", "icd10": "F41.1"}},
        {"TietueVersio": 4, "Henkilötunniste": "H-1003", "Kirjaustunniste": "R-J-3",
         "Kirjaustyyppi": "Laakemaarays", "Kirjaaja": "laakari", "Kirjausaika": "2026-10-15T11:30:00Z",
         "Aihe": "Laakitys", "Salassapitoluokka": "julkinen", "SuostumuksenLaajuus": "sosiaalihuolto",
         "Sisältö": {"lääke": "Metformiini", "annos": "500 mg"}},
    ]
    with (raw / "records.jsonl").open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def _mapping(variant: str) -> dict[str, Any]:
    """The mapping a human would write after reading the shape report for one variant."""
    document: dict[str, Any] = {
        "version": f"rehearsal-{variant}-v1",
        "note": "Written for the rehearsal's generated delivery from its shape report. Not a real mapping.",
        "ignore_source_paths": ["TietueVersio"],
        "valuesets": {
            # Copies, not the module-level dicts: a wrong mapping mutates its own value set below, and a
            # shared dict would leak that mutation into the next variant's GOOD mapping.
            "record_type": dict(RECORD_TYPE_SET),
            "topic": dict(TOPIC_SET),
            "sensitivity_tag": dict(SENSITIVITY_SET),
            "consent_scope": dict(CONSENT_SET),
        },
        "rules": [
            {"id": "source", "target_field": "source", "transform": "const", "value": "delivery"},
            {"id": "recorded-at", "source_path": "Kirjausaika", "target_field": "recorded_at",
             "transform": "date"},
            {"id": "sensitivity", "source_path": "Salassapitoluokka", "target_field": "sensitivity_tag",
             "transform": "lookup_valueset", "valueset": "sensitivity_tag"},
            {"id": "consent", "source_path": "SuostumuksenLaajuus", "target_field": "consent_scope",
             "transform": "lookup_valueset", "valueset": "consent_scope"},
        ],
    }
    if variant == "wide":
        # Wide: the payload is columns, so the mapping targets `payload.<key>` directly, and the Finnish
        # decimal comma (``72,5``, ``1 234,5``) is read by the ONE number transform. Passing the vendor's
        # string through instead is what the arrival runbook used to record as a limitation: a measurement
        # the product cannot plot. A value `decimal_fi` cannot read refuses the row by name.
        document["rules"].extend([
            {"id": "person", "source_path": "Henkilötunniste", "target_field": "source_person_id"},
            {"id": "record-id", "source_path": "Kirjaustunniste", "target_field": "record_id"},
            {"id": "record-type", "source_path": "Kirjaustyyppi", "target_field": "record_type",
             "transform": "lookup_valueset", "valueset": "record_type"},
            {"id": "author", "source_path": "Kirjaaja", "target_field": "authored_by_role"},
            {"id": "topic", "source_path": "Aihe", "target_field": "topic",
             "transform": "lookup_valueset", "valueset": "topic"},
            {"id": "weight", "source_path": "Paino", "target_field": "payload.paino_kg",
             "transform": "decimal_fi"},
            {"id": "height", "source_path": "Pituus", "target_field": "payload.pituus_cm",
             "transform": "decimal_fi"},
        ])
    else:
        document["rules"].extend([
            {"id": "person", "source_path": "Henkilötunniste", "target_field": "source_person_id"},
            {"id": "record-id", "source_path": "Kirjaustunniste", "target_field": "record_id"},
            {"id": "record-type", "source_path": "Kirjaustyyppi", "target_field": "record_type",
             "transform": "lookup_valueset", "valueset": "record_type"},
            {"id": "author", "source_path": "Kirjaaja", "target_field": "authored_by_role"},
            {"id": "topic", "source_path": "Aihe", "target_field": "topic",
             "transform": "lookup_valueset", "valueset": "topic"},
            {"id": "payload", "source_path": "Sisältö", "target_field": "payload"},
        ])
    return document


def _write(document: dict[str, Any], path: Path) -> Path:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _import_and_check(source: Path, out: Path, mapping_path: Path) -> dict[str, Any]:
    report = import_delivery(source, out, mapping=mapping_path)
    index = DeliveryIndex(out)
    try:
        personas = sorted(index.encodings())
        loaded = sum(len(index.persona(persona_id).records) for persona_id in index)
    finally:
        index.close()
    if loaded != report.records or report.records == 0:
        raise AssertionError(f"{out}: report says {report.records} records, the index resolves {loaded}")
    return {"records": report.records, "personas": report.personas, "mapping": report.mapping_version,
            "encodings": personas}


def _expected_failure(source: Path, out: Path, mapping_path: Path, expected: tuple[str, ...]) -> str:
    """Import with a WRONG mapping; return the refusal text, failing if it was accepted or is unspecific."""
    try:
        import_delivery(source, out, mapping=mapping_path)
    except (DeliveryShapeError, MappingError) as exc:
        message = str(exc)
        missing = [fragment for fragment in expected if fragment not in message]
        if missing:
            raise AssertionError(f"the refusal did not name {missing}: {message}") from exc
        return message
    raise AssertionError(f"{mapping_path} was accepted; a wrong mapping must not import")


def rehearse(workdir: Path, *, log: Any = print) -> dict[str, Any]:
    """Run the whole rehearsal under `workdir` and return what happened, including each refusal text."""
    _assert_vendor_spellings_are_not_ours()
    variants = {"wide": _variant_wide, "long": _variant_long, "jsonl": _variant_jsonl}
    summary: dict[str, Any] = {"variants": {}, "refusals": {}}
    for name, generate in variants.items():
        raw = workdir / f"delivery-{name}"
        generate(raw)
        landing = workdir / f"landing-{name}"
        document = _landing_and_profile(raw, landing)
        report_path = landing / "results" / "delivery-shape-report.json"
        summary_path = landing / "results" / "delivery-shape-summary.txt"
        shapes = {entry["source_path"]: entry for entry in document["files"]}
        log(f"[{name}] land -> {landing}")
        log(f"[{name}] profile -> {report_path}")
        for source_path, entry in shapes.items():
            log(f"[{name}]   {source_path} [{entry['format']}] layout={entry.get('layout')} "
                f"rows={entry.get('rows_total')} encoding={entry['encoding']} "
                f"columns={[column['name'] for column in entry.get('columns', [])]}")
        summary["variants"][name] = {
            "raw": str(raw), "landing": str(landing), "report": str(report_path),
            "summary": str(summary_path), "shape": shapes, "sampling": document.get("sampling"),
        }

        good = _write(_mapping(name), workdir / f"mapping-{name}-good.json")
        # The IMPORT reads the LANDING ZONE, not the raw directory: that is the arrival-day path, and it
        # proves the profiled bytes are the imported bytes.
        result = _import_and_check(landing, workdir / f"import-{name}", good)
        log(f"[{name}] import -> {workdir / f'import-{name}'}  personas={result['personas']} "
            f"records={result['records']} mapping={result['mapping']}")
        summary["variants"][name]["import"] = {key: value for key, value in result.items()
                                               if key != "encodings"}
        summary["variants"][name]["import_dir"] = str(workdir / f"import-{name}")
        summary["variants"][name]["encodings"] = result["encodings"]

        # 1. A rule pointing at a source path that exists nowhere.
        wrong = _mapping(name)
        wrong["version"] = f"rehearsal-{name}-wrong-path"
        wrong["rules"].append({"id": "note", "source_path": "MuistiinpanoPuuttuu",
                               "target_field": "heading"})
        text = _expected_failure(landing, workdir / f"refused-{name}-path",
                                 _write(wrong, workdir / f"mapping-{name}-wrong-path.json"),
                                 ("rules that matched no row", "'MuistiinpanoPuuttuu'"))
        summary["refusals"][f"{name}/wrong-path"] = text
        log(f"[{name}] WRONG-PATH refused: {text}")

        # 2. A value the value set does not declare. The delivery still carries the vendor spelling of the
        #    FIRST row's topic, so the lookup must miss on it and name it.
        first_topic = {"wide": "VerenpaineSeuranta", "long": "Yleiskirjaus", "jsonl": "Yleiskirjaus"}[name]
        wrong = _mapping(name)
        wrong["version"] = f"rehearsal-{name}-lookup-miss"
        wrong["valuesets"]["topic"].pop(first_topic)
        text = _expected_failure(landing, workdir / f"refused-{name}-lookup",
                                 _write(wrong, workdir / f"mapping-{name}-lookup-miss.json"),
                                 ("is not in valueset", f"'{first_topic}'"))
        summary["refusals"][f"{name}/lookup-miss"] = text
        log(f"[{name}] LOOKUP-MISS refused: {text}")

        # 3. A mapping with no rule for a required envelope field.
        wrong = _mapping(name)
        wrong["version"] = f"rehearsal-{name}-missing-target"
        wrong["rules"] = [rule for rule in wrong["rules"] if rule["target_field"] != "sensitivity_tag"]
        text = _expected_failure(landing, workdir / f"refused-{name}-target",
                                 _write(wrong, workdir / f"mapping-{name}-missing-target.json"),
                                 ("no rule for required envelope field", "sensitivity_tag"))
        summary["refusals"][f"{name}/missing-target"] = text
        log(f"[{name}] MISSING-TARGET refused: {text}")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workdir", type=Path, default=None,
                        help="where to generate and import (a temporary directory by default)")
    parser.add_argument("--keep", action="store_true", help="keep the work directory after the run")
    args = parser.parse_args(argv)
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="luvn-rehearsal-"))
    workdir.mkdir(parents=True, exist_ok=True)
    print(f"rehearsal workdir: {workdir}")
    try:
        summary = rehearse(workdir)
    except AssertionError as exc:
        print(f"REHEARSAL FAILED: {exc}", file=sys.stderr)
        return 1
    print()
    print(f"rehearsal: {len(summary['variants'])} layout(s) imported, "
          f"{len(summary['refusals'])} wrong mapping(s) refused for the named reason")
    for name, item in summary["variants"].items():
        print(f"  {name:6s} {item['import']['personas']} personas, {item['import']['records']} records "
              f"via {Path(item['import']['mapping']).name}")
    if not args.keep and args.workdir is None:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
