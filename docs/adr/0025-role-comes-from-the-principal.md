# ADR-0025 — The role comes from the signed-in user, and the worklist is a scoped metadata projection

Status: **accepted by the owner.** It is the
step-0 decision the re-foundation from the database upward depends on: two independent oracles reviewing the
full-release load found that the database, the API shape and the authorization model cannot be finished
separately, because each encounter bakes a role in at build time while the UI lets the *viewer* choose one.

Relates to [ADR-0001](0001-picture-contract-at-the-seam.md) (the picture contract),
[ADR-0004](0004-silent-absence.md) (silent absence), [ADR-0011](0011-ai-architecture-layers.md) (AI layers
consume only `Scoped`), [ADR-0016](0016-swiss-1-foundations-is-the-ui-authority.md), and
[ADR-0020](0020-fhir-r4-is-the-mapping-target.md) (R4 is a future mapping target, not a licence to remodel).

## Context

The corpus is no longer a bounded demonstration. Measured on the built release: **32,336 people
with at least one canonical record, 546,085 records and 38,469 encounters**, drawn from two cohorts — one
where clinical and social people are the same 15,000, and one where they are **separate** sets (15,000
clinical `SYN-ASI-0…`, 2,336 social `SYN-ASI-2…` with data). Of the 45,000 registered people, 12,664 are
named only by source tables the mapper turns into no record, and are deliberately not padded in.

Fourteen statements in the current code and documents were true of the 128-encounter demonstration and are
false at this size. The three that decide the architecture:

1. **`GET /api/encounters` walks the corpus on every call.** `main._catalogue_rows` resolves every encounter
   and memoises every person's complete validated record set before filtering, counting or slicing
   (`main.py:213-254`, `encounters.py:124-168`, `sqldelivery.py:632-642`). At 38,469 encounters that is
   ~65,000 serialized queries through one lock-guarded `pyodbc` connection and roughly 1.18 GB of record
   objects — measured at +217 MB per 100,000 records — against a container with **1 GiB and one replica**.
   The page size does not bound the work, and the SPA requests this twice at bootstrap (`App.jsx:201,243`).
2. **The role is a viewer choice, not an authorization fact.** The builder pre-generates an encounter per
   (person, role, purpose) pair (`build_veil_corpus.py` LENSES) and the UI lets the signed-in user switch
   lens and compare roles (`challenge-demo.mjs`, `App.jsx`). `LUVN_ACCESS_POLICY` is **unset**, which
   `access.py` documents as "every ingress-authorized presenter sees all encounters". Entra Easy Auth
   authenticates; nothing authorizes.
3. **The SPA can only address the first 200 encounters.** `CATALOGUE_PAGE_SIZE = 200` is treated as the whole
   catalogue (`App.jsx:12-18`), so a deep link or a same-person sibling outside that page is unreachable.

`/api/encounters/{id}/picture` requires **no actor** (`main.py:333-337`), so no read is attributable today,
and the permission axes (`consent_scope`, `topic`, `authored_by_role`, `sensitivity_tag`) are **declared by
the builder** — the release carries none of them.

## Decision

**1. "All data" is defined in three separate senses, and only the third is a product claim.**
   - **(a) Raw preservation.** The release bytes are retained under the organiser's deletion rules, outside
     the product's read path. This is an operator commitment, not a UI promise.
   - **(b) Source accounting.** Every source row is accounted for as *mapped*, *explicitly excluded* (with
     the reason: capped, future-dated, unlinked, undatable) or *refused*. This lives in an operator-only
     manifest beside the corpus, with the blob hashes and counts.
   - **(c) Person visibility.** Every person named by at least one *mapped* row has a briefing. A registered
     person the release says nothing else about is **not** padded into the corpus (12,664 people, measured).
   No surface may claim (a) or (b) on the strength of (c).

**2. Role and purpose come from the signed-in principal.** The encounter keeps its modelled role/purpose as
operator metadata, but **authorization** uses the role claim of the authenticated user. Consequence: the
role selector in the UI becomes a *view* of the user's own lenses, or is removed; cross-role comparison for
one person is a supervisor capability, gated and audited, not a default. The care-relationship limb
(a professional assigned to *this* person) cannot be met with this release, because no assignment data
exists; that gap is recorded as **unmet**, and no RLS policy over a single shared reader identity may be
presented as per-professional authorization.

