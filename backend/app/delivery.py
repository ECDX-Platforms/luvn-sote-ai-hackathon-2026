"""Streaming importer for the 13.10.2026 delivery (CSV profiles + JSONL records).

The real delivery schema is **not available yet**. The normal importer is strict; `dry_run_delivery`
collects every row it refuses, writes its report FIRST, and only then raises, so a refusal always leaves a
readable account of what was examined and why it was refused. The payload aliases below are **PROVISIONAL**
until the delivery confirms them:

- ``profiles.csv``: ``source_person_id,persona_id,label`` (header required).
- ``encounters.csv``: optional operator-owned ``encounter_id,source_person_id,role,purpose,example_label,demo_name`` rows; the source key must exist in profiles and the role must be valid for the purpose.
- ``records.jsonl``: one JSON object per line with the ``Record`` envelope keys,
  ``payload`` as a nested object. ``records.csv`` is also accepted, with
  ``payload`` as a JSON string.

What is a refusal and what is a warning: an envelope field that fails validation (a missing or bad
``record_id``/``source_person_id``/``recorded_at``/``sensitivity_tag``/``consent_scope``, an unknown
``record_type``), an unresolvable source person, a duplicate record id, an undeclared ``topic``, and a
rendered record whose payload maps to nothing are all HARD refusals. Only an unknown *payload key* on a
known type is a warning, because it is content the product simply does not render.

The importer streams the records input, builds the person and encounter indexes once, and writes one
``Persona`` file per person plus a SQLite index ``persona_id -> file``. An
encounter then loads one person's records, never the corpus. Records whose
``source_person_id`` cannot be bound to the profiles, and duplicate record IDs
anywhere in the delivery, are rejected rather than guessed.

`on_error="quarantine"` is the opt-in alternative to a strict load: every refused row is staged in a
quarantine destination with its source locator, raw row and reason, and the remainder is loaded. The
default stays strict, and a quarantine run never exits non-zero by itself -- only `--fail-on-quarantine`,
or a run that loaded nothing, does.

**The mapping path is the arrival path.** `import_delivery(..., mapping=<file>)` and `load_corpus(...,
mapping=<file>)` read a delivery in ANY layout `app/landing.py` accepts -- a wide or long CSV, JSONL, one
JSON array, one file per person, nested directories, a zip, a raw directory or a landing zone -- and turn
every raw row into the canonical envelope through `app/mapping.py`'s declarative rules, BEFORE binding. A
vendor `record_type` or `topic` spelling this code does not declare is therefore translated by a
`lookup_valueset` rule instead of refused. What the mapping file must be explicit about:

- a rule for every `REQUIRED_ENVELOPE_TARGETS` field, or the import refuses before reading a byte and names
  the field (a missing permission axis is never defaulted);
- every source column, or listed in `ignore_source_paths` / `ignore_source_files` (nothing is dropped
  silently);
- a rule that matches at least one row, unless a sibling rule maps the same target field.

The PEOPLE are the distinct mapped `source_person_id` values (persona id = source person id), because a raw
vendor delivery carries its person identifier in its records and not in a separate `profiles.csv`. Without
`mapping`, none of this applies and the canonical `profiles.csv` + `records.jsonl`/`records.csv` path is
unchanged.

Unverified against the real schema: the exact column names and code systems, the
operator's actual ``Rooli``/``Palvelukonteksti`` mapping, and the delivery's own
record-type spellings. ``Lähdehenkilötunniste`` is never inferred from a name or a note.
"""

from __future__ import annotations

import csv
from datetime import date
import json
import math
import os
import re
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from itertools import permutations
from pathlib import Path
from threading import RLock
from typing import Any, cast, get_args

from pydantic import ValidationError

from . import landing, source
from .encounters import Appointment, appointment_from_row, Encounter, EncounterMetadata, metadata_from_row
from .mapping import MappingError, MappingSpec
from .policy import roles as policy_roles
from .purpose import Purpose, palvelukonteksti, palvelukontekstit
from .purpose import topics as declared_topics
from .quarantine import Refusal, RefusalLog, write_summary
from .sanity import SanityAccumulator
from .upm import (
    ID_MAX_LENGTH,
    LABEL_MAX_LENGTH,
    ConsentHolder,
    LifeStage,
    Persona,
    Record,
)

# The envelope fields a mapping must state a rule for. `source` is NOT here: the importer states provenance
# itself and would override any mapping rule. `payload` is not here: it is content, not a permission axis,
# and a record with an empty payload is caught by the content-maps-to-nothing gate. `authored_by_role` IS
# here because `Record` requires it; leaving it out would make the failure message the generic
# "record missing columns" instead of naming the one field the mapping forgot.
REQUIRED_ENVELOPE_TARGETS = ("record_id", "source_person_id", "record_type", "authored_by_role",
                             "recorded_at", "topic", "sensitivity_tag", "consent_scope")
PROFILE_COLUMNS = ("source_person_id", "persona_id", "label")
# Eight, the same columns `backend/scripts/build_veil_corpus.py` writes. `consent_holders` and `life_stage` are
# REQUIRED here: the reader checks only the columns it declares, so a missing or misspelled `consent_holders`
# header would otherwise be accepted and default to self-only consent. The reader refuses a header
# missing either column by name, which is what keeps that from happening silently.
# `appointments.csv` is optional: the scheduled occasions the day view reads. `domain` names the source table's
# consent scope (clinical appointment -> treatment, home-care visit -> social_care); `encounter_id` is the lens that
# opens the person's briefing in that domain, blank when the person has none.
APPOINTMENT_COLUMNS = ("appointment_id", "source_person_id", "domain", "scheduled_on", "status_text",
                       "urgency_text", "specialty", "contact_record_id", "encounter_id")
APPOINTMENT_DOMAINS = ("treatment", "social_care")
ENCOUNTER_COLUMNS = ("encounter_id", "source_person_id", "role", "purpose", "example_label", "demo_name",
                     "consent_holders", "life_stage")
# Two lens axes the bound catalogue declares per encounter and the delivery shape could not express at
# all, so a delivery could not reproduce a guardian lens or a child lens:
#
#   * `consent_holders` — which holders this lens covers. It defaulted to `("self",)`, which INVERTS the
#     guardian lens: the record the child holds under their own confidentiality right is served and the
#     guardian-held record is silently absent. Getting a permission axis backwards by omission is the
#     worst failure this codebase has a word for, so the column exists and is checked.
#   * `life_stage` — which life stage the lens's catalogue rows are keyed by. A declared axis, never
#     derived from a birth year (`backend/app/encounters.py` says why), and with the same default as
#     `Encounter`.
#
# Deliberately NOT expressible here: `kypsyysarvio`, `age_at_encounter` and the inherited case
# exclusions. The first is per-encounter and the current round's scope forbids touching it; the other two
# need a trusted catalogue this shape does not carry, and inventing them from the delivery would be the
# derivation `encounters.py` warns against.
# Derived from the declared literals rather than restated. A value added to `ConsentHolder` must not be
# silently refused here, and one removed must not still be accepted: two hand-kept copies of a permission
# vocabulary is two chances to disagree about who a lens may speak for.
ENCOUNTER_HOLDERS = get_args(ConsentHolder)
ENCOUNTER_LIFE_STAGES = get_args(LifeStage)
# Azure SQL and the SQLite import index must refuse the same encounter shapes, so the CHECK value lists
# are generated from the one declaration each engine's own reader validates against. Two hand-kept copies
# of a permission vocabulary are two chances to disagree about who a lens may speak for.
ENCOUNTER_ROLE_VALUES = tuple(sorted(policy_roles()))
ENCOUNTER_PURPOSE_VALUES = palvelukontekstit()
# `consent_holders` is stored comma-joined, so the constraint cannot be a plain membership test: every
# ORDERED combination of declared holders is enumerated (two holders -> four strings). The importer keeps
# the operator's order, so enumerating permutations is what makes the check exact instead of a shape test
# that would also admit "selfguardian".
CONSENT_HOLDER_STRINGS = tuple(
    ",".join(combination) for size in range(1, len(ENCOUNTER_HOLDERS) + 1)
    for combination in permutations(ENCOUNTER_HOLDERS, size))


def _sql_literal_list(values: Iterable[str]) -> str:
    """One SQL literal list, usable in both engines' CHECK constraints."""
    return ", ".join("'" + str(value).replace("'", "''") + "'" for value in values)


# The four declared encounter enums, as SQL literal lists, shared by `import_delivery`'s SQLite DDL and
# `sqldelivery`'s Azure SQL DDL so the two cannot drift.
ENCOUNTER_CHECK_SQL = {
    "role": _sql_literal_list(ENCOUNTER_ROLE_VALUES),
    "purpose": _sql_literal_list(ENCOUNTER_PURPOSE_VALUES),
    "consent_holders": _sql_literal_list(CONSENT_HOLDER_STRINGS),
    "life_stage": _sql_literal_list(ENCOUNTER_LIFE_STAGES),
}
RECORD_COLUMNS = ("record_id", "source_person_id", "record_type", "authored_by_role",
                  "recorded_at", "topic", "sensitivity_tag", "consent_scope", "payload")
# The DEMO corpus, not `delivery-sample`: the shipped default has to pass the repository's own arrival
# check (D3), and the two-person sample warns on five types. `delivery-sample` remains the loader's own
# fixture. The default corpus choice belongs to the corpus workstream; this line is the one it needs.
DEFAULT_DELIVERY_DIR = Path(__file__).resolve().parents[2] / "data" / "experimental" / "delivery-demo"
_INDEX_NAME = "index.sqlite"
# Bumped whenever the index schema changes, and checked on open. Version 3 gave the SQLite tables the
# foreign keys, NOT NULLs and enum CHECKs Azure SQL already had (and dropped the duplicated `persona_id`
# from `encounters`, which the join through `people` already provides), so an index written by version 2
# is refused with the deliberate error below instead of being silently misread. Version 4 adds the
# `people.path` CHECK -- `people/[A-Za-z0-9._-]*.jsonl`, the shape the importer has always written and
# the only shape the reader accepts -- so a hand-built index that points anywhere else is refused at the
# DDL boundary rather than trusted.
INDEX_SCHEMA_VERSION = 5
_PEOPLE_DIR = "people"
# The ONLY shape the importer ever writes into `people.path`: `people/<filename>.jsonl`, one path segment,
# no traversal, a filename `_SAFE_ID` can produce. `people/*.jsonl` is NOT enough -- GLOB `*` spans
# separators, so it matches `people/../../elsecret.jsonl`. Measured against SQLite 3.53: `[^/]` and
# adjacent character classes do not behave as POSIX GLOB would, and a class embedded in a CHECK literal
# did not match `people/p.jsonl` either, so the predicate that is verified here is INSTR-based. It refuses
# `people/../../elsecret.jsonl`, `people/..x.jsonl`, `people/x/y.jsonl`, `not-people/p.jsonl`,
# `people/p.txt`, `/abs/p.jsonl` and `people/.hidden.jsonl`, and admits every shape the importer writes.
# This is the DDL half; `DeliveryIndex._person_file` is the authoritative containment check at read time.
_PEOPLE_PATH_EXPRESSION = (
    "instr(path, 'people/') = 1 AND instr(substr(path, 8), '/') = 0 AND instr(path, '..') = 0 "
    "AND substr(path, -6) = '.jsonl' AND substr(path, 8, 1) <> '.'")
