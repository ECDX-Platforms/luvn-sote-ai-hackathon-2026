# ADR-0020 — FHIR R4 is the mapping target; the archived Health Accelerator is rejected as a shape donor

Status: accepted by the author, 2026-10-10, as a direction to record and **not** to migrate. No implementation
is authorised by this record. It settles what to cite and what to refuse, so that the question is not reopened
during the delivery week.

## Context

The real corpus arrives 2026-10-13: 50,000 synthetic person profiles combining health and social services per
person, as CSV or JSON, with the structure unknown until the day. An entity decomposition was proposed —
adopting the data model of Microsoft's archived Dynamics 365 Health Accelerator, a FHIR R4 model on Dataverse —
on the argument that the current three-table storage strands our own 25-field `Record` envelope in a JSON blob.

That argument was examined adversarially and is partly right and partly misattributed.

**What is right.** `backend/app/upm.py` defines a 25-field envelope and `backend/app/sqldelivery.py` persists it
with one line, `record.model_dump_json()`. The DDL therefore exposes four columns and leaves 21 fields beyond the
reach of any index, join, or constraint. The real defect is not invisibility but **unenforceability**:
`CHECK (json is well-formed)` validates syntax only, so every invariant the envelope declares — a required
`sensitivity_tag`, the heading pair, the `state` enum — is bypassable by a single direct `INSERT`.

**What is misattributed.** The proposed donor is 334 entities (326 custom, 8 reused out-of-box), of which
roughly 290 are FHIR *backbone element* tables named after FHIR element paths. Against that, the envelope spans
about six entities — the comparison is 2%, not two-thirds. Its coding layer is four columns with **no
code-system identifier at all**: the entire notion of which coding system a code belongs to is a 163-member
numeric picklist. That is precisely the defect ADR-0015 already records in us, so adopting it would import the
bug we are trying to fix. It ships **no security roles** (`<Roles></Roles>`, zero `<RolePrivilege>`) and
contains **no record-level access logic** (no `ownerid`, `grant`, `AssignRequest` or `PrincipalAccess` handling),
so it cannot supply the permission machinery that is this product's core. Its resources are STU3/R4 drift
(`ReferralRequest` and `BodySite` were removed or dropped; `ProcedureRequest` is the pre-R4 name), its README
links the unversioned FHIR specification, and its artifacts are dated 2018-08-15.

The owner also observed that the delivery is CSV, not a database — which is true but does not make a retired
Dataverse model the remedy.

## Decision

1. **FHIR R4 is the mapping target.** When the corpus structure is known, the corpus is mapped toward FHIR R4
   resource vocabulary — `Patient`, `Encounter`, `EpisodeOfCare`, `Condition`, `Observation`, `MedicationRequest`,
   `Procedure`, `CarePlan`, `ServiceRequest`, `Task` — as a **mapping**, not a migration. The canonical internal
   target remains the existing `Record` envelope. **R4 is not the newest FHIR release:** HL7's own R4 landing
   page says "The current version which supercedes this version is 5.0.0", and R4 is labelled "Mixed Normative
   and STU". This record claims R4 as the *mapping* target only, and no document in this repository may call R4
   current. *Why this is written down: "R4 is the mapping target" is not a currency claim, and the distinction
   was previously left to the reader — see [`../references.md`](../references.md) §4 and §8.*
2. **The archived Health Accelerator is rejected as a shape donor.** It is cited in this record only as an
   anti-pattern, with the 334-table outcome and the absent code-system identifier as the evidence. It is not a
   component, not an authority, and not a template. Per its own README it is a *lossy re-expression* of a public
   standard; if FHIR shape is wanted, take FHIR.
3. **openEHR conformance is refused, and the refusal is about what conformance means there.** This record does
   **not** claim "openEHR alignment" as a partial conformity. FHIR R4 is an exchange standard that defines
   resources and a framework implementers constrain with profiles, whereas openEHR conformance is
   **certification against the notional openEHR Platform defined in openEHR's own CNF component** — its
   Conformance Guide, Platform Conformance Test Schedule, Platform Profiles and Conformance Certificate are
   published at **DEVELOPMENT** status — so it is a certification we have not undergone and whose artefacts
   (archetypes, templates, OPTs, terminology bindings) we do not have. No relationship between HL7 and openEHR
   is asserted anywhere in this repository: no primary joint statement was retrieved.
   [`../references.md`](../references.md) §4 carries the quotes and marks that relationship **unverified**.
4. **No structural migration before the corpus settles the shape.** Migrating to entity tables before seeing the
   delivery would commit to a schema chosen without evidence, and `upm.py` deliberately leaves the payload
   unnormalised *because the real schema is not yet known*.
5. **The enforceable invariants are the real prize, and they do not require 334 tables.** The six columns
   examined for promotion were narrowed by review to **`record_type`, `sensitivity_tag`, `consent_scope`** —
   projection plus indexes, keeping `record_json` as the source of truth. `topic` was rejected because it is
   functionally dependent on `record_type` and purpose and its silent-exclusion semantics hide a bad value;
   `state` because no policy predicate reads it and `WHERE state = 'final'` would silently drop unset rows;
   `case_id` because it is free-form with no join target. `consent_holder` was rejected outright: it is an
   *encounter*-level axis, and promoting it onto `records` would create a second source of truth for a permission
   axis — the inversion ADR-0013 exists to prevent.
6. **The projection is deferred until its consumer exists.** `policy.cut` filters in Python and the SQL index
   reads `SELECT record_json WHERE source_person_id = ?`, so promoted columns would be write-only until the read
   path changes, and the schema change forces recreate, re-harden, re-grant and a full reload. Adding columns
   with no reader is cost without benefit; recording the decision is not.

## Consequences

- A future reader knows the direction and does not re-propose the archived accelerator. This is the record's
  main purpose: to make the rejection durable rather than a conversation.
- The vocabulary question stays open until the corpus supplies it — which is the point, and is why the mapping
  layer is built to be revised without a redeploy.
- The unenforceability of the envelope's invariants remains an open defect. It is named here so it is not lost,
  and it is answerable with three columns when a reader exists for them.
- Nothing in this record claims clinical adequacy. The organiser states that the corpus may not be used as
  evidence of clinical or professional performance, and the review's standing objection — that the production
  verifier has never been validated against clinician labels — is untouched by any storage decision.
