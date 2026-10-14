"""Policy: the role-visibility cut and the decision tables, loaded once.

The cut used to run independently in `decide`, `llm`, `grounding` and
`pipeline`, each re-reading `visibility.json` from disk. It now runs once per
request and travels as a `Scoped` value, so no consumer re-derives the
permitted set. `table()` caches the parsed JSON behind one interface.

Policy before rank: scope + sensitivity + topic, applied BEFORE generation.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import source
# pi-lens-ignore: reportMissingImports
from .purpose import palvelukonteksti, palvelukontekstit, topics as declared_topics
from .upm import Kypsyysarvio, Persona, Record

TABLES_DIR = Path(__file__).resolve().parent / "tables"

# The record types the purpose filter below admits BY TOPIC. A record of one of these types whose topic
# belongs to no declared topic can never be admitted under any purpose, so excluding it is not a policy
# decision -- it is a silent drop that looks exactly like a person who has nothing. `policy.cut` refuses
# one instead of producing an empty briefing. Basic-info rows are deliberately outside this set: their
# admission is the explicit `record_type == perustieto and topic == demografia` rule, so a non-demographic
# basics topic is excluded by a rule, not by an undeclared vocabulary.
PURPOSE_FILTERED_TYPES = frozenset({
    source.DIAGNOSIS, source.MEDICATION, source.FREE_TEXT,
    source.ARVIOINTI, source.MEASUREMENT, source.LAB_RESULT,
    source.APPOINTMENT, source.DECISION,
})


class UnknownVocabularyError(ValueError):
    """A record carries a value from a vocabulary this code does not declare.

    Deliberately not a warning and deliberately not an empty permitted set: an undeclared value is how a
    briefing comes back empty while the application looks like it worked, which is the failure this class
    exists to make impossible. The message names the offending value AND the known set, because the only
    useful next action is to map or declare it.
    """

    def __init__(self, axis: str, unknown: list[str], known: list[str]):
        rendered = ", ".join(repr(value) for value in unknown)
        self.axis = axis
        self.unknown = list(unknown)
        self.known = sorted(known)
        super().__init__(f"unknown {axis} {rendered}; known {axis} values: {self.known}")


def _assert_no_unknown_vocabulary(persona: Persona) -> None:
    """Refuse an undeclared type or topic before the purpose filter can silently drop it."""
    unknown_types = sorted({r.record_type for r in persona.records
                            if r.record_type not in source.RECORD_TYPES})
    if unknown_types:
        raise UnknownVocabularyError("record_type", unknown_types, list(source.RECORD_TYPES))
    known_topics = declared_topics()
    unknown_topics = sorted({r.topic for r in persona.records
                             if r.record_type in PURPOSE_FILTERED_TYPES and r.topic not in known_topics})
    if unknown_topics:
        raise UnknownVocabularyError("topic", unknown_topics, sorted(known_topics))


@lru_cache(maxsize=None)
def _parsed(name: str) -> dict:
    path = TABLES_DIR / f"{name}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FileNotFoundError(f"decision table not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in decision table {path}: {exc}") from exc


def table(name: str) -> dict:
    """A fresh copy of a decision/visibility table, parsed once per process.

    The deep copy is what keeps the cache from becoming process-poisoning
    mutable state: callers cannot mutate the shared parse.
    """
    return copy.deepcopy(_parsed(name))


@dataclass(frozen=True)
class Scoped:
    """A Henkilö already cut to one Rooli and, when supplied, Palvelukonteksti."""

    persona: Persona
    role: str
    purpose: str | None = None
    encounter_id: str | None = None

    @property
    def records(self) -> list[Record]:
        return self.persona.records

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(r.record_id for r in self.persona.records)



def cut(
    persona: Persona,
    role: str = "laakari",
    purpose: str | None = None,
    encounter_id: str | None = None,
    consent_holders: tuple[str, ...] | None = None,
    inherited_restricted_case_ids: frozenset[str] = frozenset(),
    kypsyysarvio: Kypsyysarvio | None = None,
    age_at_encounter: int | None = None,
) -> Scoped:
    policy = table("visibility")
    cfg = policy["roles"].get(role)
    if cfg is None:
        raise ValueError(f"unknown role {role!r}; known roles: {sorted(policy['roles'])}")
    if purpose is not None:
        if purpose not in palvelukontekstit():
            raise ValueError(f"unknown purpose {purpose!r}; known purposes: {sorted(palvelukontekstit())}")
        row = palvelukonteksti(purpose)
        if role not in row.roles:
            raise ValueError(f"role {role!r} is not available for purpose {purpose!r}")
    allowed_topics = palvelukonteksti(purpose).topics if purpose is not None else frozenset()
    if purpose is not None:
        # Fail closed BEFORE the topic filter runs: an undeclared topic or type would otherwise be dropped
        # by the filter and the caller would receive a permitted set that is empty for a reason nobody can
        # see. Refusing names the value and the known set, which is the mapping instruction for arrival day.
        _assert_no_unknown_vocabulary(persona)
    rank = policy["sensitivity_rank"]
    if age_at_encounter is not None and (
        not isinstance(age_at_encounter, int) or isinstance(age_at_encounter, bool) or age_at_encounter < 0
    ):
        raise ValueError("age_at_encounter must be a non-negative integer")
    # Illustrative vendor product default; use exact encounter age, never a birth-year estimate.
    default_holder = "guardian" if age_at_encounter is not None and age_at_encounter < 12 else None
    if kypsyysarvio is not None and kypsyysarvio.scope == "resepti":
        prescription = next((r for r in persona.records if r.record_id == kypsyysarvio.prescription_record_id), None)
        if (prescription is None or prescription.record_type != source.MEDICATION
                or prescription.encounter_id != encounter_id):
            raise ValueError("prescription-scoped assessment must bind a medication in this encounter")

    def holder_for(record: Record) -> str | None:
        return record.projected_consent_holder(kypsyysarvio, encounter_id, default_holder)

    visible = [
        r
        for r in persona.records
        if r.consent_scope in cfg["consent_scopes"]
        and (r.case_id is None or r.case_id not in inherited_restricted_case_ids)
        and (consent_holders is None or holder_for(r) is None or holder_for(r) in consent_holders)
        and rank[r.sensitivity_tag] <= rank[cfg["max_sensitivity"]]
        and r.topic not in cfg["exclude_topics"]
        and (
            purpose is None
            or (
                r.record_type == source.BASIC_INFO and r.topic == "demografia"
            ) or (
                r.record_type in PURPOSE_FILTERED_TYPES
                and r.topic in allowed_topics
            )
        )
    ]
    return Scoped(
        persona=persona.model_copy(update={"records": visible}),
        role=role,
        purpose=purpose,
        encounter_id=encounter_id,
    )


def cut_for_encounter(persona: Persona, encounter) -> Scoped:
    """Apply encounter context through the only policy cut.

    The effective exclusion set is evaluated in one place.
    """
    return cut(
        persona, encounter.role, encounter.purpose, encounter.encounter_id,
        consent_holders=encounter.consent_holders,
        inherited_restricted_case_ids=encounter.inherited_restricted_case_ids,
        kypsyysarvio=encounter.kypsyysarvio,
        age_at_encounter=encounter.age_at_encounter,
    )


def roles() -> dict:
    return table("visibility")["roles"]


def visible_records(persona: Persona, role: str = "laakari") -> list[Record]:
    """Convenience for callers that only need the records, not the Scoped value."""
    return cut(persona, role).records