# The boolean form of the same rule, for callers that want to test a path without a database (the tests
# assert this is the text the DDL carries).
_PEOPLE_PATH_GLOB = _PEOPLE_PATH_EXPRESSION
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# The operator corpus is bounded in BYTES (`sqldelivery.MAX_CORPUS_FILE_BYTES`), and the catalogue endpoint is
# paged, so this is a ceiling rather than the contract a caller depends on. It stays a ceiling so a runaway
# corpus is refused before it is parsed into memory, but it is sized for the release this product actually
# serves: 45,000 people and 38,469 encounters across the two cohorts. The previous value of 128 refused the
# real corpus outright (`encounters.csv exceeds the bounded limit of 128`, 2026-10-13).
MAX_OPERATOR_ENCOUNTERS = 250_000
# Only canonical record spellings are currently verified in the assumed delivery. The map is derived from
# `source.RECORD_TYPES`, so an added canonical type cannot leave this list behind. Unknown vendor types are
# hard refusals at import and are reported by the dry run; do not guess aliases into the permission path.
RECORD_TYPE_ALIASES = {value: value for value in source.RECORD_TYPES}

# PROVISIONAL mirror of frontend/src/challenge-demo.mjs SOURCE_FIELDS. The drift test parses that
# definition; importing does not make either side authoritative.
RENDER_PAYLOAD_KEYS = {
    source.BASIC_INFO: {"sukupuoli", "syntymävuosi", "äidinkieli", "kotikunta", "asumismuoto"},
    source.DIAGNOSIS: {"nimi", "icd10", "tila"},
    source.MEDICATION: {"lääke", "annos", "tila", "puuttuneet_annokset_30d"},
    source.FREE_TEXT: {"otsikko", "teksti", "käyntityyppi"},
    source.ARVIOINTI: {"instrumentti", "versio"},
    source.MEASUREMENT: {"paino_kg", "pituus_cm", "verenpaine", "syke", "lämpö_c", "spo2"},
    source.LAB_RESULT: {"tutkimus", "arvo", "yksikkö", "tulkinta", "viitearvo"},
    source.APPOINTMENT: {"kanava", "suunta", "aihe", "yhteenveto"},
    source.DECISION: {"päätös", "voimassa"},
}
# Payload-name adapters are separate from policy fields. The non-identity spellings are legacy names
# in our own sample/generator, not claims about the unavailable vendor delivery.
PAYLOAD_KEY_ALIASES = {
    record_type: {key: key for key in keys} for record_type, keys in RENDER_PAYLOAD_KEYS.items()
}
PAYLOAD_KEY_ALIASES[source.BASIC_INFO].update({"nimi": "nimi", "näyttötunniste": "näyttötunniste"})
PAYLOAD_KEY_ALIASES[source.FREE_TEXT]["kirjaaja"] = "kirjaaja"
# `vastaukset` is the one INGRESS-ONLY key: it is how a source record carries an instrument's answers, and
# nothing renders it directly — `assess.score` turns it into the item rows and the total, which is what the
# briefing shows. Unregistered, every delivery carrying a scored instrument reported it as an *unmapped*
# payload key, so a clean delivery of scored PHQ-9/IADL rows could not reconcile without a warning that was
# really a declaration the map had not made. The shipped sample has carried `vastaukset` all along.
PAYLOAD_KEY_ALIASES[source.ARVIOINTI]["vastaukset"] = "vastaukset"
# Two more INGRESS-ONLY keys, written by the VEIL corpus builder (`scripts/build_veil_corpus.py`) when it reads a
# clinical note by its sections: `osiot` is the note's own `## ` sections keyed by heading (the esitieto reader cites
# one section rather than the whole note), and `lähde_record_id` names, on a record derived from a note section (a
# measurement line, the risk statement, the functioning statement), the note it was read from. Neither is rendered as
# a field; both are provenance the briefing follows.
PAYLOAD_KEY_ALIASES[source.FREE_TEXT].update({"osiot": "osiot", "lähde_record_id": "lähde_record_id"})
PAYLOAD_KEY_ALIASES[source.MEASUREMENT]["lähde_record_id"] = "lähde_record_id"
# `verenpaine`/`syke` used to be registered here as aliases for keys the product accepted but did not
# render, because our own sample and generator named the measurement while the tiles read only paino/pituus.
# They are render keys now, so the identity aliases above already cover them and a second registration here
# would only be a place for the two definitions to drift apart.
PAYLOAD_KEY_ALIASES[source.LAB_RESULT].update({
    "tulos": "arvo", "viitealue": "viitearvo", "viitearvo": "viitearvo",
})
PAYLOAD_KEY_ALIASES[source.DECISION].update({
    "kuvaus": "päätös", "ratkaisu": "päätös", "voimassa_alkaen": "voimassa_alkaen",
})
# Render keys whose ABSENCE is not a coverage gap. A measurement row is one measurement, and a corpus that carries
# weights and blood pressures but never a temperature or an oxygen saturation has nothing missing: those two vitals
# are read when a source writes them (the VEIL notes write temperatures) and are silent otherwise. Reporting them as
# gaps would make every corpus without a thermometer `warning` forever, which is the noise the per-type finding was
# redesigned to avoid. They stay render keys so a row carrying only a temperature is renderable, not "maps to nothing".
OPTIONAL_RENDER_KEYS = {source.MEASUREMENT: {"lämpö_c", "spo2"}}
USEFUL_PAYLOAD_KEYS = {
    source.BASIC_INFO: {"syntymävuosi"}, source.DIAGNOSIS: {"nimi"},
    source.MEDICATION: {"lääke"}, source.FREE_TEXT: {"teksti"},
    # `mittaus` is the one type whose useful keys are read as an INTERSECTION rather than a subset
    # (see the call sites): a measurement row is useful when it carries ANY of the four things the tiles
    # render, because one row is one measurement. Before 2026-10-09 the useful set was
    # `{"verenpaine", "syke"}` while the render set was `{"paino_kg", "pituus_cm"}` — disjoint, so no
    # measurement row could ever reconcile clean, and a delivery containing measurements could never reach
    # `status: ready`. The fix is that all four are now rendered; the useful set is therefore the render
    # set itself, and `test_every_types_useful_set_includes_a_render_key` pins that they intersect.
    source.ARVIOINTI: {"instrumentti"},
    source.MEASUREMENT: {"paino_kg", "pituus_cm", "verenpaine", "syke", "lämpö_c", "spo2"},
    source.LAB_RESULT: {"tutkimus"}, source.APPOINTMENT: {"kanava"},
    source.DECISION: {"päätös"},
}


# A decision text that announces itself as a draft. The VEIL social-work export carries decision drafts whose
# text begins with a `# PÄÄTÖSLUONNOS` heading and no envelope `state`, so the briefing showed them as settled
# decisions (seen in the live app 2026-10-14). Leading `#`s and whitespace are allowed; the word must stand alone.
_DRAFT_DECISION_HEADING = re.compile(r"^\s*#*\s*PÄÄTÖSLUONNOS(?![\w])", re.IGNORECASE)
# Where a decision text lives: the `päätös` payload of a decision record, and the `teksti` of the social-work note
# the VEIL builder writes from the `decision_text` source field (`scripts/build_veil_corpus.py`, `_social_note`).
_DECISION_TEXT_KEYS = {source.DECISION: ("päätös", "teksti"), source.FREE_TEXT: ("teksti", "päätös")}


def derived_record_state(record: Record) -> Record:
    """The record with `state = "draft"` derived from its own text, when the envelope stated no state.

    A stated state always wins; the text is never altered. Applied where a record is built from a delivery
    row (`_bind_record`, so the file import and the SQL loader derive the same state) and where a stored
    record is read back, so a corpus loaded before the rule carries the mark without a reload.
    """
    if record.state is not None:
        return record
    for key in _DECISION_TEXT_KEYS.get(record.record_type, ()):
        text = record.payload.get(key)
        if isinstance(text, str) and _DRAFT_DECISION_HEADING.match(text):
            return record.model_copy(update={"state": "draft"})
    return record


