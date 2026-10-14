"""Land first, parse never: a format-agnostic landing zone and a shape profiler.

The 2026-10-13 delivery arrives as "50 000 profiles, CSV or JSON, format unknown until the day". Every
importer in this repository before now assumed a shape (`profiles.csv` + `records.jsonl`), and
`fetch_corpus_blob` even raises when a named file is missing. That assumption is the thing this module
removes from the arrival path:

* `land_delivery` stores bytes VERBATIM, content-addressed by SHA-256, with a `delivery_run` manifest
  (files, bytes, manifest hash, received_at). It accepts one big CSV, one JSON per person, a single JSON
  array, JSONL, many files, nested directories, or a zip -- because it interprets nothing.
* `profile_delivery` reads the landing zone and reports the SHAPE: detected encoding and delimiter, header
  and column names, per-field type/null-rate/distinct/min-max/samples, the candidate vocabularies (the
  distinct values of every low-cardinality string column -- how the vendor's vocabulary is discovered),
  candidate roles (which columns look like a person id, an encounter id, a timestamp, a code, a free-text
  note, a record type), and structural facts (wide vs long, nested vs flat, shared keys).

It maps NOTHING. Mapping is `app/mapping.py` and its versioned file.
"""

from __future__ import annotations

import codecs
import csv
import hashlib
import shutil
import heapq
import io
import json
import math
import mimetypes
import re
import statistics
import zipfile
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .mapping import parse_date

# 1 MiB of file I/O at a time, and only the first 4 KiB retained for content sniffing.
_CHUNK_BYTES = 1024 * 1024
_SNIFF_BYTES = 4096
LANDING_FILES = "files"
MANIFEST_NAME = "manifest.json"
# Distinct-value tracking is capped so a 258 MB delivery profiles in bounded memory. An exact set holds the
# first `DISTINCT_CAP` distinct values; past that the column is marked capped and its cardinality comes from
# a HyperLogLog sketch, and its samples from a deterministic reservoir. Both are ESTIMATES and the report
# says so per column (`distinct_count_is_estimate`, `samples_method`) and once for the whole document.
DISTINCT_CAP = 200
LOW_CARDINALITY_MAX = 40
SAMPLE_CAP = 5
# Reservoir size for a capped column's samples. Bigger than SAMPLE_CAP so the reported samples are not just
# the first five values seen, and fixed so memory does not grow with the row count.
SAMPLE_RESERVOIR = 64
HLL_PRECISION = 12
# Bounded head sample for encoding detection. The whole document used to be read to decide utf-8 vs
# latin-1; a sample plus a per-line validity fallback gives the same answer without holding the file.
ENCODING_SAMPLE_BYTES = 262_144
# The largest NON-array JSON document this reader will hold in memory. An array streams element by
# element and JSONL streams line by line, so this is the one shape that cannot: locating the row array
# inside a single object means parsing the whole document, and `json.loads` needs the whole text first
# (roughly 3x the bytes once decoded). 64 MiB is far above any delivery whose rows could still fit, and
# a larger one is refused BY NAME with the limit, not by the allocator.
MAX_WHOLE_DOCUMENT_BYTES = 64 * 1024 * 1024
# The delimiter/header detection window: the first N non-blank lines, read from the bounded head sample.
DETECT_LINES = 80
# Candidate-free-text heuristic: a string column whose distinct ratio and mean length both look like prose.
FREE_TEXT_RATIO = 0.5
FREE_TEXT_MEAN_LENGTH = 40
DATE_NAME = re.compile(r"(aika|pvm|päivä|date|time|timestamp|kirjattu|recorded|created|updated)", re.IGNORECASE)
PERSON_NAME = re.compile(r"(henkilö|person|potilas|asiakas|hetu|ssn|patient)", re.IGNORECASE)
ENCOUNTER_NAME = re.compile(r"(käynti|encounter|tapahtuma|visit|episod|palvelutapahtuma|asiointi)", re.IGNORECASE)
CODE_NAME = re.compile(r"(koodi|code|icd|atc|loinc|snomed|nimike|kanta|classification)", re.IGNORECASE)
TYPE_NAME = re.compile(r"(tyyppi|type|laji|record_type|kirjaus)", re.IGNORECASE)
ID_NAME = re.compile(r"(^|[_\W])(id|tunniste|tunnus|avain|key)($|[_\W])", re.IGNORECASE)
# Finnish decimal comma: "72,5" is a number in a Finnish export, not a string. Read by the shape report so
# a decimal-comma column is profiled as numeric (and its min/max are numbers) rather than as free text.
_DECIMAL_COMMA = re.compile(r"^-?\d{1,3}(?: \d{3})*(?:,\d+)?$|^-?\d+,\d+$")
_FINNISH_OR_ISO_DATE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
    r"|^\d{1,2}\.\d{1,2}\.\d{4}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?$")
_DELIMITERS = (",", ";", "\t", "|")


class _RowCounter:
    """How many rows the profiler holds at once. The assertion behind "bounded memory, not a claim".

    A profiler that materialises its rows (or a per-column list of every value) drives this to the row
    count; the streaming implementation keeps it at one. `profile_delivery` resets it and reports the high
    water in the document, so the report itself states the bound it was produced under.
    """

    def __init__(self) -> None:
        self.in_flight = 0
        self.high_water = 0
        self.rows = 0

    def enter(self) -> None:
        self.in_flight += 1
        self.rows += 1
        if self.in_flight > self.high_water:
            self.high_water = self.in_flight

    def leave(self) -> None:
        self.in_flight -= 1

    def reset(self) -> None:
        self.in_flight = 0
        self.high_water = 0
        self.rows = 0


PROFILE_ROWS = _RowCounter()


class LandingError(ValueError):
    """A landing zone or a delivery cannot be read without guessing."""


# --- landing ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LandedFile:
    source_path: str
    path: str
    sha256: str
    bytes: int
    content_type: str
    received_at: str


@dataclass(frozen=True)
class LandingManifest:
    received_at: str
    files: tuple[LandedFile, ...]
    manifest_hash: str
    total_bytes: int
    manifest_path: Path


