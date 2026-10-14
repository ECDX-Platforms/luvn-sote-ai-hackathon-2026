"""Minimum-viable declarative mapping: `source_path -> target_field`, versioned in the repository.

The 06:00 problem this solves: the vendor schema is unknown on the day, and `RECORD_TYPE_ALIASES` /
`PAYLOAD_KEY_ALIASES` in `delivery.py` are code, so a mapping revision used to be a code change and a
redeploy. A mapping file is not code. It is loaded at run time, so the operator edits ONE versioned file
(`backend/mappings/*.json`) and re-runs the step.

Six transforms, deliberately only six:

* ``identity``        -- the source value is the target value.
* ``const``           -- the target is a constant that the source does not carry (e.g. ``source``).
* ``lookup_valueset`` -- the source spelling is looked up in a named value set (vendor vocabulary -> ours).
* ``date``            -- ONE date parser accepting Finnish ``DD.MM.YYYY``, ISO-8601 and epoch.
* ``decimal_fi``      -- ONE Finnish number parser: ``72,5`` and ``1 234,5`` (normal, non-breaking or thin
  space) become the real number ``72.5``/``1234.5``. A value it cannot read is REFUSED by name, never
  passed through as the string the vendor wrote -- a weight that is the string ``"72,5"`` is a measurement
  the product cannot plot, and it is also the exact shape of "a real column mapped to the wrong meaning".
* ``int_fi``          -- the integer variant (no decimal part, space/NBSP thousand groups). A decimal comma
  is refused rather than truncated.

Two fail-closed rules:

* an **unknown source path** -- a key present in the row that no rule maps and the spec does not
  explicitly ignore -- is a hard failure. Silently dropping a vendor field is how a briefing goes quietly
  empty, which is the failure this whole module exists beside.
* a ``lookup_valueset`` miss is a hard failure naming the value and the value set. It is not passed
  through and not defaulted.

Three more things the delivery day needs, all explicit rather than implicit:

* ``ignore_source_paths`` names columns the mapping deliberately drops; ``ignore_source_files`` does the
  same for whole files (a vendor profile file beside the records, a ``.DS_Store``). A file is never skipped
  silently -- what is not mapped and not listed here fails the import by name.
* ``require_targets`` refuses a mapping that states no rule for an envelope field the importer must bind.
  A rule per field is the minimum; whether a rule SUPPLIES a value for a given row is checked per row by
  the importer, because an empty vendor cell must not become a defaulted permission axis.
* ``"optional": true`` on a rule declares that the delivery MAY not carry that column at all. Optional
  rules are exempt from the "a rule that matched no row is a typo" refusal, and an optional rule that
  matched nothing is REPORTED BY NAME in the run's sanity report. That is what lets the seeded provisional
  mapping carry a plausible-but-unconfirmed measurement column without either refusing a delivery that
  spells it differently or dropping it in silence.
"""

from __future__ import annotations

import fnmatch
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

TRANSFORMS = ("identity", "const", "lookup_valueset", "date", "decimal_fi", "int_fi")
# A target field may be nested one level under a container (`payload.paino_kg`), because a vendor WIDE CSV
# carries the payload as columns rather than as one JSON cell. The container is the envelope field; the tail
# is the payload key. Deeper nesting is refused rather than guessed at: the payload is one level deep.
_NESTED_TARGET = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)$")
_FINNISH_DATE = re.compile(
    r"^(\d{1,2})\.(\d{1,2})\.(\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$")


class MappingError(ValueError):
    """A mapping rule, a mapped value, or a source row cannot be mapped without guessing."""


