#!/usr/bin/env python
"""Arrival-day delivery intake as commands an operator can paste.

`app.delivery` has always had `dry_run_delivery` and `import_delivery`; neither had a command, so
`arrival-runbook.md` could only describe them. This is that command and the intake steps beside it: no
policy, no network, no model — it reads a delivery and writes a report or an import.

    # 0. Land the bytes first; interpret nothing. Any layout is accepted (CSV, JSON, JSONL, zip, dirs).
    .venv/bin/python scripts/reconcile_delivery.py land /path/to/delivery --out /var/luvn/landing

    # 0b. Profile the landing zone: encoding, delimiter, columns, candidate vocabularies and roles.
    .venv/bin/python scripts/reconcile_delivery.py profile /var/luvn/landing --out /var/luvn/landing

    # 1. Reconcile. Imports nothing; writes its report even when it refuses.
    .venv/bin/python scripts/reconcile_delivery.py dry-run /path/to/delivery --out /tmp/luvn-dry-run

    # 1b. PREVIEW -- the human gate. Maps the delivery, shows N fully mapped records field by field
    #     (source column -> target field, source value beside mapped value), and runs the mechanical
    #     sanity checks. Imports nothing. This is the step a person reads before step 2.
    .venv/bin/python scripts/reconcile_delivery.py preview /var/luvn/landing \\
        --mapping mappings/delivery-mapping-2026-10-13.json --sample 5 --to /tmp/luvn-preview
    .venv/bin/python scripts/reconcile_delivery.py preview /var/luvn/landing \\
        --mapping mappings/delivery-mapping-2026-10-13.json --sample 10 --profiles 50000

    # 2. Import. Strict by default; quarantine keeps the good rows when the delivery is unknown.
    .venv/bin/python scripts/reconcile_delivery.py import /path/to/delivery --out /tmp/luvn-import
    .venv/bin/python scripts/reconcile_delivery.py import /path/to/delivery --out /tmp/luvn-import \\
        --on-error=quarantine

    # 2b. The arrival-day import: a raw delivery in ANY layout, mapped into the canonical envelope by a
    #     declarative file. `source` may be a landing zone, a directory, a zip, or one file.
    .venv/bin/python scripts/reconcile_delivery.py import /var/luvn/landing --out /tmp/luvn-import \\
        --mapping mappings/delivery-mapping-2026-10-13.json

    # 3. After a vocabulary revision, re-attempt only the quarantined rows. `--mapping` re-maps the raw
    #    vendor rows a mapping import quarantined, so a corrected value set recovers them without a re-run.
    .venv/bin/python scripts/reconcile_delivery.py retry-quarantine \\
        /tmp/luvn-import/quarantine/quarantine.jsonl --import-dir /tmp/luvn-import
    .venv/bin/python scripts/reconcile_delivery.py retry-quarantine \\
        /tmp/luvn-import/quarantine/quarantine.jsonl --import-dir /tmp/luvn-import \\
        --mapping mappings/delivery-mapping-2026-10-13-v2.json

    # 4. Check a declarative mapping revision before using it.
    .venv/bin/python scripts/reconcile_delivery.py map /path/to/records.jsonl \\
        --mapping mappings/delivery-mapping-v1.json --dry-run

    # 5. DELETE the run after the event. Refuses without the confirmation word, is idempotent, and emits a
    #    dated record that names what it removed AND what it cannot reach (see the record's first lines).
    .venv/bin/python scripts/reconcile_delivery.py delete <run-dir-or-manifest-hash> \\
        --confirm I_CONFIRM_DELETE --import-dir /var/luvn/import-20261013 \\
        --receipt /var/luvn/deletion-receipts

Exit status is the decision, so this composes with a shell:

    0  the step did what it says (`ready`, or an import that finished)
    1  the delivery was refused, the reconciliation found something to map, or a retry still has work
    2  the command was used wrongly

`dry-run` writes `results/delivery-reconciliation-report.json` and prints the flag summary. A refusal is
deliberately non-zero: the runbook's whole point is that arrival day is the *second* run, and a
reconciliation that returns success while reporting warnings is how that stops being true. The report is
written BEFORE the refusal, so the printed `report:` path is always real.

Read `arrival-runbook.md` before running either step against anything real. Verifying this importer is
NOT verifying model egress: no record of the delivery may be sent to the decision model or to Azure AI Foundry
until the Art. 6/9 basis, the Art. 28 processor terms and the Art. 35 DPIA exist (issue 12).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.delivery import (  # noqa: E402
    DeliveryShapeError, dry_run_delivery, import_delivery, retry_quarantine,
)
from app.deletion import (  # noqa: E402
    DELETE_CONFIRMATION, DeletionError, delete_delivery_run,
)
from app.landing import (  # noqa: E402
    LandingError, land_delivery, profile_delivery, read_rows, render_shape_summary,
)
from app.mapping import MappingError, MappingSpec  # noqa: E402
from app.preview import PREVIEW_SEED, preview_delivery, write_preview  # noqa: E402

# The per-type keys a report reader has to look at, in the order the runbook lists them.
FLAG_KEYS = ("missing_render_keys", "unmapped_payload_keys", "useful_set_miss_rows", "content_maps_to_nothing")


def _flagged(document: dict) -> dict[str, dict]:
    return {
        name: {key: item[key] for key in FLAG_KEYS if item.get(key)}
        for name, item in document["types"].items()
        if any(item.get(key) for key in FLAG_KEYS)
    }


def _report_path(out_dir: Path) -> Path:
    return (out_dir / "results" / "delivery-reconciliation-report.json").resolve()


def _print_refusals(document: dict) -> None:
    reasons = document.get("refusals") or []
    if not reasons:
        return
    print(f"refused: {document.get('rows_refused')} of {document.get('rows_examined')} rows examined",
          file=sys.stderr)
    for reason in reasons:
        print(f"  {reason['code']} x{reason['count']}: {reason['reason']}", file=sys.stderr)
        for example in reason.get("examples", [])[:5]:
            values = example.get("values") or {}
            print(f"      {example.get('locator')} {example.get('record_id') or ''} {values}",
                  file=sys.stderr)


def dry_run(source_dir: Path, out_dir: Path) -> int:
    report_path = _report_path(out_dir)
    try:
        report = dry_run_delivery(source_dir, out_dir)
    except DeliveryShapeError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        # The report is written before the refusal, so this path is real. Say so only when it is.
        if report_path.is_file():
            print(f"report:  {report_path}", file=sys.stderr)
            _print_refusals(json.loads(report_path.read_text(encoding="utf-8")))
        else:
            print("report:  (not written)", file=sys.stderr)
        return 1
    document = json.loads(report.report_path.read_text(encoding="utf-8"))
    flagged = _flagged(document)
    print(f"status:   {report.status}")
    print(f"profiles: {report.profiles_seen}")
    print(f"records:  {report.records_seen}")
    print(f"examined: {report.rows_examined}  refused: {report.rows_refused}")
    print(f"report:   {report.report_path}")
    if document["unmapped_record_types"]:
        print(f"unmapped record types: {document['unmapped_record_types']}")
    if document["unmapped_payload_keys_by_unmapped_type"]:
        print(f"unmapped keys on unmapped types: {document['unmapped_payload_keys_by_unmapped_type']}")
    for name, flags in sorted(flagged.items()):
        print(f"  {name}: {flags}")
    if report.status != "ready":
        # `warning` means: map the named keys, then run this step again. `failed` already raised above.
        print("not ready: map the flagged keys in app/delivery.py and re-run the dry run", file=sys.stderr)
        return 1
    print("ready: every rendered type is renderable, useful and free of unmapped keys")
    return 0


def run_import(source_dir: Path, out_dir: Path, on_error: str, fail_on_quarantine: bool,
               mapping_path: Path | None = None, expected_profiles: int | None = None) -> int:
    try:
        report = import_delivery(source_dir, out_dir, on_error=on_error,
                                 fail_on_quarantine=fail_on_quarantine, mapping=mapping_path,
                                 expected_profiles=expected_profiles)
    except (DeliveryShapeError, MappingError, LandingError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    shape = json.loads(report.shape_report.read_text(encoding="utf-8"))
    if report.mapping_version:
        print(f"mapping:    {report.mapping_version} ({report.mapping_path})")
    print(f"mode:       {on_error}")
    print(f"personas:   {report.personas}")
    print(f"records:    {report.records}")
    print(f"encounters: {report.encounters}")
    print(f"skipped:    {report.skipped}")
    print(f"shape:      {report.shape_report}")
    print(f"artifacts:  {report.shape_report.parent / 'artifacts.json'}")
    for name, item in sorted(shape["types"].items()):
        print(f"  {name:20s} rows={item['rows_seen']:5d} useful={item['useful_set_met']:5d}")
    # The mechanical checks over the mapped output, in the run's own summary. They REPORT here; `preview`
    # is the step that REFUSES before an import, and the exit status below is unchanged on purpose: a
    # quarantine run's contract is that it loads the good rows without failing, and a check does not get to
    # silently rewrite that contract after the fact.
    refused_by_check = False
    if report.sanity_report and report.sanity_report.is_file():
        sanity = json.loads(report.sanity_report.read_text(encoding="utf-8"))
        print(f"checks:     {report.sanity_report}")
        for finding in sanity["findings"]:
            refused_by_check = refused_by_check or finding["severity"] == "refusal"
            print(f"  {finding['severity']:7s} {finding['check']}: {finding['summary']}")
            print(f"          heuristic, not proof: {finding['heuristic']}")
        if not sanity["findings"]:
            print("  no mechanical check fired; that is not evidence the mapping is right -- run "
                  "`preview` and read the column pairs")
    if report.quarantined:
        summary = json.loads(report.quarantine_summary.read_text(encoding="utf-8")) \
            if report.quarantine_summary else {"reasons": []}
        print(f"loaded:      {report.records}")
        print(f"quarantined: {report.quarantined}")
        print(f"quarantine:  {report.quarantine_path}")
        print(f"summary:     {report.quarantine_summary}")
        for reason in summary.get("reasons", []):
            print(f"  {reason['code']} x{reason['count']}: {reason['reason']}")
        if summary.get("empty_useful_types"):
            print(f"  note: types with rows but no useful row: {summary['empty_useful_types']}")
        if report.records == 0:
            print("nothing loaded: every row was quarantined", file=sys.stderr)
            return 1
    elif report.skipped:
        print(f"skipped rows are a refusal to guess; inspect the report: {report.skipped}", file=sys.stderr)
        return 1
    if refused_by_check:
        print("WARNING: a mechanical check REFUSED this mapping's output (see `checks:` above). The corpus "
              "was loaded anyway -- run `preview` before serving it, and fix the mapping if a column pair is "
              "wrong.", file=sys.stderr)
    print(f"set LUVN_DELIVERY_DIR={report.output_dir.resolve()} to serve this import")
    return 0


def run_land(source: Path, out_dir: Path) -> int:
    try:
        manifest = land_delivery(source, out_dir)
    except LandingError as exc:
        print(f"landing refused: {exc}", file=sys.stderr)
        return 1
    print(f"manifest:    {manifest.manifest_path}")
    print(f"manifest_hash: {manifest.manifest_hash}")
    print(f"files:       {len(manifest.files)}  bytes: {manifest.total_bytes}")
    for entry in manifest.files:
        print(f"  {entry.source_path}  {entry.bytes} bytes  {entry.content_type}  sha256:{entry.sha256[:12]}")
    return 0


def run_profile(landing_dir: Path, out_dir: Path | None) -> int:
    try:
        document = profile_delivery(landing_dir, out_dir or landing_dir)
    except LandingError as exc:
        print(f"profile refused: {exc}", file=sys.stderr)
        return 1
    print(render_shape_summary(document))
    if out_dir is not None:
        print(f"report:  {(Path(out_dir) / 'results' / 'delivery-shape-report.json').resolve()}")
        print(f"summary: {(Path(out_dir) / 'results' / 'delivery-shape-summary.txt').resolve()}")
    return 0


def run_map(source: Path, mapping_path: Path, out_dir: Path | None, dry_run_mode: bool,
            limit: int) -> int:
    try:
        spec = MappingSpec.load(mapping_path)
    except MappingError as exc:
        print(f"mapping refused: {exc}", file=sys.stderr)
        return 1
    try:
        # The dry run may sample (its report states `rows_examined`); an applied mapping must see every row.
        rows, meta = read_rows(source, limit=None if (not dry_run_mode or limit == 0) else limit)
    except LandingError as exc:
        print(f"cannot read {source}: {exc}", file=sys.stderr)
        return 1
    document = spec.dry_run(rows)
    document["source"] = str(source)
    document["source_format"] = meta.get("format")
    print(f"mapping:     {spec.version} ({spec.path})"
          f"{'  PROVISIONAL' if spec.provisional else ''}")
    print(f"source:      {source} [{meta.get('format')}]  rows: {len(rows)}")
    print(f"{'rule':28s} {'source_path':28s} {'target_field':22s} {'transform':16s} matched")
    for rule in document["rules"]:
        print(f"{rule['id']:28s} {str(rule['source_path']):28s} {rule['target_field']:22s} "
              f"{rule['transform']:16s} {rule['matched_rows']}")
    if document["rules_matched_nothing"]:
        print(f"rules matched nothing: {document['rules_matched_nothing']}")
    if document["unknown_source_paths"]:
        print(f"unknown source paths (refused): {document['unknown_source_paths']}", file=sys.stderr)
    if dry_run_mode:
        if out_dir is not None:
            results = Path(out_dir)
            results.mkdir(parents=True, exist_ok=True)
            (results / "delivery-mapping-dry-run.json").write_text(
                json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"report:      {(results / 'delivery-mapping-dry-run.json').resolve()}")
        return 1 if document["unknown_source_paths"] else 0
    if out_dir is None:
        print("map without --dry-run needs --out", file=sys.stderr)
        return 2
    mapped_dir = Path(out_dir) / "mapped"
    mapped_dir.mkdir(parents=True, exist_ok=True)
    destination = mapped_dir / "records.jsonl"
    written = 0
    # Truncate first: a mapping revision applied twice must not silently double the corpus.
    destination.write_text("", encoding="utf-8")
    with destination.open("a", encoding="utf-8") as stream:
        for index, row in enumerate(rows):
            try:
                target = spec.apply(row)
            except MappingError as exc:
                print(f"refused at row {index + 1}: {exc}", file=sys.stderr)
                return 1
            stream.write(json.dumps(target, ensure_ascii=False) + "\n")
            written += 1
    print(f"wrote {written} mapped rows to {destination.resolve()}")
    return 0


def run_preview(source: Path, mapping_path: Path, out_dir: Path | None, sample: int,
                scan_limit: int | None, expected_profiles: int | None, seed: int) -> int:
    """The human gate: show what the mapping produced, run the checks, import nothing.

    Exit 0 means no check refused and the mapping refused no row -- NOT that the mapping is right. A human
    still has to read the column pairs; that judgement is the whole point of the step, and no exit status
    can make it.
    """
    try:
        report = preview_delivery(source, mapping_path, sample=sample, scan_limit=scan_limit,
                                  expected_profiles=expected_profiles, seed=seed)
    except (MappingError, LandingError, DeliveryShapeError) as exc:
        print(f"preview refused: {exc}", file=sys.stderr)
        return 1
    print(report.text)
    if out_dir is not None:
        json_path, text_path = write_preview(report, out_dir)
        print(f"preview json: {json_path}")
        print(f"preview text: {text_path}")
    else:
        print("preview: no --to directory given; this record was printed and not written anywhere")
    if report.refuses():
        print("preview: the checks above REFUSE this mapping's output as it stands -- fix the mapping "
              "before importing (nothing was imported)", file=sys.stderr)
        return 1
    print("preview: no refusal. That is not a proof the mapping is right: read the column pairs above.")
    return 0


def run_delete(run_id: str, *, confirm: str, landing_dir: Path | None, import_dirs: list[Path],
               persons: Path | None, use_sql: bool, blob_container: str | None, receipt: Path | None,
               tmp_dir: Path | None, delivery_dir: Path | None, keep_tmp: bool,
               confirmed_by: str | None) -> int:
    person_ids: list[str] = []
    if persons is not None:
        try:
            person_ids = [line.strip() for line in persons.read_text(encoding="utf-8").splitlines()
                          if line.strip()]
        except OSError as exc:
            print(f"delete refused: cannot read --persons {persons}: {exc}", file=sys.stderr)
            return 1
    connection = None
    if use_sql:
        try:
            from app import sqldelivery  # noqa: PLC0415 - only imported when a real SQL deletion is asked

            connection = sqldelivery.connect()
        except Exception as exc:  # noqa: BLE001 - report it in the record, not only on stderr
            print(f"delete: could not open Azure SQL ({exc}); the SQL step will be recorded as failed",
                  file=sys.stderr)
    try:
        record = delete_delivery_run(
            run_id, confirm=confirm, landing_dir=landing_dir, connection=connection,
            person_ids=person_ids, indexes=import_dirs, blob_container=blob_container,
            tmp_dir=tmp_dir, delivery_dir=delivery_dir, receipt_dir=receipt,
            confirmed_by=confirmed_by, keep_tmp=keep_tmp)
    except DeletionError as exc:
        print(f"delete refused: {exc}", file=sys.stderr)
        return 2
    print(record.text)
    if receipt is not None:
        print(f"receipt: {record.json_path}")
        print(f"receipt: {record.text_path}")
    else:
        print("delete: no --receipt directory given; this record was printed and NOT written anywhere")
    if record.raises():
        print("delete: at least one step FAILED (see the record); the delivery is not deleted", file=sys.stderr)
        return 1
    print("delete: done as far as this job can reach. The record's NOT_REACHABLE list is part of the "
          "answer, not a footnote.", file=sys.stderr)
    return 0


def run_retry_quarantine(quarantine_path: Path, import_dir: Path, out: Path | None,
                         mapping_path: Path | None = None) -> int:
    try:
        report = retry_quarantine(quarantine_path, import_dir, out, mapping=mapping_path)
    except (DeliveryShapeError, MappingError, LandingError) as exc:
        print(f"retry refused: {exc}", file=sys.stderr)
        return 1
    print(f"attempted: {report.attempted}")
    print(f"recovered: {report.recovered}")
    print(f"remaining: {report.remaining}")
    print(f"output:    {report.output.resolve()}")
    print(f"summary:   {report.output.with_name('quarantine-retry-summary.json').resolve()}")
    return 1 if report.remaining else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="step", required=True)

    dry = sub.add_parser("dry-run", help="reconcile the delivery; imports nothing, writes its report first")
    dry.add_argument("source_dir", type=Path)
    dry.add_argument("--out", type=Path, required=True)

    prev = sub.add_parser("preview", help="show fully mapped records field by field and run the sanity "
                                          "checks; imports nothing (the human gate before import)")
    prev.add_argument("source", type=Path, help="a raw delivery, a landing zone, a zip or a single file")
    prev.add_argument("--mapping", type=Path, required=True)
    prev.add_argument("--sample", type=int, default=5,
                      help="how many fully mapped records to render for a human to read")
    prev.add_argument("--to", type=Path, default=None,
                      help="write results/delivery-mapping-preview.json and .txt under this directory")
    prev.add_argument("--scan-limit", type=int, default=0,
                      help="rows to scan for the checks; 0 (default) scans the whole delivery")
    prev.add_argument("--profiles", type=int, default=None,
                      help="the vendor's declared profile count, for the cardinality check")
    prev.add_argument("--seed", type=int, default=None,
                      help="sampling seed; the default is fixed so two people sample the same records")

    imp = sub.add_parser("import", help="stream the delivery into per-person files and an index")
    imp.add_argument("source_dir", type=Path)
    imp.add_argument("--out", type=Path, required=True)
    imp.add_argument("--mapping", type=Path, default=None,
                     help="map a raw delivery of ANY accepted layout through this declarative file; "
                          "the mapping must state a rule for every required envelope field")
    imp.add_argument("--on-error", choices=("strict", "quarantine"), default="strict",
                     help="strict refuses the whole delivery on the first bad row (default); "
                          "quarantine stages bad rows and loads the remainder")
    imp.add_argument("--fail-on-quarantine", action="store_true",
                     help="exit non-zero when anything was quarantined")
    imp.add_argument("--profiles", type=int, default=None,
                     help="the vendor's declared profile count, for the cardinality check in the run's "
                          "delivery-sanity-report.json")

    land = sub.add_parser("land", help="store the delivery bytes verbatim with a delivery_run manifest")
    land.add_argument("source", type=Path)
    land.add_argument("--out", type=Path, required=True)

    prof = sub.add_parser("profile", help="report the landing zone's shape (encoding, delimiter, vocabularies)")
    prof.add_argument("landing_dir", type=Path)
    prof.add_argument("--out", type=Path, default=None)

    mapping = sub.add_parser("map", help="check or apply a declarative mapping revision")
    mapping.add_argument("source", type=Path, help="a records file or a directory holding one")
    mapping.add_argument("--mapping", type=Path, required=True)
    mapping.add_argument("--dry-run", action="store_true", help="print per-rule match counts; map nothing")
    mapping.add_argument("--out", type=Path, default=None)
    mapping.add_argument("--limit", type=int, default=10000,
                         help="rows to read for the dry run; 0 reads every row")

    retry = sub.add_parser("retry-quarantine", help="re-attempt quarantined rows with the current vocabulary")
    retry.add_argument("quarantine_path", type=Path)
    retry.add_argument("--import-dir", type=Path, required=True)
    retry.add_argument("--out", type=Path, default=None)
    retry.add_argument("--mapping", type=Path, default=None,
                       help="re-map the raw vendor rows a mapping import quarantined through this "
                            "revised mapping before binding them")

    dele = sub.add_parser("delete", help="delete a delivery run's reachable copies and emit the dated "
                                         "record that names what it could NOT reach")
    dele.add_argument("run_id", help="the landing directory (or its manifest hash) that names the run")
    dele.add_argument("--confirm", choices=(DELETE_CONFIRMATION,), required=True,
                      help=f"the exact word that confirms a deletion: {DELETE_CONFIRMATION}")
    dele.add_argument("--landing-dir", type=Path, default=None,
                      help="the landing zone holding the run's manifest.json, when run_id is a hash")
    dele.add_argument("--import-dir", type=Path, action="append", default=[],
                      help="an import output directory of this run; its index.sqlite is read for the people "
                           "this run indexed, then removed (repeatable)")
    dele.add_argument("--persons", type=Path, default=None,
                      help="a file of source_person_id values, one per line, when no index is available")
    dele.add_argument("--sql", action="store_true",
                      help="open Azure SQL and delete this run's rows (needs the driver and a token)")
    dele.add_argument("--blob-container", default=None,
                      help="Blob container to delete; needs LUVN_STORAGE_ACCOUNT_URL and the storage SDK")
    dele.add_argument("--receipt", type=Path, default=None,
                      help="directory for the JSON and text deletion record")
    dele.add_argument("--tmp-dir", type=Path, default=None, help="temp directory to sweep for luvn-* copies")
    dele.add_argument("--delivery-dir", type=Path, default=None,
                      help="a LUVN_DELIVERY_DIR working copy to remove (defaults to the environment)")
    dele.add_argument("--keep-tmp", action="store_true",
                      help="do NOT sweep temp working copies; the record says the sweep was skipped")
    dele.add_argument("--confirmed-by", default=None,
                      help="who is confirming; recorded as text, not verified")

    args = parser.parse_args(argv)
    if args.step == "dry-run":
        if not args.source_dir.is_dir():
            print(f"no such delivery directory: {args.source_dir}", file=sys.stderr)
            return 2
        return dry_run(args.source_dir, args.out)
    if args.step == "preview":
        if not args.source.exists():
            print(f"no such delivery: {args.source}", file=sys.stderr)
            return 2
        if not args.mapping.is_file():
            print(f"no such mapping file: {args.mapping}", file=sys.stderr)
            return 2
        seed = args.seed if args.seed is not None else PREVIEW_SEED
        return run_preview(args.source, args.mapping, args.to, args.sample,
                           args.scan_limit or None, args.profiles, seed)
    if args.step == "import":
        if not args.source_dir.exists():
            print(f"no such delivery: {args.source_dir}", file=sys.stderr)
            return 2
        if args.mapping is None and not args.source_dir.is_dir():
            print(f"import without --mapping needs a delivery DIRECTORY holding profiles.csv and "
                  f"records.jsonl/records.csv, not {args.source_dir}", file=sys.stderr)
            return 2
        if args.mapping is not None and not args.mapping.is_file():
            print(f"no such mapping file: {args.mapping}", file=sys.stderr)
            return 2
        return run_import(args.source_dir, args.out, args.on_error, args.fail_on_quarantine, args.mapping,
                          args.profiles)
    if args.step == "delete":
        return run_delete(args.run_id, confirm=args.confirm, landing_dir=args.landing_dir,
                          import_dirs=list(args.import_dir), persons=args.persons, use_sql=args.sql,
                          blob_container=args.blob_container, receipt=args.receipt, tmp_dir=args.tmp_dir,
                          delivery_dir=args.delivery_dir, keep_tmp=args.keep_tmp,
                          confirmed_by=args.confirmed_by)
    if args.step == "land":
        return run_land(args.source, args.out)
    if args.step == "profile":
        return run_profile(args.landing_dir, args.out)
    if args.step == "map":
        if not args.dry_run and args.out is None:
            print("map needs --dry-run or --out", file=sys.stderr)
            return 2
        return run_map(args.source, args.mapping, args.out, args.dry_run, args.limit)
    return run_retry_quarantine(args.quarantine_path, args.import_dir, args.out, args.mapping)


if __name__ == "__main__":
    raise SystemExit(main())
