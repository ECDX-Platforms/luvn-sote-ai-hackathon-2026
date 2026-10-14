"""Immutable modelled encounters; API callers resolve IDs, not role/purpose strings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from . import source
from .purpose import Purpose
from .upm import ConsentHolder, Kypsyysarvio, LifeStage, Persona


@dataclass(frozen=True)
class Encounter:
    encounter_id: str
    persona_id: str
    role: str
    purpose: Purpose
    source_person_id: str | None = None
    # Illustrative, non-statutory: which consent holders this lens covers and the modelled life stage
    # its catalogue rows are keyed by. A lens never receives a record whose declared holder it does
    # not cover, and that gap is reported only as a neutral absence.
    consent_holders: tuple[ConsentHolder, ...] = ("self",)
    life_stage: LifeStage = "adult"
    case_id: str | None = None
    process_type: str | None = None
    origin_organisation: str | None = None
    origin_service_unit: str | None = None
    # Materialised effective exclusions supplied for this encounter and applied by the single policy cut.
    inherited_restricted_case_ids: frozenset[str] = frozenset()
    # Exact encounter age supplied by the trusted catalog; never derived from birth year.
    age_at_encounter: int | None = None
    kypsyysarvio: Kypsyysarvio | None = None


@dataclass(frozen=True)
class EncounterMetadata:
    """One encounter's catalogue metadata: the columns a worklist reads, and never a person's records.

    The worklist is computed over these alone. `source_person_id` is what the people count is taken over
    (`COUNT(DISTINCT source_person_id)`), and `example_label` is the display name the row carries. A `None`
    `example_label` means the store holds no label for the encounter, and the row is served without one;
    the catalogue keeps no label map of its own.
    """

    encounter_id: str
    source_person_id: str | None
    role: str
    purpose: str
    example_label: str | None
    demo_name: str | None
    consent_holders: tuple[str, ...]
    life_stage: str


@dataclass(frozen=True)
class Appointment:
    """One scheduled occasion from the release's own appointment tables, joined to the lens that opens it.

    `domain` is the source table's (clinical `ajanvaraus` -> `treatment`, social home-care visit ->
    `social_care`), never a role. `encounter_id` is the lens the builder emitted for that person and domain,
    or None when the person has no lens there -- such a row is scheduled but has no briefing to open.
    `status_text` and `urgency_text` are the source's own strings, verbatim; neither is interpreted here.
    """

    appointment_id: str
    source_person_id: str
    domain: str
    scheduled_on: date
    status_text: str | None
    urgency_text: str | None
    specialty: str | None
    contact_record_id: str | None
    encounter_id: str | None
    # From the joined encounter row; None when `encounter_id` is None.
    role: str | None = None
    purpose: str | None = None
    example_label: str | None = None


def appointment_from_row(appointment_id, source_person_id, domain, scheduled_on, status_text, urgency_text,
                         specialty, contact_record_id, encounter_id, role=None, purpose=None,
                         example_label=None) -> Appointment:
    """One `appointments LEFT JOIN encounters` row, from either engine."""
    when = scheduled_on if isinstance(scheduled_on, date) else date.fromisoformat(str(scheduled_on)[:10])
    return Appointment(
        appointment_id=str(appointment_id), source_person_id=str(source_person_id), domain=str(domain),
        scheduled_on=when, status_text=str(status_text) if status_text else None,
        urgency_text=str(urgency_text) if urgency_text else None,
        specialty=str(specialty) if specialty else None,
        contact_record_id=str(contact_record_id) if contact_record_id else None,
        encounter_id=str(encounter_id) if encounter_id else None,
        role=str(role) if role else None, purpose=str(purpose) if purpose else None,
        example_label=str(example_label) if example_label else None)


def metadata_from_row(encounter_id, source_person_id, role, purpose, example_label, demo_name,
                      consent_holders, life_stage) -> EncounterMetadata:
    """One `encounters JOIN people` row as metadata, with the same defaults the catalogue index applies."""
    return EncounterMetadata(
        encounter_id=str(encounter_id), source_person_id=str(source_person_id), role=str(role),
        purpose=str(purpose), example_label=str(example_label or ""), demo_name=str(demo_name or ""),
        consent_holders=tuple(part for part in str(consent_holders or "self").split(",") if part),
        life_stage=str(life_stage or "adult"))


def resolve_encounter(
    encounter_id: str,
    personas: list[Persona] | Mapping[str, Persona],
    encounters: Mapping[str, Encounter],
    persona_cache: dict[str, Persona] | None = None,
) -> tuple[Encounter, Persona]:
    """Resolve the authoritative person/role/purpose tuple or reject it closed.

    `encounters` is the catalogue the caller is resolving against -- the store's `encounter_index()`, or
    an evaluation script's own map -- and it has no default: this module carries no catalogue of its own.

    `persona_cache`, when given, is a REQUEST-SCOPED memo (the catalogue endpoint builds one). A
    SQL-backed index re-fetches and re-validates a person's whole records JSON per `get`, so a caller
    resolving k encounters that share a person paid k times; the memo makes it once. It is keyed by
    `persona_id`, never shared across requests, and a missing person is not cached as one.
    """
    encounter = encounters.get(encounter_id)
    if encounter is None:
        raise ValueError(f"unknown encounter {encounter_id!r}")
    if isinstance(personas, Mapping):
        # Store's mapping was validated once at load; encounter resolution is O(1) keyed by persona id.
        persona = persona_cache.get(encounter.persona_id) if persona_cache is not None else None
        if persona is None:
            persona = personas.get(encounter.persona_id)
            if persona is not None and persona_cache is not None:
                persona_cache[encounter.persona_id] = persona
    else:
        # List callers are the test/fixture seam; validate their unindexed input.
        record_ids = [record.record_id for person in personas for record in person.records]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("duplicate record ID")
        source_owners: dict[str, set[str]] = {}
        for person in personas:
            if person.source_person_id:
                source_owners.setdefault(person.source_person_id, set()).add(person.persona_id)
        if any(len(owners) > 1 for owners in source_owners.values()):
            raise ValueError("source person key maps to multiple people")
        persona = next((p for p in personas if p.persona_id == encounter.persona_id), None)
    if persona is None:
        raise ValueError(f"encounter {encounter_id!r} has no configured person")
    if encounter.source_person_id is not None:
        if not encounter.source_person_id or persona.source_person_id != encounter.source_person_id:
            raise ValueError("source person does not match encounter")
        if any(record.source_person_id != encounter.source_person_id for record in persona.records):
            raise ValueError("record source person does not match encounter")
        source.perustieto_for(persona.records)  # raises on ambiguous basics
    return encounter, persona