**3. The worklist is a scoped metadata projection in SQL.** Paging, search and counting happen over
`encounters` metadata joined to `people` — never over `records.record_json`. Therefore:
   - `people` for a worklist count means `COUNT(DISTINCT source_person_id)` over the caller's authorized
     encounter set. It is **not** distinct `example_label`, which is what `_encounter_summary` computes today.
   - Ordering is by `encounter_id`; the primary scroll uses an **opaque keyset cursor** bound to corpus
     generation, role/purpose filter and caller scope; stale or tampered cursors are refused. Offset paging
     survives only as a compatibility path with a declared deep-offset ceiling.
   - Substring search keeps the current `_fold` semantics (NFKD + casefold) and must be **parity-tested**
     against whatever SQL collation or index replaces it, on Finnish diacritics, before the replacement.
   - Page size and response bytes are bounded and measured; the bound is a response contract, not a corpus
     limit.

**4. A single-encounter metadata route exists**, so a deep link and a lens sibling are resolved by id and
scope, never by scanning a page. The first-200 bootstrap is removed.

**5. Every resolved encounter read emits one append-only audit event** — actor, encounter id, corpus
generation, and whether the read was permitted — carrying no clinical text and no person identifiers beyond
the encounter the actor already reached.

**6. The corpus bound is a manifest plus byte and line limits, not an encounter row cap.** The loader stages
a versioned, immutable prefix and publishes a pointer only after all three blob hashes verify; it loads in
bounded committed batches into a **staged** database, validates counts, integrity, foreign keys and a
representative picture, and only then is the app's read target switched. A failed stage never touches the
live corpus and a 546k-row single transaction is forbidden.

## Consequences

- **The database layer is not finished until (3) is implemented and measured.** Indexing and tier sizing
  alone multiply the same repeated work; a bigger SKU is not the fix.
- **The UI cannot be finished before (2).** Removing the role selector is a UI change forced by an
  authorization decision, which is why it is recorded here rather than discovered during polish.
- **The acceptance test for the data/database layer is a scale smoke test**: a timed, memory-measured
  `GET /api/encounters?limit=50` and a single picture against the full corpus, plus an integrity readback.
  It is not deferred to the end-to-end layer.
- The 14 statements listed by the architecture review (in `App.jsx`, `worklist.mjs`, `delivery.py`,
  `store.py`, `README.md`, `docs/hosting-runbook.md`, `docs/architecture-as-built.md`) become
  false when the full corpus is live, and must be corrected as part of the switch. `check_doc_claims` does
  not check corpus counts, so the gate will not catch them.
- Nothing here authorizes normalising the record schema, adding Azure AI Search or Redis, or adding Foundry
  capability; those keep their existing decisions.

## Alternatives considered

- **Load the full corpus first and fix the read path afterwards.** Rejected on measurement: the first page
  load would need ~1.18 GB in a 1 GiB single-replica container, and the failure would be silent in App
  Insights, because only pipeline-stage events are emitted.
- **Raise the replica count and SKU so the current read path survives.** Rejected as the primary fix: it
  multiplies ~65,000 queries per request rather than removing them, and leaves the SPA's first-200 limit.
- **Keep the viewer-selected role and add row-level security over the shared reader identity.** Rejected:
  the read path carries no actor, so RLS cannot bind to a principal, and the UI would still be choosing the
  role it is then trusted on.

---

## Amendment — the honest axis is the encounter, not the role

**Status: accepted by the owner; reviewed and CLOSED.** An independent oracle
reviewed this amendment against the code and the record and returned **accept as written**: it confirmed
`EncounterScope` holds exactly the three encounter selectors (`access.py:147-180`), that `resolve_scope`
keys on the principal alone and never on a role (`access.py:217-231`, `:479-502`), that no authorization
path consults an encounter's role (`main.py:309-351`, `:370-378`), and that the quoted docstring argument
is faithful (`access.py:21-24`). It found the three stated consequences supported and none overstated, and
held that **no code change follows** — this amendment makes the accepted decision agree with `access.py`
as built and tested. Nothing was left open, so the amendment is closed. The decision above stands; two
sentences of §2 are corrected, and the original text is left in place so the correction is legible rather
than silently overwritten.