def parse_date(value: Any) -> datetime:
    """The one date parser: Finnish ``DD.MM.YYYY``, ISO-8601, or epoch seconds/milliseconds.

    A naive value is read as UTC, because the source delivery carries no timezone and inventing a local
    one would silently shift every timestamp. A value this cannot parse raises rather than returning None:
    a dropped timestamp is how a record disappears from a time-ordered briefing.
    """
    if isinstance(value, bool):
        raise MappingError(f"cannot parse a boolean as a date: {value!r}")
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if abs(value) >= 1e11 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    text = str(value).strip()
    if not text:
        raise MappingError("empty value cannot be parsed as a date")
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return parse_date(float(text))
    found = _FINNISH_DATE.match(text)
    if found:
        day, month, year, hour, minute, second = found.groups()
        return datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0),
                        int(second or 0), tzinfo=timezone.utc)
    try:
        iso = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MappingError(f"unrecognised date {text!r}; expected DD.MM.YYYY, ISO-8601 or epoch") from exc
    return iso if iso.tzinfo is not None else iso.replace(tzinfo=timezone.utc)


# The thousand separators a Finnish export actually uses: the normal space, the non-breaking space, the
# thin space and the narrow no-break space. All four appear in real files, and a parser that accepts only
# the one you happen to have seen is a parser that refuses a real weight at 06:00.
_GROUP_SPACE = "[ \u00a0\u202f\u2009]"
# ONE Finnish decimal shape, and the reason each branch exists:
#
#   ``\d{1,3}(space\d{3})+``      -- ``1 234`` / ``1\u00a0234``: space-grouped thousands.
#   ``\d{1,3}(\.\d{3})+(?=,\d+)`` -- ``1.234,5``: dot-grouped thousands, accepted ONLY when a comma decimal
#                                    part follows, because then it is unambiguous. The lookahead is the whole
#                                    point: ``1.234`` on its own stays the float 1.234, not the integer 1234.
#   ``\d+``                       -- no grouping at all (``12345,6``).
#
# The integer part must have a digit, and a separator must have a non-empty fraction: ``72,`` and ``,5`` are
# refused rather than guessed at.
_DECIMAL_FI = re.compile(
    r"^(?P<sign>[+-]?)(?P<integer>\d{1,3}(?:" + _GROUP_SPACE + r"\d{3})+"
    r"|\d{1,3}(?:\.\d{3})+(?=,\d+)|"
    r"\d+)(?:(?P<separator>[.,])(?P<fraction>\d+))?$")
_INTEGER_FI = re.compile(r"^(?P<sign>[+-]?)(?P<integer>\d{1,3}(?:" + _GROUP_SPACE + r"\d{3})+|\d+)$")


def _number_refusal(value: Any, transform: str, reason: str) -> None:
    raise MappingError(
        f"cannot read {value!r} as a number with the {transform!r} transform: {reason}; a numeric field "
        f"must be a real number, and passing the vendor's string through would leave a measurement the "
        f"product cannot use (map a different column, or fix the value with the vendor)")


def _plain_number(value: Any, transform: str) -> float | None:
    """An already-numeric JSON cell, checked for the two non-numbers JSON can carry."""
    if isinstance(value, bool):
        _number_refusal(value, transform, "a boolean is not a number")
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            _number_refusal(value, transform, "not a finite number")
        return number
    return None


def parse_decimal_fi(value: Any) -> float:
    """``72,5`` / ``1 234,5`` / ``1\u00a0234,5`` / ``72.5`` -> the real number.

    The one number parser, exactly as `parse_date` is the one date parser. A value it cannot read raises
    `MappingError` naming the value and the transform. It never returns None and never passes the source
    string through: `72,5` as a string is a measurement the briefing cannot plot, and a numeric field whose
    values are strings is also the visible signature of a column mapped to the wrong MEANING.
    """
    number = _plain_number(value, "decimal_fi")
    if number is not None:
        return number
    text = value.strip() if isinstance(value, str) else str(value).strip()
    if not text:
        _number_refusal(value, "decimal_fi", "an empty value is not a number")
    found = _DECIMAL_FI.match(text)
    if found is None:
        _number_refusal(value, "decimal_fi",
                        "expected a Finnish number such as '72,5', '1 234,5' or '72.5'")
    integer = found.group("integer").replace(".", "")
    integer = re.sub(_GROUP_SPACE, "", integer)
    fraction, separator = found.group("fraction"), found.group("separator")
    # A dot is already the decimal point; a comma is the Finnish decimal separator and becomes one.
    rendered = f"{found.group('sign')}{integer}" + (f".{fraction}" if separator else "")
    return float(rendered)


