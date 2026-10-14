# ADR-0024 — The urgency legend is an ordering basis, not a verdict

Status: accepted by the owner on the recommendation of a `claude-fable-5.1` oracle review that
**refused** the larger version. Records what was shipped: the classifier's own leading urgency level is shown
**in words beside its probability distribution**, under a label that names its own limit — and nothing else.

## Context

An alternative plan for the same corpus (a fork of this repository's own architecture) proposes a
**per-record urgency shape**: a named urgency level per record, carrying the classifier's recorded reason and
the floor it had to clear, rendered on the record the way `risk_reasons_by_record` now is
([ADR-0023](0023-the-core-proposes-nothing.md)).

Three facts decided the outcome, and each was checked against the code rather than the prose:

1. **The urgency signal already exists, per record, on the wire.** `app.decision_model` asks the `urgency`
   `score` question for every `Kirjaus`; the validated distribution travels as
   `gate_probabilities.urgency`. What the plan proposes is therefore mostly a **re-presentation**.
2. **Two of its three fields do not exist and would have to be invented.** The classifier returns
   `score`, `confidence`, `probabilities` and `legend` — there is no reason text anywhere in the response.
   And urgency clears **no floor**: the named floors apply to the relevance, time-sensitivity and
   subject-binding gates only (the recorded Decision-1 contract). Presenting a
   "floor it cleared" would fabricate a threshold.
3. **A per-record *level* changes the posture; a distribution does not.** `docs/regulatory-position.md` §2
   answers the regulatory material's "alerts on risk data" trigger by saying the only severity wording on a row is
   **source-supplied** (`tulkinta`), "never computed by us". A named, model-derived urgency level on a record
   is a computed severity statement about the patient — the same genus as the action suggestions that
   `app/actions.py` exists to keep out of the core. The recorded Decision-1 contract is explicit that the score "does not affect
   visibility, inclusion or omission" and that "no model decides clinical priority"; a visible level per
   record makes that read false whatever the code does.

## Decision

**The urgency distribution is shown with the classifier's own leading level in words, labelled as an
ordering basis, and nothing more.** Concretely:

- `backend/scripts/render_decision_protocol.py` renders the five legend strings from
  `app.decision_model.URGENCY_LEVELS` into `docs/decision-protocol.md`, each beside a short **UI label**, and
  the document states the limit in words: the levels describe *scheduling* urgency, never treatment urgency,
  and a level is never shown as a patient-level verdict.
- `frontend/src/challenge-demo.mjs` holds `URGENCY_UI_LABELS` and `leadingUrgencyLabel()`. The label is the
  **argument-max of the distribution already on the wire** — never a new number, and `null` when the
  distribution is unusable, rather than a default level.
- The AI-provenance row renders, for the urgency gate only:
  `kiireellisyys (järjestysperuste, ei hoidon kiireellisyys): <distribution> — <label>`.

**What is deliberately not added:** no new `Picture` field, no per-record badge, no status tone or colour, no
severity word, no "reason", no "floor", and no change to what the classifier is asked. The request the model
receives is unchanged, so the pinned protocol digest moves only because the descriptor's own `used_for` text
became more precise — a change this ADR records so the pin is not moved silently.

## Consequences

- **A reader now sees a level in words where they previously saw only numbers.** That is the whole gain, and
  it is bounded: the words are the classifier's own `criteria` text, not a sentence this repository composes,
  and the surrounding label says what the number is for.
- **The legend cannot drift from the question.** `backend/tests/test_decision_protocol.py` fails if the
  document's legend rows and `app.decision_model.URGENCY_LEVELS` disagree, and
  `frontend/src/challenge-demo.test.mjs` fails if `URGENCY_UI_LABELS` and the document's label column
  disagree, in both directions and on the count. A frontend test also refuses any label that reads as a
  clinical severity or a treatment instruction.
- **"Ordering-only" remains a property of the code, not only of the prose.** The score still does not select,
  withhold or omit; it sorts already-eligible records and fixes the order offered to the narrator.
- **The larger shape stays unshipped, and the reason is recorded rather than implied.** If a future round
  wants a per-record urgency label, it needs a *reason* field the classifier does not currently return and a
  decided position on computed severity wording — neither of which is a re-presentation of what exists.

## Open item this round did not settle

`docs/regulatory-position.md:39` still describes the situation picture as having "no diagnosis, no treatment,
**no scoring, no ordering**". The system does order by the classifier's urgency score, and that ordering
predates this round. The register row is inaccurate and is left for its owner: correcting it means deciding
whether the shipped ordering is disclosed in the declared purpose of use, which is a positioning decision
rather than an engineering one.

**Resolved by the owner: O3 — disclosed in the regulatory position as narrowed wording.** The blanket
"no ordering" clause is replaced by *"The system orders already permitted and selected records by a validated
scheduling-urgency score; urgency never selects, withholds or omits a record, and inclusion and omission are
decided before it runs."* — the formulation already recorded at `:62-64` above.