class DeliveryShapeError(ValueError):
    """A row does not match the assumed delivery shape.

    Carries the stable refusal `code` and the offending `values` when the raise site knows them, so the
    reconciliation report can count refusals by reason and show examples without re-parsing messages. The
    position of the row is supplied by the caller that knows the locator.
    """

    def __init__(self, message: str, *, code: str = "shape",
                 values: dict[str, Any] | None = None, record_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.values = dict(values or {})
        self.record_id = record_id


def _envelope_error(record_id: str | None, error: ValidationError) -> DeliveryShapeError:
    """Turn a Pydantic envelope failure into a refusal that names the field and the offending value.

    "invalid required envelope field" on its own was the thing this task exists to remove: at 06:00 an
    operator needs to know WHICH field and WHAT value the vendor spelled differently.
    """
    first = error.errors()[0] if error.errors() else {}
    location = ".".join(str(part) for part in first.get("loc", ())) or "envelope"
    offending = first.get("input")
    if isinstance(offending, (str, int, float, bool)) or offending is None:
        rendered: Any = offending
    else:
        rendered = repr(offending)
    # The two engines must refuse the same shape with the SAME wording, so a length refusal names the field
    # and the column's width rather than relying on Pydantic's own phrasing.
    if first.get("type") == "string_too_long":
        limit = (first.get("ctx") or {}).get("max_length")
        return DeliveryShapeError(
            f"record {record_id!r} has a {len(str(offending))}-character {location!r}; the limit is "
            f"{limit} characters (the width of the SQL column)",
            code="field_too_long", values={location: rendered, "limit": limit},
            record_id=record_id if isinstance(record_id, str) else None)
    return DeliveryShapeError(
        f"record {record_id!r} has an invalid envelope field {location!r}: {first.get('msg', 'invalid')}"
        f" (value {rendered!r})",
        code="invalid_envelope",
        values={location: rendered},
        record_id=record_id if isinstance(record_id, str) else None,
    )


def _safe_persona_id(value: str, key: str) -> str:
    """A persona ID becomes a filename, so it must not carry path syntax."""
    persona_id = (value or key).strip()
    if not _SAFE_ID.fullmatch(persona_id) or persona_id in {".", ".."}:
        raise DeliveryShapeError(
            f"unsafe persona_id {persona_id!r}; use a leading letter or digit and only . _ -",
            code="unsafe_persona_id", values={"persona_id": persona_id})
    return persona_id


@dataclass(frozen=True)
class ImportReport:
    personas: int
    records: int
    encounters: int
    skipped: int
    output_dir: Path
    shape_report: Path
    quarantined: int = 0
    quarantine_path: Path | None = None
    quarantine_summary: Path | None = None
    mapping_version: str | None = None
    mapping_path: Path | None = None
    # The mechanical checks over the mapped output, written whether or not anything was quarantined. A run
    # that does not say what its own output looked like is not self-describing, which is the whole point of
    # `app/sanity.py`: arrival day has to be the SECOND run of a check, not the first.
    sanity_report: Path | None = None


@dataclass(frozen=True)
class DryRunReport:
    profiles_seen: int
    records_seen: int
    status: str
    report_path: Path
    rows_examined: int = 0
    rows_refused: int = 0


def _iter_located_rows(source_dir: Path) -> Iterator[tuple[str, dict | None, DeliveryShapeError | None]]:
    """Every records row with its source locator, plus a malformed line as data rather than an abort.

    A truncated JSONL line is the likeliest real failure, and refusing the whole file because of it is the
    opposite of what the 06:00 operator needs. Yielding the error lets a strict load raise exactly as
    before while a quarantine load stages the one bad line and keeps reading.
    """
    jsonl = source_dir / "records.jsonl"
    csv_path = source_dir / "records.csv"
    if jsonl.is_file():
        with jsonl.open(encoding="utf-8") as stream:
            for line_no, line in enumerate(stream, start=1):
                locator = f"records.jsonl:{line_no}"
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, RecursionError) as exc:
                    # `RecursionError` is a `RuntimeError`, not a `ValueError`: a deeply nested JSON line
                    # raises it, and without catching it here one ~20 KB line killed the whole import in
                    # strict AND quarantine mode, so quarantine's "every refused row is on disk" invariant
                    # never ran. It is a refused row with a named reason, like any other malformed line.
                    yield locator, None, DeliveryShapeError(
                        f"records.jsonl line {line_no} is not JSON ({type(exc).__name__}: {exc})",
                        code="malformed_row")
                    continue
                if not isinstance(row, dict):
                    yield locator, None, DeliveryShapeError(
                        f"records.jsonl line {line_no} is not a JSON object", code="malformed_row")
                    continue
                yield locator, row, None
    elif csv_path.is_file():
        with csv_path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or set(RECORD_COLUMNS) - set(reader.fieldnames):
                raise DeliveryShapeError(f"records.csv must have columns {RECORD_COLUMNS}",
                                         code="missing_columns",
                                         values={"missing": sorted(set(RECORD_COLUMNS) - set(reader.fieldnames or []))})
            for row in reader:
                locator = f"records.csv:{reader.line_num}"
                payload = row.get("payload") or "{}"
                try:
                    row["payload"] = json.loads(payload)
                except (ValueError, RecursionError) as exc:
                    yield locator, None, DeliveryShapeError(
                        f"records.csv payload must be JSON ({type(exc).__name__}: {exc})",
                        code="malformed_row")
                    continue
                yield locator, row, None
    else:
        raise DeliveryShapeError("delivery needs records.jsonl or records.csv", code="missing_records")


def _iter_rows(source_dir: Path) -> Iterator[dict]:
    """Strict rows iterator: the first malformed line raises, exactly as the importer has always done."""
    for _locator, row, error in _iter_located_rows(source_dir):
        if error is not None:
            raise error
        if row is not None:
            yield row


def _map_raw_row(spec: MappingSpec, raw: dict,
                 locator: str) -> tuple[dict | None, DeliveryShapeError | None, set[str]]:
    """One raw vendor row -> `(canonical row, refusal, matched rule ids)`, the single mapping-to-envelope seam.

    Shared by the streaming importer and `retry_quarantine`, so a quarantined row re-mapped after a mapping
    revision goes through exactly the same translation, required-target check and payload parsing as it did
    on the first pass. The raw vendor row is never mutated: the target is a fresh dict.
    """
    try:
        target, matched = spec.apply_with_matches(raw)
    except MappingError as exc:
        return None, DeliveryShapeError(str(exc), code="mapping_refused", values={"locator": locator}), set()
    missing = [field for field in REQUIRED_ENVELOPE_TARGETS if target.get(field) in (None, "")]
    if missing:
        return None, DeliveryShapeError(
            f"{locator} supplies no value for required envelope field(s) {missing}; the mapping "
            f"{spec.version} must map every one of them for every row, and an empty vendor cell is not a "
            f"default",
            code="missing_required_target", values={"locator": locator, "missing": missing}), matched
    target["source"] = "delivery"
    payload = target.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, RecursionError) as exc:
            return None, DeliveryShapeError(
                f"{locator} payload is a string that is not JSON ({type(exc).__name__}: {exc}); a CSV "
                f"payload cell must hold a JSON object",
                code="invalid_payload", values={"locator": locator}), matched
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return None, DeliveryShapeError(
            f"{locator} payload is a JSON {type(payload).__name__}, not an object",
            code="invalid_payload", values={"locator": locator}), matched
    target["payload"] = payload
    return target, None, matched


def _iter_mapped_rows(source_dir: Path, spec: MappingSpec,
                      rule_matches: dict[str, int]) -> Iterator[tuple[str, dict | None, DeliveryShapeError | None]]:
    """Raw delivery rows -> canonical envelope rows, through the declarative mapping.

    This is the arrival-day path: the vendor's spelling is translated by a `lookup_valueset` rule BEFORE the
    envelope is bound, so a `record_type` or `topic` this code does not declare is mapped into one it does
    rather than refused. Every failure an operator can act on is named with its locator:

    * an unknown source path or a value-set miss is the mapping's own `MappingError` text (which names the
      offending value and the known set);
    * a required envelope field with no rule at all is refused before any row is read (`require_targets`),
      and a rule that supplies no value for THIS row is refused here by field name;
    * a file whose layout cannot be read is refused by `landing.iter_located_rows` with its format named.

    `rule_matches` is filled with each rule's matched-row count so the caller can refuse a rule that matched
    nothing anywhere -- a source path that exists nowhere is a typo, not a column the vendor left out.

    A refused row is yielded WITH its raw vendor row, so a quarantine run keeps a row `retry-quarantine`
    can re-map after a mapping revision instead of an entry with nothing to re-attempt.
    """
    try:
        located = landing.iter_located_rows(source_dir, skip=spec.ignores_file)
        for locator, row, error in located:
            if error is not None:
                yield locator, None, DeliveryShapeError(error, code="malformed_row",
                                                        values={"locator": locator})
                continue
            if row is None:
                continue
            target, refusal, matched = _map_raw_row(spec, row, locator)
            for rule_id in matched:
                rule_matches[rule_id] = rule_matches.get(rule_id, 0) + 1
            if refusal is not None:
                yield locator, row, refusal
                continue
            yield locator, target, None
    except landing.LandingError as exc:
        # A layout the mapping cannot express, a binary file, or a file that yielded no rows. Refused by
        # name, never skipped: importing nothing from a 700 000-row delivery is the silent failure.
        raise DeliveryShapeError(str(exc), code="unsupported_layout") from exc


def _rules_matched_nothing(spec: MappingSpec, rule_matches: dict[str, int],
                           *, optional: bool = False) -> list[str]:
    """Rules whose source path appeared in no row, excluding alternatives for a target that was mapped.

    Delegates to the mapping's own rule (one implementation, so the import and the dry run cannot disagree).
    `optional=True` returns the rules the mapping declared the delivery may not carry at all: those are
    reported by name in the run's sanity report instead of refusing the import.
    """
    return spec.rules_matched_nothing(rule_matches, optional=optional)


def _read_profiles(source_dir: Path, refusals: RefusalLog | None = None) -> dict[str, dict]:
    """Read the profile bindings. With a `refusals` log, a bad ROW is staged and the rest is kept.

    A missing header, a missing file or an empty file is still file-level: there is no binding at all, so
    no load can proceed and the caller decides. A single unsafe or duplicated row is different -- with a
    quarantine log the other 49 999 people must still be loadable, and a record bound to the skipped key
    is then refused as an unresolvable reference in its own right.
    """
    path = source_dir / "profiles.csv"
    if not path.is_file():
        raise DeliveryShapeError("delivery needs profiles.csv", code="missing_profiles")
    profiles: dict[str, dict] = {}
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or set(PROFILE_COLUMNS) - set(reader.fieldnames):
            raise DeliveryShapeError(f"profiles.csv must have columns {PROFILE_COLUMNS}",
                                     code="missing_columns",
                                     values={"missing": sorted(set(PROFILE_COLUMNS) - set(reader.fieldnames or []))})
        for row in reader:
            locator = f"profiles.csv:{reader.line_num}"
            try:
                key = (row.get("source_person_id") or "").strip()
                if not key:
                    raise DeliveryShapeError("every profile needs a source_person_id",
                                             code="unbound_source_person", values={"source_person_id": ""})
                if key in profiles:
                    raise DeliveryShapeError(f"duplicate source_person_id {key!r}",
                                             code="duplicate_profile_key",
                                             values={"source_person_id": key})
                row = dict(row)
                row["persona_id"] = _safe_persona_id(row.get("persona_id") or "", key)
                # The source person id becomes SQL's `NVARCHAR(128)` key; the label becomes `NVARCHAR(256)`.
                # Checked here, before any write, so both engines refuse the same corpus with a named error
                # instead of SQL rolling the whole load back with Msg 8152.
                for field, limit in (("source_person_id", ID_MAX_LENGTH), ("label", LABEL_MAX_LENGTH)):
                    value = str(row.get(field) or "")
                    if len(value) > limit:
                        raise DeliveryShapeError(
                            f"profile {key!r} has a {len(value)}-character {field}; the limit is {limit} "
                            f"characters (the width of the SQL column)",
                            code="field_too_long",
                            values={"field": field, "length": len(value), "limit": limit, "locator": locator})
            except DeliveryShapeError as exc:
                if refusals is None:
                    raise
                refusals.record(Refusal.from_error(exc, locator=locator, raw=dict(row)))
                continue
            profiles[key] = row
    if not profiles:
        raise DeliveryShapeError("profiles.csv is empty", code="empty_profiles")
    return profiles


def _normalize_payload(record_type: str, payload: dict) -> tuple[dict, dict[str, int]]:
    aliases = PAYLOAD_KEY_ALIASES.get(record_type, {})
    normalized: dict = {}
    used: dict[str, int] = {}
    for key, value in payload.items():
        canonical = aliases.get(key, key)
        if canonical in normalized and normalized[canonical] != value:
            raise DeliveryShapeError(f"conflicting payload aliases for {canonical!r}")
        normalized[canonical] = value
        if key != canonical:
            alias = f"{key}->{canonical}"
            used[alias] = used.get(alias, 0) + 1
    return normalized, used


