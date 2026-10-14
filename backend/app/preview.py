"""Show a human what a mapping PRODUCED before anything is imported.

This is the answer to the author's own biggest stated risk: *"a plausible-but-wrong mapping that matches
every rule imports cleanly and semantically wrong -- nothing in the path can detect a real column mapped to
the wrong meaning."* No code can detect a wrong meaning. A person can, and only if the code puts the SOURCE
COLUMN NAME beside the TARGET FIELD NAME and the source value beside the mapped value, for records that
actually came out of the delivery.

So `preview_delivery` runs the mapping over the real delivery -- any layout `app/landing.py` accepts -- and
returns:

* a deterministic reservoir SAMPLE of fully mapped canonical records, each rendered field by field as
  ``source column -> target field   source value = mapped value`` so a swapped pair is obvious on sight;
* the mechanical checks from `app.sanity` over every row it scanned, each naming what it saw and stating
  that it is a heuristic;
* the mapping's own refusals, counted by code with examples -- a preview never passes over a refusal
  silently.

It imports NOTHING: no per-person files, no index, no SQL, no people. `import` remains the only step that
writes a corpus, and `preview` is the step a human is supposed to read before running it.

Why a subcommand (`preview`) and not `import --sample`: an import directory is a corpus that can be served
by pointing `LUVN_DELIVERY_DIR` at it, so a flag that turned an import into "not quite an import" would be
one mistyped flag away from serving unreviewed data. A separate verb cannot be mistaken for one, needs no
output directory of its own, and its report says `"imports_records": false`.
"""

from __future__ import annotations

import json
import random
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import landing
from .delivery import REQUIRED_ENVELOPE_TARGETS, _map_raw_row
from .mapping import MappingSpec
from .sanity import HEURISTIC_NOTE, SanityAccumulator

# Deterministic by default, like the corpus generator: a preview that samples differently on every run is a
# preview two people cannot compare notes about.
PREVIEW_SEED = 20261013
# Field values longer than this are truncated in the rendered text (the JSON keeps them whole).
RENDER_VALUE_WIDTH = 34
RENDER_SOURCE_WIDTH = 26
RENDER_TARGET_WIDTH = 24


@dataclass(frozen=True)
class PreviewField:
    """One rule as it acted on one row: the pairing a human has to judge."""

    rule_id: str
    source_path: str | None
    target_field: str
    transform: str
    source_value: Any
    mapped_value: Any
    supplied: bool
    changed: bool

    def as_dict(self) -> dict[str, Any]:
        return {"rule_id": self.rule_id, "source_path": self.source_path,
                "target_field": self.target_field, "transform": self.transform,
                "source_value": self.source_value, "mapped_value": self.mapped_value,
                "supplied": self.supplied, "changed": self.changed}


@dataclass(frozen=True)
class PreviewRecord:
    locator: str
    matched_rules: tuple[str, ...]
    fields: tuple[PreviewField, ...]
    ignored_source_paths: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"locator": self.locator, "matched_rules": list(self.matched_rules),
                "ignored_source_paths": list(self.ignored_source_paths),
                "fields": [entry.as_dict() for entry in self.fields]}


@dataclass
class PreviewReport:
    document: dict[str, Any]
    text: str
    json_path: Path | None = None
    text_path: Path | None = None

    @property
    def findings(self) -> list[dict[str, Any]]:
        return list(self.document["sanity"]["findings"])

    @property
    def refusals(self) -> list[dict[str, Any]]:
        return list(self.document["sanity"]["refusals"])

    def refuses(self) -> bool:
        """Did any check refuse, or did the mapping refuse any row? A preview gate is a refusal list."""
        return bool(self.refusals) or any(entry["severity"] == "refusal" for entry in self.findings)


def _target_value(mapped: dict[str, Any], target_field: str) -> Any:
    if "." not in target_field:
        return mapped.get(target_field)
    container, key = target_field.split(".", 1)
    holder = mapped.get(container)
    if isinstance(holder, dict):
        return holder.get(key)
    return None


def _preview_fields(spec: MappingSpec, raw: dict[str, Any], mapped: dict[str, Any],
                    matched: set[str]) -> tuple[PreviewField, ...]:
    """Every rule, as it acted on this row, in the mapping file's own order."""
    entries: list[PreviewField] = []
    for rule in spec.rules:
        source_value = rule.value if rule.transform == "const" else raw.get(rule.source_path)
        supplied = rule.id in matched
        mapped_value = _target_value(mapped, rule.target_field) if supplied else None
        entries.append(PreviewField(
            rule_id=rule.id, source_path=rule.source_path, target_field=rule.target_field,
            transform=rule.transform, source_value=source_value, mapped_value=mapped_value,
            supplied=supplied,
            changed=supplied and rule.transform != "const" and mapped_value != source_value))
    return tuple(entries)


