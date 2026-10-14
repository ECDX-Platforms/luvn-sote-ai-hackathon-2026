# Picture is typed at the external seam; internal deciders stay untyped

## Status

Accepted and implemented: `Picture` is the shipped contract at the seam, with
owner-approved extensions.

## Context

The situation picture — what the API returns and `App.jsx` consumes — is a
Pydantic `Picture` model in `backend/app/picture.py`, the single definition of
that contract. FastAPI derives the OpenAPI schema from it, and
`backend/tests/test_contract.py` pins the keys the UI reads. Feeding it, `decide` deliberately keeps exchanging plain
dicts: the silent-break failure mode only exists where the contract crosses to
another language, so typing the internal hops would add interface surface
without adding depth. (`grounding` is the one exception: its validator returns
a typed result rather than a plain dict.)

## Considered options

- **Type every layer end to end** (`Decision`, `Features`, `TraceEntry`).
  Rejected: adds interface surface with no second consumer — nothing outside
  Python reads those shapes.
- **Leave the whole thing as untyped dicts.** Rejected: renaming a key failed
  silently in the UI (rendered `undefined`) with no test to catch it.

## Consequences

- Internal refactors stay cheap; only a change to `Picture` is a contract change.
- `visible_record_ids` and the triage `features` are intentionally absent from
  `Picture` — they are decision-layer internals and must not cross the seam.
- Narration provenance crosses the seam through `narration_generation_status`
  and `narration_generation_model`: a real model call is distinguishable from
  unavailable, failed, or timed-out generation, so a half-configured
  environment cannot silently masquerade as Foundry.

## Synthetic form and duplicate projection

**Owner-approved extension.** The source `Record` may carry optional
`FormMetadata` (`form_type`, `version`, `service`, and an explicit source `due_at`) and an
optional `concept_key` assigned by deterministic source mapping/fixtures. The key names the
same measured observation, including its subject/window; similar text or values alone never
create a match. `Record.recorded_at` is filled-at and `Record.authored_by_role` is filled-by
role, reused rather than duplicated in the source envelope. Existing structured
assessments project `instrumentti`/`versio` and use `Record.topic` only as a
modelled service label; no due date is inferred from an assessment.

The selected-record projection adds:

- `RecordView.form: FormProvenance | None`, exposing selected form type/version/service,
  filled-by role and optional source due date. Existing `RecordView.recorded_at` remains the
  form's filled-at timestamp.
- `RecordView.concept_key: str | None`, only when the source/fixture explicitly assigned it.
- `Picture.duplicate_facts: list[DuplicateFact]`, groups the explicit concept key when at
  least two distinct selected form types carry it. Each group contains only selected
  `record_ids`; both source values remain on their ordinary source views. No source is picked
  as winner and no source is hidden.
- `Picture.form_catalogue: list[FormCatalogueEntry]`, a purpose-only synthetic catalogue of
  modeled required/optional entries. It reports `present`, `incomplete`, `due` only if a
  selected form supplies a `due_at` that has passed, or `not_shown_in_briefing`. The last
  status means absent from this selected view, not absent from the person's source data or
  legally missing. Entry source IDs are only IDs in selected `Picture.records`; suggestions
  are optional, synthetic and professional-controlled. These rows make no statutory/clinical
  due claim and add no age/life-stage dimension.

This extension does not disclose `Poissuljettu` identities/counts and introduces no authority
precedence. It is an explicit wire change, covered by `test_contract.py`; tests pin that every
record ID in duplicate/catalogue metadata is a subset of selected `Picture.records` IDs. RAI
remains a supplied source result, consumed as given rather than as a scoring engine.

## Phase 3 illustrative consent-holder and life-stage vocabulary (source envelope only)

**Owner-approved illustrative default; non-statutory.** Phase 3 adds vocabulary to the
*source envelope and the modelled lens*, and deliberately changes **no** `Picture` field:

- `Record.consent_holder` (`self` | `guardian`, optional) declares which consent a source record
  requires. `Encounter.consent_holders` declares which holders a lens covers, and `policy.cut`
  filters before selection, so an uncovered record never reaches the briefing at all.
- `Encounter.life_stage` (`child` | `adult`) is declared by the modelled lens. It is never derived
  from a birth year: age is a triage input, not a permission or relevance category. The form
  catalogue is keyed by `(purpose, life_stage)`, extending the Phase 2 purpose-only key.
- The uncovered record is reported only as the existing neutral `not_shown_in_briefing` state, with
  no ID, count, flag or distinct "hidden" reason, so a lens cannot be probed for what it withholds.
- A minor may hold their own confidentiality rights for some service areas, so "the guardian lens
  sees everything" is not a safe default; the neuvola lens here covers `guardian` and does not cover
  a `self` record. Represented-person verification, expiry and real consent rules remain unmodelled
  and are not claimed.

## Owner-approved extension — source heading and entry provenance

The owner explicitly approves this nested `RecordView` wire extension for the alignment follow-up:
`heading` is a local/national pair, and optional `käyntiaika`, `vastaanottajat`, `erikoisala` and
`suorituspaikka` are projected only when the source record carries them. `toiminto` is not projected:
no purpose rule gives it a meaning. Missing values are omitted, not rendered as empty slots.
`suorituspaikka` is display-only (`kirjaava yksikkö`), not a permission axis; `vastaanottajat` is for
this entry only, never contact history. No top-level `Picture` field is added, and no fixture values are
authorized by this wire decision.

## Owner-approved extension — the declared purpose of use crosses the seam

The owner approves one top-level field, so that the demo's **purpose of use** is served rather than
paraphrased in the UI:

- `Picture.intended_use: str | None`, a display-only product statement of what the briefing is for
  (*näyttää ammattilaiselle tämän palvelutilanteen kannalta olennaiset ja sallitut esitiedot, jotka on
  koottu lähteistä ja tarkistettu lähdeviitteisiin*), single-sourced from `backend/app/intended_use.py`
  so the served value and `docs/regulatory-position.md` cannot drift.

Its limits, stated as the limits of the field:

- **It is a product statement, not a legal classification.** It carries no statute, regulation article,
  risk class, device class, conformity statement or deadline. The classification reading lives only in
  `docs/regulatory-position.md`, explicitly labelled non-authoritative, and no such value is reachable
  from the UI (`legal_basis` stays empty and absent from `RecordView`).
- **It discloses nothing about a withheld record.** The sentence is encounter-independent text: no ID,
  no count, no denominator, and no state that varies with what `policy.cut` removed.
- **It grants no authority precedence.** It is not an input to `policy.cut`, selection, `decide`,
  assessment, proposals or narration, and it changes no permission axis.
- **Absence renders nothing.** A picture without the field shows no row, so an older payload or a
  non-narrated view is unchanged.

This is an explicit wire change, covered by `test_contract.py` (the key is in `FRONTEND_KEYS`), by
`tests/test_intended_use.py` (the served sentence appears verbatim in the regulatory-position document,
and contains no article, statute or class marker), and by the frontend pinned-string inventory.