def _bind_record(row: dict, profiles: dict[str, dict]) -> Record:
    record_id = row.get("record_id")
    missing = [column for column in RECORD_COLUMNS if column not in row and column != "payload"]
    if missing:
        raise DeliveryShapeError(f"record missing columns {missing}", code="missing_columns",
                                 values={"missing": missing}, record_id=record_id)
    key = str(row.get("source_person_id") or "").strip()
    if key not in profiles:
        raise DeliveryShapeError(f"record {record_id!r} has no bound source person",
                                 code="unbound_source_person",
                                 values={"source_person_id": key}, record_id=record_id)
    payload = row.get("payload")
    record_type = row.get("record_type")
    if record_type not in RECORD_TYPE_ALIASES:
        raise DeliveryShapeError(
            f"record {record_id!r} has unknown record_type {record_type!r}; known record types: "
            f"{sorted(RECORD_TYPE_ALIASES)}",
            code="unknown_record_type", values={"record_type": record_type}, record_id=record_id)
    # Fail closed on an undeclared topic: the purpose filter would silently drop it and the briefing would
    # look like a person who has nothing. The message names the value and the known set, which is the
    # mapping instruction for arrival day.
    topic = row.get("topic")
    known_topics = declared_topics()
    if topic not in known_topics:
        raise DeliveryShapeError(
            f"record {record_id!r} has unknown topic {topic!r}; known topics: {sorted(known_topics)}",
            code="unknown_topic", values={"topic": topic}, record_id=record_id)
    if not isinstance(payload, dict):
        raise DeliveryShapeError(f"record {record_id!r} payload must be an object",
                                 code="invalid_payload", record_id=record_id)
    normalized, _used = _normalize_payload(RECORD_TYPE_ALIASES[record_type], payload)
    try:
        return derived_record_state(Record.model_validate({**row, "record_type": RECORD_TYPE_ALIASES[record_type],
                                                           "payload": normalized, "source": "delivery",
                                                           "source_person_id": key}))
    except ValidationError as exc:
        # A Pydantic failure is a refusal with the same shape as every other one, so a quarantine load can
        # stage it by code and the report can name the field and the offending value.
        raise _envelope_error(record_id if isinstance(record_id, str) else None, exc) from exc


def _bind_encounter(row: dict, profiles: dict[str, dict], known_roles: dict,
                    seen: set[str]) -> dict:
    encounter_id = _safe_persona_id(row.get("encounter_id") or "", "")
    source_person_id = (row.get("source_person_id") or "").strip()
    role = (row.get("role") or "").strip()
    purpose = (row.get("purpose") or "").strip()
    if encounter_id in seen:
        raise DeliveryShapeError(f"duplicate encounter_id {encounter_id!r}", code="duplicate_encounter_id",
                                 values={"encounter_id": encounter_id})
    profile = profiles.get(source_person_id)
    if profile is None:
        raise DeliveryShapeError(f"encounter {encounter_id!r} has no bound source person",
                                 code="unbound_source_person",
                                 values={"source_person_id": source_person_id})
    if role not in known_roles or purpose not in palvelukontekstit():
        raise DeliveryShapeError(
            f"encounter {encounter_id!r} has an unknown role or purpose; known roles {sorted(known_roles)}, "
            f"known purposes {sorted(palvelukontekstit())}",
            code="unknown_role_or_purpose", values={"role": role, "purpose": purpose})
    if role not in palvelukonteksti(purpose).roles:
        raise DeliveryShapeError(f"role {role!r} is not permitted for purpose {purpose!r}",
                                 code="role_not_permitted",
                                 values={"role": role, "purpose": purpose})
    holders_raw = (row.get("consent_holders") or "").strip()
    if holders_raw:
        holders = tuple(part.strip() for part in re.split(r"[,;]", holders_raw) if part.strip())
        unknown = [holder for holder in holders if holder not in ENCOUNTER_HOLDERS]
        if unknown or not holders:
            raise DeliveryShapeError(
                f"encounter {encounter_id!r} declares consent_holders {unknown or holders_raw!r}; "
                f"a lens that covers no known holder, or an unknown one, is a permission axis "
                f"this shape will not guess at; known holders {sorted(ENCOUNTER_HOLDERS)}",
                code="unknown_consent_holder", values={"consent_holders": holders_raw})
    else:
        holders = ("self",)
    life_stage = (row.get("life_stage") or "").strip() or "adult"
    if life_stage not in ENCOUNTER_LIFE_STAGES:
        raise DeliveryShapeError(
            f"encounter {encounter_id!r} declares life_stage {life_stage!r}, which is not a "
            f"declared life stage; known life stages {sorted(ENCOUNTER_LIFE_STAGES)}",
            code="unknown_life_stage", values={"life_stage": life_stage})
    seen.add(encounter_id)
    # The labels go into `NVARCHAR(256)` columns in SQL; without this check a 300-character label imported
    # locally and then killed the whole SQL load transaction with Msg 8152. Named here, before any write.
    for column in ("example_label", "demo_name"):
        value = str(row.get(column) or "")
        if len(value) > LABEL_MAX_LENGTH:
            raise DeliveryShapeError(
                f"encounter {encounter_id!r} has a {len(value)}-character {column}; the limit is "
                f"{LABEL_MAX_LENGTH} characters (the width of the SQL column)",
                code="field_too_long",
                values={"field": column, "length": len(value), "limit": LABEL_MAX_LENGTH})
    # No `persona_id` here: `encounters` holds the bound person by `source_person_id` in BOTH engines, and
    # the persona is resolved through `people` at read time. Carrying a second person key in the index is
    # how the SQLite encounters table and Azure SQL's disagreed about where the binding lives (D10).
    return {**row, "encounter_id": encounter_id, "source_person_id": source_person_id,
            "role": role, "purpose": purpose,
            "consent_holders": holders, "life_stage": life_stage}


def _read_encounters(source_dir: Path, profiles: dict[str, dict],
                     refusals: RefusalLog | None = None) -> list[dict]:
    """Read the bounded operator-selected (person, role, purpose) demo mappings.

    This is not one encounter per corpus profile: keeping the owner file capped
    prevents GET /api/encounters from walking the 50k-profile delivery.

    With a `refusals` log a single bad catalogue row is staged and the rest is kept; the bounded-limit
    guard is file-level and still raises, because it is a decision about the whole file.
    """
    path = source_dir / "encounters.csv"
    if not path.is_file():
        return []
    rows: list[dict] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or set(ENCOUNTER_COLUMNS) - set(reader.fieldnames):
            raise DeliveryShapeError(f"encounters.csv must have columns {ENCOUNTER_COLUMNS}",
                                     code="missing_columns",
                                     values={"missing": sorted(set(ENCOUNTER_COLUMNS) - set(reader.fieldnames or []))})
        known_roles = policy_roles()
        for row in reader:
            locator = f"encounters.csv:{reader.line_num}"
            try:
                rows.append(_bind_encounter(row, profiles, known_roles, seen))
            except DeliveryShapeError as exc:
                if refusals is None:
                    raise
                refusals.record(Refusal.from_error(exc, locator=locator, raw=dict(row)))
                continue
            if len(rows) > MAX_OPERATOR_ENCOUNTERS:
                raise DeliveryShapeError(f"encounters.csv exceeds the bounded limit of {MAX_OPERATOR_ENCOUNTERS}",
                                         code="encounters_over_limit")
    return rows


def _read_appointments(source_dir: Path, profiles: dict[str, dict], encounter_ids: set[str],
                       refusals: RefusalLog | None = None) -> list[dict]:
    """Read `appointments.csv` when the corpus carries one: validated rows in file order, else an empty list.

    A row must name a profiled person, a declared domain, an ISO date and (when it names one) an encounter the
    same corpus declares. A bad row is refused by name, or staged when a `refusals` log is given.
    """
    path = source_dir / "appointments.csv"
    if not path.is_file():
        return []
    rows: list[dict] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or set(APPOINTMENT_COLUMNS) - set(reader.fieldnames):
            raise DeliveryShapeError(f"appointments.csv must have columns {APPOINTMENT_COLUMNS}",
                                     code="missing_columns",
                                     values={"missing": sorted(set(APPOINTMENT_COLUMNS) - set(reader.fieldnames or []))})
        for row in reader:
            locator = f"appointments.csv:{reader.line_num}"
            try:
                rows.append(_bind_appointment(row, profiles, encounter_ids, seen))
            except DeliveryShapeError as exc:
                if refusals is None:
                    raise
                refusals.record(Refusal.from_error(exc, locator=locator, raw=dict(row)))
    return rows


def _bind_appointment(row: dict, profiles: dict[str, dict], encounter_ids: set[str], seen: set[str]) -> dict:
    appointment_id = (row.get("appointment_id") or "").strip()
    person = (row.get("source_person_id") or "").strip()
    domain = (row.get("domain") or "").strip()
    when = (row.get("scheduled_on") or "").strip()
    encounter_id = (row.get("encounter_id") or "").strip()
    if not appointment_id or len(appointment_id) > ID_MAX_LENGTH:
        raise DeliveryShapeError("appointments.csv needs an appointment_id of at most 128 characters",
                                 code="invalid_appointment_id", values={"appointment_id": appointment_id})
    if appointment_id in seen:
        raise DeliveryShapeError(f"duplicate appointment_id {appointment_id!r}", code="duplicate_appointment_id",
                                 values={"appointment_id": appointment_id})
    if person not in profiles:
        raise DeliveryShapeError(f"appointment {appointment_id!r} names an unknown person {person!r}",
                                 code="unknown_source_person", values={"source_person_id": person})
    if domain not in APPOINTMENT_DOMAINS:
        raise DeliveryShapeError(f"appointment {appointment_id!r} has an undeclared domain {domain!r}",
                                 code="invalid_domain", values={"domain": domain})
    try:
        scheduled_on = date.fromisoformat(when)
    except ValueError as exc:
        raise DeliveryShapeError(f"appointment {appointment_id!r} has no ISO date: {when!r}",
                                 code="invalid_date", values={"scheduled_on": when}) from exc
    if encounter_id and encounter_id not in encounter_ids:
        raise DeliveryShapeError(f"appointment {appointment_id!r} names an undeclared encounter {encounter_id!r}",
                                 code="unknown_encounter", values={"encounter_id": encounter_id})
    seen.add(appointment_id)
    return {"appointment_id": appointment_id, "source_person_id": person, "domain": domain,
            "scheduled_on": scheduled_on.isoformat(),
            "status_text": (row.get("status_text") or "").strip() or None,
            "urgency_text": (row.get("urgency_text") or "").strip() or None,
            "specialty": (row.get("specialty") or "").strip() or None,
            "contact_record_id": (row.get("contact_record_id") or "").strip() or None,
            "encounter_id": encounter_id or None}


