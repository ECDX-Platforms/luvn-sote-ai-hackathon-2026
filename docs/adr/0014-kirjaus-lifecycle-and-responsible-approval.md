# ADR-0014 — Kirjaus lifecycle, authorship and responsible approval

Status: **accepted as a design; optional state/approver envelope and narration filter implemented; three owner-authorised synthetic `draft` rows shipped; further fixture data remains parked pending explicit authorization.** Derived from the vendor's Lifecare training corpus (scan 2026-10-07). Illustrative synthetic modelling, not a statutory rule.

## Owner decision, 2026-10-07 — parked harder than its siblings

- **Accepted as a design.** The lifecycle and the author/approver split are the right shape, and the
  framing stands: a draft presented as settled is the same failure class as narrating an unsupported
  sentence.
- **Parked harder than ADR-0012/0013/0015, and not only until after the judged event.** Implementing it
  requires fixture **`state` values — i.e. new synthetic data** — and the owner has deferred new data.
  The blocker is therefore a **data decision**, not a scheduling one. This cannot start even post-event
  until the owner authorises the fixture change.
- **Record only for data.** At acceptance, no new envelope, pipeline or fixture changes were authorized
  under this ADR until the owner approved new data. The optional fields/filter now present do not waive
  that fixture-data gate.

## Implementation disposition, 2026-10-07

**IMPLEMENTED:** optional `state` / `responsible_approver` provenance fields and the generation-only exclusion for `draft` records. Three product encounter fixtures carry an owner-authorised `state: draft` row (bound-note-002/003/004-draft); nothing else was populated or edited.

**DATA-GATED:** the owner has deferred new synthetic data. Adding a further `state: draft` row (and showing the corpus's grey/italic convention) remains blocked until explicit authorization; the three authorised rows are the exception; existing records remain absent-state and preserve their settled-evidence behavior. The new provenance-field decision in issue 01 does not authorize fixture authoring.

## Context

At the time of this decision, the `Record` envelope carried `authored_by_role` and `recorded_at` but no
state/approver values. The optional provenance fields and generation filter now exist; product fixtures
still do not supply a state. The source system gives a record a **lifecycle** and separates **who typed
it** from **who answers for it**:

- States: **`luonnos` → `Valmis` → `Hyväksytty`**. "Myös kirjausalusta sulkeutuu, kun tallennus on
  tehty Valmis- tai Hyväksytty-tilassa." `Keskeneräiset kirjaukset` is its own application.
- Supervision: "Keskeneräiset kirjaukset on tarkoitettu omien sekä käyttäjän valvoman opiskelijan
  keskeneräisten kirjausten seuraamiseen. Jos sinut on merkitty **vastuulliseksi vastaanottajaksi**
  opiskelijan kirjauksille, widgetillä näkyy myös opiskelijan kirjaukset ja **sinun täytyy hyväksyä
  ne**." A student entry stays "luonnostilaisena" until then.
- Restricted visibility of some marks: "Virheellisten tietojen poistaminen — **Merkintä näkyy vain
  pääkäyttäjille**."
- Non-retractability once acted on: "Jos lääkemääräykseen on tehty **antokirjausmerkintöjä**, et voi
  lopettaa lääkemääräystä virhekirjauksena."
- Non-final documents exist in other families too: `Lomakepalvelu` saves `Luonnos` then `Hyväksytty`;
  `Terveys- ja hoitosuunnitelma` is explicitly saved as `luonnos` for later completion.

A briefing that presents a draft as if it were a settled, approved fact is making a claim the record
does not support — the same failure class as narrating an unsupported sentence.

## Decision

1. Add a **`state`** to the `Kirjaus` envelope (`draft` / `final` / `approved`), as **provenance
   metadata, never a selection input**.
2. Add a **`responsible_approver`** distinct from `authored_by_role`. The record's *authority* is the
   approver's, not the author's; an author may be a student.
3. A record in a **non-final state** is treated as *unfinished evidence*: it may be shown as present
   but is **never narrated as a settled fact**, and never silently promoted. This mirrors the existing
   `Arvioimaton` posture — absence of a settled value is not a value.
4. The briefing **states** authorship/approval provenance and **never implies the viewer authored**
   the record.
5. A record with dependent downstream facts (e.g. an administered dose) is **not retractable**: the
   source must be kept, consistent with "never drop a source, no winner".

## Boundaries

- **No workflow engine.** No approval queue, no approvals UI, no write-back. This is provenance only.
- `state` must not become a relevance or permission signal: a draft a role may see is still seen.
- We do not model the `pääkäyttäjä`-only visibility mark beyond noting that some record-level marks
  are role-restricted; if we ever surface it, it goes through `policy.cut` like everything else.
- This ADR does not change the "a simulated role in a demo is not proof of the viewer's authenticated
  identity or real authorization" standing boundary in `CONTEXT.md` — it strengthens it by giving the
  author/approver split a real mechanism.

## Consequences

- Once explicitly authorized, fixtures may gain `state` and, where a student is modelled,
  `responsible_approver`; until then, no fixture value is invented.
- A new test must prove that a non-final record never produces narration prose (fail-closed, like the
  grounding verifier) while remaining visible in the selected source list.
- `Arvioimaton`/`ei_arvioitavissa` and `state` are **different things** and must not be conflated: the
  first is "no assessable evidence", the second is "evidence exists but is not yet final".
- Provenance display must not leak the approver's identity where the role cannot see it.

## Relationship to existing architecture

This is a `Picture`-provenance feature, so it touches the ADR-0001 contract question. It adds no new
*narrative* field: `state`/`approver` are source metadata, and any wire change requires the explicit
ADR-0001 amendment already demanded by the standing constraints. Until then this stays in the
`Kirjaus` envelope and the selection/provenance surfaces, not in narrated prose.
