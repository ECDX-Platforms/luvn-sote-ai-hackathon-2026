"""The scale rehearsal, run as a test at a size the gate can afford.

`scripts/rehearse_delivery_scale.py` is the runnable artifact; the tracked measurement at 100 000 records is
the recorded rehearsal artifact under `docs/evidence/`. What this test pins is that the
script's own machinery keeps working: the four stages run on a foreign layout, one row is in flight in the
profiler (the streaming claim's structural half), the index resolves what the import reported, and the
memory-bound merge turns two measured passes into a verdict with its numbers attached.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts import rehearse_delivery_scale


def test_a_single_pass_measures_every_stage_and_bounds_the_profiler(tmp_path: Path):
    document = rehearse_delivery_scale.run_single(tmp_path / "scale", profiles=40, records=800,
                                                  log=lambda *_args: None)

    assert document["kind"] == "delivery-scale-pass"
    assert document["records"] == 800 and document["profiles"] == 40
    assert {stage["name"] for stage in document["stages"]} == {
        "generate", "land", "profile", "map", "import", "integrity"}
    for stage in document["stages"]:
        assert stage["wall_s"] >= 0 and stage["peak_rss_mb"] > 0
    for key in ("generate_wall_s", "land_wall_s", "profile_wall_s", "map_wall_s", "import_wall_s",
                "integrity_wall_s", "pipeline_wall_s", "peak_rss_mb", "source_mb", "index_mb"):
        assert key in document, key
    assert document["source_mb"] > 0 and document["index_mb"] > 0
    # The structural half of the streaming claim: the profiler holds ONE row.
    assert document["profiler_rows_held_at_once_high_water"] == 1
    assert document["import"]["records"] == 800 and document["import"]["personas"] == 40
    assert document["import"]["quarantined"] == 0
    assert document["integrity"]["records_on_disk"] == 800
    assert document["integrity"]["matches_import"] is True
    # The mapping translated the vendor vocabulary AND read the Finnish decimal comma as a number.
    stage = {entry["name"]: entry for entry in document["stages"]}
    assert stage["map"]["rows_mapped"] == 800 and stage["map"]["rows_refused"] == 0
    assert stage["import"]["mapping"] == "scale-rehearsal-v1"


def test_the_generated_delivery_is_a_foreign_layout_the_mapping_must_translate(tmp_path: Path):
    raw = tmp_path / "raw"
    rows, size = rehearse_delivery_scale.generate_delivery(raw, profiles=10, records=50)

    assert rows == 50 and size > 0
    text = (raw / "mittaukset.csv").read_bytes().decode("latin-1")
    assert text.splitlines()[0].split(";")[0] == "TietueVersio"
    assert "MittausTulos" in text, "a vendor record_type spelling this code does not declare"
    assert "VerenpaineSeuranta" in text, "a vendor topic spelling this code does not declare"
    assert "," in text, "Finnish decimal commas"
    assert rehearse_delivery_scale.finnish_decimal(1234.5) == "1 234,5"
    assert rehearse_delivery_scale.finnish_decimal(72.5) == "72,5"


def test_the_memory_bound_merge_reports_its_numbers_and_a_verdict():
    full = {"records": 100_000, "peak_rss_mb": 120.0, "profiler_rows_held_at_once_high_water": 1,
            "stages": []}
    baseline = {"records": 25_000, "peak_rss_mb": 100.0}
    document = rehearse_delivery_scale.build_artifact(full, baseline, generated_at="2026-10-10T00:00:00Z")

    assert document["kind"] == "delivery-scale-rehearsal"
    bound = document["memory_bound"]
    assert bound["measured"] is True
    assert bound["records_ratio"] == 4.0 and bound["peak_rss_ratio"] == 1.2
    assert bound["streaming_bounds_memory"] is True
    assert "not by the number of rows" in bound["verdict"]
    assert "monotone" in bound["method"]
    # This assertion used to require the literal "NOT streaming": the limit text claimed `land_delivery`
    # read each source file whole. That claim was stale -- `landing.land_delivery` streams in chunks (see
    # `test_landing_never_reads_a_file_whole`) -- so the artifact asserted a limit the code no longer had.
    # The OLD EXPECTATION WAS WRONG, and it is replaced with the current design plus its real cost.
    assert any("streams each source file in chunks" in limit for limit in document["limits"])
    assert not any("reads each source file whole" in limit for limit in document["limits"])


def test_a_single_size_artifact_says_the_comparison_is_missing():
    document = rehearse_delivery_scale.build_artifact(
        {"records": 1000, "peak_rss_mb": 50.0, "profiler_rows_held_at_once_high_water": 1, "stages": []},
        None, generated_at="2026-10-10T00:00:00Z")
    assert document["memory_bound"]["measured"] is False
    assert "not in this artifact" in document["memory_bound"]["why"]


def test_the_script_runs_from_the_command_line_and_writes_the_artifact(tmp_path: Path):
    destination = tmp_path / "artifact.json"
    status = rehearse_delivery_scale.main(["--single", "--records", "400", "--profiles", "20",
                                           "--workdir", str(tmp_path / "work"), "--json",
                                           str(destination)])
    assert status == 0
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert document["records"] == 400
    assert document["profiler_rows_held_at_once_high_water"] == 1