def _validate_dry_run_record(row: dict, profiles: dict[str, dict], seen_record_ids: set[str]) -> Record:
    missing = [column for column in RECORD_COLUMNS if column not in row]
    if missing:
        raise DeliveryShapeError(f"record missing columns {missing}", code="missing_columns",
                                 values={"missing": missing}, record_id=row.get("record_id"))
    record_id, source_person_id = row.get("record_id"), row.get("source_person_id")
    if not isinstance(record_id, str) or not record_id.strip():
        raise DeliveryShapeError("record_id must be a non-empty string", code="missing_record_id")
    if not isinstance(source_person_id, str) or not source_person_id.strip():
        raise DeliveryShapeError(f"record {record_id!r} has no bound source person",
                                 code="unbound_source_person", record_id=record_id)
    source_person_id = source_person_id.strip()
    if source_person_id not in profiles:
        raise DeliveryShapeError(f"record {record_id!r} has no bound source person",
                                 code="unbound_source_person",
                                 values={"source_person_id": source_person_id}, record_id=record_id)
    if record_id in seen_record_ids:
        raise DeliveryShapeError(f"duplicate record ID {record_id!r}", code="duplicate_record_id",
                                 values={"record_id": record_id}, record_id=record_id)
    seen_record_ids.add(record_id)
    record_type = row.get("record_type")
    if not isinstance(record_type, str) or not record_type.strip():
        raise DeliveryShapeError(f"record {record_id!r} has no record_type", code="missing_record_type",
                                 record_id=record_id)
    known_topics = declared_topics()
    if row.get("topic") not in known_topics:
        raise DeliveryShapeError(
            f"record {record_id!r} has unknown topic {row.get('topic')!r}; known topics: "
            f"{sorted(known_topics)}",
            code="unknown_topic", values={"topic": row.get("topic")}, record_id=record_id)
    try:
        # The reconciliation pass is the ONE place an undeclared `record_type` must be representable: its
        # job is to report vendor spellings as warnings, so it opts out of the model's record-type rule by
        # validation context. The write path (`_bind_record`, the SQL loader) validates with no context.
        return Record.model_validate({**row, "record_id": record_id, "record_type": record_type.strip(),
                                      "source": "delivery", "source_person_id": source_person_id},
                                     context={"allow_undeclared_record_type": True})
    except ValidationError as exc:
        raise _envelope_error(record_id, exc) from exc


def dry_run_delivery(source_dir: Path, output_dir: Path) -> DryRunReport:
    """Reconcile the delivery and write the report BEFORE any refusal is raised.

    The report is the deliverable, not the exception. It records how many rows were examined and refused,
    and for each distinct refusal reason the count plus up to five example locators with the offending
    values, so an operator at 06:00 can tell one bad row from fifty thousand without re-running anything.

    A refusal is raised only after the report is on disk. Warnings (an unknown payload key on a known type)
    do not raise. A rendered type whose payload maps to nothing is a hard failure, and so is any envelope,
    binding, duplicate or vocabulary miss -- unknown type and key NAMES that still validate are the only
    warnings, which is what this docstring used to claim and the code did not do.

    This pass never imports records.
    """
    refusals = RefusalLog()
    profiles: dict[str, dict] = {}
    seen_record_ids: set[str] = set()
    records_seen = 0
    rows_examined = 0
    type_aliases: dict[str, int] = {}
    payload_aliases: dict[str, dict[str, int]] = {}
    unmapped_types: dict[str, int] = {}
    unmapped_keys_by_unmapped_type: dict[str, dict[str, int]] = {}
    by_type: dict[str, dict] = {}

    try:
        profiles = _read_profiles(source_dir, refusals=refusals)
    except DeliveryShapeError as exc:
        refusals.record(Refusal.from_error(exc, locator="profiles.csv"))
    if profiles:
        try:
            _read_encounters(source_dir, profiles, refusals=refusals)
        except DeliveryShapeError as exc:
            refusals.record(Refusal.from_error(exc, locator="encounters.csv"))
        try:
            located = _iter_located_rows(source_dir)
            for locator, row, row_error in located:
                rows_examined += 1
                if row_error is not None:
                    refusals.record(Refusal.from_error(row_error, locator=locator))
                    continue
                if row is None:
                    continue
                try:
                    record = _validate_dry_run_record(row, profiles, seen_record_ids)
                except DeliveryShapeError as exc:
                    refusals.record(Refusal.from_error(exc, locator=locator, raw=row))
                    continue
                records_seen += 1
                raw_type = record.record_type
                record_type = RECORD_TYPE_ALIASES.get(raw_type)
                if record_type is None:
                    unmapped_types[raw_type] = unmapped_types.get(raw_type, 0) + 1
                    type_keys = unmapped_keys_by_unmapped_type.setdefault(raw_type, {})
                    for key in record.payload:
                        type_keys[key] = type_keys.get(key, 0) + 1
                    continue
                if raw_type != record_type:
                    alias = f"{raw_type}->{record_type}"
                    type_aliases[alias] = type_aliases.get(alias, 0) + 1

                normalized, used_aliases = _normalize_payload(record_type, record.payload)
                alias_counts = payload_aliases.setdefault(record_type, {})
                for alias, count in used_aliases.items():
                    alias_counts[alias] = alias_counts.get(alias, 0) + count
                item = by_type.setdefault(record_type, {
                    "rows_seen": 0, "missing_render_keys": {}, "unmapped_payload_keys": {},
                    "render_keys_present": set(),
                    "known_non_rendered_keys": {}, "useful_set_miss_rows": 0,
                    "content_maps_to_nothing": 0, "content_maps_to_nothing_record_ids": [],
                })
                item["rows_seen"] += 1
                render_keys = RENDER_PAYLOAD_KEYS[record_type]
                visible_keys = render_keys | ({"nimi", "näyttötunniste"} if record_type == source.BASIC_INFO else set())
                renderable = any(
                    (isinstance(value, str) and bool(value.strip()))
                    or (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value))
                    for key, value in normalized.items() if key in visible_keys
                )
                if not renderable:
                    item["content_maps_to_nothing"] += 1
                    item["content_maps_to_nothing_record_ids"].append(record.record_id)
                # Which render keys this row CARRIES, accumulated per type. The finding is reported below as the keys
                # NO row carries, and the reason is a multi-valued type: one `mittaus` row is one measurement, so a
                # weight-only row lacking `syke` is not a gap in the delivery — the next row may be a pulse. Counting
                # per row made every partial-measurement corpus warn forever, which is noise an operator learns to
                # skim; the question the arrival audit actually asks is "can this corpus ever fill the Syke tile?".
                item["render_keys_present"].update(key for key in normalized if key in render_keys)
                for key in set(record.payload) - set(PAYLOAD_KEY_ALIASES[record_type]):
                    item["unmapped_payload_keys"][key] = item["unmapped_payload_keys"].get(key, 0) + 1
                for key in normalized.keys() - render_keys:
                    item["known_non_rendered_keys"][key] = item["known_non_rendered_keys"].get(key, 0) + 1
                useful = USEFUL_PAYLOAD_KEYS[record_type]
                useful_met = (bool(useful & normalized.keys()) if record_type == source.MEASUREMENT
                              else useful <= normalized.keys())
                if not useful_met:
                    item["useful_set_miss_rows"] += 1
        except DeliveryShapeError as exc:
            refusals.record(Refusal.from_error(exc, locator="records"))

    for record_type, item in by_type.items():
        # Reported with the row count, as before, so a reader sees "no row of these 40 carries `syke`" — which
        # is the shape of every remaining finding, because a key present in ANY row is supplied.
        item["missing_render_keys"] = {key: item["rows_seen"]
                                       for key in sorted(RENDER_PAYLOAD_KEYS[record_type]
                                                         - OPTIONAL_RENDER_KEYS.get(record_type, set()))
                                       if key not in item["render_keys_present"]}
        item.pop("render_keys_present")

    hard_misses = {record_type: item["content_maps_to_nothing_record_ids"]
                   for record_type, item in by_type.items() if item["content_maps_to_nothing"]}
    has_issues = bool(unmapped_types or unmapped_keys_by_unmapped_type) or any(
        item["missing_render_keys"] or item["unmapped_payload_keys"] or item["useful_set_miss_rows"]
        for item in by_type.values()
    )
    refusal_summary = refusals.as_dict()
    if refusals:
        status = "refused"
    elif hard_misses:
        status = "failed"
    elif has_issues:
        status = "warning"
    else:
        status = "ready"
    report = {
        "kind": "delivery-reconciliation",
        "scope": "source-wide ingestion audit; not a role-facing Picture",
        "imports_records": False,
        "status": status,
        "profiles_seen": len(profiles),
        "records_seen": records_seen,
        "rows_examined": rows_examined,
        "rows_refused": refusal_summary["rows_refused"],
        "refusals": refusal_summary["reasons"],
        "record_type_aliases_applied": dict(sorted(type_aliases.items())),
        "payload_key_aliases_applied": {key: dict(sorted(value.items()))
                                        for key, value in sorted(payload_aliases.items())},
        "unmapped_record_types": dict(sorted(unmapped_types.items())),
        "unmapped_payload_keys_by_unmapped_type": {
            key: dict(sorted(value.items())) for key, value in sorted(unmapped_keys_by_unmapped_type.items())
        },
        "types": dict(sorted(by_type.items())),
    }
    root = output_dir.resolve()
    report_path = (root / "results" / "delivery-reconciliation-report.json").resolve()
    if not report_path.is_relative_to(root):
        raise DeliveryShapeError("dry-run report path must stay inside its output directory",
                                 code="unsafe_output_path")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if refusals:
        # The report is on disk before this raise; `reconcile_delivery.py dry-run` prints its real path.
        raise DeliveryShapeError(refusals.message(), code="refused")
    if hard_misses:
        details = "; ".join(f"{record_type} ({len(record_ids)}): {', '.join(record_ids)}"
                             for record_type, record_ids in sorted(hard_misses.items()))
        raise DeliveryShapeError(f"rendered record payload maps to no displayable content: {details}",
                                 code="content_maps_to_nothing")
    return DryRunReport(len(profiles), records_seen, report["status"], report_path,
                        rows_examined=rows_examined, rows_refused=refusal_summary["rows_refused"])


