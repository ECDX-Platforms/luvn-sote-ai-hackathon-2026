"""Source-payload accessors: the one module that knows source field names.

`fixtures.py` loads records with their source-shaped payload left untouched --
that non-normalization is deliberate (see `upm.py`). This module is the other
half of the swap seam: it maps those source keys to the small set of domain
facts the decision layer actually needs. A real Veil.AI export changes the
bodies here; callers keep asking for `temperature(record)`, never
`payload["lämpö_c"]`.

Namespace for the concept names lives in `CONTEXT.md`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from typing import Any

from .upm import FormMetadata, Record

# Record types as they appear in the source payload (Finnish, source vocabulary).
MEDICATION = "lääkitys"
FREE_TEXT = "vapaateksti"
BASIC_INFO = "perustieto"
DIAGNOSIS = "diagnoosi"
APPOINTMENT = "asiointitapahtuma"
MEASUREMENT = "mittaus"
LAB_RESULT = "tutkimustulos"
ARVIOINTI = "arviointi"
DECISION = "päätös"

# Lomakepalvelu document kinds. CONSTANTS ONLY: no record instance, no count and no rendering.
# An authorable `todistus`/`lausunto`/`ilmoitus` type would enter the classifier's candidate set
# untested and inflate the coverage counts, so no fixture supplies one.
DOCUMENT_KINDS = ("todistus", "lausunto", "ilmoitus")

# Every record type this codebase models, in one definition. `delivery.RECORD_TYPE_ALIASES` derives its
# identity map from this tuple (so an added type is not a second hand-kept list), and `policy.cut` refuses
# a record whose type is outside it rather than silently excluding it from a purpose view.
RECORD_TYPES = (MEDICATION, FREE_TEXT, BASIC_INFO, DIAGNOSIS, APPOINTMENT, MEASUREMENT, LAB_RESULT,
                ARVIOINTI, DECISION)

MEDICATION_ACTIVE = "aktiivinen"


def _payload(record: Record) -> dict[str, Any]:
    return record.payload


# Keys that identify the person or name them. Enumerated from the built corpus (every payload key of all
# seven record types): `näyttötunniste` is the only one present, on perustieto. `henkilötunnus` and the
# name keys are not in the corpus; they are listed so a source that adds them is still stripped.
# `nimi` is deliberately NOT here: on diagnoosi it is the diagnosis name, which is clinical substance.
IDENTITY_KEYS = frozenset({"näyttötunniste", "henkilötunnus", "etunimi", "sukunimi"})


def basics_payload(record: Record, audience: str) -> dict[str, Any]:
    """Project a record's payload at the egress boundary, by audience.

    `model`, every record type: the payload minus every identity key; basics has no model substance, so `{}`.
    Nothing else is narrowed: the verifier must still check a sentence against the record it cites.
    `display`: unchanged. Basics gets its allowlist; other types pass through as before.
    """
    if audience == "model":
        if record.record_type == BASIC_INFO:
            return {}
        return {key: value for key, value in record.payload.items() if key not in IDENTITY_KEYS}
    if record.record_type != BASIC_INFO:
        if audience == "display":
            return record.payload
        raise ValueError(f"unknown basics projection audience: {audience}")
    if audience == "display":
        allowed = ("nimi", "näyttötunniste")
        if record.topic == "demografia":
            allowed += ("sukupuoli", "syntymävuosi", "äidinkieli", "kotikunta", "asumismuoto")
        return {key: value for key in allowed if (value := record.payload.get(key)) is not None
                and ((key == "syntymävuosi" and isinstance(value, int) and not isinstance(value, bool))
                     or (key != "syntymävuosi" and isinstance(value, str)))}
    raise ValueError(f"unknown basics projection audience: {audience}")


def assert_model_safe(body: str) -> None:
    """Enforcement at the transport boundary (work order L2B): the literal outbound text must not contain
    any `IDENTITY_KEYS` member as a JSON key.

    This is a last-resort net, not the projection itself: `basics_payload(record, "model")` is what decides
    what a model may see, and every call site must still route through it. This assertion only catches a
    call site that forgot to -- it scans for the quoted key string literally, so it cannot tell an identity
    value reached the model under some *other* key, only that an identity-named key did not. It runs at
    every known transport boundary (`llm._complete_bounded`, `llm._complete_prompt_agent`,
    `decision_model._post`) immediately before the body leaves the process.
    """
    for key in IDENTITY_KEYS:
        if f'"{key}"' in body:
            raise ValueError(f"outbound model request body carries identity key {key!r}")


NEUTRAL_IDENTITY = "Henkilöllisyystieto ei saatavilla"


def perustieto_for(records: Iterable[Record]) -> Record | None:
    """The single demografia Perustieto for one Kohtaaminen, or None.

    More than one is ambiguous: no single identity can be bound, so this raises
    rather than silently taking the first. Encounter binding and the display path
    share this one rule.
    """
    basics = [record for record in records
              if record.record_type == BASIC_INFO and record.topic == "demografia"]
    if len(basics) > 1:
        raise ValueError("ambiguous basics records")
    return basics[0] if basics else None


def identity_header(records: Iterable[Record]) -> str:
    """The display header for the accessor's Perustieto, or the neutral label."""
    basics = perustieto_for(records)
    if basics is None:
        return NEUTRAL_IDENTITY
    fields = basics_payload(basics, "display")
    values = [fields[key] for key in ("nimi", "näyttötunniste") if fields.get(key)]
    return " · ".join(str(value) for value in values) or NEUTRAL_IDENTITY


