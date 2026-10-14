"""Mechanical checks on what a mapping PRODUCED, so a wrong pairing is visible before it is imported.

The failure this module exists beside, in the author's own words: *"a plausible-but-wrong mapping that
matches every rule imports cleanly and semantically wrong -- nothing in the path can detect a real column
mapped to the wrong meaning."* That is true, and it is the reason the real defence is a human reading the
field-by-field preview (`app/preview.py`). These checks cannot prove a meaning. They can only catch the
impossibilities a wrong meaning usually leaves behind:

* a timestamp that cannot be when the record was authored (before 1900, or in the future);
* a value that dominates a field which must vary (one `record_id` for the whole corpus);
* `source_person_id` with one distinct value per record -- an id that is unique per ROW is a record id,
  not a person id, and the import would happily create one person per record;
* a required envelope field no row supplies (the vendor's cell is empty, or the rule points at a column
  that is empty);
* a free-text field whose values are codes, or a code/vocabulary field whose values are prose;
* a numeric field still carrying the vendor's Finnish spelling (`72,5` as a string) because no
  `decimal_fi` rule was wired to it.

**Every finding names what it saw and says it is a heuristic.** A check that only fails is useless at
06:00; a check that claims to be proof is worse, because it invites trust it has not earned. The report
carries that statement once for the whole document as well as per finding.

Memory: rows stream through `observe`. The only structure that grows with the corpus is the exact set of
`source_person_id` values, and it grows with the number of PEOPLE (which the importer indexes anyway), not
with the number of records. Every other tracker is capped, and says so when capped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .mapping import MappingError, parse_date

# The document-level statement. It is deliberately in the artifact and in the rendered preview, because a
# reader who skims the findings list must still not mistake it for a proof of correctness.
HEURISTIC_NOTE = (
    "these checks are HEURISTICS over the mapped values, not a proof that a column carries the meaning its "
    "target field names; only a human reading the field-by-field preview can judge a meaning"
)

REFUSAL = "refusal"
WARNING = "warning"

# A record authored before this is a mapping accident, not a record: no Finnish patient record predates
# 1900, and a value that does is almost always an id, a version or a mis-parsed number in a timestamp slot.
PLAUSIBLE_EARLIEST = datetime(1900, 1, 1, tzinfo=timezone.utc)
# Clock skew and the delivery's own timezone must not make a same-day record "future-dated".
FUTURE_TOLERANCE = timedelta(days=1)
# A share of rows this high, on a field that must vary, is a wrong column rather than a run of bad rows.
DOMINANCE_SHARE = 0.99
# Below this many rows a constant field is a small file, not a corpus: no check fires.
DOMINANCE_MIN_ROWS = 50
# How the checks decide a value "looks like" one thing or another. Both are shape heuristics with no
# vocabulary in them, which is the point: the vendor's vocabulary is unknown.
MIN_PROSE_LENGTH = 20
MAX_CODE_LENGTH = 16
HEURISTIC_SHARE = 0.5
# Fields that must vary row to row (an id), or record to record (a timestamp).
VARYING_FIELDS = ("record_id", "source_person_id", "recorded_at")
# Envelope fields carrying a CODE from a declared vocabulary, and payload fields that are prose. A value
# that looks like prose in one of the former, or a code in one of the latter, is the visible signature of
# two columns swapped.
VOCABULARY_FIELDS = ("record_type", "topic", "sensitivity_tag", "consent_scope", "authored_by_role")
FREE_TEXT_FIELDS = ("payload.teksti", "payload.otsikko", "payload.yhteenveto", "payload.tulkinta",
                    "payload.nimi", "payload.käyntityyppi")
CODE_FIELDS = ("payload.icd10", "payload.atc", "payload.koodi", "payload.yksikkö", "payload.instrumentti")
# Fields the product plots or sums. A STRING here is the "no numeric transform" hole the arrival runbook
# used to record: `72,5` is a measurement the product cannot use.
NUMERIC_FIELDS = ("payload.paino_kg", "payload.pituus_cm", "payload.syke", "payload.arvo",
                  "payload.systolinen", "payload.diastolinen", "payload.syntymävuosi",
                  "payload.puuttuneet_annokset_30d")
_NUMERIC_SUFFIX = re.compile(r"_(kg|cm|mmhg|mmol_l|bpm|lkm|kpl|ml|dl|mg)$")
# A Finnish or anglo number as a STRING: exactly what `decimal_fi` exists to turn into a real number.
_NUMERIC_TEXT = re.compile(
    r"^[+-]?\d{1,3}(?:[ \u00a0\u202f\u2009]\d{3})*(?:[.,]\d+)?$|^[+-]?\d+[.,]\d+$")
_CODE_SHAPED = re.compile(
    r"^[A-Z]{1,4}[-_]?\d{1,6}(?:[.,]\d+)?$|^[A-Z]\d{2}(?:\.\d+)?$|^\d+(?:[.,]\d+)?$"
    r"|^[a-z]{1,5}[-_]\d{1,6}$")
_TRACKER_CAP = 512


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else str(value).strip()


def _looks_like_prose(value: Any) -> bool:
    """Long, spaced, non-numeric: prose rather than a code or a date. A heuristic, nothing more."""
    text = _text(value)
    if len(text) < MIN_PROSE_LENGTH or " " not in text:
        return False
    if _NUMERIC_TEXT.match(text):
        return False
    try:
        parse_date(text)
    except MappingError:
        return True
    return False


def _looks_like_code(value: Any) -> bool:
    """Short, unspaced, digit-bearing or ALL-CAPS: a code rather than a sentence. A heuristic, nothing more."""
    text = _text(value)
    if not text or len(text) > MAX_CODE_LENGTH or " " in text:
        return False
    return bool(_CODE_SHAPED.match(text))


@dataclass
class _Tracker:
    """Exact counts for the first `cap` distinct values, then a floor. Bounded on purpose.

    A field that genuinely varies passes the cap and stops being checked for dominance -- which is correct,
    because a field with hundreds of distinct values cannot be dominated by one of them. When the cap is
    reached the report says `distinct_at_least` rather than pretending to an exact count.
    """

    cap: int = _TRACKER_CAP
    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0
    capped: bool = False

    def add(self, value: Any) -> None:
        key = _text(value)
        self.total += 1
        if key in self.counts:
            self.counts[key] += 1
            return
        if not self.capped:
            self.counts[key] = 1
            if len(self.counts) >= self.cap:
                self.capped = True

    @property
    def top(self) -> tuple[str, int] | None:
        if not self.counts:
            return None
        return max(self.counts.items(), key=lambda item: item[1])

    @property
    def repeated(self) -> bool:
        return any(count > 1 for count in self.counts.values())


@dataclass(frozen=True)
class Finding:
    """One mechanical check's result: what it saw, why it is a heuristic, and the raw evidence."""

    check: str
    severity: str
    summary: str
    heuristic: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"check": self.check, "severity": self.severity, "summary": self.summary,
                "heuristic": self.heuristic, "detail": self.detail}


