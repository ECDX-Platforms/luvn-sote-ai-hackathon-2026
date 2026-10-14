# LUVN — implemented architecture and Decision-1 relevance contract

**This document is the detailed record** — every stage, its evidence, and how each claim was verified. For the same system explained plainly, with diagrams, read [`ARCHITECTURE.md`](../ARCHITECTURE.md) first.

**Status.** The synthetic product demo is deployed as a container app on Azure Container Apps, behind Entra Easy Auth at the ingress, and reads back `Provisioned` / `Healthy` with 100% of traffic, `minReplicas: 2` and `maxReplicas: 4`. It was shipped by a targeted `az containerapp update`, not a template deployment.

The product is one backend-backed briefing with a day view and a record view. The day view lists the scheduled encounters with a warm-up readiness indicator and opens clinical appointments in the doctor lens. The record view carries the pre-filled structured forms (accept or edit), the attention panel, and the assistant docked as a right-hand side pane on every destination. Decision-1's findings appear as information rows in the attention panel — what it added, what it put first and what it called relevant and current — with the ordering caveat kept off the chips. The doctor lens sees the whole clinical record, and a result from the last year is baseline in every view. The diagnoses table is folded to one row per code and status with historical medications behind a disclosure; lab labels are plain Finnish; and the narration agent writes a five-to-seven-sentence source-grounded summary. A Käypä hoito question publishes the guideline citation beside the verified sentence. Knowledge retrieval runs over Azure SQL `VECTOR(1536)` with `text-embedding-3-large` (514 chunks loaded, with a token-overlap fallback when it is unconfigured). The component library is the UI authority (ADR-0026), with its own `AppShell`, tables and blocks; the tool-using assistant is the only conversational surface (ADR-0027), the keyword route is deleted and the opening prompts are derived from the attention summary. Decision-1 runs as a 30-second stage with 10-second calls inside a 160-second preparation window.

## Current architecture — read this first

The sections further down record how this was built. **This section describes what the
system is now.** Each claim is pinned by a test or an artifact; where one is not, it says so.

### The four layers, and the one boundary rule

| layer | what it decides | where |
| --- | --- | --- |
| Deterministic core | permission, baseline selection, decisions, assessments, proposals, catalogue, duplicate grouping, impact | `policy`, `selection.baseline`, `pipeline`, `decide` |
| Decision-1 (classifier) | which *permitted* free-text notes are relevant, and whether a narration sentence is supported | `decision` |
| Foundry (generator) | ≤4 cited Finnish sentences | `narration`, `llm` |
| UI | presentation of the above, with 0-click access to sources, why-relevant, coverage and impact | `frontend/src` |

The response also carries the demo's declared purpose of use as the display-only `Picture.intended_use`
(single-sourced from `app/intended_use.py`; ADR-0001), and machine-readable marking headers for
model-drafted text (`app/provenance.py`; ADR-0017) whenever the generation branch produced accepted prose.

**Boundary rule: every AI path reads only `Scoped`** — the post-`cut` record set. Nothing upstream of the cut
is reachable by Decision-1, by the generator, or by the browser.

### One construction seam for permission

`policy.cut_for_encounter(persona, encounter) -> Scoped` (`backend/app/policy.py`) is the single place a
permission context is built, and it is what makes the boundary rule enforceable rather than merely intended.
Its callers and guard coverage are enumerated in
[Resolution seam, Record provenance and delivery adapter](#resolution-seam-record-provenance-and-delivery-adapter).
The AST guards reject direct/attribute `cut` calls and imports, plus direct/attribute `Scoped` construction
and construction through an imported alias; type-only `Scoped` imports remain allowed.

**Why it exists.** Two API paths previously assembled the context differently: the encounter list called
`cut(persona, role, purpose, id)` while the feedback route passed `consent_holders` and `kypsyysarvio`. Capacity and consent are per-encounter rather than per-person: the modelled `Record` carries `life_stage` and a derived `consent_holder`, which is a projection of the encounter's `Kypsyysarvio` and never a second identity. That
index therefore computed an identity header over a **wider** record set than the picture and could name a
patient whose identity the guardian-scoped lens withholds. A later revision of the seam unified the context, and the
**disclosure itself was closed later still**, when `person_name`, `fixture_label` and
`identity_available` from `/api/encounters` — so that index now serves **no per-lens cut data at all**.
Regression tests: `test_runtime_api.py:268` and `:302`.

Case-level restriction runs through the same seam: the parameter existed but **no caller passed
it**, so the restriction never fired until the seam wired it.

### Invariants (non-negotiable, each test-pinned)

1. **Silent absence** (ADR-0004) — an excluded Kirjaus's identity, text and **count** are disclosed nowhere.
   Not by a row, a badge, or a total. Even a zero-vs-nonzero indicator is forbidden.
2. **Cause-neutral absence** — the absence state renders **identically** for every reason.
   Pipeline states (not shortlisted, budget, too long, unavailable) are labelled per row; the cut never
   produces a row or a count at all.
3. **No derived number may require a withheld denominator.** Every displayed derived number must be
   recomputable from records already rendered, or it is absent. One sentence settles the coverage line, the
   severity count and the impact estimate. It does **not** govern the 50k import report, which is source-wide
   and never role-facing.
4. **An absence is never filled.** No invented identity, name or value is presented where the source is
   silent. Unavailable identity renders neutrally.
5. **No computed thresholds, ranges or legal claims.** A lab reference range is rendered **iff the source
   supplies one**; it is never derived. `legal_basis` is **absent from `RecordView` entirely** — not empty,
   not defaulted — and no candidate statute is UI-reachable.
6. **A draft is never narrated as settled.** Drafts are excluded from generation input, remain
   policy-visible, and render grey and italic.
7. **0 clicks** to sources, why-relevant, coverage and impact.

### What the wire carries

Per-record provenance is the point of the wire: `käyntiaika`, `vastaanottajat` (this entry's recipients only —
never a recipient history), `erikoisala`, `suorituspaikka` (**display-only**; never a permission axis),
`heading` (a validated local+national **pair**), `code_system`, `state`, `authored_by_role`,
`responsible_approver`, `responsible_clinician` (patient-level, display-only, distinct from the approver),
`encounter_id` and `case_id` as permitted attribution only, `temporal_status`, `selection_reasons`, `form`,
`concept_key`. Required `RecordView` fields also include `record_id`, `source`, `record_type`, `topic`,
`recorded_at` and `payload`; optional `Record` field `source_person_id` binds a source record to its
subject before the permission cut. The complete model surface is named here **and nowhere else** — the seam
section below links back to this list rather than repeating it — and [`CONTEXT.md`](../CONTEXT.md) carries the
domain vocabulary.

Absent `form` and `concept_key` are now omitted like other optional fields, so absence renders nothing.

Document kinds: `source.DOCUMENT_KINDS = ("todistus", "lausunto", "ilmoitus")` is **source vocabulary only** —
a constant, zero record instances, no rendering, and no participation in coverage counts.

### Narration, and which path produced it

`Picture.narration_generation_path` records the branch that **actually ran**: `agent_reference`,
`direct_fallback`, or `direct_chat_completions` when no agent is configured. It is never inferred from
configuration — `versioned_agent_result` is true only for accepted `agent_reference` prose. This exists
because a configured agent once failed token acquisition at 0 ms and **every** briefing silently
fell through to the direct fallback while looking perfectly healthy; the trial could equally have recorded
`agent_version: 8` without Agent v8 ever running.

- Permission is checked before generation; generation sees `Scoped` only.
- **One** bounded re-prompt, shared preparation deadline (`PREPARATION_WINDOW_S = 160.0`): invalid membership,
  all-unsupported/unknown, and a citation-less draft are all retryable. Retry feedback carries only rejected
  sentence IDs, reasons and permitted citation IDs — **never the rejected prose**.
- **A safety hit is terminal** — no retry. A content-filter block does **not** fall back to the direct path.
- Fail-closed: any failure yields the deterministic briefing with **no prose**.

### Appointments, the briefings cache and the warm-up job

The zero-touch claim as a data path, each step pinned by `backend/tests/test_briefings.py`.

- **Appointments.** The loader writes the release's scheduled occasions to the SQL `appointments` table
  (`sqldelivery.py`; indexed by `ix_appointments_day`, keyed to `people` and `encounters` by foreign key). The
  day view's read, `GET /api/worklist` (`main._worklist_day_rows`), joins one day's rows to the lens that opens
  each (`appointments_for_day`) and to the cache's `attention` summary; it reads **no record** and runs **no
  pipeline**. `readiness` is `ready` when a row built *today* exists, whichever day is being planned.
- **The cache** (`briefings.py`, the `briefings` table, created by the loader and written only by the app).
  `BriefingKey` = `encounter_id` + `corpus_generation` (what `load_state` names) + selection `RULE_VERSION` +
  `agent_version()` (`LUNA_FOUNDRY_AGENT_NAME`:`LUNA_FOUNDRY_AGENT_VERSION` | decision model | `PREFILL_VERSION`).
  `today` is a column beside the row, not a key part; `is_fresh` requires `status == "ready"` and today's date,
  so a stale row is rebuilt and the rebuild replaces it (`test_a_stale_row_is_rebuilt_and_a_rebuild_replaces_it`).
  The stored object is the post-`cut` `Picture`; the route still authorizes and audits every read and answers
  `X-LUVN-Briefing-Source: cache` or `built`. The compare arms (`decision-off`, `foundry-off`) are never cached.
  Fixture and delivery-directory stores get `NullBriefingCache` unless `LUVN_BRIEFING_CACHE=memory`. A cache
  write that fails is logged, and the briefing just built is still released.
- **The warm-up job.** `python -m app.warmup --days N [--concurrency 2] [--role …]` collects the encounter ids
  of the coming days' appointments and calls `briefings.warm`, which builds every id with no fresh row and
  returns `requested` / `fresh` / `built` / `failed`; one failure never stops the run, and nothing is deleted.
  It runs as the Container Apps job `luvn-warm-briefings` on the app's image and identity (the identity holds
  `db_datareader` plus `INSERT`/`UPDATE` on `briefings` only), scheduled at 04:00 UTC — the schedule is Azure
  state, not a file in this repository — and is started by hand after a corpus load,
  because the generation is in the key. A request that misses builds the same picture once through
  `briefings.build_and_cache`; the chat routes read the same row (`_chat_picture`) rather than rebuilding.

