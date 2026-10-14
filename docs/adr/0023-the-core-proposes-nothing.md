# ADR-0023 — The core proposes nothing: actions move behind an optional module, and a risk reason attaches to its record

Status: accepted by the owner. **Supersedes the "risk scoring and recommended actions are
retained" half of the earlier alternative-plan answer.** It implements the two changes that reading
recommended (the alternative-architecture lessons, §2.3 and §2.4) and leaves one open.

## Context

An alternative plan for the same corpus — a fork of this repository's own architecture — made two
structural choices this repository had not:

1. **Its core proposes nothing.** "No action recommendations are produced at any stage"; every suggestion
   lives in an optional downstream module that receives "exactly one thing: the final structured JSON".
   This is the team contract's rule 6 and rule 7, stated as architecture rather than as discipline.
2. **Risk is per record and carries its reason.** "Risk assessment lives here, in the classifier, and
   nowhere else", attached to the specific record it is about, "rather than a bare label on the patient". It
   did not delete risk; it changed its shape.

This repository did the opposite on both. `app.decide.decide()` evaluated a third table, `actions.json`, on
every request and returned `actions` as part of the core decision, wired into the external `Picture`
(`app/picture.py`) and rendered at alert severity. And `risk` was one global patient-level row per view.

`docs/regulatory-position.md` §2 already recorded the exposure honestly — the regulatory material's warning is
that a system which "alerts on risk data" may change status, and this repository's answer was that the level
and the suggestions come from deterministic tables marked illustrative. That answered a **trigger with a
caveat** at a point where a **shape change** was available. The owner has now chosen the shape change for
the actions half and the provenance half, and records it here rather than leaving it implicit.

## Decision

**1. The core proposes nothing.** `decide()` no longer evaluates `actions.json` and no longer returns an
`actions` key. Suggested next steps are produced by a new optional module, `app.actions`, which reads **two
scalars** — `situation_type` (a triage signal over the person's records, not the encounter's service situation) and `risk_level` — from the core's finished output and returns a list of
strings. It never receives a record, a persona, a `Scoped`, or a store handle, so it cannot leak what it was
never given; the module boundary holds by construction rather than by policy.

`Picture.actions` remains in the wire shape and defaults to `[]`. The module is **on by default**
(`LUVN_ACTIONS_MODULE`; `0`/`false`/`no`/`off` switches it off), so the default configuration shows what it showed
before. A deployment that wants the simpler posture flips one variable, and the briefing remains complete
without it: situation, risk, records and citations are unaffected. `scripts/check.sh` passes in both states.

**2. A risk reason attaches to the record that produced it.** `Picture.risk_reasons_by_record` maps a
permitted record id to the reason sentence(s) of the risk rule that fired, and the record view renders it as
*Riskihavainnon peruste* on the record. Only rules with single-record provenance contribute — the medication
conflict names both ends, and the vital-sign and missed-dose rules name the record holding the value. A rule
with no record behind it (an absence, a count, or a score assembled across records) contributes **nothing**,
because a record invented for it would be a filled absence, which [ADR-0004](0004-silent-absence.md) forbids.

This is provenance, not a new judgement: it restates the rule the tables already ran. It is deliberately
**not** an urgency assessment. To be exact, because an earlier draft of this ADR got it wrong: the per-record
urgency Score *does* exist — `app.decision_model` asks the `urgency` `score` question for every `Kirjaus`
(`decision_model.py`, the `ROLE_SELECTION` fan-out), its distribution is already on the wire as
`gate_probabilities.urgency`, and the recorded Decision-1 contract makes it
**ordering-only**. What this ADR refuses is *promoting* it to a per-record label, which would be a new
clinical claim rather than a restatement of an existing one.

## What does not change

- The deterministic tables still decide. `situation.json`, `risk.json` and `actions.json` are unchanged;
  only the stage that evaluates `actions.json` moved.
- No threshold, floor, priority, or ordering changes. The rules and their outputs are the same; only who
  evaluates the actions table, and when, is different.
- No record becomes visible that was not visible. `risk_reasons_by_record` is keyed on the **permitted**
  set, and a test asserts every key is a permitted record.
- The declared purpose sentence (`app/intended_use.py`) is unchanged, and its drift test still passes.
  This ADR does not add a status word to the wire.

## Consequences, stated as limits

- **The `actions` table is still shipped.** This ADR moves *where it runs*, not *whether the strings
  exist*. A reader who takes "the core proposes nothing" to mean the system no longer suggests next steps
  has misread it: with the module on, `actions` is non-empty and the UI shows it, exactly as before. The
  architectural claim — that the core can be run without it — is what is now true and tested.
- **`risk_level` is still one global value per view.** Only its *provenance* became per-record.
  `risk_reasons_by_record` is keyed on the **permitted** set, but the record view renders only the records in
  the **selected** tier, so the reason is shown *when the record is selected*. A conflict whose note was
  de-prioritised out of that tier leaves the risk row reading `korkea` with no record carrying the reason —
  which is the correct silence under [ADR-0004](0004-silent-absence.md) (absent, not hidden), not an omission
  to paper over. The bounded urgency work that shipped with this round is recorded in
  [ADR-0024](0024-the-urgency-legend-is-an-ordering-basis.md): the classifier's own leading level is shown
  **in words beside its distribution**, labelled as an ordering basis, and no per-record verdict, reason,
  floor or status colour is added.
- **`risk-arvioimaton` and the count-based rules carry no provenance**, deliberately. A view whose risk row
  is an absence shows no per-record reason, which is the correct silence.
- **The regulatory reading is unchanged.** `docs/regulatory-position.md` §1–§2 continue to describe the
  system as it is, including that the actions table is illustrative and not clinical guidance. This ADR
  narrows the exposure; it does not claim the device question is answered, and Annex III remains open and
  owned there.