def import_delivery(source_dir: Path, output_dir: Path, *, on_error: str = "strict",
                    fail_on_quarantine: bool = False,
                    mapping: MappingSpec | str | Path | None = None,
                    expected_profiles: int | None = None) -> ImportReport:
    """Stream the delivery into per-person files plus SQLite person/encounter indexes.

    `on_error="strict"` (the default) refuses the whole delivery on the first bad row, exactly as before.
    `on_error="quarantine"` is the opt-in alternative for a delivery nobody has seen: every row refused by
    envelope validation, an undeclared vocabulary value, or an unresolvable reference is staged in
    `<output_dir>/quarantine/quarantine.jsonl` with its locator, raw row and reason, and the remainder is
    loaded. `fail_on_quarantine` is the caller's explicit "treat any quarantine as failure"; a quarantine
    run is otherwise successful, non-zero only when nothing at all loaded.

    `mapping` is the arrival-day path and changes which files are read: given a mapping file (or an already
    loaded `MappingSpec`), the delivery may be ANY layout `landing.py` accepts -- a wide or long CSV, JSONL,
    one JSON array, one file per person, nested directories, a zip, a raw directory or a landing zone -- and
    the raw rows are mapped into the canonical envelope BEFORE binding. The mapping must state a rule for
    every `REQUIRED_ENVELOPE_TARGETS` field or this refuses before reading a row, and the PEOPLE are the
    distinct `source_person_id` values the mapping produces (persona_id = source_person_id), because a raw
    vendor delivery carries its person identifier in the records and not in a separate profiles.csv. Without
    `mapping`, nothing about the canonical path changes.
    """
    if on_error not in ("strict", "quarantine"):
        raise ValueError(f"unknown on_error mode {on_error!r}; use 'strict' or 'quarantine'")
    spec: MappingSpec | None = None
    if mapping is not None:
        spec = mapping if isinstance(mapping, MappingSpec) else MappingSpec.load(Path(mapping))
        # The whole point: a permission axis must never be defaulted. A missing rule is named here, before
        # the first byte, so a 258 MB delivery is not read to discover it.
        spec.require_targets(REQUIRED_ENVELOPE_TARGETS)
    quarantine = on_error == "quarantine"
    quarantine_dir = output_dir / "quarantine"
    refusals = RefusalLog(quarantine_dir / "quarantine.jsonl" if quarantine else None)
    # The checks stream in lockstep with the load. They REPORT here (the run's own account of what it
    # produced); `preview` is the step that refuses BEFORE an import. `expected_profiles` is the vendor's
    # own stated count when the operator has it.
    sanity = SanityAccumulator(complete_scan=True, expected_profiles=expected_profiles)
    profiles: dict[str, dict] = {}
    encounter_rows: list[dict] = []
    counts: dict[str, int] = {}
    shape: dict[str, dict] = {}
    basics_seen: dict[str, str] = {}
    rule_matches: dict[str, int] = {}
    person_stream = None
    person_stream_id: str | None = None
    connection = None
    try:
        if spec is None:
            profiles = _read_profiles(source_dir, refusals=refusals if quarantine else None)
        people_dir = output_dir / _PEOPLE_DIR
        people_dir.mkdir(parents=True, exist_ok=True)
        # A previous import may have removed profiles; clear only this adapter's output.
        for stale_person_file in people_dir.glob("*.jsonl"):
            stale_person_file.unlink()
        index_path = output_dir / _INDEX_NAME
        if index_path.exists():
            index_path.unlink()

        encounter_rows = (_read_encounters(source_dir, profiles, refusals=refusals if quarantine else None)
                          if spec is None else [])
        counts = dict.fromkeys(profiles, 0)
        # Source-wide ingestion audit only; never forward these population counts into a role-facing Picture.
        shape = {record_type: {"rows_seen": 0, "useful_set_met": 0, "unexpected_keys": {},
                               "render_keys_present": dict.fromkeys(sorted(keys), 0),
                               "render_keys_missing": dict.fromkeys(sorted(keys), 0)}
                 for record_type, keys in RENDER_PAYLOAD_KEYS.items()}
        connection = sqlite3.connect(index_path)
        # SQLite defaults `foreign_keys` OFF for backwards compatibility, and the pragma is per CONNECTION:
        # the writer turns it on here, `retry_quarantine` turns it on for its own connection, and readers do
        # not need it (it gates DML). Without this the FK clauses below would parse and do nothing.
        connection.execute("PRAGMA foreign_keys = ON")
        # The delivered corpus's index is a LOCAL IMPORT ARTIFACT; the authoritative shape is Azure SQL's
        # (`sqldelivery.SCHEMA`), and these tables now carry the same keys, NOT NULLs and enum CHECKs. The
        # one deliberate difference: records live in per-person JSONL here and in a `records` table there,
        # so a rule over records (the JSON envelope check, the one-demografia rule) is either a table there
        # or a `seen_*` entry here.
        # SQLite's own historical quirk: a non-INTEGER PRIMARY KEY does NOT imply NOT NULL, so
        # `persona_id TEXT PRIMARY KEY` accepted a NULL "person". Every key is declared NOT NULL here.
        connection.execute("CREATE TABLE people (persona_id TEXT NOT NULL PRIMARY KEY, "
                           "source_person_id TEXT NOT NULL UNIQUE, label TEXT, path TEXT NOT NULL, "
                           "CHECK (persona_id <> ''), CHECK (source_person_id <> ''), "
                           # The importer writes exactly `people/<persona_id>.jsonl` and the reader accepts
                           # nothing else, so the DDL itself refuses a hand-built row that points anywhere
                           # else (`_PEOPLE_PATH_EXPRESSION` is also why `people/*.jsonl` was not enough).
                           # `DeliveryIndex._person_file` re-checks containment at read time, because an
                           # index can predate this CHECK and a path can still name another persona's file.
                           f"CHECK ({_PEOPLE_PATH_EXPRESSION}))")
        connection.execute(
            "CREATE TABLE encounters (encounter_id TEXT NOT NULL PRIMARY KEY, "
            "source_person_id TEXT NOT NULL REFERENCES people(source_person_id), "
            f"role TEXT NOT NULL CHECK (role IN ({ENCOUNTER_CHECK_SQL['role']})), "
            f"purpose TEXT NOT NULL CHECK (purpose IN ({ENCOUNTER_CHECK_SQL['purpose']})), "
            "example_label TEXT, demo_name TEXT, "
            "consent_holders TEXT CHECK (consent_holders IS NULL OR consent_holders IN "
            f"({ENCOUNTER_CHECK_SQL['consent_holders']})), "
            "life_stage TEXT CHECK (life_stage IS NULL OR life_stage IN "
            f"({ENCOUNTER_CHECK_SQL['life_stage']})))")
        connection.execute(
            "CREATE TABLE appointments (appointment_id TEXT NOT NULL PRIMARY KEY, "
            "source_person_id TEXT NOT NULL REFERENCES people(source_person_id), "
            f"domain TEXT NOT NULL CHECK (domain IN ({', '.join(repr(d) for d in APPOINTMENT_DOMAINS)})), "
            "scheduled_on TEXT NOT NULL, status_text TEXT, urgency_text TEXT, specialty TEXT, "
            "contact_record_id TEXT, encounter_id TEXT REFERENCES encounters(encounter_id))")
        connection.execute("CREATE INDEX ix_appointments_day ON appointments (scheduled_on, encounter_id)")
        connection.execute("CREATE TABLE seen_records (record_id TEXT NOT NULL PRIMARY KEY)")
        # "Exactly one demografia perustieto per person" cannot be a constraint over records that are files,
        # so the import records the first one here and the PRIMARY KEY refuses a second. The Python
        # `basics_seen` check below is what makes the refusal a named DeliveryShapeError; this table is what
        # makes a hand-built index unable to hold two.
        connection.execute("CREATE TABLE seen_basics (source_person_id TEXT NOT NULL PRIMARY KEY)")
        # Stamped so `DeliveryIndex` can refuse an index from a different shape by name.
        # pi-lens-ignore: sql-injection -- reviewed: PRAGMA takes a module integer constant, never runtime input
        connection.execute(f"PRAGMA user_version = {INDEX_SCHEMA_VERSION}")
        # Streamed one record at a time, but the write handle is held while consecutive records share
        # a person: one open/append/close per PERSON instead of per RECORD, which at 50k profiles is
        # ~700k file operations removed. Rotation is on the person key, so an interleaved file still
        # produces correct output — it simply costs more opens, at worst the previous behaviour.
        located = (_iter_located_rows(source_dir) if spec is None
                   else _iter_mapped_rows(source_dir, spec, rule_matches))
        for locator, row, row_error in located:
            if row_error is not None:
                if not quarantine:
                    raise row_error
                # `row` is the RAW vendor row when the mapping refused it and None when the row itself could
                # not be parsed; keeping it is what makes `retry-quarantine --mapping` able to recover.
                refusals.record_error(row_error, locator=locator, raw=row)
                sanity.refuse(row_error.code, str(row_error), locator, row_error.values)
                continue
            if row is None:
                continue
            key = str(row.get("source_person_id") or "").strip()
            if spec is not None and key not in profiles:
                # In mapping mode the mapped source_person_id defines the people: the first row naming a
                # person creates them. The persona id is the same string, validated as a filename.
                try:
                    profiles[key] = {"persona_id": _safe_persona_id(key, key), "label": ""}
                except DeliveryShapeError as exc:
                    if not quarantine:
                        raise
                    refusals.record_error(exc, locator=locator, raw=row)
                    sanity.refuse(exc.code, str(exc), locator, exc.values)
                    continue
            profile = profiles.get(key)
            if profile is None:
                # An unbound key must fail loudly, never be dropped silently.
                error = DeliveryShapeError(f"record {row.get('record_id')!r} has no bound source person",
                                           code="unbound_source_person",
                                           values={"source_person_id": key}, record_id=row.get("record_id"))
                if not quarantine:
                    raise error
                refusals.record_error(error, locator=locator, raw=row)
                sanity.refuse(error.code, str(error), locator, error.values)
                continue
            try:
                record = _bind_record(row, profiles)
            except DeliveryShapeError as exc:
                if not quarantine:
                    raise
                refusals.record_error(exc, locator=locator, raw=row)
                sanity.refuse(exc.code, str(exc), locator, exc.values)
                continue
            # The checks see the MAPPED envelope, row by row, before anything is written for it. Cheap and
            # bounded: a handful of counters and the exact set of mapped person ids.
            sanity.observe(row, locator)
            # `source.perustieto_for` binds ONE demografia Perustieto per person and raises on two, so an
            # ambiguous person is refused here (or has the extra row quarantined) instead of failing later at
            # request time -- one ambiguous person in 50 000 must not be a 500 on their briefing.
            if record.record_type == source.BASIC_INFO and record.topic == "demografia":
                first = basics_seen.get(key)
                if first is not None:
                    error = DeliveryShapeError(
                        f"person {key!r} has more than one demografia perustieto "
                        f"({first!r} and {record.record_id!r}); no single identity can be bound",
                        code="ambiguous_basics", values={"source_person_id": key, "first": first},
                        record_id=record.record_id)
                    if not quarantine:
                        raise error
                    refusals.record_error(error, locator=locator, raw=row)
                    sanity.refuse(error.code, str(error), locator, error.values)
                    continue
                basics_seen[key] = record.record_id
                binds_basics = True
            else:
                binds_basics = False
            try:
                connection.execute("INSERT INTO seen_records VALUES (?)", (record.record_id,))
            except sqlite3.IntegrityError:
                error = DeliveryShapeError(f"duplicate record ID {record.record_id!r}",
                                           code="duplicate_record_id",
                                           values={"record_id": record.record_id}, record_id=record.record_id)
                if not quarantine:
                    raise error from None
                refusals.record_error(error, locator=locator, raw=row)
                sanity.refuse(error.code, str(error), locator, error.values)
                continue
            item = shape[record.record_type]
            payload_keys = record.payload.keys()
            item["rows_seen"] += 1
            useful = USEFUL_PAYLOAD_KEYS[record.record_type]
            useful_met = (bool(useful & payload_keys) if record.record_type == source.MEASUREMENT
                          else useful <= payload_keys)
            if useful_met and (record.record_type != source.LAB_RESULT or
                               bool({"arvo", "tulkinta"} & payload_keys)):
                item["useful_set_met"] += 1
            for field in RENDER_PAYLOAD_KEYS[record.record_type]:
                item["render_keys_present"][field] += field in payload_keys
                item["render_keys_missing"][field] += field not in payload_keys
            for field in payload_keys - RENDER_PAYLOAD_KEYS[record.record_type]:
                item["unexpected_keys"][field] = item["unexpected_keys"].get(field, 0) + 1
            persona_id = (profile.get("persona_id") or key).strip()
            if persona_id != person_stream_id:
                if person_stream is not None:
                    person_stream.close()
                person_stream = (people_dir / f"{persona_id}.jsonl").open("a", encoding="utf-8")
                person_stream_id = persona_id
            if person_stream is None:
                raise RuntimeError("person output stream was not opened")
            person_stream.write(json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n")
            if binds_basics:
                # Only once the row is actually written: an import that refused the record must not leave a
                # `seen_basics` entry that would refuse the person's real demografia on the next attempt.
                connection.execute("INSERT INTO seen_basics VALUES (?)", (key,))
            counts[key] = counts.get(key, 0) + 1
        for key, profile in profiles.items():
            persona_id = (profile.get("persona_id") or key).strip()
            people_path = people_dir / f"{persona_id}.jsonl"
            if not people_path.exists():
                people_path.touch()  # indexed people always resolve to a file
            connection.execute("INSERT INTO people VALUES (?, ?, ?, ?)",
                               (persona_id, key, profile.get("label") or "", f"{_PEOPLE_DIR}/{persona_id}.jsonl"))
        for row in encounter_rows:
            connection.execute("INSERT INTO encounters VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (row["encounter_id"], row["source_person_id"],
                                row["role"], row["purpose"], row["example_label"], row["demo_name"],
                                ",".join(row.get("consent_holders") or ("self",)),
                                row.get("life_stage") or "adult"))
        for row in _read_appointments(source_dir, profiles, {r["encounter_id"] for r in encounter_rows}):
            connection.execute("INSERT INTO appointments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               tuple(row[column] for column in APPOINTMENT_COLUMNS))
        connection.commit()
    finally:
        if person_stream is not None:
            person_stream.close()
        if connection is not None:
            connection.close()
        refusals.close()
    total = sum(counts.values())
    if spec is not None:
        silent = _rules_matched_nothing(spec, rule_matches)
        if silent:
            # A rule that matched no row anywhere is the mapping's own typo, and the old failure mode was
            # silence: the rule simply did nothing while the import reported success. Raised before the
            # success reports are written, in every mode: it is a defect of the FILE, not of one row.
            raise DeliveryShapeError(
                f"mapping {spec.version} has rules that matched no row: {'; '.join(silent)}; a source_path "
                f"that exists nowhere is a typo, not a column the vendor left empty",
                code="rules_matched_nothing", values={"rules": silent})
    report_path = output_dir / "results" / "payload-shape-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({"kind": "delivery-payload-shape", "types": shape},
                                      ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sanity_path = report_path.parent / "delivery-sanity-report.json"
    sanity_document = sanity.as_dict()
    if spec is not None:
        optional_missing = _rules_matched_nothing(spec, rule_matches, optional=True)
        if optional_missing:
            # A declared-optional column the delivery did not carry. Not a refusal -- the mapping said the
            # vendor might not send it -- but never silent either: the run states what it did not map.
            sanity_document["findings"].append({
                "check": "optional_rules_matched_nothing", "severity": "warning",
                "summary": (f"{len(optional_missing)} rule(s) the mapping declares OPTIONAL matched no row, "
                            f"so the delivery does not carry those columns: {'; '.join(optional_missing)}"),
                "heuristic": ("this is a fact about the delivery, reported because a mapping that quietly "
                              "maps nothing is the failure this path exists to prevent; whether the column "
                              "is genuinely absent, spelled differently, or the mapping's typo is still a "
                              "judgement a human makes from the shape report"),
                "detail": {"rules": optional_missing}})
    sanity_path.write_text(json.dumps(sanity_document, ensure_ascii=False, indent=2, default=str) + "\n",
                           encoding="utf-8")
    (report_path.parent / "artifacts.json").write_text(json.dumps({
        "kind": "delivery-import-artifacts",
        "artifacts": [{"kind": "delivery-payload-shape", "path": report_path.name},
                      {"kind": "delivery-sanity-checks", "path": sanity_path.name}],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    empty = [record_type for record_type, item in shape.items()
             if item["rows_seen"] and item["useful_set_met"] == 0]
    quarantine_summary_path: Path | None = None
    if quarantine:
        quarantine_summary_path = write_summary(
            quarantine_dir / "quarantine-summary.json", loaded=total, quarantined=len(refusals),
            log=refusals, extra={"empty_useful_types": empty, "mode": on_error})
        if fail_on_quarantine and len(refusals):
            # The summary and the shape report are on disk before this raise, so the caller gets both a
            # non-zero status and the account of what was quarantined.
            raise DeliveryShapeError(refusals.message(), code="quarantine_refused")
    elif empty:
        raise DeliveryShapeError(f"rendered record types have zero useful rows: {', '.join(empty)}",
                                 code="zero_useful_rows")
    return ImportReport(personas=len(profiles), records=total, encounters=len(encounter_rows),
                        skipped=len(refusals) if quarantine else 0, output_dir=output_dir,
                        shape_report=report_path, quarantined=len(refusals),
                        quarantine_path=quarantine_dir / "quarantine.jsonl" if quarantine else None,
                        quarantine_summary=quarantine_summary_path,
                        mapping_version=spec.version if spec is not None else None,
                        mapping_path=spec.path if spec is not None else None,
                        sanity_report=sanity_path)


@dataclass(frozen=True)
class QuarantineRetryReport:
    attempted: int
    recovered: int
    remaining: int
    output: Path


def retry_quarantine(quarantine_path: Path, import_dir: Path,
                     output: Path | None = None,
                     mapping: MappingSpec | str | Path | None = None) -> QuarantineRetryReport:
    """Re-attempt quarantined rows against the CURRENT vocabulary (and mapping) and load the ones that bind.

    A quarantine file is not a graveyard: after a mapping or vocabulary revision the rows it holds must be
    recoverable without re-reading 258 MB. Each stored raw row is re-bound through the same `_bind_record`
    the strict importer uses, resolved against the people index the import already wrote, and appended to
    that person's file when it binds. Rows that still fail are written to `output` (default:
    `quarantine-retry.jsonl` beside the input) with the current reason, so the file shrinks as the mapping
    improves.

    `mapping` is the mapped-import counterpart: a quarantined row from a mapping import holds the RAW vendor
    row, so a revised mapping file re-maps it through `_map_raw_row` -- the same translation, required-target
    check and payload parsing the first pass used -- before the envelope is bound. Without `mapping`, the
    stored row must already be canonical, exactly as before.

    This loads ROWS ONLY. A row whose source person was never indexed cannot be recovered here -- the
    person itself was refused -- and stays in the quarantine with that reason.
    """
    quarantine_path = Path(quarantine_path)
    import_dir = Path(import_dir)
    spec: MappingSpec | None = None
    if mapping is not None:
        spec = mapping if isinstance(mapping, MappingSpec) else MappingSpec.load(Path(mapping))
        spec.require_targets(REQUIRED_ENVELOPE_TARGETS)
    index_path = import_dir / "index.sqlite"
    if not index_path.is_file():
        raise DeliveryShapeError(f"no import index at {index_path}; retry needs the import it came from",
                                 code="missing_index")
    output = Path(output) if output is not None else quarantine_path.with_name("quarantine-retry.jsonl")
    if output.resolve() == quarantine_path.resolve():
        raise DeliveryShapeError("the retry output must differ from the quarantine input",
                                 code="unsafe_output_path")
    # Staged into a temporary sibling and swapped in with `os.replace` when the pass completes. The old
    # code opened the output `"w"` (truncating), appended per row, and rewrote the input at the end: a
    # death between those two writes left the rows quarantined so far in the output and every other one
    # only in the input, which the next pass would... not read, because it was gone. Now every refused row
    # is on disk under `staged` from the moment it is refused, and the previous output is untouched until
    # the swap. A re-run folds `staged` in first, so nothing refused by a crashed pass is lost.
    staged = output.with_name(output.name + ".tmp")
    staged.parent.mkdir(parents=True, exist_ok=True)
    prior = output.read_text(encoding="utf-8") if output.is_file() else ""
    if staged.is_file():
        prior += staged.read_text(encoding="utf-8")
    if prior:
        staged.write_text(prior, encoding="utf-8")
    remaining = RefusalLog(staged)
    connection = sqlite3.connect(index_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    attempted = recovered = 0
    try:
        people = {row["source_person_id"]: {"persona_id": row["persona_id"]}
                  for row in connection.execute("SELECT source_person_id, persona_id FROM people")}
        for line in quarantine_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            attempted += 1
            try:
                entry = json.loads(line)
            except (ValueError, RecursionError):
                remaining.record(Refusal(code="unreadable_quarantine_entry",
                                         reason="quarantine line is not JSON",
                                         locator=f"{quarantine_path.name}:{attempted}"))
                continue
            locator = str(entry.get("locator") or f"{quarantine_path.name}:{attempted}")
            raw = entry.get("raw")
            if not isinstance(raw, dict):
                remaining.record(Refusal(code="missing_raw_row",
                                         reason="quarantine entry kept no raw row to re-attempt",
                                         locator=locator, values=entry.get("values") or {}))
                continue
            if spec is not None:
                # The stored row is the VENDOR row: re-map it through the revised mapping first.
                row, refusal, _matched = _map_raw_row(spec, raw, locator)
                if refusal is not None:
                    remaining.record(Refusal.from_error(refusal, locator=locator, raw=raw))
                    continue
                if row is None:  # pragma: no cover -- `_map_raw_row` returns one or the other
                    continue
            else:
                row = raw
            key = str(row.get("source_person_id") or "").strip()
            if key not in people:
                remaining.record(Refusal(code="unbound_source_person",
                                         reason=f"record {row.get('record_id')!r} has no bound source person",
                                         locator=locator, values={"source_person_id": key}, raw=raw))
                continue
            try:
                record = _bind_record(row, people)
            except DeliveryShapeError as exc:
                remaining.record(Refusal.from_error(exc, locator=locator, raw=raw))
                continue
            try:
                connection.execute("INSERT INTO seen_records VALUES (?)", (record.record_id,))
            except sqlite3.IntegrityError:
                remaining.record(Refusal(code="duplicate_record_id",
                                         reason=f"duplicate record ID {record.record_id!r}",
                                         locator=locator, values={"record_id": record.record_id}, raw=raw))
                continue
            if record.record_type == source.BASIC_INFO and record.topic == "demografia":
                # The index's own one-demografia rule (`seen_basics`); a recovered identity row that would
                # make a person ambiguous must stay quarantined, exactly as the first pass would have it.
                try:
                    connection.execute("INSERT INTO seen_basics VALUES (?)", (key,))
                except sqlite3.IntegrityError:
                    remaining.record(Refusal(
                        code="ambiguous_basics",
                        reason=f"person {key!r} already has a demografia perustieto in this index",
                        locator=locator, values={"source_person_id": key}, raw=raw))
                    continue
            person_path = import_dir / _PEOPLE_DIR / f"{people[key]['persona_id']}.jsonl"
            with person_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n")
            recovered += 1
        connection.commit()
    finally:
        connection.close()
        remaining.close()
    # Atomic swap: readers and a re-run see EITHER the previous output or the complete new one, never a
    # half-written file. Until this rename the staged rows are on disk under `staged`, so a crash between
    # any two writes loses nothing that the next (or a previous) run cannot re-read.
    os.replace(staged, output)
    write_summary(output.with_name("quarantine-retry-summary.json"), loaded=recovered,
                  quarantined=len(remaining), log=remaining, extra={"mode": "retry"})
    return QuarantineRetryReport(attempted=attempted, recovered=recovered, remaining=len(remaining),
                                 output=output)


class DeliveryIndex(Mapping[str, Persona]):
    """Lazy person mapping and owner catalog; resolves one person's records per lookup."""
    def __init__(self, output_dir: Path):
        self._dir = output_dir
        index_path = output_dir / _INDEX_NAME
        # Checked BEFORE opening, because `sqlite3.connect("file:...?mode=ro", uri=True)` on a missing file
        # raises a raw `sqlite3.OperationalError: unable to open database file` — which names neither the
        # path nor the command that creates one. The wrong-version case below already answers that way.
        if not index_path.is_file():
            raise DeliveryShapeError(
                f"no delivery index at {index_path}; build one with "
                f"`python backend/scripts/reconcile_delivery.py import <corpus> --out {output_dir}`",
                code="missing_index", values={"path": str(index_path)})
        self._connection = sqlite3.connect(
            f"file:{index_path}?mode=ro", uri=True, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._lock = RLock()
        found = self._connection.execute("PRAGMA user_version").fetchone()
        version = int(found[0]) if found is not None else 0
        if version != INDEX_SCHEMA_VERSION:
            self._connection.close()
            raise DeliveryShapeError(
                f"the index in {output_dir} was built by an older revision (schema {version}, this code "
                f"writes {INDEX_SCHEMA_VERSION}). Rebuild it with "
                f"`python backend/scripts/reconcile_delivery.py import <corpus> --out {output_dir}`")

    def encodings(self) -> list[tuple[str, str, str]]:
        with self._lock:
            return [(row["persona_id"], row["source_person_id"], row["label"])
                    for row in self._connection.execute("SELECT persona_id, source_person_id, label FROM people")]

    def has(self, persona_id: str) -> bool:
        with self._lock:
            return self._connection.execute(
                "SELECT 1 FROM people WHERE persona_id = ?", (persona_id,)).fetchone() is not None

    def _person_file(self, persona_id: str, row) -> Path:
        """Resolve `people.path` and refuse anything that is not the import's own `people/*.jsonl` file.

        `path` is a column, and an index is a file the operator can hand-edit or rebuild by hand: it used to
        be trusted verbatim, so `people/../../outside-secret.jsonl` was followed (`pathlib` does not stop at
        the base) and an absolute path replaced the base entirely. A `path` of `people/victim.jsonl` under a
        foreign `persona_id` was a complete cross-persona misattribution. The DDL now carries
        `CHECK (path GLOB 'people/*.jsonl')`; this is the second, independent check at read time, so an
        index that predates the CHECK (or was built by another writer) cannot become a file read either.
        """
        raw = str(row["path"] or "")
        base = self._dir.resolve()
        person_path = (self._dir / raw).resolve()
        if not person_path.is_relative_to(base) or person_path.parent != base / _PEOPLE_DIR:
            raise DeliveryShapeError(
                f"the index in {self._dir} points person {persona_id!r} at {raw!r}, which resolves outside "
                f"the import's own people directory ({base / _PEOPLE_DIR}); refused rather than read",
                code="unsafe_person_path", values={"path": raw, "resolved": str(person_path)})
        if person_path.suffix != ".jsonl" or not _SAFE_ID.fullmatch(person_path.stem):
            raise DeliveryShapeError(
                f"the index in {self._dir} points person {persona_id!r} at {raw!r}; the import only ever "
                f"writes people/<persona_id>.jsonl, so this path is refused rather than read",
                code="unsafe_person_path", values={"path": raw})
        return person_path

    def __getitem__(self, persona_id: str) -> Persona:
        with self._lock:
            row = self._connection.execute("SELECT * FROM people WHERE persona_id = ?", (persona_id,)).fetchone()
        if row is None:
            raise KeyError(persona_id)
        person_path = self._person_file(persona_id, row)
        # `path` is a column a hand-built index can point anywhere; a missing file used to surface as a raw
        # `FileNotFoundError` from `.read_text`, which names the file but not the index that names it.
        if not person_path.is_file():
            raise DeliveryShapeError(
                f"the index in {self._dir} points person {persona_id!r} at {person_path}, which is not "
                f"there; rebuild the index with "
                f"`python backend/scripts/reconcile_delivery.py import <corpus> --out {self._dir}`",
                code="missing_person_file", values={"path": str(person_path)})
        records = [derived_record_state(Record.model_validate_json(line)) for line in
                   person_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        # The index's binding and the file's own records must name the same source person. A file can be
        # pointed at from a foreign `persona_id` row even when the path itself is legal, which would serve
        # person Y's records to person X; the records carry the identity to check.
        wrong = [(record.record_id, record.source_person_id) for record in records
                 if record.source_person_id is not None
                 and record.source_person_id != row["source_person_id"]]
        if wrong:
            raise DeliveryShapeError(
                f"the index in {self._dir} binds person {persona_id!r} to source person "
                f"{row['source_person_id']!r}, but {len(wrong)} record(s) in {person_path.name} name "
                f"{wrong[0][1]!r} instead (first record {wrong[0][0]!r}); refused rather than served as "
                f"this person's records",
                code="person_binding_mismatch",
                values={"path": str(person_path), "expected_source_person_id": row["source_person_id"],
                        "found_source_person_ids": sorted({found for _rid, found in wrong}),
                        "records": [rid for rid, _found in wrong[:5]]})
        return Persona(persona_id=row["persona_id"], label=row["label"] or row["persona_id"],
                       source_person_id=row["source_person_id"], records=records)

    def persona(self, persona_id: str) -> Persona:
        return self[persona_id]

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            identifiers = [row["persona_id"] for row in
                           self._connection.execute("SELECT persona_id FROM people")]
        return iter(identifiers)

    def __len__(self) -> int:
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) FROM people").fetchone()
        count = row[0] if row is not None else 0
        return count if isinstance(count, int) and not isinstance(count, bool) else 0

    def encounter_index(self) -> dict[str, Encounter]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT e.*, p.persona_id FROM encounters e "
                "JOIN people p ON p.source_person_id = e.source_person_id").fetchall()
        return {row["encounter_id"]: Encounter(
            encounter_id=row["encounter_id"], persona_id=row["persona_id"], role=row["role"],
            purpose=cast(Purpose, row["purpose"]), source_person_id=row["source_person_id"],
            consent_holders=cast(tuple[ConsentHolder, ...],
                                 tuple(part for part in (row["consent_holders"] or "self").split(",") if part)),
            life_stage=cast(LifeStage, row["life_stage"] or "adult"))
                for row in rows}

    def encounter_labels(self) -> dict[str, tuple[str, str]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT encounter_id, example_label, demo_name FROM encounters").fetchall()
        return {row["encounter_id"]: (row["example_label"], row["demo_name"]) for row in rows}

    def encounter_metadata(self) -> list[EncounterMetadata]:
        """Every encounter's worklist metadata, joined to its person, in id order. Reads no record file.

        This is the worklist's only read: it names the `encounters` and `people` tables and never opens a
        `people/*.jsonl` file, which `__getitem__` does for one person at a time, for the picture route.
        """
        with self._lock:
            rows = self._connection.execute(
                "SELECT e.encounter_id, e.source_person_id, e.role, e.purpose, e.example_label, e.demo_name, "
                "e.consent_holders, e.life_stage FROM encounters e "
                "JOIN people p ON p.source_person_id = e.source_person_id ORDER BY e.encounter_id").fetchall()
        return [metadata_from_row(*tuple(row)) for row in rows]

    def appointments_for_day(self, day: date) -> list[Appointment]:
        """The day's scheduled occasions, each joined to the lens that opens it. Reads no record file."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT a.appointment_id, a.source_person_id, a.domain, a.scheduled_on, a.status_text, "
                "a.urgency_text, a.specialty, a.contact_record_id, a.encounter_id, "
                "e.role, e.purpose, e.example_label FROM appointments a "
                "LEFT JOIN encounters e ON e.encounter_id = a.encounter_id "
                "WHERE a.scheduled_on = ? ORDER BY a.appointment_id", (day.isoformat(),)).fetchall()
        return [appointment_from_row(*tuple(row)) for row in rows]

    def appointment_days(self, start: date, end: date, role: str | None = None) -> dict[str, int]:
        """How many scheduled occasions each day in [start, end] carries (for one role's lens when given),
        keyed by ISO date; empty days absent."""
        with self._lock:
            if role is None:
                rows = self._connection.execute(
                    "SELECT scheduled_on, COUNT(*) FROM appointments WHERE scheduled_on BETWEEN ? AND ? "
                    "GROUP BY scheduled_on", (start.isoformat(), end.isoformat())).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT a.scheduled_on, COUNT(*) FROM appointments a "
                    "JOIN encounters e ON e.encounter_id = a.encounter_id "
                    "WHERE a.scheduled_on BETWEEN ? AND ? AND e.role = ? GROUP BY a.scheduled_on",
                    (start.isoformat(), end.isoformat(), role)).fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def briefing_cache(self):
        """A local backend's cache: memory when `LUVN_BRIEFING_CACHE=memory`, otherwise none (the index is read-only)."""
        from .briefings import cache_for_local_backend

        return cache_for_local_backend()

    def close(self) -> None:
        with self._lock:
            self._connection.close()
