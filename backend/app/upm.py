"""UPM: the Unified Patient/Persona-record envelope.

Every sourced record is wrapped in the same envelope. The payload keeps the
shape its source system would emit -- we do NOT normalize it. That sloppiness
is deliberate: it is the swap seam. When real Veil.AI data arrives, only this
model (and fixtures.py) should need to change.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Illustrative demo policy vocabulary, not a legal taxonomy.
SensitivityTag = Literal["none", "sensitive", "highly_sensitive"]
ConsentScope = Literal["treatment", "social_care", "research", "self", "unknown"]
# Compatibility projection of which holder a lens covers; never a permission grant by itself.
ConsentHolder = Literal["self", "guardian"]
KypsyysarvioValue = Literal["paatoskykyinen", "ei_paatoskykyinen_tiedot_huoltajalle"]
# Illustrative life-stage axis, declared by the modelled lens. Never derived from a birth year:
# age is a triage input, not a permission or relevance category (see issue 04).
LifeStage = Literal["child", "adult"]
RecordState = Literal["draft", "final", "approved"]

# The lengths Azure SQL's columns have (`sqldelivery.SCHEMA`: NVARCHAR(128) on the id columns,
# NVARCHAR(256) on the label columns). Enforced HERE as well, so the two engines refuse the same corpus:
# without them the local import accepted a 200-character id and the SQL INSERT then failed with Msg 8152
# and rolled the WHOLE load back -- "`import_delivery` succeeded and `load_sql` died" on arrival day.
ID_MAX_LENGTH = 128
LABEL_MAX_LENGTH = 256
class Heading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local: str
    national: str

    @model_validator(mode="after")
    def _known_pair(self):
        if (self.local, self.national) not in HEADING_PAIRS.values():
            raise ValueError("heading must be a pair from the source mapping")
        return self


# Course 104, module 2333: "HTA-otsikot (Yhteydenotto ja Hoidon tarpeen arviointi)
# voidaan organisaation pyynnöstä liittää kansallisiin otsikoihin, jolloin Omakantaan
# siirtyvät otsikot Etäkontakti (Yhteydenotto) ja Suunnitelma (Hoidon tarpeen arviointi)."
# Course 89, module 1902 supplies the national heading list. Esitiedot is also a
# maternity-pathway component name; do not reuse it as a label for our groupings.
# Toimintakyky is the national heading THL Sote-sanastot uses for functional-capacity assessment
# content; the vendor corpus's own local heading for that content is "Testaus- ja arviointitulokset"
# (course 89, module 1902 — see ADR-0015's provenance table), so the pair below aliases that local
# heading to the national one, the same pattern as the Yhteydenotto/Hoidon tarpeen arviointi pairs.
# "Testaus- ja arviointitulokset" itself keeps its own identity entry below for records that cite it
# verbatim; this is an ADDITIONAL alias, not a replacement.
HEADING_PAIRS: dict[str, tuple[str, str]] = {
    "Yhteydenotto": ("Yhteydenotto", "Etäkontakti"),
    "Etäkontakti": ("Yhteydenotto", "Etäkontakti"),
    "Hoidon tarpeen arviointi": ("Hoidon tarpeen arviointi", "Suunnitelma"),
    "Toimintakyky": ("Testaus- ja arviointitulokset", "Toimintakyky"),
    **{name: (name, name) for name in (
        "Nykytila", "Esitiedot", "Diagnoosi", "Lääkehoito", "Fysiologiset mittaukset",
        "Toimenpiteet", "Testaus- ja arviointitulokset", "Hoitotoimet", "Konsultaatio/Lähete",
        "Ennaltaehkäisy", "Suunnitelma", "Tulosyy",
    )},
}


class Kypsyysarvio(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: KypsyysarvioValue
    assessed_by_role: str
    assessed_at: datetime
    origin_organisation: str | None = None
    scope: Literal["palvelutapahtuma", "resepti"] = "palvelutapahtuma"
    # Required exact medication-record binding when scope is `resepti`.
    prescription_record_id: str | None = None
    # No statute value ships without owner/legal approval.
    legal_basis: str | None = None

    @model_validator(mode="after")
    def _scope_has_one_context(self):
        if (self.scope == "resepti") != (self.prescription_record_id is not None):
            raise ValueError("prescription-scoped assessment requires exactly one prescription_record_id")
        return self

    @property
    def consent_holder(self) -> ConsentHolder:
        return "self" if self.value == "paatoskykyinen" else "guardian"


class FormMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    form_type: str
    service: str
    version: str | None = None
    due_at: datetime | None = None


class Record(BaseModel):
    # extra="forbid": a misspelled envelope key must fail at load, not be dropped
    # silently at the source swap seam.
    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(max_length=ID_MAX_LENGTH)
    source: str
    record_type: str
    # Two spellings of the doctor role are deliberately different namespaces, and must not be unified.
    # `laakari` is the PRODUCT role key the permission cut uses (visibility.json, policy.cut). The umlaut
    # spelling is the source's verbatim clinician_role, pinned byte-for-byte by test_veil_corpus.py. This
    # field is read for display and provenance only, never by the cut. A future reader that filters by
    # author normalises at the MAPPING layer, never in the database.
    authored_by_role: str
    recorded_at: datetime
    topic: str
    # heading is authoring structure; topic remains the coarse decision-table axis.
    heading: Heading | None = None

    @field_validator("heading", mode="before")
    @classmethod
    def _map_heading(cls, value):
        if isinstance(value, str):
            try:
                local, national = HEADING_PAIRS[value]
            except KeyError as exc:
                raise ValueError(f"unknown source heading {value!r}") from exc
            return {"local": local, "national": national}
        return value

    # `record_type` and `topic` were plain `str`, so the model accepted any spelling while the SQL CHECK
    # constraints (and `policy.cut`) declared a closed set: the two layers disagreed about what a record is.
    # These are the SAME declarations the delivery importer and the SQL CHECKs read (`source.RECORD_TYPES`,
    # `purpose.topics`), imported lazily because `source` imports this module.
    #
    # The reconciliation pass is the one deliberate exception. Reporting an undeclared vendor spelling is
    # `dry_run_delivery`'s job, so it passes a validation context instead of the model being looser for
    # everyone; the write path (`_bind_record`, the SQL loader) validates with no context and refuses.
    @field_validator("record_type")
    @classmethod
    def _declared_record_type(cls, value: str, info) -> str:
        from .source import RECORD_TYPES

        if info.context and info.context.get("allow_undeclared_record_type"):
            return value
        if value not in RECORD_TYPES:
            raise ValueError(f"undeclared record_type {value!r}; declared record types: "
                             f"{sorted(RECORD_TYPES)}")
        return value

    @field_validator("topic")
    @classmethod
    def _declared_topic(cls, value: str, info) -> str:
        from .purpose import topics

        if info.context and info.context.get("allow_undeclared_topic"):
            return value
        known = topics()
        if value not in known:
            raise ValueError(f"undeclared topic {value!r}; declared topics: {sorted(known)}")
        return value
    # Intent only: concept_key maps to a source code system; it is not itself a code.
    code_system: str | None = None
    # Provenance only; absence preserves the legacy settled-evidence behaviour.
    state: RecordState | None = None
    responsible_approver: str | None = None
    # Patient-level responsible clinician: display-only source text, distinct from the record-level
    # `responsible_approver`. No ownership entity, registry or permission meaning.
    responsible_clinician: str | None = None
    # Must remain empty until the owner approves a specific legal basis; absence beats an unapproved value.
    legal_basis: str | None = None
    # Required, not defaulted: a renamed/missing source field must fail loudly,
    # never fail open (the previous "none" default declassified the record).
    sensitivity_tag: SensitivityTag
    consent_scope: ConsentScope
    # Source-shaped payload: intentionally untyped so fixtures stay honest.
    payload: dict[str, Any] = Field(default_factory=dict)
    source_person_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)
    encounter_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)
    case_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)
    form: FormMetadata | None = None
    concept_key: str | None = None
    # `käyntiaika` is source provenance, distinct from when the record was entered.
    käyntiaika: datetime | None = None
    # This entry's recipients only; never prior `yhteydenotot` contact history.
    vastaanottajat: list[str] | None = None
    erikoisala: str | None = None
    # Display-only provenance, shown as "kirjaava yksikkö"; never a permission axis.
    suorituspaikka: str | None = None
    # Legacy/source projection, retained for old rows. An explicit encounter Kypsyysarvio supplies
    # the effective projection in policy; when absent, this preserves today's behaviour byte-for-byte.
    consent_holder: ConsentHolder | None = None

    def projected_consent_holder(
        self, assessment: Kypsyysarvio | None, encounter_id: str | None,
        default_holder: ConsentHolder | None = None,
    ) -> ConsentHolder | None:
        """Project only matching encounter/prescription context; otherwise preserve the legacy holder."""
        if assessment is not None and self.encounter_id == encounter_id:
            if assessment.scope == "palvelutapahtuma" or (
                self.record_type == "lääkitys" and self.record_id == assessment.prescription_record_id
            ):
                return assessment.consent_holder
        if default_holder is not None and self.encounter_id == encounter_id:
            return default_holder
        return self.consent_holder


class Persona(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona_id: str = Field(max_length=ID_MAX_LENGTH)
    label: str = Field(max_length=LABEL_MAX_LENGTH)
    records: list[Record]
    source_person_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)
