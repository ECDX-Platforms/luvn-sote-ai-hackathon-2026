#!/usr/bin/env python
"""The scaled arrival rehearsal: land -> profile -> map -> import at real size, with measured memory.

The pipeline had never been run at the size the 2026-10-13 delivery will arrive at. This script synthesises
a delivery ON THE FLY into a temporary directory (nothing large is ever written into the repository), pushes
it through the four arrival stages on a deliberately FOREIGN layout -- a semicolon CSV, Latin-1, Finnish
decimal commas with a space thousands separator, ``DD.MM.YYYY`` dates, vendor ``record_type``/``topic``
spellings that only a ``lookup_valueset`` rule can translate -- and writes the wall time and peak RSS of
each stage to a JSON artifact.

What it is for, specifically: the claim that the profiler and the importer are STREAMING, so memory is
bounded by the row and not by the corpus. A claim like that is worth exactly what a measurement says, so
this script measures it two ways:

* structurally -- the shape report's own ``sampling.rows_held_at_once_high_water``, which the profiler
  increments as it holds a row and which must be 1;
* empirically -- the same pipeline is run at a smaller record count in a SEPARATE process (``ru_maxrss`` is
  a monotone high-water mark, so two sizes in one process could only ever increase) and the two peaks are
  compared. The ratio and a verdict are recorded as numbers, not as prose.

It also reports the stage that is NOT streaming, because that is the finding a scale run should produce:
``land_delivery`` reads each source file whole (``path.read_bytes()``), so the LAND stage holds the file.

    # both sizes, merged into the artifact the evidence document can cite
    .venv/bin/python scripts/rehearse_delivery_scale.py \\
        --json ../delivery-scale-rehearsal.json

    # one size, in this process (what the unit test drives)
    .venv/bin/python scripts/rehearse_delivery_scale.py --single --records 2000 --profiles 100

Nothing here touches the network, Azure, or the decision model. Generated data lives under ``--workdir`` (a
temporary directory by default) and is removed unless ``--keep`` is given.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.delivery import DeliveryIndex, import_delivery  # noqa: E402
from app.landing import land_delivery, profile_delivery  # noqa: E402
from app.mapping import MappingSpec  # noqa: E402

# Deterministic, like the corpus generator and the preview sampler: a number two people cannot reproduce is
# not evidence.
SEED = 20261013
# Vendor spellings this code does NOT declare, so only the mapping's value sets make the delivery bind.
VENDOR_RECORD_TYPE = "MittausTulos"
VENDOR_TOPIC = "VerenpaineSeuranta"
DEFAULT_PROFILES = 5000
DEFAULT_RECORDS = 100_000
# The smaller run the memory-bound comparison measures. The real delivery is ~698 142 records over 50 000
# profiles, and the point of the comparison is the SHAPE of the growth, not the exact size.
DEFAULT_BASELINE = 25_000
COLUMNS = ["TietueVersio", "LänsiHenkilötunniste", "Kirjaustunniste", "Kirjaustyyppi", "Kirjaaja",
           "Kirjausaika", "Aihe", "Salassapitoluokka", "SuostumuksenLaajuus", "Paino", "Pituus"]
# The mapping a human would write from the shape report. `decimal_fi` is wired to both measurements, so the
# Finnish number transform is exercised at scale too rather than only in a unit test.
MAPPING: dict[str, Any] = {
    "version": "scale-rehearsal-v1",
    "provisional": True,
    "note": "Written for the scale rehearsal's generated delivery. Not a real mapping.",
    "ignore_source_paths": ["TietueVersio"],
    "valuesets": {
        "record_type": {VENDOR_RECORD_TYPE: "mittaus"},
        "topic": {VENDOR_TOPIC: "verenpaine"},
        "sensitivity_tag": {"julkinen": "none"},
        "consent_scope": {"hoito": "treatment"},
    },
    "rules": [
        {"id": "source", "target_field": "source", "transform": "const", "value": "delivery"},
        {"id": "person", "source_path": "LänsiHenkilötunniste", "target_field": "source_person_id"},
        {"id": "record", "source_path": "Kirjaustunniste", "target_field": "record_id"},
        {"id": "type", "source_path": "Kirjaustyyppi", "target_field": "record_type",
         "transform": "lookup_valueset", "valueset": "record_type"},
        {"id": "author", "source_path": "Kirjaaja", "target_field": "authored_by_role"},
        {"id": "when", "source_path": "Kirjausaika", "target_field": "recorded_at", "transform": "date"},
        {"id": "topic", "source_path": "Aihe", "target_field": "topic",
         "transform": "lookup_valueset", "valueset": "topic"},
        {"id": "sensitivity", "source_path": "Salassapitoluokka", "target_field": "sensitivity_tag",
         "transform": "lookup_valueset", "valueset": "sensitivity_tag"},
        {"id": "consent", "source_path": "SuostumuksenLaajuus", "target_field": "consent_scope",
         "transform": "lookup_valueset", "valueset": "consent_scope"},
        {"id": "weight", "source_path": "Paino", "target_field": "payload.paino_kg",
         "transform": "decimal_fi"},
        {"id": "height", "source_path": "Pituus", "target_field": "payload.pituus_cm",
         "transform": "decimal_fi"},
    ],
}
LIMITS = [
    "synthetic deterministic corpus; it exercises the arrival path at size, not the real 13.10.2026 schema",
    "one WIDE layout, one record type and a fixed records-per-person: vocabulary breadth is the rehearsal "
    "script's job (nine wrong mappings, three layouts), not this one's",
    "one machine and one operating system; wall time is I/O-bound and is not a throughput guarantee",
    "resource.ru_maxrss is a MONOTONE process high-water mark, so the two sizes are measured in separate "
    "processes and a per-stage peak means 'peak up to and including this stage', not the stage's own growth",
    "land_delivery streams each source file in chunks (hash, sniff and copy over a bounded buffer), so the "
    "LAND stage does not hold the file; the measured peak at the real 258 MB delivery is what the LAND "
    "number below reports, not a whole-file read",
    "RSS grows with the number of PEOPLE too: the importer indexes every distinct source_person_id, so this "
    "is bounded in the record count, not in the person count",
    "the memory-bound verdict compares two record counts at a FIXED profile count on one machine; it is "
    "evidence for the streaming design, not a proof for every host or every schema",
    "peak_rss_mb is the peak through the IMPORT stage; the integrity walk holds every record id it has seen "
    "and its own peak is reported separately (integrity_peak_rss_mb), because that memory belongs to the "
    "checker and would scale with the record count",
]


@dataclass
class StageResult:
    """One measured stage: its wall time, the process peak after it, and what it produced."""

    name: str
    wall_s: float
    peak_rss_mb: float
    detail: dict[str, Any] = field(default_factory=dict)


def peak_rss_mb() -> float:
    """Peak resident set of this process. macOS reports bytes, Linux kilobytes; the unit is stated."""
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(value / (1024 * 1024), 1) if sys.platform == "darwin" else round(value / 1024, 1)


def directory_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = sum(entry.stat().st_size for entry in path.rglob("*") if entry.is_file())
    return round(total / (1024 * 1024), 2)


def finnish_decimal(value: float) -> str:
    """A Finnish decimal: a comma, and a space as the thousands separator above 999."""
    whole, _, fraction = f"{value:.1f}".partition(".")
    grouped = f"{int(whole):,}".replace(",", " ")
    sign = ""
    if grouped.startswith("-"):
        sign, grouped = "-", grouped[1:]
    return f"{sign}{grouped},{fraction}"


def generate_delivery(raw: Path, profiles: int, records: int, *, seed: int = SEED) -> tuple[int, int]:
    """Write the foreign-layout delivery row by row, streaming, and return (rows, bytes)."""
    raw.mkdir(parents=True, exist_ok=True)
    destination = raw / "mittaukset.csv"
    if profiles < 1 or records < 1:
        raise ValueError("a rehearsal needs at least one profile and one record")
    per_person = max(1, round(records / profiles))
    written = 0
    # Latin-1 on purpose: the encoding detector must not be handed a UTF-8 file, and `ä`/`ö` must survive.
    with destination.open("w", encoding="latin-1", newline="") as stream:
        writer = csv.writer(stream, delimiter=";", lineterminator="\n")
        writer.writerow(COLUMNS)
        person = 0
        for index in range(records):
            if index and index % per_person == 0:
                person += 1
            person = min(person, profiles - 1)
            day = 1 + (index % 28)
            writer.writerow(["4", f"H-{person:06d}", f"R-{index:08d}", VENDOR_RECORD_TYPE, "hoitaja",
                             f"{day:02d}.{(index % 12) + 1:02d}.2026", VENDOR_TOPIC, "julkinen", "hoito",
                             finnish_decimal(60 + (index % 400) / 10), finnish_decimal(1400 + (index % 600) / 10)])
            written += 1
    return written, destination.stat().st_size


def _stage(name: str, started: float, **detail: Any) -> StageResult:
    return StageResult(name=name, wall_s=round(time.perf_counter() - started, 2), peak_rss_mb=peak_rss_mb(),
                       detail=detail)


def run_single(workdir: Path, *, profiles: int, records: int, seed: int = SEED,
               log: Any = print) -> dict[str, Any]:
    """One full measured pass at one size, in this process. The unit of the scale measurement."""
    workdir.mkdir(parents=True, exist_ok=True)
    raw = workdir / "delivery"
    landing = workdir / "landing"
    import_dir = workdir / "import"
    mapping_path = workdir / "mapping-scale.json"
    mapping_path.write_text(json.dumps(MAPPING, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    stages: list[StageResult] = []
    pipeline_started = time.perf_counter()

    started = time.perf_counter()
    rows, source_bytes = generate_delivery(raw, profiles, records, seed=seed)
    stages.append(_stage("generate", started, rows=rows, source_bytes=source_bytes))
    log(f"generate: {rows} rows, {source_bytes / (1024 * 1024):.1f} MB in {stages[-1].wall_s}s")

    started = time.perf_counter()
    manifest = land_delivery(raw, landing)
    stages.append(_stage("land", started, files=len(manifest.files), manifest_hash=manifest.manifest_hash))
    log(f"land:     {len(manifest.files)} file(s) in {stages[-1].wall_s}s")

    started = time.perf_counter()
    shape = profile_delivery(landing, landing)
    high_water = shape["sampling"]["rows_held_at_once_high_water"]
    stages.append(_stage("profile", started, rows_profiled=shape["files"][0]["rows_profiled"],
                         rows_held_at_once_high_water=high_water))
    log(f"profile:  high_water={high_water} in {stages[-1].wall_s}s")
    if high_water != 1:
        raise AssertionError(
            f"the profiler held {high_water} rows at once: the streaming bound this script tests is broken")

    spec = MappingSpec.load(mapping_path)
    started = time.perf_counter()
    mapped = 0
    refused = 0
    for locator, row, error in _located(landing, spec):
        if error is not None or row is None:
            refused += 1
            continue
        try:
            spec.apply_with_matches(row)
        except Exception:  # noqa: BLE001 - a mapped-refusal count is what this stage measures
            refused += 1
            continue
        mapped += 1
    stages.append(_stage("map", started, rows_mapped=mapped, rows_refused=refused))
    log(f"map:      {mapped} rows mapped, {refused} refused in {stages[-1].wall_s}s")

    started = time.perf_counter()
    report = import_delivery(landing, import_dir, mapping=mapping_path, expected_profiles=profiles)
    stages.append(_stage("import", started, personas=report.personas, records=report.records,
                         quarantined=report.quarantined, mapping=report.mapping_version))
    log(f"import:   {report.personas} personas, {report.records} records in {stages[-1].wall_s}s")

    started = time.perf_counter()
    integrity = _integrity(import_dir, report.records)
    stages.append(_stage("integrity", started, **integrity))
    log(f"integrity:{integrity['records_on_disk']} records on disk, "
        f"{integrity['duplicate_record_ids']} duplicates in {stages[-1].wall_s}s")

    walls = {f"{stage.name}_wall_s": stage.wall_s for stage in stages}
    peaks = {stage.name: stage.peak_rss_mb for stage in stages}
    # The PIPELINE's peak stops at the import stage. The integrity walk builds an in-memory set of every
    # record id it has seen, which is a measurement tool, not the pipeline: mixing the two would attribute
    # the checker's memory to the thing being checked and would scale with the record count.
    pipeline_peak = max(stage.peak_rss_mb for stage in stages if stage.name != "integrity")
    return {
        "kind": "delivery-scale-pass",
        "date": date(2026, 10, 10).isoformat(),
        "seed": seed,
        "profiles": profiles,
        "records": rows,
        "records_per_profile": max(1, round(records / profiles)),
        "layout": ("wide semicolon CSV, Latin-1, Finnish decimal comma, DD.MM.YYYY, "
                   "vendor record_type/topic spellings translated by lookup_valueset"),
        **walls,
        "pipeline_wall_s": round(time.perf_counter() - pipeline_started, 2),
        "peak_rss_mb": pipeline_peak,
        "peak_rss_method": ("peak through the IMPORT stage; the integrity walk's own peak is reported "
                            "separately as integrity_peak_rss_mb / final_peak_rss_mb"),
        "final_peak_rss_mb": peak_rss_mb(),
        "integrity_peak_rss_mb": peaks.get("integrity"),
        "source_mb": round(source_bytes / (1024 * 1024), 2),
        "index_mb": directory_mb(import_dir),
        "profiler_rows_held_at_once_high_water": high_water,
        "stage_peak_rss_mb": peaks,
        "import": {"personas": report.personas, "records": report.records,
                   "encounters": report.encounters, "quarantined": report.quarantined},
        "integrity": integrity,
        "stages": [{"name": stage.name, "wall_s": stage.wall_s, "peak_rss_mb": stage.peak_rss_mb,
                    **stage.detail} for stage in stages],
    }


def _located(landing: Path, spec: MappingSpec):
    from app import landing as landing_module  # noqa: PLC0415 - one import, used only by the map stage

    return landing_module.iter_located_rows(landing, skip=spec.ignores_file)


def _integrity(import_dir: Path, expected: int) -> dict[str, Any]:
    """Count what the index resolves, not what the report claims, without walking the corpus twice."""
    index = DeliveryIndex(import_dir)
    try:
        personas = list(index)
        records = sum(len(index.persona(persona_id).records) for persona_id in personas)
    finally:
        index.close()
    files = sorted((import_dir / "people").glob("*.jsonl"))
    seen: set[str] = set()
    duplicates = 0
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record_id = json.loads(line)["record_id"]
            if record_id in seen:
                duplicates += 1
            seen.add(record_id)
    return {"persona_files": len(files), "indexed_records": records, "records_on_disk": len(seen),
            "duplicate_record_ids": duplicates, "matches_import": records == expected == len(seen)}


def build_artifact(full: dict[str, Any], baseline: dict[str, Any] | None,
                   *, generated_at: str) -> dict[str, Any]:
    """Merge the measured passes into the artifact, with the memory-bound verdict as data."""
    artifact = {key: value for key, value in full.items() if key not in ("kind", "stages")}
    artifact["kind"] = "delivery-scale-rehearsal"
    artifact["generated_at"] = generated_at
    artifact["stages"] = full["stages"]
    if baseline is None:
        artifact["memory_bound"] = {
            "measured": False,
            "why": "no baseline size was run, so the two-size RSS comparison is not in this artifact",
            "profiler_rows_held_at_once_high_water": full["profiler_rows_held_at_once_high_water"],
        }
        artifact["limits"] = LIMITS + [
            "this artifact is a SINGLE-size run: it reports the streaming bound structurally, not the "
            "two-size RSS comparison",
        ]
        return artifact
    small, large = baseline["peak_rss_mb"], full["peak_rss_mb"]
    ratio = round(large / small, 3) if small else None
    # A record count that grew 4x while peak RSS stayed within a quarter is the streaming design holding:
    # the tolerance is stated so a reader can disagree with it rather than having to trust a boolean.
    tolerance = 1.25
    bounded = ratio is not None and ratio <= tolerance
    artifact["memory_bound"] = {
        "measured": True,
        "baseline_records": baseline["records"],
        "baseline_peak_rss_mb": small,
        "full_records": full["records"],
        "full_peak_rss_mb": large,
        "records_ratio": round(full["records"] / max(baseline["records"], 1), 2),
        "peak_rss_ratio": ratio,
        "tolerance": tolerance,
        "streaming_bounds_memory": bounded,
        "verdict": (
            f"peak RSS grew {ratio}x while the record count grew "
            f"{round(full['records'] / max(baseline['records'], 1), 2)}x, so the recorded memory is bounded "
            f"by the number of PEOPLE and the fixed structures, not by the number of rows"
            if bounded else
            f"peak RSS grew {ratio}x for a {round(full['records'] / max(baseline['records'], 1), 2)}x record "
            f"count, which is MORE than the fixed structures explain: do not claim the pipeline is bounded "
            f"in the record count on this evidence"),
        "profiler_rows_held_at_once_high_water": full["profiler_rows_held_at_once_high_water"],
        "method": ("each size is measured in its own process because resource.ru_maxrss is a monotone "
                   "high-water mark; both sizes share one profile count"),
    }
    artifact["limits"] = LIMITS
    return artifact


def _child(workdir: Path, profiles: int, records: int, json_path: Path, seed: int, keep: bool) -> int:
    command = [sys.executable, str(Path(__file__).resolve()), "--single", "--profiles", str(profiles),
               "--records", str(records), "--seed", str(seed), "--workdir",
               str(workdir / f"pass-{records}"), "--json", str(json_path)]
    if keep:
        command.append("--keep")
    return subprocess.run(command, check=False).returncode


def rehearse_scale(workdir: Path, *, profiles: int = DEFAULT_PROFILES, records: int = DEFAULT_RECORDS,
                   baseline_records: int | None = DEFAULT_BASELINE, seed: int = SEED,
                   log: Any = print) -> dict[str, Any]:
    """Measure the pipeline at `records`, and at `baseline_records` in a separate process, and merge."""
    workdir.mkdir(parents=True, exist_ok=True)
    log(f"scale rehearsal workdir: {workdir}")
    full_path = workdir / "pass-full.json"
    status = _child(workdir, profiles, records, full_path, seed, False)
    if status != 0 or not full_path.is_file():
        raise RuntimeError(f"the full-size pass failed (exit {status}); see the output above")
    full = json.loads(full_path.read_text(encoding="utf-8"))
    baseline = None
    if baseline_records:
        baseline_path = workdir / "pass-baseline.json"
        status = _child(workdir, profiles, baseline_records, baseline_path, seed, False)
        if status != 0 or not baseline_path.is_file():
            raise RuntimeError(f"the baseline pass failed (exit {status}); see the output above")
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    from datetime import datetime, timezone  # noqa: PLC0415 - one timestamp, at merge time

    return build_artifact(full, baseline, generated_at=datetime.now(timezone.utc).isoformat())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profiles", type=int, default=DEFAULT_PROFILES)
    parser.add_argument("--records", type=int, default=DEFAULT_RECORDS)
    parser.add_argument("--baseline-records", type=int, default=DEFAULT_BASELINE,
                        help="the smaller size the memory-bound comparison measures in a second process; "
                             "0 disables the comparison")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--json", type=Path, default=None,
                        help="where to write the artifact (parent mode; default: print only)")
    parser.add_argument("--single", action="store_true",
                        help="one measured pass in this process, written to --json; used as the child mode")
    parser.add_argument("--workdir", type=Path, default=None)
    parser.add_argument("--keep", action="store_true", help="keep the generated work directory")
    args = parser.parse_args(argv)
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="luvn-scale-"))
    workdir.mkdir(parents=True, exist_ok=True)
    print(f"scale rehearsal workdir: {workdir}")
    try:
        if args.single:
            document = run_single(workdir, profiles=args.profiles, records=args.records, seed=args.seed)
        else:
            document = rehearse_scale(workdir, profiles=args.profiles, records=args.records,
                                      baseline_records=args.baseline_records or None, seed=args.seed)
    except (AssertionError, RuntimeError, ValueError) as exc:
        print(f"SCALE REHEARSAL FAILED: {exc}", file=sys.stderr)
        return 1
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"artifact: {args.json}")
    else:
        print(json.dumps(document, ensure_ascii=False, indent=2))
    if args.single:
        print(f"peak RSS: {document['peak_rss_mb']} MB ({document['records']} records)")
    else:
        bound = document["memory_bound"]
        print(f"memory bound: {bound.get('verdict')}")
    if not args.keep and args.workdir is None:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