### The pre-fill stage

`prefill.py`, called by `pipeline` on the post-`cut` set (`prefill.build(permitted, purpose, today, …)`),
carried on the wire as `Picture.esitiedot` and `Picture.prefilled_forms`; pinned by `backend/tests/test_prefill.py`.

- **Level 1 — the county's own headings, deterministic.** `CLINICAL_HEADINGS` (`Tulosyy ja esitiedot`,
  `Terveyteen vaikuttavat tekijät`, `Riskitiedot`, `Aikaisempi hoito`, `Lääkehoito`, `Fysiologiset mittaukset`,
  `Toimintakyky`; `SOCIAL_HEADINGS` for `functioning_service_needs`) are filled from the permitted notes'
  `payload.osiot` sections and the structured records that belong under each (medications, diagnoses,
  measurements), every item citing its records, at most `MAX_ITEMS_PER_HEADING` per heading. A heading
  nothing permitted answers is `unknown`, rendered *Kysy vastaanotolla*. No model runs; level 1 never fails.
- **Level 2 — the official forms, verified.** `FORMS_BY_PURPOSE` declares which of the release's forms belong
  to each purpose, honestly (`official`, or `related` with a note where no official form exists). One
  strict-schema `gpt-6.1-sol` call per form (`PrefillAnswerSet`, extraction only, inference forbidden by the
  instructions) proposes answers; an answer citing a record outside the permitted set is dropped before any
  verifier call; **every** remaining answer is verified by Decision-1 against exactly its cited records
  (`decision_model.verify_narration`, batches of `VERIFY_BATCH`), and a verdict other than `supported`
  downgrades it to `unknown`. The published citations are the **verifier's** `supporting_record_ids`, never
  the model's claim. A fully answered scored form gets `assess.score_from_form`; a partial one gets no total.
- **Budget and failure.** `PREFILL_BUDGET_S` (env `LUVN_PREFILL_BUDGET`, default 45 s) inside the shared
  preparation deadline; a slow or failed generator yields an open form (`timed_out` / `failed`), never a failed
  briefing; without Foundry every form is listed `unavailable` and level 1 still answers.
- **`PREFILL_VERSION`** (`"prefill-1"`) is part of the briefings cache key, so a changed pre-fill is a different
  briefing and the warm-up rebuilds it.

### Delivery ingest (the 50 000-record path)

`delivery.dry_run_delivery(source_dir, output_dir) -> DryRunReport` **reports**; `import_delivery(...) ->
ImportReport` imports. A refusal **writes its report first**, so `results/delivery-reconciliation-report.json`
always exists after a refusal and records how many rows were examined and refused and, per distinct reason,
the count plus up to five example locators with the offending values. Payload-**key** aliases are real and
map vendor key names to ours. **Record-type aliases are currently an identity map of canonical names**, so no
vendor record type is actually remapped today — do not describe that as working. The alias layer stays
separate from the permission path. Severity rule: an unmapped **payload key** is a warning; a record whose
permitted content maps to nothing is a **hard failure**, named and counted; a source-binding, `recorded_at`,
sensitivity, consent-scope, record-type or **topic** miss stays a **hard failure**. An undeclared `topic`
(and an unknown `record_type`) is refused rather than silently excluded by the purpose filter, which is what
used to turn an unknown vendor vocabulary into an empty briefing that looked like a working app.
`import_delivery(..., on_error="quarantine")` is the opt-in alternative: a row refused by envelope
validation, an undeclared vocabulary value or an unresolvable reference is staged in
`quarantine/quarantine.jsonl` with its locator, raw row and reason, and the remainder loads; the default is
still strict, and `retry_quarantine` re-attempts stored raw rows after a vocabulary revision. `landing.py`
lands bytes verbatim behind a `delivery_run` manifest (SHA-256, bytes, content type, received_at) for any
layout, and `profile_delivery` reports the shape — encoding, delimiter, header, per-field type/null
rate/distinct/min/max/samples, candidate vocabularies and candidate roles — without mapping anything.
`app/mapping.py` plus `backend/mappings/delivery-mapping-v1.json` (PROVISIONAL) is the versioned,
run-time-loaded `source_path -> target_field` layer with `identity`, `const`, `lookup_valueset` and one date
parser (`DD.MM.YYYY`, ISO-8601, epoch); an unknown source path is a hard failure. Payload key names remain
**PROVISIONAL** until the real delivery confirms them. That delivery has not been loaded.