def _as_int(value: Any) -> int | None:
    # bool is an int subclass; True must not read as 1 dose or year 1.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def is_medication(record: Record) -> bool:
    return record.record_type == MEDICATION


def is_active_medication(record: Record) -> bool:
    return is_medication(record) and _payload(record).get("tila") == MEDICATION_ACTIVE


def is_assessment(record: Record) -> bool:
    return record.record_type == ARVIOINTI


def drug_name(record: Record) -> str | None:
    value = _payload(record).get("lääke")
    return str(value) if value is not None else None


def missed_doses(record: Record) -> int | None:
    if not is_medication(record):
        return None
    return _as_int(_payload(record).get("puuttuneet_annokset_30d"))


def birth_year(record: Record) -> int | None:
    return _as_int(_payload(record).get("syntymävuosi"))


def temperature(record: Record) -> int | float | None:
    return _as_number(_payload(record).get("lämpö_c"))


def spo2(record: Record) -> int | float | None:
    return _as_number(_payload(record).get("spo2"))


# "158/92": digits only, so "158/", "korkea", "1a8/92" and "158/92/1" all fail to match and read as absent.
_BLOOD_PRESSURE = re.compile(r"([0-9]+)/([0-9]+)")


def blood_pressure(record: Record) -> tuple[int, int] | None:
    """The `verenpaine` pair as (systolic, diastolic) ints, or None. Never raises: an unparseable value is not evidence."""
    if record.record_type != MEASUREMENT:
        return None
    raw = _payload(record).get("verenpaine")
    match = _BLOOD_PRESSURE.fullmatch(raw.strip()) if isinstance(raw, str) else None
    return (int(match.group(1)), int(match.group(2))) if match else None


def note_text(record: Record) -> str:
    p = _payload(record)
    return f"{p.get('otsikko', '')} {p.get('teksti', '')}"


def temporal_status(record: Record, today: date) -> str:
    """Use source visit time when present; fall back to the legacy entry-recorded time."""
    if record.record_type == MEDICATION:
        return "current" if is_active_medication(record) else "history"
    if record.record_type == "diagnoosi":
        return "current" if _payload(record).get("tila") == "aktiivinen" else "history"
    if record.record_type not in {FREE_TEXT, MEASUREMENT, LAB_RESULT, APPOINTMENT, ARVIOINTI, DECISION}:
        return "current"
    event_time = record.käyntiaika or record.recorded_at
    age_days = (today - event_time.date()).days
    if age_days < 0:
        return "future"
    return "current" if age_days <= 365 else "history"


def string_values(record: Record) -> list[str]:
    """Every string in the payload -- except untrusted identity basics."""
    if record.record_type == BASIC_INFO:
        return []
    return [v for v in record.payload.values() if isinstance(v, str)]


def assessment_instrument(record: Record) -> str | None:
    value = _payload(record).get("instrumentti")
    return str(value) if value is not None else None


def form_metadata(record: Record) -> FormMetadata | None:
    """Return explicit form data or the assessment fields already in the source record."""
    if record.form is not None:
        return record.form
    if record.record_type != ARVIOINTI:
        return None
    instrument = assessment_instrument(record)
    if not instrument:
        return None
    version = _payload(record).get("versio")
    # Existing assessment records have no separate service field; topic is only a modelled label.
    return FormMetadata(form_type=instrument, version=version if isinstance(version, str) else None,
                        service=record.topic)


def assessment_answers(record: Record) -> dict[str, int]:
    """The {item_code: points} map, or {} when absent/malformed."""
    raw = _payload(record).get("vastaukset")
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, int) and not isinstance(v, bool)}
