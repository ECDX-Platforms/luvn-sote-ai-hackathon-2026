# ADR-0012 — Capacity and consent are per-encounter, not per-person

Status: **accepted as proposed; age-default and exact prescription-scoped projection implemented in the encounter API seam (2026-10-08); source age/assessment data and legal basis remain DATA-GATED.** Derived from the vendor's Lifecare training corpus (scan 2026-10-07). Not law. The statutory citations below are
the *vendor's*, reproduced so a rule can be bound to its stated origin; they are not our legal position.

## Owner decision, 2026-10-07

- **Accepted as proposed.** `consent_holder` remains a **derived projection**, and the existing
  `consent_holder` tests stay the **migration gate**: if the projection is faithful, they must keep
  passing unchanged. That is the acceptance criterion for this decision, not a nice-to-have.
- **Deferred, and the deferral is part of the decision.** This ADR changes `policy.cut` — our most
  safety-critical code path — and it would land immediately after a fresh product deployment. Trading a
  modelling improvement for avoidable operational risk in the permission cut, right after a deploy, is a
  bad trade. Implementation therefore waits until after the judged event. The deployment record owns
  which revision is current; this decision record deliberately does not name it, because a revision name
  in an ADR goes stale on the next deploy.

## Implementation disposition, 2026-10-07

**IMPLEMENTED:** optional `Kypsyysarvio` on `Encounter`, its exact prescription-record binding, and deterministic projection through `Record.projected_consent_holder`. `Encounter.age_at_encounter` triggers the illustrative `<12` guardian default only when explicitly supplied; birth year is never used to infer it. All API paths use the same `cut_for_encounter` seam.

The frozen pre-ADR-0012 proof remains based on clean `c41edcb` and actual existing production arguments: across all 13 product/evaluator encounters, permitted sorted IDs and projected `consent_holder` values match exactly, with no newly visible/hidden records and no projection changes. Existing consent-holder migration tests remain unchanged and green.

**DATA-GATED:** no exact age or `Kypsyysarvio` values were authored on product encounters, no `legal_basis` is approved, and birth-year-only records do not activate the default. API tests supply synthetic age/assessment/case contexts transiently.

## Context

`upm.Record.consent_holder` is `Literal["self", "guardian"]` stored **per record**, and
`policy.cut` receives a fixed `consent_holders` tuple from the caller. The Phase 3 guardian lens keys
off a declared `life_stage` axis. All of these describe the **person**, statically.

The source system does not work that way. From the `Alaikäisen puolesta asiointi` course:

> "MP1-versiosta alkaen asetetaan alle 12-vuotiaille kypsyysarvion oletustietona
> **Ei päätöskykyinen, tiedot huoltajalle** –arvo 1.1.2024 alkaen."

> "Arvio kyvykkyydestä tehdään **jokaisella käynnillä (palvelutapahtuma) ja jokaiselle reseptille**."

> "Alaikäinen ei voi kieltää **kaikkien** tietojen näkymistä huoltajalle, vaan päätös ja arvio
> kyvykkyydestä tehdään **jokaisella hoitokäynnillä erikseen**."

> "Kypsyysarvio on muokattavissa sen organisaation sisällä, jossa se on alun perin tehty."

> "Kypsyysarvio huomioidaan myös: Ajanvarauksessa · Kansalaisen ajanvarauksessa · Laskutuksessa."

and a prescription quiz item: *"Voitko lähettää 15-vuotiaalle tehdyn reseptin ilman
kypsyysarviotietoa?"*. The vendor cites `Lakimuutoksen myötä (703/2023 34 §)`.

So capacity is a **per-encounter, per-record, professionally assessed, legally defaulted** fact, and
it propagates into booking and billing. Our static attribute is the wrong shape, not merely the wrong
word.

## Decision

Model capacity/disclosure as an encounter-scoped, professionally assessed fact — a **`Kypsyysarvio`**
— rather than a person-scoped consent attribute.

- A `Kypsyysarvio` binds to a `Kohtaaminen` (palvelutapahtuma) and, for `scope="resepti"`, to one
  explicit `prescription_record_id`; it changes no sibling prescription or non-prescription record.
- Its value is a small controlled set: at minimum *decision-capable* and *not decision-capable,
  information to guardian*. The age default (`<12 → not decision-capable, information to guardian`)
  applies **only where no assessment exists**.
- It carries `assessed_by_role`, `assessed_at` and `origin_organisation`; it is editable only within
  the organisation that made it.
- It is **deterministic policy input**, applied in `policy.cut` before any model. No model assigns,
  infers, revises or overrides capacity.
- `consent_holder` on `Record` is retained as a **derived projection** for compatibility, computed
  from the encounter's `Kypsyysarvio` — not authored per record.
- A `legal_basis` string may travel with the value (see [ADR-0015](0015-source-aligned-record-envelope.md))
  so the briefing can state the rule it applied.

## Boundaries

- No clinical or legal validation. Our value set is illustrative synthetic policy; the vendor's
  `<12` default is a *product* default, not a statutory threshold we may assert.
- Age is a **default trigger, never a permission**. A birth year alone must not grant or remove access.
- An **absent** assessment is not a denial. Unassessed falls back to the current conservative lens,
  never to "the guardian sees everything".
- Neither a model nor the briefing may reveal *whether* an assessment exists for another person's
  encounter.
- Booking/billing propagation is recorded, not modelled in 1.1.

## Consequences

- The guardian lens becomes a projection of per-encounter capacity; `life_stage` stays a declared
  presentation axis and is not a permission source.
- Fixtures need a `kypsyysarvio` wherever a minor's records are in play, plus a
  `no-assessment` case.
- The existing `consent_holder` tests are the migration gate: if the projection is faithful they keep
  passing unchanged.
- This **supersedes the storage** of `consent_holder` in `upm.py`, not the `Poissuljettu` /
  silent-absence contract (ADR-0004), which is untouched.

## Relationship to existing architecture

`policy.cut` remains the single authority and still decides before any model (ADR-0011). This ADR
changes *what the cut reads* — an encounter-scoped capacity fact instead of a per-record consent
string — and nothing about the order of operations. It is compatible with ADR-0008's
"permission alone is not relevance".