def _content_type(name: str, data: bytes) -> str:
    if data[:4] == b"PK\x03\x04":
        return "application/zip"
    if name.lower().endswith(".jsonl") or name.lower().endswith(".ndjson"):
        return "application/x-ndjson"
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def _iter_source_files(source: Path) -> list[Path]:
    if source.is_dir():
        return sorted(path for path in source.rglob("*") if path.is_file())
    if source.is_file():
        return [source]
    raise LandingError(f"no such delivery to land: {source}")


def land_delivery(source: Path, landing_dir: Path, *, received_at: datetime | None = None) -> LandingManifest:
    """Copy the delivery's bytes verbatim into a content-addressed landing zone plus a manifest.

    Nothing is decoded, sniffed or renamed here. A zip is landed as one file: the shape profiler opens it,
    which keeps "landing" a byte operation and makes re-profiling possible without the vendor re-sending.
    """
    source = Path(source)
    landing_dir = Path(landing_dir)
    received = received_at or datetime.now(timezone.utc)
    if received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    stamp = received.isoformat()
    files: list[LandedFile] = []
    for path in _iter_source_files(source):
        # Streamed in chunks rather than read whole. The delivery is expected to be ~258 MB, and reading a
        # file of that size into memory once per file is a peak the landing step has no reason to pay: the
        # hash needs every byte but never all of them at once, the sniffer looks at the first four, and the
        # copy is a copy. Measured before the change: +10 MB of peak for a 9.6 MB source.
        digest = hashlib.sha256()
        size = 0
        sample = b""
        with path.open("rb") as stream:
            while True:
                block = stream.read(_CHUNK_BYTES)
                if not block:
                    break
                digest.update(block)
                size += len(block)
                if len(sample) < _SNIFF_BYTES:
                    sample += block[: _SNIFF_BYTES - len(sample)]
        hexdigest = digest.hexdigest()
        relative = f"{LANDING_FILES}/{hexdigest[:2]}/{hexdigest}"
        destination = landing_dir / relative
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            with path.open("rb") as src, destination.open("wb") as dst:
                shutil.copyfileobj(src, dst, _CHUNK_BYTES)
        name = path.name if source.is_file() else str(path.relative_to(source))
        files.append(LandedFile(source_path=name, path=relative, sha256=hexdigest, bytes=size,
                                content_type=_content_type(name, sample), received_at=stamp))
    if not files:
        raise LandingError(f"the delivery at {source} has no files to land")
    files.sort(key=lambda entry: entry.source_path)
    canonical = json.dumps([asdict(entry) for entry in files], ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    manifest_hash = hashlib.sha256(canonical).hexdigest()
    manifest_path = landing_dir / MANIFEST_NAME
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({
        "kind": "delivery-run",
        "received_at": stamp,
        "manifest_hash": manifest_hash,
        "total_bytes": sum(entry.bytes for entry in files),
        "files": [asdict(entry) for entry in files],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return LandingManifest(received_at=stamp, files=tuple(files), manifest_hash=manifest_hash,
                           total_bytes=sum(entry.bytes for entry in files), manifest_path=manifest_path)


def read_manifest(landing_dir: Path) -> dict[str, Any]:
    path = Path(landing_dir) / MANIFEST_NAME
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LandingError(f"no landing manifest at {path}; run the land step first") from exc
    except json.JSONDecodeError as exc:
        raise LandingError(f"invalid landing manifest {path}: {exc}") from exc
    except RecursionError as exc:
        # Deeply nested JSON is a `RuntimeError`, not a `ValueError`; a hand-written manifest must be
        # refused with a LandingError like any other invalid one instead of killing the process.
        raise LandingError(f"invalid landing manifest {path}: too deeply nested ({exc})") from exc
    if document.get("kind") != "delivery-run" or not isinstance(document.get("files"), list):
        raise LandingError(f"{path} is not a delivery_run manifest")
    return document


# --- shape profiling --------------------------------------------------------------------------------


def _utf8_prefix_is_valid(data: bytes) -> bool:
    """Is this byte string valid UTF-8, allowing for a multi-byte character cut off at the very end?

    The profiler samples a bounded head, so its last bytes can be the first half of a character. Strictly
    decoding the sample would then report `latin-1` for a UTF-8 file and mojibake it, which is exactly the
    failure `detect_encoding` exists to prevent.
    """
    for trim in range(4):
        candidate = data[:len(data) - trim] if trim else data
        try:
            candidate.decode("utf-8")
            return True
        except UnicodeDecodeError as exc:
            if exc.end < len(candidate):
                return False  # a genuinely invalid byte, not a truncated tail
    return False


def detect_encoding(data: bytes) -> str:
    """UTF-8 / UTF-16 / Latin-1, decided by validity, most specific first."""
    if data.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    if _utf8_prefix_is_valid(data):
        return "utf-8"
    if data.count(b"\x00") > max(1, len(data) // 10):
        try:
            data.decode("utf-16")
            return "utf-16"
        except UnicodeDecodeError:
            pass
    return "latin-1"


def detect_delimiter(text: str) -> tuple[str | None, list[int]]:
    """The delimiter whose field count is most consistent across rows, ties broken by more columns.

    Decided by consistency, not by frequency: a Finnish export with decimal commas (`1,5`) must not be read
    as comma-separated just because commas are common.
    """
    lines = [line for line in text.splitlines()[:80] if line.strip()]
    if not lines:
        return None, []
    best: tuple[tuple[float, int], str, list[int]] | None = None
    for delimiter in _DELIMITERS:
        try:
            counts = [len(next(csv.reader([line], delimiter=delimiter))) for line in lines]
        except csv.Error:
            continue
        if not counts or max(counts) < 2:
            continue
        modal = statistics.mode(counts)
        consistency = counts.count(modal) / len(counts)
        score = (consistency, modal)
        if best is None or score > best[0]:
            best = (score, delimiter, counts)
    return (best[1], best[2]) if best is not None else (None, [])


def _as_number(value: Any) -> float | None:
    """A float for a genuine number, including Finnish ``72,5``; None when the value is not one."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "").replace("\u00a0", "")
    if _DECIMAL_COMMA.match(text):
        return float(text.replace(",", "."))
    try:
        return float(text)
    except ValueError:
        return None


def _classify(value: Any, name: str) -> str:
    if value is None or value == "":
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "date" if DATE_NAME.search(name) else "int"
    if isinstance(value, float):
        return "date" if DATE_NAME.search(name) else "float"
    if isinstance(value, (dict, list)):
        return "object" if isinstance(value, dict) else "array"
    text = str(value).strip()
    if _FINNISH_OR_ISO_DATE.match(text):
        try:
            parse_date(text)
            return "date"
        except ValueError:
            return "string"
    if _DECIMAL_COMMA.match(text):
        return "float"
    return "string"


def _value_key(value: Any) -> str:
    """The distinct-value key: JSON-canonical for containers, ``str`` otherwise (as before)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)


def _looks_like_data(value: str) -> bool:
    """A header cell is a name, not a value: numbers and dates mean the first row is already data."""
    text = value.strip()
    if not text:
        return True
    if re.fullmatch(r"-?\d+(?:[.,]\d+)?", text):
        return True
    return bool(_FINNISH_OR_ISO_DATE.match(text))


class _Hll:
    """HyperLogLog cardinality sketch, for a column whose exact set overflowed its cap.

    A capped set alone can only say "at least 200", which loses the two things the arrival report exists to
    tell an operator: how many distinct values the column really has, and whether it is one row per person.
    This is a bounded estimate (~1.6% standard error at precision 12) and every report that carries one says
    so rather than presenting it as a count.
    """

    __slots__ = ("precision", "registers")

    def __init__(self, precision: int = HLL_PRECISION) -> None:
        self.precision = precision
        self.registers = bytearray(1 << precision)

    def add(self, key: str) -> None:
        digest = hashlib.blake2b(key.encode("utf-8", "surrogatepass"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        index = value >> (64 - self.precision)
        width = 64 - self.precision
        tail = value & ((1 << width) - 1)
        rank = width + 1 if tail == 0 else width - tail.bit_length() + 1
        if rank > self.registers[index]:
            self.registers[index] = rank

    def estimate(self) -> int:
        m = 1 << self.precision
        alpha = 0.7213 / (1 + 1.079 / m)
        harmonic = sum(2.0 ** -register for register in self.registers)
        raw = alpha * m * m / harmonic
        zeros = self.registers.count(0)
        # Linear counting where the sketch is sparse; the bias correction only matters for large ranges.
        if raw <= 2.5 * m and zeros:
            return max(0, round(m * math.log(m / zeros)))
        return max(0, round(raw))


class _ColumnAccumulator:
    """One column's bounded profile: counts, a capped exact set, an HLL sketch and a reservoir.

    The previous profiler took a Python list of every value in the column — 700 000 rows times the column
    count, which is the ~1 GB peak this class removes. Every structure here is fixed-size (or bounded by
    `DISTINCT_CAP` / `SAMPLE_RESERVOIR`) regardless of how many rows arrive.
    """

    __slots__ = ("name", "types", "nulls", "distinct", "capped", "sketch", "reservoir",
                 "minimum", "maximum", "min_key", "max_key", "length_sum", "length_count")

    def __init__(self, name: str) -> None:
        self.name = name
        self.types: dict[str, int] = {}
        self.nulls = 0
        self.distinct: dict[str, int] = {}
        self.capped = False
        self.sketch = _Hll()
        self.reservoir: list[tuple[int, str]] = []  # max-heap of (-hash, value); bottom-k sample
        self.minimum: Any = None
        self.maximum: Any = None
        self.min_key: Any = None
        self.max_key: Any = None
        self.length_sum = 0
        self.length_count = 0

    def _sample(self, key: str) -> None:
        digest = hashlib.blake2b(key.encode("utf-8", "surrogatepass"), digest_size=8).digest()
        heapq.heappush(self.reservoir, (-int.from_bytes(digest, "big"), key))
        if len(self.reservoir) > SAMPLE_RESERVOIR:
            heapq.heappop(self.reservoir)

    def observe(self, value: Any) -> None:
        kind = _classify(value, self.name)
        self.types[kind] = self.types.get(kind, 0) + 1
        if kind == "null":
            self.nulls += 1
            return
        key = _value_key(value)
        if len(self.distinct) < DISTINCT_CAP or key in self.distinct:
            self.distinct[key] = self.distinct.get(key, 0) + 1
        else:
            self.capped = True
        self.sketch.add(key)
        if self.capped:
            self._sample(key)
        text = str(value)
        self.length_sum += len(text)
        self.length_count += 1
        if kind in ("int", "float", "date"):
            # Dates are compared as parsed instants but REPORTED in the vendor's own spelling, because the
            # spelling is what the operator writes a `date` rule against. A column that mixes epoch numbers
            # and ISO strings used to raise TypeError on the first comparison (`str < int`): comparing a
            # column's own min/max must not depend on the file being type-homogeneous.
            if kind == "date":
                try:
                    comparable, rendered = parse_date(value), value
                except (ValueError, OverflowError, OSError):
                    return
            else:
                comparable = _as_number(value)
                if comparable is None:
                    return
                rendered = comparable
            if self.min_key is None or comparable < self.min_key:
                self.min_key, self.minimum = comparable, rendered
            if self.max_key is None or comparable > self.max_key:
                self.max_key, self.maximum = comparable, rendered

    def profile(self, rows_profiled: int) -> dict[str, Any]:
        inferred = "null"
        non_null = {kind: count for kind, count in self.types.items() if kind != "null"}
        if non_null:
            inferred = max(non_null, key=lambda kind: non_null[kind])
        estimate = self.sketch.estimate() if self.capped else len(self.distinct)
        if self.capped:
            ordered = sorted(self.reservoir, key=lambda item: -item[0])
            samples = [key for _hash, key in ordered[:SAMPLE_CAP]]
        else:
            ordered = sorted(self.distinct.items(), key=lambda item: (-item[1], item[0]))
            samples = [key for key, _count in ordered[:SAMPLE_CAP]]
        profile: dict[str, Any] = {
            "name": self.name,
            "inferred_type": inferred,
            "types_seen": self.types,
            "null_rate": round(self.nulls / rows_profiled, 4) if rows_profiled else 0.0,
            "distinct_count": f"~{estimate}" if self.capped else estimate,
            "distinct_count_value": estimate,
            "distinct_count_is_estimate": self.capped,
            "distinct_lower_bound": DISTINCT_CAP if self.capped else None,
            "distinct_capped": self.capped,
            "distinct_method": "exact" if not self.capped else
                               f"capped-set(<{DISTINCT_CAP})+hyperloglog(p={HLL_PRECISION})",
            "distinct_ratio": round(estimate / rows_profiled, 4) if rows_profiled else 0.0,
            "distinct_ratio_is_estimate": self.capped,
            "mean_length": round(self.length_sum / self.length_count, 2) if self.length_count else 0.0,
            "min": json.dumps(self.minimum, ensure_ascii=False) if self.minimum is not None else None,
            "max": json.dumps(self.maximum, ensure_ascii=False) if self.maximum is not None else None,
            "samples": samples,
            "samples_method": "exact-most-frequent" if not self.capped else "deterministic-reservoir",
        }
        if inferred == "string" and not self.capped and 0 < len(self.distinct) <= LOW_CARDINALITY_MAX:
            profile["candidate_vocabulary"] = [
                {"value": key, "count": count} for key, count in ordered[:LOW_CARDINALITY_MAX]]
        return profile


def _candidate_role(name: str, profile: dict[str, Any]) -> list[str]:
    roles: list[str] = []
    inferred = profile["inferred_type"]
    distinct = profile["distinct_count_value"]
    ratio = profile["distinct_ratio"]
    if inferred in ("int", "float") or DATE_NAME.search(name):
        if inferred == "date":
            roles.append("timestamp")
    elif inferred == "string":
        if DATE_NAME.search(name):
            roles.append("timestamp")
        if PERSON_NAME.search(name) and (ratio >= 0.5 or ID_NAME.search(name)):
            roles.append("person_identifier")
        if ENCOUNTER_NAME.search(name):
            roles.append("encounter_identifier")
        if CODE_NAME.search(name):
            roles.append("code")
        if TYPE_NAME.search(name) and isinstance(distinct, int) and distinct <= LOW_CARDINALITY_MAX:
            roles.append("record_type")
        if ratio >= FREE_TEXT_RATIO and profile["mean_length"] >= FREE_TEXT_MEAN_LENGTH:
            roles.append("free_text_note")
        elif profile["mean_length"] >= FREE_TEXT_MEAN_LENGTH and not ID_NAME.search(name) \
                and not DATE_NAME.search(name):
            # Long strings are prose even when they repeat: a note column can carry the same standard
            # sentence many times and a distinct-ratio test alone would call it a category.
            roles.append("free_text_note")
    if ID_NAME.search(name) and inferred in ("string", "int") and ratio >= 0.5:
        roles.append("identifier")
    if PERSON_NAME.search(name) and "person_identifier" not in roles and ratio >= 0.5:
        roles.append("person_identifier")
    if ENCOUNTER_NAME.search(name) and "encounter_identifier" not in roles:
        roles.append("encounter_identifier")
    return roles


def _profile_stream(rows: Iterable[tuple[int, dict | None, str | None]], columns: list[str],
                    counter: _RowCounter | None = None) -> dict[str, Any]:
    """Profile one file's rows as they arrive, holding one row at a time and no per-row value list.

    `columns` is the CSV header when there is one; a JSON row contributes its keys as they are seen, so a
    file whose objects carry different keys is profiled rather than guessed from a sample of one.
    """
    accumulators: dict[str, _ColumnAccumulator] = {}
    order: list[str] = list(columns)
    rows_total = 0
    rows_profiled = 0
    rows_wrong_width = 0
    for _number, row, error in rows:
        rows_total += 1
        if error is not None or row is None:
            rows_wrong_width += 1
            continue
        if counter is not None:
            counter.enter()
        try:
            for name, value in row.items():
                accumulator = accumulators.get(name)
                if accumulator is None:
                    accumulator = accumulators[name] = _ColumnAccumulator(name)
                    if name not in order:
                        order.append(name)
                accumulator.observe(value)
            rows_profiled += 1
        finally:
            if counter is not None:
                counter.leave()
    profiles = []
    for name in order:
        accumulator = accumulators.get(name)
        if accumulator is None:  # a declared CSV column no row carried
            accumulator = _ColumnAccumulator(name)
        column = accumulator.profile(rows_profiled)
        column["candidate_roles"] = _candidate_role(name, column)
        profiles.append(column)
    person_ids = [column["name"] for column in profiles if "person_identifier" in column["candidate_roles"]]
    encounter_ids = [column["name"] for column in profiles if "encounter_identifier" in column["candidate_roles"]]
    layout = "unknown"
    if person_ids:
        primary = next(column for column in profiles if column["name"] == person_ids[0])
        # An estimated distinct count carries ~1.6% error, so "one row per person" is a near-equality rather
        # than an equality; a column with one row per person must not read as "repeated rows" on rounding.
        if rows_profiled and primary["distinct_count_value"] >= rows_profiled * 0.97:
            layout = "wide (one row per person)"
        elif encounter_ids:
            layout = "long (many rows per person)"
        else:
            layout = "wide or repeated rows per person"
    return {
        "rows_total": rows_total,
        "rows_profiled": rows_profiled,
        "rows_wrong_field_count": rows_wrong_width,
        "columns": profiles,
        "candidate_person_identifiers": person_ids,
        "candidate_encounter_identifiers": encounter_ids,
        "layout": layout,
    }


def _rows_from_json(document: Any) -> tuple[list[dict], dict[str, Any]]:
    if isinstance(document, list):
        rows = [row for row in document if isinstance(row, dict)]
        return rows, {"nested": any(isinstance(value, (dict, list)) for row in rows for value in row.values()),
                      "container": None, "top_level_keys": []}
    if isinstance(document, dict):
        containers = {key: value for key, value in document.items()
                      if isinstance(value, list) and value and isinstance(value[0], dict)}
        if containers:
            key = max(containers, key=lambda name: len(containers[name]))
            return containers[key], {"nested": True, "container": key, "top_level_keys": sorted(document)}
        return [document], {"nested": any(isinstance(value, (dict, list)) for value in document.values()),
                            "container": None, "top_level_keys": sorted(document)}
    return [], {"nested": False, "container": None, "top_level_keys": []}


@dataclass(frozen=True)
class RawFile:
    """What was detected about one delivery file, before any row is read.

    `format` uses the shape report's own vocabulary: ``csv``, ``json`` (one document, streamed when it is an
    array), ``jsonl`` (one JSON object per line), ``unparsed`` or ``binary``. An ``unparsed``/``binary`` file
    carries a `reason`, because "could not read" is a layout the mapping cannot express and must say so.
    """

    source_path: str
    format: str
    encoding: str
    delimiter: str | None = None
    delimiter_candidates: tuple[int, ...] = ()
    header_present: bool | None = None
    columns: tuple[str, ...] = ()
    nested: bool = False
    container: str | None = None
    top_level_keys: tuple[str, ...] = ()
    bytes: int = 0
    reason: str | None = None


def _is_zip(path: Path) -> bool:
    try:
        with Path(path).open("rb") as stream:
            return stream.read(4) == b"PK\x03\x04"
    except OSError:
        return False


def _expand_zip(name: str, path: Path) -> Iterator[tuple[str, Path, str | None]]:
    """Open a zip into its members; a non-zip is itself."""
    if not _is_zip(path):
        yield name, path, None
        return
    try:
        with zipfile.ZipFile(path) as archive:
            members = sorted(member for member in archive.namelist() if not member.endswith("/"))
    except zipfile.BadZipFile:
        yield name, path, None  # `_describe_file` reports the unreadable archive by name
        return
    if not members:
        yield name, path, None
        return
    for member in members:
        yield f"{name}!{member}", path, member


def _iter_delivery_files(source: Path) -> Iterator[tuple[str, Path, str | None]]:
    """(locator, archive-or-file path, zip member) for every file, read from the landing manifest if there is one.

    A landed zip is unpacked to its members, so the operator profiles the same file names the vendor sent
    rather than one opaque blob.
    """
    source = Path(source)
    if source.is_dir():
        if (source / MANIFEST_NAME).is_file():
            for entry in read_manifest(source)["files"]:
                yield from _expand_zip(entry["source_path"], source / entry["path"])
            return
        found = False
        for candidate in sorted(path for path in source.rglob("*") if path.is_file()):
            name = str(candidate.relative_to(source))
            if name == MANIFEST_NAME or name.startswith("results/"):
                continue
            found = True
            yield from _expand_zip(name, candidate)
        if not found:
            raise LandingError(f"{source} holds no delivery files")
        return
    if source.is_file():
        yield from _expand_zip(source.name, source)
        return
    raise LandingError(f"no such delivery to read: {source}")


def _member_stream(path: Path, member: str | None) -> tuple[io.BufferedReader, Any]:
    """A seekable binary stream plus whatever owns it (a `ZipFile` for a member, else None)."""
    if member is None:
        return io.BufferedReader(Path(path).open("rb")), None
    archive = zipfile.ZipFile(path)
    return io.BufferedReader(archive.open(member)), archive


def _decode_line(raw: bytes, encoding: str) -> str:
    try:
        return raw.decode(encoding)
    except UnicodeDecodeError:
        if encoding == "latin-1":
            raise
        # The validity-first rule `detect_encoding` applies to a whole file, applied per line: a head sample
        # whose first 256 KB happen to be ASCII cannot mojibake a Latin-1 `ä` further down the file.
        return raw.decode("latin-1")


def _iter_text_lines(stream: io.BufferedReader, encoding: str) -> Iterator[str]:
    if encoding == "utf-16":
        wrapper = io.TextIOWrapper(stream, encoding="utf-16", newline="")
        try:
            yield from wrapper
        finally:
            wrapper.detach()  # the caller owns `stream`; do not let the wrapper close it
        return
    for raw in stream:
        yield _decode_line(raw, encoding)


def _first_physical_line(stream: io.BufferedReader, encoding: str) -> str:
    stream.seek(0)
    for line in _iter_text_lines(stream, encoding):
        return line
    return ""


def _peek_first_char(stream: io.BufferedReader, encoding: str) -> str:
    stream.seek(0)
    sample = stream.read(4096)
    stream.seek(0)
    return sample.decode(encoding, errors="replace").lstrip()[:1]


def _json_structure_from_sample(sample_text: str) -> dict[str, Any]:
    """Structural facts for a JSON document, from the head sample alone when it is whole.

    A single array streams, so the whole document is never parsed here: when the sample does not parse, the
    first container key visible in it is reported (or none) rather than loading the document twice.
    """
    try:
        document = json.loads(sample_text)
    except (ValueError, RecursionError):
        for match in re.finditer(r'"((?:[^"\\]|\\.){1,128})"\s*:\s*\[', sample_text):
            key = json.loads(f'"{match.group(1)}"')
            return {"nested": True, "container": key, "top_level_keys": ()}
        return {"nested": False, "container": None, "top_level_keys": ()}
    _rows, structural = _rows_from_json(document)
    return {"nested": structural["nested"], "container": structural["container"],
            "top_level_keys": tuple(structural["top_level_keys"])}


def _describe_file(path: Path, member: str | None, source_path: str) -> RawFile:
    """Detect one file's layout from a bounded head sample, then re-open it per pass."""
    stream, owner = _member_stream(path, member)
    try:
        stream.seek(0, io.SEEK_END)
        size = stream.tell()
        stream.seek(0)
        sample = stream.read(ENCODING_SAMPLE_BYTES)
        if sample[:4] == b"PK\x03\x04":
            return RawFile(source_path=source_path, format="unparsed", encoding="utf-8", bytes=size,
                           reason="a zip nested inside a zip; unpack and land the inner archive itself")
        encoding = detect_encoding(sample)
        base = {"source_path": source_path, "encoding": encoding, "bytes": size}
        if b"\x00" in sample[:1024] and encoding != "utf-16":
            return RawFile(format="binary", **base)
        try:
            text = sample.decode(encoding)
        except UnicodeDecodeError:
            return RawFile(format="binary", **base)
        stripped = text.lstrip()
        if stripped[:1] == "[":
            return RawFile(format="json", **base, **_json_structure_from_sample(text))
        if stripped[:1] == "{":
            if _is_jsonl_first_line(_first_physical_line(stream, encoding)):
                return RawFile(format="jsonl", **base)
            return RawFile(format="json", **base, **_json_structure_from_sample(text))
        delimiter, counts = detect_delimiter(text)
        if delimiter is None:
            return RawFile(format="unparsed", **base,
                           reason="no consistent delimiter and not JSON: the mapping cannot address rows")
        parsed = list(csv.reader([line for line in text.splitlines()[:DETECT_LINES] if line.strip()],
                                 delimiter=delimiter))
        columns: tuple[str, ...] = ()
        header_present = False
        if parsed:
            first = parsed[0]
            header_present = bool(all(value.strip() and not _looks_like_data(value) for value in first)
                                  and len(set(first)) == len(first) and len(parsed) > 1)
            columns = tuple(first) if header_present else tuple(
                f"column_{index + 1}" for index in range(len(first)))
        return RawFile(format="csv", **base, delimiter=delimiter, delimiter_candidates=tuple(counts),
                       header_present=header_present, columns=columns)
    finally:
        stream.close()
        if owner is not None:
            owner.close()


def _is_jsonl_first_line(line: str) -> bool:
    """Is the first physical line one complete JSON object that is a ROW rather than a container?

    A single object holding a list of objects (`{"events": [...]}`) is one JSON document whose rows are that
    list — `_rows_from_json`'s own rule — so it is not JSONL even when the whole document fits on one line.
    """
    try:
        parsed = json.loads(line)
    except (ValueError, RecursionError):
        # A deeply nested first line is not a JSONL row, which is the answer the caller needs; letting
        # `RecursionError` through would kill the sniff before `_iter_jsonl_rows` could refuse the row.
        return False
    if not isinstance(parsed, dict):
        return False
    containers = [value for value in parsed.values()
                  if isinstance(value, list) and value and isinstance(value[0], dict)]
    return not containers


def _iter_csv_rows(stream: io.BufferedReader, raw: RawFile,
                   locator: str) -> Iterator[tuple[int, dict | None, str | None]]:
    stream.seek(0)
    reader = csv.reader(_iter_text_lines(stream, raw.encoding), delimiter=raw.delimiter or ",")
    columns = list(raw.columns)
    if raw.header_present:
        next(reader, None)
    for number, fields in enumerate(reader, start=1):
        if len(fields) != len(columns):
            yield number, None, (f"{locator} row {number} has {len(fields)} field(s) where the header has "
                                 f"{len(columns)}")
            continue
        yield number, dict(zip(columns, fields)), None


def _iter_jsonl_rows(stream: io.BufferedReader, raw: RawFile,
                     locator: str) -> Iterator[tuple[int, dict | None, str | None]]:
    stream.seek(0)
    for number, line in enumerate(_iter_text_lines(stream, raw.encoding), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (ValueError, RecursionError) as exc:
            yield number, None, f"{locator} line {number} is not JSON ({type(exc).__name__})"
            continue
        if not isinstance(value, dict):
            yield number, None, f"{locator} line {number} is not a JSON object"
            continue
        yield number, value, None


def _iter_text_chunks(stream: io.BufferedReader, encoding: str,
                      size: int = 65536) -> Iterator[str]:
    """Text chunks for a streamed JSON array, decoded across chunk boundaries by an incremental decoder."""
    stream.seek(0)
    if encoding == "utf-16":
        wrapper = io.TextIOWrapper(stream, encoding="utf-16")
        try:
            while True:
                chunk = wrapper.read(size)
                if not chunk:
                    return
                yield chunk
        finally:
            wrapper.detach()
        return
    decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
    while True:
        block = stream.read(size)
        if not block:
            break
        try:
            text = decoder.decode(block)
        except UnicodeDecodeError as exc:
            raise LandingError(f"a JSON array that does not decode as {encoding}: {exc}") from exc
        if text:
            yield text
    tail = decoder.decode(b"", final=True)
    if tail:
        yield tail


def _iter_json_array_items(chunks: Iterable[str]) -> Iterator[Any]:
    """Stream a top-level JSON array element by element, holding one element and a small text buffer.

    A 700 000-row delivery is the shape this exists for: `json.load` on the whole array would be another
    several-hundred-megabyte peak, which is what "stream the profiler" is about.
    """
    decoder = json.JSONDecoder()
    buffer = ""
    started = False
    closed = False
    for chunk in chunks:
        buffer += chunk
        while True:
            stripped = buffer.lstrip()
            if not started:
                if not stripped:
                    buffer = ""
                    break
                if stripped[0] != "[":
                    raise LandingError("a JSON document that is not an array, an object or JSONL")
                buffer = stripped[1:]
                started = True
                continue
            if not stripped:
                buffer = ""
                break
            if stripped[0] == "]":
                closed = True
                return
            if stripped[0] == ",":
                buffer = stripped[1:]
                continue
            try:
                value, end = decoder.raw_decode(stripped)
            except ValueError:
                break  # an incomplete element: read more of the array
            except RecursionError as exc:
                # A deeply nested array ELEMENT is not an incomplete element; looping for more text would
                # re-raise on every chunk and end the import with a traceback. Refuse it by name instead,
                # which is the same posture every other malformed row takes.
                raise LandingError(
                    f"a JSON array element is too deeply nested to parse ({type(exc).__name__})") from exc
            yield value
            buffer = stripped[end:]
    if not closed:
        raise LandingError("a JSON array that ends before its closing bracket")


def _iter_json_rows(stream: io.BufferedReader, raw: RawFile,
                    locator: str) -> Iterator[tuple[int, dict | None, str | None]]:
    if _peek_first_char(stream, raw.encoding) == "[":
        for index, value in enumerate(_iter_json_array_items(_iter_text_chunks(stream, raw.encoding)),
                                      start=1):
            if not isinstance(value, dict):
                yield index, None, f"{locator} array element {index} is not a JSON object"
                continue
            yield index, value, None
        return
    stream.seek(0)
    stream.seek(0, io.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    if size > MAX_WHOLE_DOCUMENT_BYTES:
        # A single non-array JSON object is the one shape this reader cannot stream: finding the row array
        # means holding the document. Refusing it BY NAME, with the limit and the alternative, is honest
        # about the shape; the old code read the whole thing into RAM (a 258 MB export would be ~3x that
        # once decoded) and reported the OOM as whatever the allocator said.
        raise LandingError(
            f"{locator} is {size} bytes; a JSON document that is not an array is read whole, so it is "
            f"refused above {MAX_WHOLE_DOCUMENT_BYTES} bytes. Split it into JSONL or a top-level array, "
            f"which stream element by element")
    try:
        document = json.loads(stream.read().decode(raw.encoding))
    except RecursionError as exc:
        raise LandingError(
            f"{locator} is too deeply nested to parse ({type(exc).__name__})") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise LandingError(f"{locator} is not readable JSON: {exc}") from exc
    rows, _structural = _rows_from_json(document)
    if not rows:
        raise LandingError(f"{locator} is a JSON document with no object rows to map")
    for index, row in enumerate(rows, start=1):
        yield index, row, None


def _iter_file_rows(path: Path, member: str | None, raw: RawFile,
                    locator: str) -> Iterator[tuple[int, dict | None, str | None]]:
    if raw.format in ("unparsed", "binary"):
        return
    stream, owner = _member_stream(path, member)
    try:
        if raw.format == "csv":
            yield from _iter_csv_rows(stream, raw, locator)
        elif raw.format == "jsonl":
            yield from _iter_jsonl_rows(stream, raw, locator)
        elif raw.format == "json":
            yield from _iter_json_rows(stream, raw, locator)
    finally:
        stream.close()
        if owner is not None:
            owner.close()


def iter_raw_files(source: Path, *, skip: Callable[[str], bool] | None
                   = None) -> Iterator[tuple[RawFile, Iterator[tuple[int, dict | None, str | None]]]]:
    """Every delivery file with its detected shape and a lazy row iterator.

    Accepts a landing directory (via its manifest), a raw directory, a single file, a zip, nested
    directories, and one file per person. Nothing is materialised: a file is opened to describe it and
    re-opened to iterate it, so peak memory is one row plus the bounded profiler structures.
    """
    for locator, path, member in _iter_delivery_files(Path(source)):
        if skip is not None and skip(locator):
            continue
        raw = _describe_file(path, member, locator)
        yield raw, _iter_file_rows(path, member, raw, locator)


def iter_located_rows(source: Path, *, skip: Callable[[str], bool] | None
                      = None) -> Iterator[tuple[str, dict | None, str | None]]:
    """Flat `(locator, row, error)` stream over a whole delivery, for the mapping importer.

    A file whose LAYOUT cannot be read at all (no delimiter, binary, a nested zip) and a file that yields
    no rows are both hard failures that name the file and its format: silently importing nothing from a
    700 000-row delivery is the failure this whole path exists to prevent. A malformed ROW is data, so the
    strict importer can refuse it and the quarantine importer can stage it.
    """
    for raw, rows in iter_raw_files(source, skip=skip):
        if raw.format in ("unparsed", "binary"):
            raise LandingError(
                f"{raw.source_path} has layout [{raw.format}], which a row mapping cannot express: "
                f"{raw.reason or 'binary content'}")
        produced = False
        for number, row, error in rows:
            produced = True
            yield f"{raw.source_path}:{number}", row, error
        if not produced:
            raise LandingError(
                f"{raw.source_path} has layout [{raw.format}] and produced no rows: an empty file, or a "
                f"shape the mapping cannot express; list it in the mapping's ignore_source_files if it is "
                f"not a records source")


def read_rows(source: Path, limit: int | None = None) -> tuple[list[dict], dict[str, Any]]:
    """Read a raw or landed file (or a directory holding one) into rows, using the profiler's detection.

    Shared by the mapping dry run so a mapping revision is checked against the SAME reading the shape
    report described, not a second parser that can disagree with it. This one MATERIALISES the rows it
    returns, which is why it is the dry-run reader and not the importer.
    """
    source = Path(source)
    if source.is_dir() and not (source / MANIFEST_NAME).is_file():
        chosen = next((path for path in (source / "records.jsonl", source / "records.csv") if path.is_file()),
                      None)
        if chosen is None:
            files = sorted(path for path in source.rglob("*") if path.is_file())
            if len(files) != 1:
                raise LandingError(f"{source} holds {len(files)} files; pass one records file")
            chosen = files[0]
        source = chosen
    if not source.exists():
        raise LandingError(f"no such records file: {source}")
    rows: list[dict] = []
    meta: dict[str, Any] = {}
    for raw, iterator in iter_raw_files(source):
        meta = {"format": raw.format, "encoding": raw.encoding, "delimiter": raw.delimiter,
                "delimiter_candidates": list(raw.delimiter_candidates),
                "header_present": raw.header_present, "columns": list(raw.columns),
                "nested": raw.nested, "container": raw.container,
                "top_level_keys": list(raw.top_level_keys), "bytes": raw.bytes, "path": raw.source_path}
        if raw.reason:
            meta["reason"] = raw.reason
        if raw.format in ("unparsed", "binary"):
            return [], meta
        for _number, row, error in iterator:
            if error is not None:
                raise LandingError(error)
            if row is not None:
                rows.append(row)
    if limit is not None:
        rows = rows[:limit]
    meta["rows_total"] = len(rows)
    return rows, meta


def _shared_keys(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keyed: list[tuple[str, set[str]]] = []
    for entry in files:
        columns = {column["name"] for column in entry.get("columns", [])}
        if columns:
            keyed.append((entry["source_path"], columns))
    shared = []
    for index, (left_name, left) in enumerate(keyed):
        for right_name, right in keyed[index + 1:]:
            common = sorted(left & right)
            if common:
                shared.append({"files": [left_name, right_name], "keys": common})
    return shared


def profile_delivery(landing_dir: Path, output_dir: Path | None = None) -> dict[str, Any]:
    """Read the landing zone and emit the machine-readable shape report and a human summary.

    Never maps and never imports. The report's purpose is to make the vendor's vocabulary visible before
    anyone writes a mapping rule. It STREAMS: one file is open at a time, one row is in hand at a time, and
    the per-column structures are fixed-size, so a 700 000-row JSONL costs a bounded amount of memory. The
    document records the bound it was produced under (`sampling`) and flags every estimated value.
    """
    landing_dir = Path(landing_dir)
    manifest = read_manifest(landing_dir)
    landed = {entry["source_path"]: entry for entry in manifest["files"]}
    PROFILE_ROWS.reset()
    files: list[dict[str, Any]] = []
    for raw, rows in iter_raw_files(landing_dir):
        entry: dict[str, Any] = {
            "source_path": raw.source_path, "bytes": raw.bytes, "encoding": raw.encoding,
            "format": raw.format, "delimiter": raw.delimiter,
            "delimiter_candidates": list(raw.delimiter_candidates),
            "header_present": raw.header_present, "nested": raw.nested, "container": raw.container,
            "top_level_keys": list(raw.top_level_keys)}
        outer = landed.get(raw.source_path.split("!", 1)[0])
        if outer is not None:
            entry["sha256"] = outer["sha256"]
            entry["content_type"] = outer["content_type"]
        if raw.reason:
            entry["reason"] = raw.reason
        if raw.format not in ("unparsed", "binary"):
            entry.update(_profile_stream(rows, list(raw.columns), PROFILE_ROWS))
        files.append(entry)
    document = {
        "kind": "delivery-shape-report",
        "landing_dir": str(landing_dir),
        "manifest_hash": manifest.get("manifest_hash"),
        "received_at": manifest.get("received_at"),
        "total_bytes": manifest.get("total_bytes"),
        "file_count": len(files),
        "files": files,
        "sampling": {
            "memory": "bounded: rows are streamed, never listed",
            "rows_held_at_once_high_water": PROFILE_ROWS.high_water,
            "rows_profiled_total": PROFILE_ROWS.rows,
            "distinct_exact_limit": DISTINCT_CAP,
            "distinct_beyond_limit": f"hyperloglog estimate (p={HLL_PRECISION}, ~1.6% standard error); "
                                     f"flagged per column as distinct_count_is_estimate",
            "samples_beyond_limit": "deterministic reservoir sample (bottom-k by blake2b hash); "
                                    "flagged per column as samples_method",
        },
        "shared_keys": _shared_keys(files),
        "candidate_vocabularies": {
            entry["source_path"]: {column["name"]: column["candidate_vocabulary"]
                                   for column in entry.get("columns", [])
                                   if column.get("candidate_vocabulary")}
            for entry in files if any(column.get("candidate_vocabulary")
                                      for column in entry.get("columns", []))
        },
        "candidate_roles": {
            entry["source_path"]: {column["name"]: column["candidate_roles"]
                                   for column in entry.get("columns", [])
                                   if column.get("candidate_roles")}
            for entry in files if any(column.get("candidate_roles") for column in entry.get("columns", []))
        },
    }
    if output_dir is not None:
        results = Path(output_dir) / "results"
        results.mkdir(parents=True, exist_ok=True)
        (results / "delivery-shape-report.json").write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (results / "delivery-shape-summary.txt").write_text(
            render_shape_summary(document), encoding="utf-8")
    return document


def render_shape_summary(document: dict[str, Any]) -> str:
    """The human half: what a person reads first on arrival day."""
    sampling = document.get("sampling", {})
    lines = [f"delivery shape report -- {document['file_count']} content file(s), "
             f"{document.get('total_bytes')} bytes, manifest {document.get('manifest_hash')}",
             f"shared keys across files: "
             f"{[entry['keys'] for entry in document.get('shared_keys', [])] or 'none'}",
             f"sampling: {sampling.get('memory', 'unknown')}; "
             f"distinct exact up to {sampling.get('distinct_exact_limit')} values, beyond that "
             f"{sampling.get('distinct_beyond_limit')}; samples beyond that from a "
             f"{sampling.get('samples_beyond_limit')}; rows held at once: "
             f"{sampling.get('rows_held_at_once_high_water')}", ""]
    for entry in document["files"]:
        lines.append(f"== {entry['source_path']}  [{entry.get('format')}]")
        if entry.get("reason"):
            lines.append(f"   unread: {entry['reason']}")
        lines.append(f"   encoding={entry.get('encoding')} delimiter={entry.get('delimiter')!r} "
                     f"header={entry.get('header_present')} rows={entry.get('rows_total')}"
                     f"{' container=' + str(entry['container']) if entry.get('container') else ''}"
                     f" nested={entry.get('nested')}")
        lines.append(f"   layout: {entry.get('layout')}")
        for column in entry.get("columns", []):
            roles = ",".join(column["candidate_roles"]) or "-"
            estimate = " (estimate)" if column.get("distinct_count_is_estimate") else ""
            lines.append(f"     {column['name']:28s} {column['inferred_type']:7s} "
                         f"null={column['null_rate']:<6} distinct={column['distinct_count']!s:<6}{estimate} "
                         f"roles={roles}")
            if column.get("candidate_vocabulary"):
                values = ", ".join(str(item["value"]) for item in column["candidate_vocabulary"][:12])
                lines.append(f"       vocabulary: {values}")
        lines.append("")
    return "\n".join(lines) + "\n"
