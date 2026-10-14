"""The rehearsal, run as a test: land -> profile -> write a mapping -> import, on unimported shapes.

`scripts/rehearse_delivery.py` is the runnable artifact; this drives it so the gate fails if the arrival
path stops working. What it pins:

* three deliberately non-canonical layouts (wide semicolon CSV with Latin-1 and decimal commas, long/narrow
  semicolon CSV, JSONL) land, profile, map and import with NO Python change;
* every vendor `record_type`/`topic` spelling in the rehearsal is NOT one of ours, so the import proves the
  value sets translated rather than that the spellings happened to match;
* nine wrong mappings -- a rule pointing at a source path that exists nowhere, a value the value set does
  not declare, and a missing required permission axis, once per layout -- each fail with a specific,
  readable message naming the offending rule, value or field.
"""

from __future__ import annotations

import json
from pathlib import Path

from app import source
from app.delivery import DeliveryIndex
from app.purpose import topics
from scripts import rehearse_delivery


def _rehearsal(tmp_path: Path) -> dict:
    return rehearse_delivery.rehearse(tmp_path, log=lambda *_args: None)


def test_the_rehearsal_imports_three_layouts_we_have_never_imported(tmp_path: Path):
    summary = _rehearsal(tmp_path)

    assert set(summary["variants"]) == {"wide", "long", "jsonl"}
    assert {name: item["import"]["records"] for name, item in summary["variants"].items()} == {
        "wide": 3, "long": 4, "jsonl": 3}
    assert all(item["import"]["personas"] == 3 for item in summary["variants"].values())
    assert all(item["import"]["mapping"].startswith("rehearsal-") for item in summary["variants"].values())
    # `land` then `profile` really ran, and the report says it was produced by a streaming profiler.
    for item in summary["variants"].values():
        report = json.loads(Path(item["report"]).read_text(encoding="utf-8"))
        assert report["files"] and report["files"][0]["rows_profiled"] > 0
        assert report["sampling"]["rows_held_at_once_high_water"] == 1
        assert Path(item["summary"]).is_file()


def test_the_rehearsal_profiles_the_shapes_the_variants_claim(tmp_path: Path):
    summary = _rehearsal(tmp_path)

    wide = summary["variants"]["wide"]["shape"]["mittaukset.csv"]
    assert wide["format"] == "csv" and wide["delimiter"] == ";"
    assert wide["encoding"] == "latin-1"
    assert wide["layout"] == "wide (one row per person)"
    assert wide["rows_total"] == 3
    long = summary["variants"]["long"]["shape"]["kirjaukset.csv"]
    assert long["format"] == "csv" and long["delimiter"] == ";" and long["rows_total"] == 4
    assert long["encoding"] == "latin-1"
    jsonl = summary["variants"]["jsonl"]["shape"]["records.jsonl"]
    assert jsonl["format"] == "jsonl" and jsonl["encoding"] == "utf-8" and jsonl["rows_total"] == 3


def test_the_rehearsal_vendor_spellings_are_not_our_vocabulary():
    """Anti-vacuity: if these matched, the import would succeed without translating anything."""
    assert set(rehearse_delivery.RECORD_TYPE_SET) & set(source.RECORD_TYPES) == set()
    assert set(rehearse_delivery.TOPIC_SET) & set(topics()) == set()
    assert set(rehearse_delivery.RECORD_TYPE_SET.values()) <= set(source.RECORD_TYPES)


def test_the_imported_records_carry_canonical_types_topics_and_payloads(tmp_path: Path):
    """The mapping translated the vendor's spelling BEFORE binding, and the payload survived Latin-1."""
    summary = _rehearsal(tmp_path)

    index = DeliveryIndex(Path(summary["variants"]["wide"]["import_dir"]))
    try:
        records = {record.record_id: record
                   for persona_id in index for record in index.persona(persona_id).records}
    finally:
        index.close()
    assert records["R-W-1"].record_type == source.MEASUREMENT
    assert records["R-W-1"].topic == "verenpaine"
    # The Finnish decimal comma is read by `decimal_fi` into a real number, not passed through as the
    # vendor's string: `72,5` was a measurement the product could not plot.
    assert records["R-W-1"].payload == {"paino_kg": 72.5, "pituus_cm": 168.0}
    assert records["R-W-1"].recorded_at.year == 2026 and records["R-W-1"].sensitivity_tag == "none"

    index = DeliveryIndex(Path(summary["variants"]["long"]["import_dir"]))
    try:
        records = {record.record_id: record
                   for persona_id in index for record in index.persona(persona_id).records}
    finally:
        index.close()
    assert records["R-L-2"].record_type == source.DIAGNOSIS
    assert records["R-L-2"].payload["nimi"] == "F32.1"
    assert records["R-L-3"].sensitivity_tag == "highly_sensitive"


def test_every_wrong_mapping_fails_loudly_and_specifically(tmp_path: Path):
    summary = _rehearsal(tmp_path)

    refusals = summary["refusals"]
    assert set(refusals) == {f"{name}/{kind}" for name in ("wide", "long", "jsonl")
                             for kind in ("wrong-path", "lookup-miss", "missing-target")}
    for key, text in refusals.items():
        assert text and "Traceback" not in text, key
    for name in ("wide", "long", "jsonl"):
        assert "matched no row" in refusals[f"{name}/wrong-path"]
        assert "MuistiinpanoPuuttuu" in refusals[f"{name}/wrong-path"]
        assert "is not in valueset 'topic'" in refusals[f"{name}/lookup-miss"]
        assert "known: [" in refusals[f"{name}/lookup-miss"], "the known set is the mapping instruction"
        assert "no rule for required envelope field" in refusals[f"{name}/missing-target"]
        assert "sensitivity_tag" in refusals[f"{name}/missing-target"]
    # The lookup miss names the value the DELIVERY carried, per variant.
    assert "'VerenpaineSeuranta'" in refusals["wide/lookup-miss"]
    assert "'Yleiskirjaus'" in refusals["long/lookup-miss"]


def test_the_rehearsal_script_runs_from_the_command_line(tmp_path: Path, capsys):
    status = rehearse_delivery.main(["--workdir", str(tmp_path / "rehearsal")])

    assert status == 0
    output = capsys.readouterr().out
    assert "3 layout(s) imported, 9 wrong mapping(s) refused" in output
    assert "WRONG-PATH refused" in output and "LOOKUP-MISS refused" in output
    assert "MISSING-TARGET refused" in output
    # The evidence is on disk, not only in stdout: land/profile wrote both artifacts per variant.
    assert (tmp_path / "rehearsal" / "landing-wide" / "results" / "delivery-shape-report.json").is_file()
    assert (tmp_path / "rehearsal" / "landing-wide" / "results" / "delivery-shape-summary.txt").is_file()