### What was decided against

Rejected, with reasons, so they are not re-litigated: the `potilaskansio` **aggregate** index (counts and
specialty maps need a withheld denominator); `alueellisuus` as a concept **and** a field (a present mark
discloses another organisation's care; a lens-varying mark would itself disclose a difference between
lenses); `Kiellot` in any form, including as a modelled record; the `palvelupolku` hierarchy; `toiminto`;
the citizen/end-user view; and recorded narration.
Candidate legal bases are **NAMED-NOT-RENDERED** rather than rejected: no value ships and none is
UI-reachable. Grouping permitted records by encounter/case **is** allowed — that is provenance, not an index.

### Standing limitations — state these, do not smooth them

Thresholds were inherited and are **not calibrated on real Finnish data**. Fixtures are synthetic,
author-authored and **unadjudicated**; the floor ablation is sensitivity evidence over **one recorded
candidate set**, not accuracy. The recorded evidence run **is** the path-instrumented run — what it lacks is
a **live Agent v8** trial, since its configured agent version is 7 while the deployment runs
`luvn-narration:8`. Contrast is covered by a hardcoded WCAG table, **not** exhaustive axe analysis. Hosting
template drift remains open for residual review, with the live ARM values recorded alongside. A
read-only what-if confirms NoChange for the three recorded auth/storage properties; no template was applied. The full diff still has Container App default/
probe projections, role references and App Insights defaults, so deployments remain **targeted** until those
residuals are reviewed and separately approved. Roles are **synthetic presentation lenses, not Entra
authorization**. This demo does not establish clinical accuracy, legal compliance, per-professional
authorization or 50 000-profile throughput.

The selection-only F2 additions figure is **derived** into the evidence summary and deliberately **not
restated here** — a document-claims check enforces that, so prose cannot drift from the artifact.

## Resolution seam, Record provenance and delivery adapter