**What §2 said.** *"…authorization uses the role claim of the authenticated user. Consequence: the role
selector in the UI becomes a view of the user's own lenses, or is removed…"*

**What it should have said, and now does.** Authorization scopes the principal to **encounters and people** —
the care relationship — and **a role is a presentation lens, never an authority**. The care-relationship limb is
**unmet**, because this release carries no professional-to-person assignment.

Three reasons, all of which were already in the repository when §2 was written:

1. **The code already does this.** `backend/app/access.py` scopes a principal to encounters through
   `EncounterScope` rules (exact, prefix and suffix id selectors), and its module docstring states the
   argument itself: *"The honest axis is the encounter, not the professional 'role'. The four roles in this
   product are presentation lenses over the same data, not identities … so there is no role-based authority to
   model. A rule scopes principals to encounters; nothing here consults an encounter's role."* §2 described a
   mechanism that neither exists nor should.
2. **The data cannot support role authority.** Two independent oracles and the release's own vocabulary
   agree: there is no `ammattiryhma`, no `kontakti_tyyppi`, no social-worker profession field and **no
   professional-to-person assignment** anywhere in the 2024-2026 extract. Only 1,500 clinical notes name a
   profession at all (lääkäri 1,032 / sairaanhoitaja 468).
3. **A second authorization model is the failure this ADR exists to prevent.** Left as written, §2 authorises
   building a role→authority matrix alongside the encounter scope — two implementations of one policy, which
   will diverge. That is precisely what the single enforcement point is for.

**What changes in practice:**

- **The mechanism: nothing.** No code change follows from this amendment; it makes the accepted decision agree
  with `access.py` as built and tested.
- **What may be built: a prohibition.** A role→authority matrix is now explicitly out of scope. The UI's role
  selector is not removed because "the principal supplies the role"; it is removed because **the encounter is
  the authority and a role is a lens** — lenses are gated by the caller's *encounter assignment*.
- **An expectation, recorded rather than implied:** real per-professional authorization needs an **assignment
  source the release does not have**. That is why `LUVN_ACCESS_POLICY` remains unconfigured, and why row-level
  security over one shared reader identity must never be presented as per-professional access.

**What is unchanged:** the scoped metadata worklist (§3), the single-encounter route (§4), the read-audit
event (§5) and the staged load with a pointer switch (§6). The audit row names the **actor and the encounter**
— which is the pair this amendment makes the authority.

---

## Owner decision — the role selector stays

**Status: owner decision, recorded here because it reverses a consequence of the amendment above.**

The amendment concluded that the UI's role selector is removed, on the grounds that *the encounter is the
authority and a role is a lens* — so a picker of lenses was thought to imply an authority it does not have.
**The owner has decided the selector stays.** It is recorded rather than quietly left in place, because the
amendment currently says the opposite and a reader must not have to guess which sentence governs.

What this changes, and what it does not:

- **Authorization is untouched.** The selector chooses a *presentation lens*: which of the permitted records a
  professional is shown and in what arrangement. It grants nothing. The authority is still the caller's
  **encounter scope** (`access.py`), applied before any handler body runs, and `LUVN_ACCESS_POLICY` is still the
  only thing that narrows a caller to particular encounters. A caller who can reach an encounter can choose
  either of its lenses; a caller who cannot reach it gets the same refusal whichever lens they pick.
- **The risk the amendment worried about is real but is a labelling risk, not an access risk.** A visible role
  control invites the reading "this professional's role limits what they see". The mitigation is that the
  product never presents a lens as a permission: the worklist is scoped by encounter, the refusal for an
  out-of-scope encounter is identical to an unknown id, and no server path consults a role for access. If a
  future change ever makes the selector affect *authorization*, this decision is void and the amendment's
  original consequence returns.
- **What is amended by this note:** the sentence in the amendment stating that the selector is removed. The
  rest of the amendment — principal scoped to encounters and people, a role is never an authority, the
  care-relationship limb unmet — stands unchanged and is what makes keeping the selector safe.