def _examples(values: list[tuple[str, Any]]) -> str:
    return ", ".join(f"{locator} {value!r}" for locator, value in values)


class SanityAccumulator:
    """Stream one mapped envelope at a time; ask for findings when the pass is over.

    `expected_profiles` is the vendor's own stated profile count when the operator has it (the 2026-10-13
    delivery says 50 000). `complete_scan` says whether the accumulator saw every record or only the head
    of the delivery -- the cardinality check must not compare a partial scan with a total, and says so.
    """

    def __init__(self, *, expected_profiles: int | None = None, complete_scan: bool = True,
                 now: datetime | None = None) -> None:
        self.expected_profiles = expected_profiles
        self.complete_scan = complete_scan
        self.now = now or datetime.now(timezone.utc)
        self.rows = 0
        self.people: set[str] = set()
        self.trackers = {name: _Tracker() for name in VARYING_FIELDS}
        self.timestamps = _Tracker()
        self.timestamp_min: str | None = None
        self.timestamp_max: str | None = None
        self.timestamp_window_examples: list[tuple[str, str]] = []
        self.timestamp_unreadable: list[tuple[str, Any]] = []
        self.out_of_window = 0
        self.lookalike: dict[str, _Tracker] = {
            name: _Tracker() for name in VOCABULARY_FIELDS + FREE_TEXT_FIELDS + CODE_FIELDS + NUMERIC_FIELDS}
        self.numeric_strings: dict[str, list[tuple[str, Any]]] = {}
        self.numeric_string_rows: dict[str, int] = {}
        self.refusal_counts: dict[str, int] = {}
        self.refusal_examples: dict[str, list[tuple[str, dict, str]]] = {}
        self.refused_rows = 0
        self.missing_required: dict[str, int] = {}

    # -- ingest -------------------------------------------------------------------------------------

    def observe(self, mapped: dict[str, Any], locator: str) -> None:
        """One mapped envelope row. Cheap: a handful of lookups and bounded counters."""
        self.rows += 1
        person = mapped.get("source_person_id")
        if isinstance(person, str) and person.strip():
            self.people.add(person.strip())
        for name in VARYING_FIELDS:
            value = mapped.get(name)
            if value not in (None, ""):
                self.trackers[name].add(value)
        self._observe_timestamp(mapped.get("recorded_at"), locator, mapped.get("record_id"))
        payload = mapped.get("payload")
        values = dict(payload) if isinstance(payload, dict) else {}
        for field_name in VOCABULARY_FIELDS:
            value = mapped.get(field_name)
            if value not in (None, ""):
                self.lookalike[field_name].add(value)
        for field_name in FREE_TEXT_FIELDS + CODE_FIELDS:
            key = field_name.split(".", 1)[1]
            if key in values and values[key] not in (None, ""):
                self.lookalike[field_name].add(values[key])
        for field_name in NUMERIC_FIELDS:
            key = field_name.split(".", 1)[1]
            if key in values and values[key] not in (None, ""):
                self.lookalike[field_name].add(values[key])
                if isinstance(values[key], str):
                    self._note_numeric_string(field_name, locator, values[key])
        for key, value in values.items():
            if isinstance(value, str) and _NUMERIC_SUFFIX.search(key) and _NUMERIC_TEXT.match(value.strip()):
                field_name = f"payload.{key}"
                self.lookalike.setdefault(field_name, _Tracker()).add(value)
                self._note_numeric_string(field_name, locator, value)

    def _note_numeric_string(self, field_name: str, locator: str, value: Any) -> None:
        self.numeric_string_rows[field_name] = self.numeric_string_rows.get(field_name, 0) + 1
        examples = self.numeric_strings.setdefault(field_name, [])
        if len(examples) < 5:
            examples.append((locator, value))

    def _observe_timestamp(self, value: Any, locator: str, record_id: Any) -> None:
        if value in (None, ""):
            return
        try:
            parsed = parse_date(value)
        except MappingError:
            if len(self.timestamp_unreadable) < 5:
                self.timestamp_unreadable.append((locator, value))
            self.out_of_window += 1
            return
        self.timestamps.add(parsed.isoformat())
        rendered = parsed.isoformat()
        if self.timestamp_min is None or rendered < self.timestamp_min:
            self.timestamp_min = rendered
        if self.timestamp_max is None or rendered > self.timestamp_max:
            self.timestamp_max = rendered
        if parsed < PLAUSIBLE_EARLIEST or parsed > self.now + FUTURE_TOLERANCE:
            self.out_of_window += 1
            if len(self.timestamp_window_examples) < 5:
                self.timestamp_window_examples.append(
                    (f"{locator} (record {record_id!r})", rendered))

    def refuse(self, code: str, reason: str, locator: str, values: dict[str, Any] | None = None) -> None:
        """A row the mapping refused, counted by code. A refusal is never silent in a preview."""
        self.refused_rows += 1
        self.refusal_counts[code] = self.refusal_counts.get(code, 0) + 1
        examples = self.refusal_examples.setdefault(code, [])
        if len(examples) < 5:
            examples.append((locator, dict(values or {}), reason))
        if code == "missing_required_target":
            for name in (values or {}).get("missing") or []:
                self.missing_required[name] = self.missing_required.get(name, 0) + 1

    # -- findings -----------------------------------------------------------------------------------

    def findings(self) -> list[Finding]:
        found: list[Finding] = []
        found.extend(self._timestamp_findings())
        found.extend(self._coverage_findings())
        found.extend(self._dominance_findings())
        found.extend(self._cardinality_findings())
        found.extend(self._shape_findings())
        found.extend(self._numeric_findings())
        found.extend(self._refusal_findings())
        return found

    def _timestamp_findings(self) -> list[Finding]:
        if not self.out_of_window:
            return []
        share = self.out_of_window / max(self.rows, 1)
        severity = REFUSAL if share >= HEURISTIC_SHARE else WARNING
        rendered = _examples(self.timestamp_window_examples)
        unreadable = _examples(self.timestamp_unreadable)
        summary = (
            f"recorded_at is outside the plausible window (>= 1900-01-01 and not more than "
            f"{int(FUTURE_TOLERANCE.total_seconds() // 86400)} day(s) in the future) on {self.out_of_window} "
            f"of {self.rows} rows ({share:.0%}); observed range "
            f"{self.timestamp_min or '(none read)'} .. {self.timestamp_max or '(none read)'}; examples: "
            f"{rendered or '(none)'}"
            + (f"; values that are not dates at all: {unreadable}" if unreadable else ""))
        return [Finding(
            check="timestamp_window", severity=severity, summary=summary,
            heuristic=("a timestamp outside the window is either a bad row or a mis-mapped column; this "
                       "check cannot tell which, and a legitimate delivery of planned future appointments "
                       "would trip it -- read the examples before acting"),
            detail={"out_of_window": self.out_of_window, "rows": self.rows, "share": round(share, 4),
                    "observed_min": self.timestamp_min, "observed_max": self.timestamp_max,
                    "examples": [value for _locator, value in self.timestamp_window_examples],
                    "unreadable_examples": [value for _locator, value in self.timestamp_unreadable]})]

    def _coverage_findings(self) -> list[Finding]:
        found: list[Finding] = []
        for name, count in sorted(self.missing_required.items()):
            share = count / max(self.rows, 1)
            severity = REFUSAL if share >= HEURISTIC_SHARE else WARNING
            found.append(Finding(
                check="required_field_coverage", severity=severity,
                summary=(f"required envelope field {name!r} is empty on {count} of {self.rows} rows "
                         f"({share:.0%}); the mapping states a rule for it, but the vendor's cell does not "
                         f"supply a value"),
                heuristic=("an empty cell is a fact about the delivery, not a proof that the rule points at "
                           "the wrong column; a vendor that leaves an optional cell empty on a minority of "
                           "rows is not the same failure as a permission axis that is empty everywhere"),
                detail={"field": name, "rows": self.rows, "empty_rows": count, "share": round(share, 4)}))
        return found

    def _dominance_findings(self) -> list[Finding]:
        found: list[Finding] = []
        for name, tracker in self.trackers.items():
            if name not in VARYING_FIELDS or tracker.total < DOMINANCE_MIN_ROWS or tracker.capped:
                continue
            top = tracker.top
            if top is None:
                continue
            value, count = top
            share = count / tracker.total
            if share < DOMINANCE_SHARE:
                continue
            if name == "record_id":
                severity, why = REFUSAL, "an id that is the same on every row cannot identify a record"
            elif name == "source_person_id":
                severity, why = (REFUSAL if self.complete_scan else WARNING), \
                    "an id that is the same for every row of the scan identifies one thing, not a corpus"
            else:
                severity, why = WARNING, "a timestamp that never varies can be legitimate (one export batch)"
            found.append(Finding(
                check="single_value_dominance", severity=severity,
                summary=(f"{name!r} carries the single value {value!r} on {count} of {tracker.total} rows "
                         f"({share:.1%}); {why}"),
                heuristic=("dominance is checked only while a field has fewer than "
                           f"{tracker.cap} distinct values, because a field with many distinct values "
                           "cannot be dominated by one of them; a delivery of one person's records would "
                           "legitimately show a single value here"),
                detail={"field": name, "value": value, "rows": tracker.total, "top_rows": count,
                        "share": round(share, 4), "distinct": len(tracker.counts)}))
        return found

    def _cardinality_findings(self) -> list[Finding]:
        found: list[Finding] = []
        people = len(self.people)
        if self.rows >= DOMINANCE_MIN_ROWS and people == self.rows:
            # A partial scan can show one distinct person per row by accident (a delivery interleaved
            # round-robin across people looks exactly like this for the first hundred rows), so the
            # impossible shape is only a REFUSAL when the whole delivery was seen.
            severity = REFUSAL if self.complete_scan else WARNING
            found.append(Finding(
                check="source_person_id_cardinality", severity=severity,
                summary=(f"source_person_id has {people} distinct values over {self.rows} rows -- one "
                         f"distinct value per RECORD, which is the shape of a record id (or an encounter "
                         f"id), not of a person id; the import would create one person per record"
                         + ("" if self.complete_scan else " (this scan was PARTIAL, so read it as a "
                                                          "suspicion rather than an impossibility)")),
                heuristic=("one distinct id per record is mechanically impossible for a person id unless "
                           "every person has exactly one record, which cannot be true of a 50 000-profile "
                           "delivery of ~698 000 records; a partial scan of a round-robin delivery can look "
                           "like this without being wrong, which is why the severity depends on the scan"),
                detail={"distinct_source_person_ids": people, "rows": self.rows,
                        "complete_scan": self.complete_scan}))
        if self.expected_profiles is not None:
            if people > self.expected_profiles:
                found.append(Finding(
                    check="source_person_id_cardinality", severity=REFUSAL,
                    summary=(f"{people} distinct source_person_id values were observed, more than the "
                             f"{self.expected_profiles} profiles the delivery declares; an id that is "
                             f"unique where it should repeat is a record-level id"),
                    heuristic=("the declared profile count is the vendor's own number and may itself be "
                               "wrong; what this check proves is only that the two disagree"),
                    detail={"distinct_source_person_ids": people, "expected_profiles": self.expected_profiles,
                            "complete_scan": self.complete_scan}))
            elif self.complete_scan and people * 2 < self.expected_profiles:
                found.append(Finding(
                    check="source_person_id_cardinality", severity=WARNING,
                    summary=(f"only {people} distinct source_person_id values were observed against "
                             f"{self.expected_profiles} declared profiles; either the scan missed people or "
                             f"the id column repeats across people"),
                    heuristic=("a lower distinct count is what a person id that is actually a household id, "
                               "or a partial delivery, looks like; neither is provable from the count alone"),
                    detail={"distinct_source_person_ids": people, "expected_profiles": self.expected_profiles}))
        tracker = self.trackers["record_id"]
        if tracker.total >= DOMINANCE_MIN_ROWS and tracker.repeated and not tracker.capped:
            found.append(Finding(
                check="record_id_duplicates", severity=WARNING,
                summary=(f"record_id repeats: {tracker.total} rows carry only {len(tracker.counts)} distinct "
                         f"values; the import refuses duplicate record ids, so these rows will not load"),
                heuristic=("duplicates among the first tracked values are a floor, not a total; the import's "
                           "own duplicate check is exact"),
                detail={"rows": tracker.total, "distinct": len(tracker.counts)}))
        return found

    def _shape_findings(self) -> list[Finding]:
        found: list[Finding] = []
        for name, tracker in self.lookalike.items():
            if tracker.total < DOMINANCE_MIN_ROWS or not tracker.counts:
                continue
            distinct = len(tracker.counts)
            if name in VOCABULARY_FIELDS:
                offenders = [value for value in tracker.counts if _looks_like_prose(value)]
                what = "sentence-like values"
                should = "a code from a declared vocabulary"
            elif name in FREE_TEXT_FIELDS:
                offenders = [value for value in tracker.counts if _looks_like_code(value)]
                what = "code-shaped values"
                should = "free text"
            elif name in CODE_FIELDS:
                offenders = [value for value in tracker.counts if _looks_like_prose(value)]
                what = "sentence-like values"
                should = "a code"
            else:
                continue
            if not offenders or len(offenders) / distinct < HEURISTIC_SHARE:
                continue
            found.append(Finding(
                check="code_or_prose", severity=WARNING,
                summary=(f"{name!r} should carry {should} but {len(offenders)} of {distinct} distinct "
                         f"sampled values look like {what}; "
                         f"examples: {[value for value in offenders[:5]]}"),
                heuristic=("'looks like a code' and 'looks like prose' are shape rules with no vocabulary in "
                           "them; a legitimate short free-text note, or a vocabulary value with spaces, "
                           "will look wrong to this check"),
                detail={"field": name, "distinct_sampled": distinct, "offending": offenders[:10]}))
        return found

    def _numeric_findings(self) -> list[Finding]:
        found: list[Finding] = []
        for name, examples in sorted(self.numeric_strings.items()):
            rows = self.numeric_string_rows.get(name, len(examples))
            finnish = [value for _locator, value in examples if _NUMERIC_TEXT.match(_text(value))]
            rendered = _examples(examples[:3])
            found.append(Finding(
                check="numeric_passthrough", severity=WARNING,
                summary=(f"{name!r} carries the vendor's STRING {rendered} on at least {rows} rows "
                         f"({len(finnish)} of the examples read as Finnish/anglo numbers); a numeric field "
                         f"whose values are strings is not a number the product can plot or sum"),
                heuristic=("a string in a numeric field is mechanical, and the fix is mechanical too: point "
                           "the rule at the numeric column and add \"transform\": \"decimal_fi\" (or "
                           "\"int_fi\"); but whether the column IS the measurement named by the target is "
                           "still a judgement a human must make from the preview"),
                detail={"field": name, "example_values": [value for _locator, value in examples[:5]],
                        "fix": {"transform": "decimal_fi"}}))
        return found

    def _refusal_findings(self) -> list[Finding]:
        if not self.refusal_counts:
            return []
        by_code = []
        for code, count in sorted(self.refusal_counts.items(), key=lambda item: -item[1]):
            examples = self.refusal_examples.get(code, [])
            by_code.append({"code": code, "count": count,
                            "examples": [{"locator": locator, "values": values, "reason": reason}
                                         for locator, values, reason in examples]})
        first = by_code[0]
        return [Finding(
            check="mapping_refusals", severity=REFUSAL,
            summary=(f"{self.refused_rows} of {self.rows + self.refused_rows} rows were REFUSED by the "
                     f"mapping, so this mapping does not import the delivery as it stands; most common: "
                     f"{first['code']} x{first['count']} -- "
                     f"{first['examples'][0]['reason'] if first['examples'] else ''}"),
            heuristic=("a refusal is a fact, not a heuristic: the mapping refused those rows by name. What "
                       "is not mechanical is which of the two -- the mapping or the delivery -- is wrong"),
            detail={"refused_rows": self.refused_rows, "by_code": by_code})]

    # -- serialisation ------------------------------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        findings = self.findings()
        return {
            "kind": "delivery-sanity-checks",
            "note": HEURISTIC_NOTE,
            "rows_examined": self.rows,
            "rows_refused": self.refused_rows,
            "complete_scan": self.complete_scan,
            "expected_profiles": self.expected_profiles,
            "distinct_source_person_ids": len(self.people),
            "tracked_fields_capped": sorted(name for name, tracker in self.trackers.items() if tracker.capped),
            "counts": {"refusals": self.refusal_counts,
                       "required_field_empty_rows": self.missing_required,
                       "timestamps_out_of_window": self.out_of_window},
            "findings": [finding.as_dict() for finding in findings],
            "refusals": [{"code": code, "count": count,
                          "examples": [{"locator": locator, "values": values, "reason": reason}
                                       for locator, values, reason in self.refusal_examples.get(code, [])]}
                         for code, count in sorted(self.refusal_counts.items(), key=lambda item: -item[1])],
        }