def _ignored_paths(spec: MappingSpec, raw: dict[str, Any],
                   matched_fields: set[str | None]) -> tuple[str, ...]:
    """Raw columns this row carried that no rule read and the mapping declares it drops."""
    return tuple(sorted(key for key in raw if key not in matched_fields
                        and key in spec.ignore_source_paths))


def _render_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{key}={_render_value(item)}" for key, item in value.items())
    if isinstance(value, list):
        return "[" + ", ".join(_render_value(item) for item in value) + "]"
    text = str(value).replace("\n", "\\n")
    if len(text) > RENDER_VALUE_WIDTH:
        return text[:RENDER_VALUE_WIDTH - 1] + "\u2026"
    return text


def _source_kind(source: Path) -> str:
    """What the preview is reading, for the header only: `landing.py` does the real detection."""
    if source.is_dir():
        return "landing zone" if (source / landing.MANIFEST_NAME).is_file() else "directory"
    try:
        if source.is_file() and zipfile.is_zipfile(source):
            return "zip"
    except OSError:
        return "unreadable"
    suffix = source.suffix.lstrip(".").lower()
    return suffix or "single file"


def render_preview(document: dict[str, Any]) -> str:
    """The human checklist: mappings beside values, then the checks, then the honest disclaimer."""
    lines: list[str] = []
    lines.append(f"delivery mapping preview   mapping: {document['mapping_version']}"
                 f"{'  (PROVISIONAL)' if document['provisional'] else ''}")
    lines.append(f"mapping file: {document['mapping_path']}")
    lines.append(f"source:       {document['source']} [{document['source_format']}]")
    lines.append(f"scanned:      {document['rows_scanned']} rows"
                 f"{'' if document['complete_scan'] else '  (PARTIAL SCAN)'}"
                 f"   mapped: {document['rows_mapped']}   refused: {document['rows_refused']}")
    lines.append(f"sample:       {len(document['records'])} of {document['rows_mapped']} mapped records, "
                 f"{document['sample_method']}")
    lines.append("")
    lines.append("READ THIS FIRST: " + HEURISTIC_NOTE)
    lines.append("A mapping is judged by whether the two NAMES on each line below belong together. The "
                 "value columns only help you see it; a plausible value in the wrong field still looks "
                 "plausible here.")
    for index, record in enumerate(document["records"], start=1):
        lines.append("")
        lines.append(f"--- record {index} of {len(document['records'])}   {record['locator']} ---")
        lines.append(f"  {'source column':{RENDER_SOURCE_WIDTH}s} -> {'target field':{RENDER_TARGET_WIDTH}s} "
                     f"{'source value':{RENDER_VALUE_WIDTH}s} mapped value")
        for entry in record["fields"]:
            source = entry["source_path"] if entry["source_path"] is not None else "(constant)"
            if not entry["supplied"]:
                marker, mapped = "(no value in this row)", ""
            elif entry["changed"]:
                marker, mapped = "=>", _render_value(entry["mapped_value"])
            else:
                marker, mapped = "=", _render_value(entry["mapped_value"])
            lines.append(f"  {source:{RENDER_SOURCE_WIDTH}s} -> {entry['target_field']:{RENDER_TARGET_WIDTH}s} "
                         f"{_render_value(entry['source_value']):{RENDER_VALUE_WIDTH}s} {marker} {mapped}"
                         f"   [{entry['transform']}]")
        if record["ignored_source_paths"]:
            lines.append(f"  (deliberately dropped by ignore_source_paths: "
                         f"{', '.join(record['ignored_source_paths'])})")
    lines.append("")
    lines.append("--- mechanical checks (HEURISTICS, not proof) ---")
    if not document["sanity"]["findings"]:
        lines.append("  no mechanical check fired. That is NOT evidence the mapping is right: none of the "
                     "checks can see a meaning, so read the column pairs above.")
    for finding in document["sanity"]["findings"]:
        lines.append(f"  {finding['severity'].upper():7s} {finding['check']}: {finding['summary']}")
        lines.append(f"          why this is only a heuristic: {finding['heuristic']}")
    if document["sanity"]["refusals"]:
        lines.append("  refusals from the mapping itself:")
        for refusal in document["sanity"]["refusals"]:
            lines.append(f"    {refusal['code']} x{refusal['count']}")
            for example in refusal["examples"][:3]:
                lines.append(f"      {example['locator']} {example['values']} {example['reason']}")
    lines.append("")
    lines.append("This step imported nothing (imports_records: false). If every line above pairs the column "
                 "you expect with the field you expect, run the import; if one does not, fix the mapping "
                 "file first.")
    return "\n".join(lines) + "\n"