def parse_int_fi(value: Any) -> int:
    """The integer variant: ``1 234`` / ``1\u00a0234`` / ``1234``. No decimal part, and none guessed.

    A decimal comma is refused rather than truncated (``72,5`` is not the integer 72), and a bare dot is
    refused too (``1.234`` reads as the float 1.234 in `parse_decimal_fi`, so reading it as 1234 here would
    be two transforms disagreeing about one string).
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = _plain_number(value, "int_fi")
        if number is None or not float(number).is_integer():
            _number_refusal(value, "int_fi", "a value with a fractional part is not an integer")
        return int(number)
    text = value.strip() if isinstance(value, str) else str(value).strip()
    if not text:
        _number_refusal(value, "int_fi", "an empty value is not a number")
    found = _INTEGER_FI.match(text)
    if found is None:
        _number_refusal(value, "int_fi",
                        "expected a thousands-separated integer such as '1 234' or '1234' (a decimal "
                        "comma belongs to the 'decimal_fi' transform)")
    digits = re.sub(_GROUP_SPACE, "", found.group("integer"))
    return int(f"{found.group('sign')}{digits}")


@dataclass(frozen=True)
class Rule:
    id: str
    target_field: str
    transform: str
    source_path: str | None = None
    value: Any = None
    valueset: str | None = None
    # A rule the mapping author declares the delivery MAY not carry at all (a plausible measurement column
    # nobody has confirmed). Optional rules are exempt from the "matched no row is a typo" refusal, and an
    # optional rule that matched nothing is REPORTED by name instead -- which is the honest way to seed a
    # mapping for a delivery whose columns are unknown until the day.
    optional: bool = False


@dataclass
class MappingSpec:
    version: str
    path: Path
    rules: tuple[Rule, ...]
    valuesets: dict[str, dict[str, Any]] = field(default_factory=dict)
    ignore_source_paths: frozenset[str] = frozenset()
    ignore_source_files: tuple[str, ...] = ()
    provisional: bool = False
    note: str = ""

    @classmethod
    def load(cls, path: Path) -> "MappingSpec":
        path = Path(path)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise MappingError(f"mapping file not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise MappingError(f"invalid JSON in mapping file {path}: {exc}") from exc
        version = document.get("version")
        if not isinstance(version, str) or not version:
            raise MappingError(f"{path}: a mapping file needs a string 'version'")
        valuesets = document.get("valuesets") or {}
        if not isinstance(valuesets, dict) or any(not isinstance(v, dict) for v in valuesets.values()):
            raise MappingError(f"{path}: 'valuesets' must be an object of objects")
        rules: list[Rule] = []
        seen_ids: set[str] = set()
        for index, raw in enumerate(document.get("rules") or []):
            rule = cls._parse_rule(path, index, raw, valuesets)
            if rule.id in seen_ids:
                raise MappingError(f"{path}: duplicate rule id {rule.id!r}")
            seen_ids.add(rule.id)
            rules.append(rule)
        if not rules:
            raise MappingError(f"{path}: a mapping file with no rules maps nothing")
        ignored = frozenset(document.get("ignore_source_paths") or [])
        ignored_files = document.get("ignore_source_files") or []
        if not isinstance(ignored_files, list) or any(not isinstance(name, str) or not name
                                                      for name in ignored_files):
            raise MappingError(f"{path}: 'ignore_source_files' must be a list of file name patterns")
        return cls(version=version, path=path, rules=tuple(rules), valuesets=valuesets,
                   ignore_source_paths=ignored, ignore_source_files=tuple(ignored_files),
                   provisional=bool(document.get("provisional")),
                   note=str(document.get("note") or ""))

    @staticmethod
    def _parse_rule(path: Path, index: int, raw: Any, valuesets: dict) -> Rule:
        where = f"{path}: rule {index}"
        if not isinstance(raw, dict):
            raise MappingError(f"{where} is not an object")
        rule_id = raw.get("id") or raw.get("source_path") or f"rule-{index}"
        transform = raw.get("transform", "identity")
        if transform not in TRANSFORMS:
            raise MappingError(f"{where} ({rule_id!r}) has unknown transform {transform!r}; known: "
                               f"{list(TRANSFORMS)}")
        target = raw.get("target_field")
        if not isinstance(target, str) or not target:
            raise MappingError(f"{where} ({rule_id!r}) needs a target_field")
        if "." in target and not _NESTED_TARGET.match(target):
            raise MappingError(f"{where} ({rule_id!r}) has target_field {target!r}; a nested target must be "
                               f"'container.key' (for example 'payload.paino_kg'), one level deep")
        source_path = raw.get("source_path")
        if transform != "const" and (not isinstance(source_path, str) or not source_path):
            raise MappingError(f"{where} ({rule_id!r}) needs a source_path unless transform is 'const'")
        valueset = raw.get("valueset")
        if transform == "lookup_valueset":
            if not isinstance(valueset, str) or valueset not in valuesets:
                raise MappingError(f"{where} ({rule_id!r}) names valueset {valueset!r}, which the file "
                                   f"does not declare; declared: {sorted(valuesets)}")
        return Rule(id=str(rule_id), target_field=target, transform=transform,
                    source_path=source_path if isinstance(source_path, str) else None,
                    value=raw.get("value"), valueset=valueset if isinstance(valueset, str) else None,
                    optional=bool(raw.get("optional", False)))

    def ignores_file(self, source_path: str) -> bool:
        """Is this delivery file deliberately not a records source? Never a silent skip: it is declared."""
        return any(fnmatch.fnmatch(source_path, pattern) for pattern in self.ignore_source_files)

    def missing_required_targets(self, required: tuple[str, ...] | list[str]) -> list[str]:
        """The envelope fields this mapping states no rule for, in the caller's order."""
        covered = {rule.target_field.split(".")[0] for rule in self.rules}
        return [field for field in required if field not in covered]

    def require_targets(self, required: tuple[str, ...] | list[str]) -> None:
        """Refuse a mapping that does not state a rule for every required envelope field.

        This is a property of the FILE, checked before a byte of the delivery is read, so a mapping that
        forgot `consent_scope` fails in the first second rather than on row 400 000. Whether a rule
        SUPPLIES a value for a row is a separate, per-row check in the importer: a rule for a column the
        vendor left empty on that row must not become a defaulted permission axis.
        """
        missing = self.missing_required_targets(required)
        if missing:
            raise MappingError(
                f"{self.path} ({self.version}) states no rule for required envelope field(s) {missing}; "
                f"every one must be mapped explicitly (a rule, an `ignore_source_paths` entry, or a transform "
                f"is not a substitute for a missing permission axis)")

    def apply(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map one source row to target fields, failing on an unknown path or a lookup miss."""
        target, _matched = self.apply_with_matches(row)
        return target

    @staticmethod
    def _assign(target: dict[str, Any], target_field: str, value: Any, rule_id: str) -> None:
        """Assign a mapped value, merging one level of nesting and refusing a contradictory second rule."""
        found = _NESTED_TARGET.match(target_field)
        if found is None:
            if target_field in target and target[target_field] != value:
                raise MappingError(
                    f"conflicting rules for target {target_field!r}: "
                    f"{target[target_field]!r} and {value!r} (rule {rule_id!r})")
            target[target_field] = value
            return
        container_name, key = found.groups()
        container = target.setdefault(container_name, {})
        if not isinstance(container, dict):
            raise MappingError(f"rule {rule_id!r} maps {target_field!r}, whose container "
                               f"{container_name!r} is already a non-object value")
        if key in container and container[key] != value:
            raise MappingError(
                f"conflicting rules for target {target_field!r}: {container[key]!r} and {value!r} "
                f"(rule {rule_id!r})")
        container[key] = value

    def apply_with_matches(self, row: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
        mapped_sources = {rule.source_path for rule in self.rules if rule.source_path}
        unknown = sorted(key for key in row
                         if key not in mapped_sources and key not in self.ignore_source_paths)
        if unknown:
            raise MappingError(
                f"unknown source path(s) {unknown}; add a rule or list them in ignore_source_paths "
                f"({self.path.name} {self.version})")
        target: dict[str, Any] = {}
        matched: set[str] = set()
        for rule in self.rules:
            if rule.transform != "const" and rule.source_path not in row:
                continue
            value = rule.value if rule.transform == "const" else row.get(rule.source_path)
            if rule.transform != "const" and value in (None, ""):
                continue
            if rule.transform == "lookup_valueset":
                table = self.valuesets[rule.valueset or ""]
                if value not in table:
                    raise MappingError(
                        f"value {value!r} is not in valueset {rule.valueset!r} for target "
                        f"{rule.target_field!r}; known: {sorted(table)}")
                value = table[value]
            elif rule.transform == "date":
                value = parse_date(value).isoformat()
            elif rule.transform == "decimal_fi":
                value = parse_decimal_fi(value)
            elif rule.transform == "int_fi":
                value = parse_int_fi(value)
            self._assign(target, rule.target_field, value, rule.id)
            matched.add(rule.id)
        return target, matched

    def rules_matched_nothing(self, rule_matches: dict[str, int], *, optional: bool = False) -> list[str]:
        """Rules that matched no row, as either a typo (the default) or a declared-optional miss.

        A rule whose source path appears in no row is the mapping's own typo -- the failure mode that used to
        be silent while the import reported success. Two exemptions, both explicit: a `const` rule supplies a
        value without reading a row, and a rule whose target field is mapped by a SIBLING rule (a vendor's
        alternative column name) is an alternative that legitimately matched nothing. `optional=True`
        selects the rules the author declared the delivery may not carry, which are reported rather than
        refused; they are never silently dropped either.
        """
        mapped_targets = {rule.target_field for rule in self.rules if rule_matches.get(rule.id)}
        silent = []
        for rule in self.rules:
            if rule.transform == "const" or rule_matches.get(rule.id):
                continue
            if rule.optional is not optional:
                continue
            if rule.target_field in mapped_targets:
                continue
            silent.append(f"{rule.id!r} (source_path {rule.source_path!r} -> target {rule.target_field!r})")
        return silent

    def dry_run(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Per-rule match counts over sample rows, so a human sees which rules matched nothing."""
        counts = {rule.id: 0 for rule in self.rules}
        unknown_paths: dict[str, int] = {}
        mapped_sources = {rule.source_path for rule in self.rules if rule.source_path}
        for row in rows:
            for key in row:
                if key not in mapped_sources and key not in self.ignore_source_paths:
                    unknown_paths[key] = unknown_paths.get(key, 0) + 1
            for rule in self.rules:
                if rule.transform == "const":
                    counts[rule.id] += 1 if rule.value is not None else 0
                elif rule.source_path in row and row.get(rule.source_path) not in (None, ""):
                    counts[rule.id] += 1
        return {
            "kind": "delivery-mapping-dry-run",
            "mapping_version": self.version,
            "mapping_path": str(self.path),
            "provisional": self.provisional,
            "rows_examined": len(rows),
            "rules": [
                {"id": rule.id, "source_path": rule.source_path, "target_field": rule.target_field,
                 "transform": rule.transform, "optional": rule.optional,
                 "matched_rows": counts[rule.id],
                 "matched_nothing": counts[rule.id] == 0}
                for rule in self.rules
            ],
            "rules_matched_nothing": [rule.id for rule in self.rules
                                      if counts[rule.id] == 0 and not rule.optional],
            "optional_rules_matched_nothing": [rule.id for rule in self.rules
                                               if counts[rule.id] == 0 and rule.optional],
            "unknown_source_paths": unknown_paths,
        }