- `main.encounter_picture` enters the sole public Picture construction path, `pipeline.build_picture_for_encounter`. After server-owned encounter/person resolution, `_build_resolved_picture` calls `policy.cut_for_encounter` exactly once and passes that `Scoped` value into decision and narration. Feedback proposal evaluation has a separate `cut_for_encounter` call, but does not construct a Picture. `test_encounter_policy_cut_is_owned_by_one_seam` guards direct `policy.cut` calls/imports and `Scoped` constructions (including aliased constructors) outside policy; `test_every_narration_path_is_downstream_of_policy_cut` asserts one cut before narration and pins generation/verifier call sites downstream.
- **Per-record provenance semantics** (the field names are enumerated once, under [What the wire carries](#what-the-wire-carries)): encounter/case identifiers are attribution on a permitted, selected record and never a history index; the patient-level responsible-clinician text is display-only and distinct from the record-level approver; the selected-entry provenance fields are shown on that entry only and never derive permission. The doc-claims gate checks the field names against the wire list above and `CONTEXT.md`.
- The delivery adapter's `RECORD_TYPE_ALIASES` and `PAYLOAD_KEY_ALIASES` are provisional mappings for the assumed schema, and `backend/mappings/delivery-mapping-v1.json` is the versioned declarative `source_path -> target_field` layer a mapping revision edits instead of code. `dry_run_delivery` uses the same aliases, reports unknown/missing payload keys and never imports data; identity binding, permission-critical fields and undeclared `topic`/`record_type` values remain hard failures (or quarantine in the opt-in mode). The real delivered schema remains pending. (`source.DOCUMENT_KINDS` is stated once, with the wire section above.)
- Lab `payload.viitearvo` is source-supplied and rendered as the “Reference” cell only when present. A delivery alias may map `viitealue` to `viitearvo`; neither the importer nor UI calculates a range from the result.
- `Picture.narration_generation_path` carries the explicit branch result: `agent_reference`, `direct_fallback` or `direct_chat_completions`. Configured agent version/model never infer which branch supplied a candidate. The trial records the returned path per encounter; `versioned_agent_result` is true only when accepted prose came from `agent_reference`, so fallback output cannot count as versioned-agent evidence. The signed-in technical view displays the Picture path. `/api/diagnostics` deliberately remains token-only: it has no per-Picture context, so it reports configured-agent token acquisition without pretending that a token probe proves which path a request used. This probe is not a readiness dependency.

## Current deployed v8 path

```mermaid
flowchart LR
    SRC[Source records: bound fixtures / future source integration] --> B[Deterministic ownership index]
    E[Modelled encounter: bound person, role, purpose] --> B
    B --> P[policy.cut: role, purpose, consent, sensitivity]
    P --> R[Deterministic baseline]
    P --> C[Permitted current notes]
    C --> S[Deterministic shortlist]
    S --> J[Decision-1 per-note fan-out and thresholds]
    J -->|relevant missed note| A[Add original source]
    J -->|relevant baseline note| H[Emphasize; retain source]
    J -->|assessed / gate_refused / not_shortlisted / budget_exhausted / too_long / unavailable| COV[Coverage totals + per-row labels for disclosed sources]
    R --> EVIDENCE[Selected post-cut evidence]
    A --> EVIDENCE
    H --> EVIDENCE
    COV --> U[React briefing + on/off comparison]

    EVIDENCE --> FORM[Source form metadata + concept_key]
    FORM --> DUP[Same fact / two forms; separate rows, no merge or winner]
    EVIDENCE --> CAT[Service catalogue: required forms and assessments]
    CAT -->|due only from source-supplied date; one neutral absence for every reason, including policy.cut exclusion| U
    DUP --> U
    EVIDENCE --> IMP[Deterministic impact estimate: distinct source labels + form type/version/service tuples; never minutes]
    IMP --> U

    EVIDENCE --> F[Foundry structured narration]
    F -->|generated or retry result| M[Source-membership check]
    M -->|valid membership| V[Decision-1 per-sentence verifier]
    M -->|invalid membership, first attempt| RETRY[One bounded re-prompt: sentence IDs, reasons, permitted citations only]
    V -->|all unsupported / unknown, first attempt| RETRY
    RETRY -->|new candidate; complete re-check| F
    V -->|supported or mixed; keep supported only| RENDER[Render accepted prose]
    M -->|invalid again / retry exhausted| FAIL[Fail closed: deterministic briefing, no prose]
    V -->|every sentence rejected / retry exhausted| FAIL
    F -->|missing key / timeout / unavailable| FAIL
    RETRY -->|deadline exhausted| FAIL
    RENDER --> U
    R --> U
    P --> D[Deterministic decisions, assessments, proposals]
    D --> U

    subgraph EVIDENCE_PLANE[Evidence plane]
        MANIFEST[evidence-manifest.json: kind to artifact path]
        MANIFEST --> RENDERER[Evidence renderer]
        RENDERER --> SUMMARY[Derived evidence summary]
        MANIFEST --> CLAIMS[Document-claims and renderer checks]
        SUMMARY --> CLAIMS
    end
```

- `app.encounters.resolve_encounter` binds nine hosted source-keyed product encounters before `app.policy.cut`. Duplicate IDs, absent or mismatched record ownership, conflicting source-person mappings and ambiguous basics reject the encounter. The manually assigned bound source keys are **derived demo identifiers**, not original-source provenance. The product encounter index provides six named synthetic **demo aliases** for the walkthrough; six evaluator-only controls are kept outside the hosted encounter index and are not sent to the product UI. The historical bound fixture basics still contain no approved names, so the briefing marks the context as synthetic; the demo names are not source-verified identity. In real use, an approved, source-backed identity must replace this presentation label. Fixture roles are presentation lenses, not claims about the signed-in viewer. The React entry renders one backend-backed briefing; the obsolete legacy preview query parameters are removed from the URL on entry. No separate scripted preview is exposed. The A/B/C keys remain internal grouping identifiers, never visible labels. Actual stage statuses live under the collapsed technical details. The failure comparison, the mode switch that requests the same encounter with `?mode=decision-off` and `?mode=foundry-off`, and the sibling-lens comparison live on one labelled "Esittäjän työkalut" surface after every briefing section and before that trace — presenter material, stated as such, not part of the professional's situation picture. The retired inert "the situation picture does not change" selector is gone.
- The picker makes comparison axes explicit: person 003 nurse mental-health vs nurse functioning varies purpose only; person 003 nurse vs doctor mental-health varies role only. The remaining person-003 nurse/person-004 social-worker functioning pair changes person and role together and is labeled as confounded. Synthetic `bound-note-019` (topic `mielenterveys`, `social_care`, `highly_sensitive`) is deliberately authored for persona 003; doctor policy admits it, nurse policy excludes it. Deterministic baseline is the nurse's five IDs (`bound-demo-003`, `bound-note-003`, `bound-dx-004`, `bound-med-004`, `bound-arviointi-003`) plus doctor-only `bound-note-019`; the person and purpose are identical. This is a policy/fixture control, not a clinical claim or wire change.
- `app.policy.cut` determines visibility before Decision-1 or Foundry. `app.selection.baseline` keeps permitted basics, active medication/diagnosis and selected assessments plus keyword/topic matches. The deployed source selection contract is the v8 selection rule, shipped with Prompt Agent v8 and the lens comparison panel. The panel sits inside the collapsed technical disclosure and fetches siblings with full narration; the role/situation fixture predates it. The shared basics projection displays only approved minimal fields and sends neither name nor synthetic display identifier to Foundry.
- `app.decision_model.classify_notes` sees current, permitted free text from **both** the baseline-selected and baseline-missed cohorts. It first builds a **deterministic shortlist**: candidates are ranked by `Palvelukonteksti` baseline-term matches, note-to-medication/diagnosis links, topic match and recency, then interleaved across cohorts; because baseline-missed candidates are zero-overlap by construction, half the missed-cohort slots are reserved for a content-first exploration order (longest note, then recency), so recency alone cannot bury a long relevant paraphrase note. The ranked list is cut at `SHORTLIST_LIMIT` (80) and assessed up to `MAX_NOTES` (80), under the 30-second stage budget (`TOTAL_BUDGET_S`, 10 s per call; raised from 8 s / 3 s so every candidate is exercised). Each candidate is one request carrying a **fan-out** of four questions against one `state` (`relevance`, `time_sensitivity`, `subject_binding`, urgency `Score`); calls run concurrently in the deterministic candidate order and are not retried. The one cap-80 run over 42 synthetic notes attempted 41 concurrent requests; all 41 returned usable answers in 409 ms, with one oversized note left `too_long`, no unavailable/invalid responses and no observed throttling. This does not establish the vendor's concurrency limit. The five-level Score (levels 0–4) is validated and used to order `Records.priority` and the order in which selected sources reach the generator (first = most time-critical when a Score is available); no probabilistic label is shipped to the generator. Probabilities stay under the existing `DecisionNoteOutcome.gate_probabilities["urgency"]` key, with no Picture field added. Four-KiB per-note input.
- `app.decision_model` gates on the returned probabilities with named constants: addition floor `0.6` (with the `time_sensitivity`/`subject_binding` choices clearing the same floor), omission floor `0.7`, margin `0.25`. The source-wide `negation` question was **dropped** from the fan-out because it cannot safely drive a source-wide prose directive; the `negation_probability` wire field is retained but unpopulated, and the prose-inversion risk stays with the grounding verifier's per-sentence `contradicts` question. A below-threshold result abstains and the raw answers stay in the audit record. `COVERAGE_BUCKETS` in `app.decision_model` owns the coverage taxonomy shown in the diagram: `not_shortlisted`, `budget_exhausted`, `too_long`, `unavailable`, and `gate_refused`. Totals and per-row outcomes cover disclosed selected notes only; an unselected note ID is not exposed.
- The paired floor ablation replays the **same recorded answers** through the same classifier: production thresholds versus all three thresholds at zero. Additions remain unchanged while wrong omissions and omitted baseline records increase; the pre-registered "omissions unchanged" prediction was **FALSIFIED**. This replaces the earlier evidence gap that described thresholds only as inherited from the vendor's routing example. It is sensitivity evidence, **not accuracy or calibration**: it replays one recorded candidate set and cannot speak for a fresh selector call. Exact figures are claim-checked in the pitch and generated from the artifact in [the evidence summary](decision-evidence-summary.md), not retyped here.
- `docs/evidence/evidence-manifest.json` indexes every cited measurement artifact by `kind`. The evidence renderer and the document-claims check resolve artifact paths through it; a test requires every listed path to exist and its artifact to declare the indexed kind. The manifest is why measurement filenames are not hardcoded outside that file.
- `app.selection.apply_decision` can add only a current permitted baseline-missed source explicitly judged `relevant`. A baseline source is never removed from the professional's selected list (its one exception is the generator's input, next bullet): relevant baseline notes may be emphasized; irrelevant, unclear or failed outcomes leave them selected but not Decision-1-emphasized. A non-relevant missed note is not added. Coverage reports candidate and attempted counts for the whole shortlist and each cohort; note-level outcomes are returned only for selected baseline notes and selected relevant additions, so an unselected note ID is not exposed.
- `app.pipeline` derives decisions, assessments and proposals from permitted sources, not Decision-1 labels. Foundry generation receives relevant additions and all baseline sources **except** current non-protected free-text notes with a completed explicit Decision-1 `irrelevant` answer above the omission floor. A filtered note remains visible in the professional's selected source list. Unclear, invalid, capped and unavailable responses keep normal baseline input; deadline and HTTP transport/status failures are surfaced as row-level `unavailable` and keep the baseline. The source-membership validation uses the filtered generation scope. A candidate rejected for invalid membership or whose sentences are all `unsupported` / `unknown` may be regenerated once within the shared preparation deadline; the retry carries only rejected sentence IDs, reasons and permitted citation IDs, never rejected prose. Each new candidate is membership-checked and verified again; a second rejection or exhausted budget remains fail-closed. Mixed outcomes keep only supported sentences. `app.decision_model.verify_narration` verifies each sentence against **only the records that sentence cites**, returning `supported` / `unsupported` / `unknown`; the generative support judge is retired. (That cited-only restriction is **LUVN's own design decision, with its reason**: the verifier is handed the cited records and nothing else because the shipped contract performs no whole-database query, embedding join or external search on this path, so the sentence's own citations are the whole evidence the check can honestly consult. It is **not** a Microsoft requirement and was previously presented as one — Microsoft documents one Groundedness evaluator whose `context` input is the full retrieved set, concatenated, and it documents no per-sentence mode and no context-restriction rule; see [`docs/references.md`](references.md) §1 and §8. The decision itself is unchanged.) Accepted citations are built from the verifier's `supporting_record_ids`; unsupported candidate text is discarded, and only the bounded sentence ID/reason is kept in `GroundingOutcome.rejected_sentences`. `Picture.narration_input_record_ids` records the generation sources separately from `selection.records`; only accepted Foundry citations appear in `Picture.citations`. `Records.priority` places Decision-1-relevant selected notes first, followed by accepted citations other than baseline notes Decision-1 did not judge relevant. Those baseline notes remain selected, citable and visible in `accessible_other`; no source is dropped. The `?mode=decision-off|foundry-off` picture mode re-runs the same encounter server-side with one layer disabled, so the AI's contribution is shown side by side; it does not bypass `policy.cut`, role binding or encounter resolution.
- `app.telemetry` collects per-stage metadata (encounter resolution, `policy.cut`, each Decision-1 relevance call, each Decision-1 grounding call, Foundry generation, the agent token/call phases and total) under one opaque request trace ID. It emits best-effort Application Insights custom events; no note text, record IDs, withheld counts, person names or other-person data are sent. **Observed in production**: a signed-in trace records `foundry.agent.token` ok (10 ms), `foundry.agent.call` ok (4580 ms; `gpt-6.1-sol`, 905 input / 224 output tokens), and `foundry.generation` with `path=agent_reference` (4593 ms), followed by grounding verification. The rendered Aino medication-follow-up briefing added `bound-note-011`, kept `bound-note-013` visible but out of generation, and showed the urgency probability distribution. A fresh five-request signed-in check observed agent success in all five encounters (5.1–39.2 s agent-call latency). Observed property keys are only `trace_id`, `stage`, `elapsed_ms`, `status`, `model`, `input_tokens`, `output_tokens`, `path` and `request_id`; a query for record IDs, person names or note text returned zero rows. This is **custom-event correlation, not a distributed-span trace**, and it is **not Foundry service-side tracing**. **Unified preparation window: 160 s server / 180 s browser** (raised from 90 s / 110 s alongside the 30 s Decision-1 stage, so narration and pre-fill keep their full budgets after it). `pipeline.PREPARATION_WINDOW_S = 160` anchors the API route, both pipeline defaults, and the live trial. The narration budgets are unchanged: `joint=80 s`, `generation=min(70, 80−8)=70 s`, and `agent=min(45, 0.8×70)=45 s`; the pre-fill budget is 45 s. The browser abort is 180 s, leaving 20 s beyond the server preparation window. Azure Container Apps HTTP ingress has a 240 s default request timeout; the per-app ARM ingress schema has no timeout override, so Bicep intentionally leaves this platform default unchanged (it exceeds the 180 s client deadline). The earlier trial also called `build_picture_for_encounter`'s 90 s default; its private helper's 45 s fallback was not used. That artifact's 39.85 s shipped-mode encounter was accepted, not a fallback. The widened evaluation explicitly supplies the shared 90 s window across eleven encounters (five product fixtures plus six evaluator-only controls). Shipped-mode `decision-on-foundry-on` used `agent_reference` 11/11 with no fallback and wall time 4.351–10.215 s; accepted narration rendered in 7/11. In the `decision-off` comparator, 10/11 used the agent and one fell back after an agent-call timeout at 44.377 s; direct fallback took 2.621 s and total wall time was 47.672 s, within the 90 s preparation window. Overall generation-enabled modes had 21/22 agent responses and 1/22 direct fallback; no budgets were adjusted. A Decision-1 request deadline or HTTP transport/status failure is recorded `unavailable` for the selected source row and leaves the baseline briefing intact. A timeout fallback changes the recorded model (`gpt-6.1-sol` via the agent vs `gpt-6.1-sol-2026-09-29` via direct fallback), so the narration model is not constant across encounters.
- A fixed four-case synthetic direct chat-completions smoke run against the Foundry project, model `gpt-6.1-sol` (deployment version `2026-09-29`), records: strict JSON 4/4, citation membership 4/4, required source citation 4/4, weak expected-fragment proxy 3/4, forbidden prompt-injection string proxy 4/4. The fragment miss is `tuki` vs `tukea`, a morphology mismatch. This is not a batch Agent Service eval or semantic/clinical accuracy result.
- `docs/evidence/live-encounter-trial-v1-summary.json` is the tracked, text-scrubbed summary of the current 13-case local live trial (seven product encounters plus six evaluator-only controls, including the doctor case); an earlier 11-case run is recorded separately, and its raw source is gitignored to avoid committing generated narration. The v8 post-deploy live-services trial used `preparation_window_s=90` and joint/generation/agent caps 80/70/45 s. Across 26 generation-enabled modes, 23 used `agent_reference` and 3 fell back to direct chat after agent-call timeouts (~44.6 s; direct calls 2.55–3.18 s). Shipped mode: agent 10/11, direct fallback 1/11, accepted narration 7/11; `decision-off`: agent 8/11, direct fallback 3/11, accepted 6/11; `foundry-off` produced no narration in 11/11. **Ordering is measured, not asserted:** the one-page evidence summary derives, from the citations the trial records, how often the model cited the first `Records.priority` source, how often it led with it, and how often it appeared within two sentences — split by whether the priority list starts with a free-text note or a structured record. The model cited the priority source in every comparable encounter but leads with it only when that source is a note; a diagnosis code or a decision is context rather than a narratable time-critical fact, which is why a bare sentence-1 rate understates the behaviour. The v7 agent led in 4/7 and v8 in 5/7 — a single-encounter difference at n=7, which is **not** evidence of improvement. Observed rates on synthetic fixtures, not a guarantee. Shipped-mode verifier evidence: **29 sentences proposed end-to-end, 24 rendered, 5 withheld** (4 `invalid_membership` before Decision-1, 1 `unknown` at the verifier). Decision-1 judged 25 and supported **24/25**. The deterministic guard and Decision-1 verifier are reported separately. This is not a gold-standard grounding measure. Decision-1 added the three authored expected notes in the selection-only F2 run. Fixtures are synthetic and unadjudicated.
- The active configuration is singular: the Foundry project route, model `gpt-6.1-sol`, Prompt Agent `luvn-narration` version 8. It was created from committed `backend/app/prompts/narration_instructions.md` (pinned by SHA-256). A strict `FreshNarration` agent_reference smoke call succeeded (3530 ms, schema valid; synthetic sentence 1 cited `synthetic-priority-first`). The live-services trial rechecked ordering against `Records.priority[0]`; the derived figures and the note-led/structured-led split are in the one-page evidence summary rather than restated here. Direct chat-completions remains the fallback (4/22 generation modes in that local trial). A route/model guard prevents the known failing project-route pairing from returning.
- **The deployed instance** sets `minReplicas: 2` / `maxReplicas: 4` and was shipped by a **targeted `az containerapp update`**, not a template deployment. Readback is `Provisioned` / `Healthy`, two to four replicas, 100% traffic, external ingress with `allowInsecure=false`, and the approved presenters as the only allowed principals. All three Container App secrets — the Foundry key, the Entra client secret and the App Insights connection string — resolve from Key Vault on the app's user-assigned identity. `/api/diagnostics` reports the deployed build and a configured-agent token probe. The authentication boundary is verified from outside by a dedicated auth-boundary check, which is not part of the gate because the gate is Azure-free. The chat and decision contract is verified from inside the container by a live-verify probe (64/64 checks).

Role-reference and App Insights projections remain under review. One earlier readback measured 100% traffic and `Healthy`, with a single replica still `RunningAtMaxScale` after 330 seconds idle; after two separate 330-second idle periods the replica remained Healthy. On a second authenticated presenter load `/api/encounters` returned in 31 ms and the first normal picture in 8.0 seconds; the first idle-cycle picture took 16.1 seconds, including 14.7 seconds in generation. A credential-free browser request redirected to Entra. Model latency varies even with a warm container. Consumption billing continues for the standing 0.5-vCPU / 1-GiB allocation (idle-rate eligibility and subscription-wide free grants affect the net amount); a deployment that does not need to stay warm can return `minReplicas` to 0 through a separately approved deployment.
- **Content safety:** the `gpt-6.1-sol` deployment runs platform default RAI policy `Microsoft.DefaultV2` (content filtering). No *custom* content-filter or Prompt Shields policy is defined. One evaluator-only synthetic non-operational threat probe completed via the agent and Decision-1 grounding without a `safety_blocked` / `content_filtered` event; it was not intensified. Evidence: `docs/evidence/content-safety-probe-v1.json`. The app records such a block distinctly if it occurs. The current cap-80 F2 live-service value run is recorded in `docs/evidence/decision-value-eval-cap80-v1.json`; its figures are **derived** into [the one-page evidence summary](decision-evidence-summary.md). The retained earlier value-eval artifacts describe the previous 40-shortlist/12-assessment cap and are historical. In shape: additions are reported with a denominator, wrong additions and missed expected additions are counted against authored keys, baseline selection omission is measured against the baseline candidate denominator, and abstention is counted against authored IDs. `bound-note-018` returned `unclear` rather than being gate-refused — the model itself chose `unclear`. Score-enabled runs vary, so independent repeats of the borderline paraphrase case are recorded in `docs/evidence/decision-value-eval-instability/summary.json` rather than summarised from a single run. No thresholds, fixture text, or answer keys were changed. All labels remain author-authored and unadjudicated. The deployed AI-impact comparison shows narration contribution, not a Decision-1 quality delta. No FinBERT comparator or other second classifier is in this encounter path. The scope is the encounter briefing, not a second clinical pathway.

The separately pinned synthetic fault fixture (`data/experimental/decision-faults-v1.json`, pinned by SHA-256) remains byte-identical and is exercised by offline mocked fail-closed checks. Earlier local live-trial figures are superseded by the tracked scrubbed summary; its raw source remains ignored and is not committed. Foundry usage/billed cost was not exposed. Mocked or provisional labels are **not human-adjudicated truth**. No historical consumed evaluation inventory was replayed or altered for this implementation.

## The deployed estate — what handles what

The logic diagram under [Current deployed v8 path](#current-deployed-v8-path) says what happens to one
briefing, stage by stage. This one says something the logic flow cannot: **which Azure component exists in the
deployed estate, what each one handles, and where the trust boundary runs.** Neither replaces the other.

```mermaid
%%{init: {"theme": "neutral", "flowchart": {"curve": "basis"}} }%%
flowchart TB
    subgraph TRUST["Trust boundary — everything above this line is unauthenticated"]
        USER["Presenter's browser<br/>React SPA + API calls on one origin"]
    end

    subgraph RG["Resource group — Sweden Central"]
        subgraph ENV["Container Apps managed environment luvn-finbert-svc-env (Consumption)"]
            INGRESS["External ingress + Entra Easy Auth (Container Apps platform)<br/>the only authentication in the estate<br/>injects X-MS-CLIENT-PRINCIPAL-ID / -NAME"]
            AUTH["Sidecar container http-auth<br/>forwards an authenticated request to the app on localhost:8000"]
            APP["Container App<br/>uvicorn on port 8000, external ingress<br/>serves the API and the built React SPA from one origin<br/>reads the principal headers for audit, and for an opt-in encounter scope"]
            LOADJOB["Container Apps job luvn-load-corpus<br/>writes the delivery corpus into Azure SQL<br/>owns --recreate and --harden"]
            VERIFYJOB["Container Apps job luvn-live-verify<br/>runs the deployed image with the app on localhost,<br/>past the ingress, for an end-to-end check"]
        end

        subgraph IDENTITY["Managed identities"]
            APPID["the app's user-assigned identity<br/>db_datareader on database luvn only<br/>reads the three Key Vault secrets"]
            LOADID["luvn-corpus-loader-identity<br/>the loader's identity<br/>db_datareader + db_datawriter + db_ddladmin"]
        end

        subgraph DATA["Data"]
            SQL["Azure SQL / database luvn<br/>the deployed corpus: 15,000 people, 14,803 encounters, 401,340 records<br/>delivery schema, 3 foreign keys, hardened constraints<br/>read at request time for every briefing"]
            BLOB["Storage account, container feedback<br/>the durable audit and conversation store<br/>records scrubbed, owner-scoped blob names"]
            ACR["Container registry, repository luvn-product<br/>the deployed image by digest plus the final-* rollback tags<br/>the app and both jobs run the same digest"]
        end

        KV["Key Vault<br/>the Foundry key, the Entra client secret<br/>and the App Insights connection string"]
        FOUNDRY["Microsoft Foundry<br/>gpt-6.1-sol and Decision-1 (both DataZoneStandard, EU data zone)<br/>prompt agent luvn-narration:8 drafts prose via agent_reference<br/>Decision-1 is called directly, typed, for relevance and verification"]
        AIAPP["Application Insights<br/>receives only customEvents named luvn.pipeline.stage<br/>no auto-instrumentation, so requests and dependencies stay empty"]
        RULE["Scheduled query rule luvn-pipeline-stage-failed<br/>alerts on status == failed"]
        LAW["Log Analytics workspace<br/>container console and system logs, the Key Vault audit stream,<br/>and the platform categories"]
        LOCK["Resource-group lock BlockDelete (CanNotDelete)<br/>refuses DELETE of child resources"]
    end

    DEV["Offline / developer-only, NOT part of the deployed estate:<br/>scripts/check.sh, the SQLite delivery index, reconcile_delivery.py"]

    USER -->|"HTTPS until the platform challenges"| INGRESS
    INGRESS -->|"authenticated request"| AUTH
    AUTH -->|"localhost:8000"| APP
    APP -->|"reads the corpus per request"| SQL
    APP -->|"writes and reads scrubbed audit records"| BLOB
    APP -->|"agent_reference prose, app-side validated"| FOUNDRY
    APP -->|"direct typed calls, provider-enforced schema"| FOUNDRY
    APP -.->|"secret references"| KV
    APP -.->|"runs as"| APPID
    APPID -.->|"db_datareader"| SQL
    LOADJOB -->|"loads and hardens the corpus"| SQL
    LOADJOB -.->|"runs as"| LOADID
    LOADID -.->|"read + write + DDL"| SQL
    VERIFYJOB -->|"exercises the API on localhost"| APP
    ACR -.->|"image by digest"| APP
    APP -->|"one luvn.pipeline.stage event per stage"| AIAPP
    RULE -.->|"queries status == failed"| AIAPP
    AUTH -.->|"sidecar stdout"| LAW
    ENV -.->|"appLogsConfiguration destination"| LAW
    KV -.->|"Key Vault audit stream"| LAW
    LOCK -.->|"guards"| RG
    DEV -.->|"not deployed"| APP
```

**Three pictures of the same estate, and each answers a different question.** The first mermaid diagram is
the **logic** flow — how a decision is actually reached, from the question through retrieval and the model
calls to a proposal. The second mermaid diagram is the **topology** — what talks to what, as text a diff
can follow. The poster below is the **deployed estate as a reader would recognise it**: the same topology
drawn with the official Microsoft Azure service icons, the request path in solid arrows and the supporting
flows dashed, generated from a declarative model and
gated so it cannot drift from that model.

[![The LUVN estate as an Azure architecture poster: the request path from the presenter's browser through Entra Easy Auth and the http-auth sidecar to the Container App, the two distinct Foundry paths, the supporting identity, key-vault, storage and registry flows, the two jobs, and the observability strip](assets/luvn-architecture.png)](assets/luvn-architecture.svg)

*[`assets/luvn-architecture.svg`](assets/luvn-architecture.svg) (vector, self-contained) ·
[`assets/luvn-architecture.png`](assets/luvn-architecture.png) (raster). Icons: the official Microsoft
Azure icon set, V24, used unmodified.*

Every node above was read from the live estate or from this repository's configuration, not inferred. The three
factual anchors that are easy to get wrong are worth stating in prose:

- **The managed environment's name is a leftover.** `luvn-finbert-svc-env` is named after a FinBERT
  Container App that was deleted. No FinBERT service exists in this path, and an environment
  name cannot be changed in place.
- **Residency is per deployment, by *zone*, not by region.** Both model deployments live in Sweden Central,
  and both `gpt-6.1-sol` and `Decision-1` are `DataZoneStandard`, so inference for either
  stays **within its data zone** (EU / US / APAC) — which is not necessarily the deployment's region. So the
  honest statement is that both paths are processed in the **EU data zone**, not that they cannot leave a
  region. `gpt-6.1-sol` previously ran as `GlobalStandard`, which carried no zone property at all and
  could be processed in any Azure region.
- **The two model paths do not carry the same output guarantee.** Decision-1 is a direct Chat Completions
  call, so the provider enforces the JSON schema (`strict: true`). The narration path goes through the
  versioned agent, which is not offered structured output, so `backend/app/llm.py` receives JSON **text** and
  validates it application-side with Pydantic. A schema-valid narration therefore rests on our validation,
  not on a provider-enforced contract.
- **Per-actor authorization is opt-in application configuration, and the ingress is still the only
  authentication.** `backend/app/access.py` reads `LUVN_ACCESS_POLICY` (inline JSON) or
  `LUVN_ACCESS_POLICY_FILE` and resolves the calling principal from the platform headers to an *encounter
  scope*; `backend/app/main.py` attaches that scope as FastAPI dependencies — `require_encounter` (403, one
  body whether or not the encounter exists) and `require_catalogue` (the catalogue is the caller's scope,
  with its counts computed over that scope) — before any handler resolves an encounter. The axis is the
  encounter and not the professional role, because the fixture roles are presentation lenses over the same
  data and there is no role-based authority to model. With both variables unset the application is open
  exactly as before, and **that is the deployed state**; with a policy configured it fails closed, including
  for a principal the policy does not name and for a policy that cannot be parsed (500
  `access_policy_unusable`). It is deliberately unconfigured on the live deployment, because turning it on
  needs an operator decision about which presenter may see which encounter and no such assignment exists.

### What handles what

| Component | What it handles | Never to be confused with |
| --- | --- | --- |
| The Container App | Serves the API and the built React SPA from one origin on port 8000. This is the only thing a user reaches. | Not an authenticator — it trusts the platform's principal headers and has no authentication of its own. |
| Container Apps managed environment `luvn-finbert-svc-env` | Hosts the app and both jobs. Its `appLogsConfiguration` destination is the workspace, which is how container console and system logs arrive. | Not a FinBERT service. The name is a leftover from a deleted app and cannot be changed in place. |
| Entra Easy Auth at the ingress | The only authentication in the estate. Injects `X-MS-CLIENT-PRINCIPAL-ID` / `-NAME`; the app reads them for audit only. | Not application code, not per-role authorization, and not a check that the signed-in presenter is entitled to the modelled lens. |
| Sidecar container `http-auth` | Forwards an authenticated request to the app on `localhost:8000` and writes its own decisions to stdout. | Not the app. Its log lines are the only place the boundary can be observed from inside. |
| The app's user-assigned identity | `db_datareader` on database `luvn`, and reads the three Key Vault secrets. | Cannot write the corpus. Its `Storage Blob Data Contributor` on the storage account is for the audit store, not for Azure SQL. |
| `luvn-corpus-loader-identity` | The loader's identity: `db_datareader` + `db_datawriter` + `db_ddladmin`. | The DDL grant is deliberate — it owns the corpus tables and `--harden` needs ALTER; it is not a misconfiguration, and the app must not have it. |
| Azure SQL / database `luvn` | Holds the deployed corpus: 15,000 people, 14,803 encounters, 401,340 records, the delivery schema with 3 foreign keys and the hardened constraint catalogue. Read at request time for every briefing. | Not written by the app. Only the loader job writes it. |
| The storage account / container `feedback` | The durable audit and conversation store: proposal decisions, chat turns, chat reviews, decision claims. Records are scrubbed (no prose, no record text) and owner-scoped by blob name. | Not a corpus store and not a place clinical text is kept. |
| The container registry, repository `luvn-product` | Holds the deployed image by digest plus the `final-*` rollback tags. The app and both jobs run the same digest. | Not the deployment mechanism. A registry tag does not deploy anything; the live revision is set by a targeted `az containerapp update`. |
| Container Apps job `luvn-load-corpus` | Loads the delivery corpus into Azure SQL from a blob; `--recreate` and `--harden` live here. | Writes the corpus. The app only reads it. |
| Container Apps job `luvn-live-verify` | Runs the deployed image with the app on localhost, past the ingress, and exercises it end to end. | Not the ingress path: it verifies the app, not the authentication boundary. A dedicated auth-boundary check does that from outside. |
| Key Vault | The three Container App secrets: the Foundry key, the Entra client secret and the App Insights connection string, all resolved as Key Vault references on the app's identity. | Not an inline-secret store. No secret value is in the deployment. |
| Microsoft Foundry | Two deployments: `gpt-6.1-sol` and `Decision-1`, both DataZoneStandard (EU data zone). The prompt agent `luvn-narration:8` drafts prose via `agent_reference`; `Decision-1` is called directly, typed, for per-note relevance and per-sentence verification. | Not the permission decision. Relevance is advisory; `policy.cut` decides visibility before any model call. |
| Application Insights | Receives only `customEvents` named `luvn.pipeline.stage`, one per pipeline stage, scrubbed of identifiers by `backend/app/telemetry.py`. | No auto-instrumentation: `requests` and `dependencies` stay empty. It is not a distributed trace and not Foundry service-side tracing. |
| Scheduled query rule `luvn-pipeline-stage-failed` | Alerts when a stage event carries `status == "failed"`. | Not a log store; the events it reads are in the workspace. It has no action group of its own beyond `luvn-owner-alerts`. |
| Log Analytics workspace | Container console and system logs (via the environment), the Key Vault audit stream, and the platform categories. | Not the App Insights events — those arrive through the component, not directly. |
| Resource-group lock `BlockDelete` (`CanNotDelete`) | Refuses DELETE of child resources. | Does not prevent updates or creations; it is not a backup. |
| Offline / developer-only: `scripts/check.sh`, the SQLite delivery index, `reconcile_delivery.py` | Local verification and the pre-deployment delivery index. | **Not deployed.** Nothing in the running estate reads them. |

### What the protocol pin does and does not cover

`backend/app/decision_protocol.py` carries a SHA-256 literal over the canonical JSON of a **structural
descriptor**: the two roles, their `state` keys, every question's name and wire type with its answer
vocabulary, and a list of gate *names*. It is a **partial structural pin**, and saying more than that would
overclaim:

- It does **not** cover the full Finnish `instructions` and criteria strings in
  `backend/app/decision_model.py` — the descriptor names a question and its type, not the prompt text.
- It does **not** cover the numeric gate values (`ADDITION_FLOOR = 0.6`, `OMISSION_FLOOR = 0.7`,
  `DECISION_MARGIN = 0.25`); the descriptor lists a gate's name, not its threshold.
- Its tests compare the selection request's `state` and question keys and types against the descriptor, and
  the verifier test checks the verifier's descriptor *shape*, not its actual request builder.

So a change to prompt wording or a threshold moves the effective behaviour without failing the pin. The pin
narrows the drift surface to the protocol's structure; it is not a behavioural contract. On the other side
of the seam, the two model paths differ in kind: a **direct typed call** (Decision-1, and the direct
narration fallback) gets a **provider-enforced** JSON schema, whereas the **agent-wrapped** narration call
gets JSON text and **application-side Pydantic validation** in `backend/app/llm.py`.

## Foundry account, project, route and model

The active configuration:

| Account | Resource group | Project / route | Model | Prompt Agent |
| --- | --- | --- | --- | --- |
| the Foundry account | the deployment resource group | the default project / project-scoped `/api/projects/<project>/openai/v1` | `gpt-6.1-sol` (version `2026-09-29`) | `luvn-narration:8` |

Project route verification with the app's strict narration schema: chat/completions 200 with schema-valid output and usage; plain Responses 200; Responses with `agent_reference` version 8 200 (metrics above). No alternate account/project pairing is used by the shipped configuration. The project route plus the legacy model binding is forbidden by `_validate_route_model`.

## Evidence limits and next gates

The local generation-input filter is deployed. A stubbed pipeline test now asserts the intended current baseline admin note is absent from the generator input while remaining visible in selection; this is a mechanism check, not a live-model matched comparison. The runtime change does not prove Decision-1 relevance quality. v2 remains a test-only fixture with no runtime wiring, provisional labels and no independent adjudication. No new live Decision-1 call, real-data transfer or matched Decision-1 on/off latency/token/cost study occurred for the filter change. Complete independent label review, assess useful and wrong additions/prominence on synthetic examples, and measure coverage and end-to-end effects before claiming the deployed filter improves relevance or undertaking a broader rollout. The demo does not prove clinical safety, legal compliance, 50,000-profile throughput or cost savings.

### Microsoft-Decision-1 documentation check

Microsoft's [Decision-1 guide](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision) describes typed **Choice**, **Score** and **Noul** questions against structured state — the same primitives the previous classifier documented, which is why the question design survived the substitution unchanged. This implementation uses that shape: one call per note with `Choice`/`Choice`/`Choice`/`Score` questions (the source-wide `Noul` negation question was dropped), a deterministic shortlist over permitted records, and risk-scaled thresholds on the returned probabilities. The model's documentation presents thresholding as its intended use ("low confidence: do not act") and that thresholds scale with risk; "confidence is not correctness" means a confident answer is not thereby true, and this implementation thresholds rather than ignores it. Cited documentation is never measured behaviour on Finnish input: published claims do not establish Finnish sote relevance, privacy, billing or our end-to-end latency. The paired floor ablation now adds sensitivity evidence beyond the thresholds' inherited vendor-example origin; it does not calibrate them or establish Finnish accuracy. Per-note isolation is kept: several different notes are never batched into one `state`, because that would widen disclosure and change per-note attribution.