def preview_delivery(source: str | Path, mapping: MappingSpec | str | Path, *, sample: int = 5,
                     scan_limit: int | None = None, expected_profiles: int | None = None,
                     seed: int = PREVIEW_SEED, now: datetime | None = None) -> PreviewReport:
    """Map the delivery, sample it, check it -- and write no corpus.

    `sample` is how many fully mapped records a human reads. The sample is a deterministic reservoir over
    every row scanned, not the first N rows: a vendor file is usually sorted by person, so the first N rows
    are frequently one person's records and hide the shape of the rest.

    `scan_limit` bounds the scan (None reads the whole delivery). A partial scan is labelled as one
    everywhere it matters, including in the cardinality check.
    """
    if sample < 1:
        raise ValueError("preview needs --sample of at least 1")
    source = Path(source)
    spec = mapping if isinstance(mapping, MappingSpec) else MappingSpec.load(Path(mapping))
    spec.require_targets(REQUIRED_ENVELOPE_TARGETS)
    accumulator = SanityAccumulator(expected_profiles=expected_profiles, complete_scan=True, now=now)
    rng = random.Random(seed)
    # Algorithm R: uniform over the rows scanned, O(sample) memory, and a record kept here is the whole
    # mapped envelope plus the raw row it came from -- the pair a human has to compare.
    reservoir: list[tuple[str, dict[str, Any], dict[str, Any], set[str]]] = []
    scanned = 0
    mapped_rows = 0
    complete_scan = True
    try:
        for locator, raw, error in landing.iter_located_rows(source, skip=spec.ignores_file):
            if error is not None:
                scanned += 1
                accumulator.refuse("malformed_row", str(error), locator)
                continue
            if raw is None:
                continue
            scanned += 1
            target, refusal, matched = _map_raw_row(spec, raw, locator)
            if refusal is not None:
                accumulator.refuse(refusal.code, str(refusal), locator, refusal.values)
                continue
            if target is None:
                continue
            mapped_rows += 1
            accumulator.observe(target, locator)
            if len(reservoir) < sample:
                reservoir.append((locator, raw, target, matched))
            else:
                index = rng.randrange(mapped_rows)
                if index < sample:
                    reservoir[index] = (locator, raw, target, matched)
            if scan_limit is not None and scanned >= scan_limit:
                complete_scan = False
                break
    except landing.LandingError as exc:
        # A layout no row mapping can express. The preview is still the useful thing to hand a human: keep
        # the sample and rows already read, and record the refusal as a finding with the file named.
        complete_scan = False
        accumulator.refuse("unsupported_layout", str(exc), str(source))
    accumulator.complete_scan = complete_scan
    records = []
    for locator, raw, target, matched in reservoir:
        fields = _preview_fields(spec, raw, target, matched)
        records.append(PreviewRecord(
            locator=locator, matched_rules=tuple(sorted(matched)), fields=fields,
            ignored_source_paths=_ignored_paths(spec, raw, {rule.source_path for rule in spec.rules})))
    document: dict[str, Any] = {
        "kind": "delivery-mapping-preview",
        "imports_records": False,
        "note": HEURISTIC_NOTE,
        "mapping_version": spec.version,
        "mapping_path": str(spec.path),
        "provisional": spec.provisional,
        "source": str(source),
        "source_format": _source_kind(source),
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "rows_scanned": scanned,
        "rows_mapped": mapped_rows,
        "rows_refused": accumulator.refused_rows,
        "complete_scan": complete_scan,
        "scan_limit": scan_limit,
        "sample_size": sample,
        "sample_method": f"deterministic reservoir over the rows scanned, seed {seed}",
        "records": [record.as_dict() for record in records],
        "sanity": accumulator.as_dict(),
    }
    return PreviewReport(document=document, text=render_preview(document))


def write_preview(report: PreviewReport, output_dir: str | Path) -> tuple[Path, Path]:
    """Write the JSON and the rendered checklist under `output_dir/results/`."""
    results = Path(output_dir) / "results"
    results.mkdir(parents=True, exist_ok=True)
    json_path = results / "delivery-mapping-preview.json"
    text_path = results / "delivery-mapping-preview.txt"
    json_path.write_text(json.dumps(report.document, ensure_ascii=False, indent=2, default=str) + "\n",
                         encoding="utf-8")
    text_path.write_text(report.text, encoding="utf-8")
    report.json_path, report.text_path = json_path, text_path
    return json_path, text_path
