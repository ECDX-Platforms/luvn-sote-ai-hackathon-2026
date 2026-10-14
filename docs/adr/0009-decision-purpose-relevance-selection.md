# ADR-0009 — Decision-1 as the bounded reader and grounding verifier

Status: accepted by owner for the synthetic Challenge 1.1 demo. Supersedes ADR-0009's
earlier v4 relevance contract. This ADR states the current selection behavior; earlier prose
that the fan-out, shortlist or abstention superseded is pruned, not annotated. The deployed
selection contract is `challenge-1.1-decision-selection-v8`. The live revision, its
commit and its image are deployment state, recorded in [architecture as built](../architecture-as-built.md)
and the deployment record; this ADR deliberately does not restate them, because a revision triple
goes stale on the next deploy while the contract does not. Prompt Agent `luvn-narration:8` was
created from the committed canonical narration instruction file.
Deployment does not establish relevance accuracy or clinical safety.

**Superseded by [ADR-0018](0018-microsoft-decision-1-replaces-the-vendor-classifier.md) on
2026-10-10: the classifier vendor was replaced by Microsoft-Decision-1, now serving both roles.
Everything below about the contract — permission first, the fan-out, shortlist-then-rerank,
risk-scaled abstention, urgency as ordering only — still holds; read the vendor as substituted and
the contract version as `challenge-1.1-decision-selection-v8`. This record is kept as written
because it documents the decision that was actually shipped.**

## Decision

Decision-1 is a vendor-calibrated System One classifier, not a generator, and it sits in two places in
the encounter path: reading permitted notes for selection, and verifying generated prose
before it renders. Both are advisory. The deterministic core keeps authority.

**Permission first.** `app.policy.cut` runs before any model. Decision-1 sees only the
post-`cut` `Scoped` set. No AI component reads the corpus directly; no index, retrieval
step or agent may read outside the permitted scope. Decision-1 cannot authorize a record, select
a person, resolve identity or consent, remove a protected baseline source, create a
patient fact, or change `Päätös`, `Arviointi`, `Ehdotus` or an assessment score.

**Fan-out, one call per `Kirjaus`.** For each candidate `Kirjaus`, one request carries
several questions against one `state` (the note is ingested once; questions are evaluated
against it in parallel):
`relevance` ([Choice](https://docs.typesafe.ai/primitives/choice), `relevant` /
`irrelevant` / `unclear`), `time_sensitivity` (Choice: current / historical / unclear),
`subject_binding` (Choice: person / other_person / object / administrative / unclear); and
`urgency` ([Score](https://docs.typesafe.ai/primitives/score), levels 0–4: how time-critical
is the fact for this visit? — **ordering only**, see below). Per-note isolation is kept:
one `state` per `Kirjaus`, never several notes batched into one state.

**Shortlist, then rerank.** Decision-1 does not see four notes. After `policy.cut`, a
deterministic shortlist ranks permitted current free-text candidates by topic match,
`Palvelukonteksti` baseline terms, links to the person's medication/diagnosis records and
recency, and interleaves baseline-selected and baseline-missed cohorts. A baseline-missed
candidate is one the baseline terms did not match, so within that cohort the keyword score
is near-zero for every row and the old order fell back to recency alone — burying a long,
content-rich relevant note behind newer filler. Half the missed-cohort slots are therefore
reserved for a content-first exploration order: lowest keyword/medication-link overlap
first, then the longest note (most content), then recency. The ranked list is
bounded by a candidate cap and the stage time budget, so the stage does not scale with the
size of a person's record. Decision-1 reranks within that bound and may abstain. The deterministic
baseline remains the floor.

**Risk-scaled abstention.** The gate reads returned `probabilities`; `confidence` is retained for audit/display but is not a gate input.
Thresholds are named module constants:

- **Addition** of a baseline-missed current free-text `Kirjaus`: floor `0.6`, margin
  `0.25` on `relevance`; plus the `time_sensitivity` (`current`) and `subject_binding`
  (`person`) choices must themselves clear floor `0.6`. A near-uniform auxiliary answer abstains — a weak probability distribution means *do not act*.
- **Omission** of a baseline current free-text `Kirjaus` from Foundry generation: floor
  `0.7`, margin `0.25`. Destructive, so the bar is higher.

**Urgency is ordering-only (Score).** The fan-out also asks for a five-level urgency Score, where levels 0–4 move from background information to time-critical information for this visit. `_score_answer` validates the response type, complete level probability distribution, sum, score range/weighted consistency, confidence and legend; malformed Score output fails closed. The distribution is recorded in the existing `DecisionNoteOutcome.gate_probabilities["urgency"]` map, and the returned score only sorts already-eligible entries in `Selection.priority_ids` / `Records.priority`. It does not affect visibility, inclusion or omission. It also fixes the order in which selected sources are presented to the generator — the first source most time-critical when a Score is available — and the narration instructions tell the narrator to follow that given order and never re-rank. The order is computed deterministically from the validated Score; no model decides clinical priority. The Picture wire shape is unchanged (ADR-0001).

**Negation is dropped from the fan-out (correction).** `negation` was originally a conjunct
of the addition gate (`negation` ≤ `0.3`), then demoted to recorded evidence after it
inverted the thing being measured. It was then attempted as a **structural narration
directive**: an answer clearing a floor would attach a deterministic per-source
"this entry records an absence" instruction to the generator. On inspection that is not
safely derivable from the primitive: the `negation` Noul is **source-wide**, while a single
`Kirjaus` can record a present finding and an absence in different clauses. A source-wide
directive would therefore tell the narrator to phrase a mixed note as wholly absent —
inverting part of it. The finding is not reliably salvable by thresholding, so the question
is **removed from the fan-out** rather than paid for. A recorded probability that changes
nothing is cost without benefit. The prose-inversion risk stays where it was already owned:
the grounding verifier's `contradicts` question rejects a sentence whose claim its cited
source contradicts, per sentence, against named evidence. The `DecisionNoteOutcome`
`negation_probability` field is retained on the frozen Picture wire (ADR-0001) for
compatibility but is no longer populated.

Below the bar nothing happens: a missed `Kirjaus` stays unselected; a baseline `Kirjaus`
stays selected, citable, and in the generation input, merely not emphasized. The vendor's
[confidence guide](https://docs.typesafe.ai/confidence) documents thresholding as the
intended use of `confidence` and states "low confidence: do not act"; this decision uses
it that way. "Confidence is not correctness" means a confident answer is not thereby true,
not that confidence is unusable. The thresholds above are a starting point derived from
the vendor's routing example, not from measured Finnish accuracy; calibration on observed
data replaces them.

**Grounding verifier.** `llm.judge_narration_support` — a second generative call to the
same model asking whether a sentence is supported — is retired. Per candidate sentence,
Decision-1 receives a `state` of the sentence plus **only the records that sentence cites**
(already restricted to the post-`cut` selected scope) and asks `support` (Choice: `supported` / `unsupported` / `unknown`), `contradicts`
(Noul), `attribution` (Choice), and one `supports:<record_id>` Noul for each cited
source. The per-source questions are evaluated in the same request; only IDs whose
support probability clears the verifier floor enter `supporting_record_ids` and the
rendered citation list. **The cited-only scope is this repository's own design decision, not a vendor
requirement:** the contract performs no whole-database query or embedding join, so the sentence's citations
are the whole evidence the check can honestly consult. Microsoft documents one Groundedness evaluator whose
`context` input is the full retrieved set, concatenated, and documents no per-sentence mode — see
[`../references.md`](../references.md) §1 and §8. *Why this note exists: the scope was previously framed as
though the platform called for it; the scope itself is unchanged.*
The deterministic membership check `grounding.validate_fresh_candidate` runs before the
verifier: a sentence citing a source outside the selected scope is never sent to Decision-1. A
generator judging a generator shares its failure mode and can be persuaded by fluent
prose; a calibrated classifier returning probabilities can abstain instead.

An invalid-membership candidate, or a candidate whose sentences are all `unsupported` or
`unknown`, may trigger **one** regeneration within the same joint preparation deadline. The
retry receives only rejected sentence IDs, bounded reasons and permitted citation IDs; it
never receives rejected sentence text or invalid source IDs. The regenerated candidate is
membership-checked and verified from scratch. It is not itself approval: if it is rejected
again or there is not enough deadline left, no rejected prose renders. Mixed results retain
only the supported sentences as before; a partial result does not trigger regeneration.

Abstention remains fail-closed: `unknown`, a below-threshold verdict, a malformed response,
a timeout or a missing key cannot render unsupported prose. Accepted citations are built
from the verifier's `supporting_record_ids`, never from a sentence's whole source list.
Unsupported sentences are retained only as bounded rejected-sentence evidence
(`GroundingOutcome.rejected_sentences`), never rendered as prose.

**Coverage is honest.** The `Picture` reports how many candidates existed, how many were
shortlisted, how many were assessed, and why the rest were not (`not_shortlisted` /
`budget_exhausted` / `too_long` / `unavailable`), plus gate-refused counts. An encounter
that assesses 12 of 80 says so. Note-level Decision-1 outcomes are exposed only for
baseline-selected notes and selected relevant additions: a missed `irrelevant` or
`unclear` note's source ID is never disclosed, and the same rule governs the trace view
and any scorecard.

**Both model layers are optional.** A missing key, a remote failure or a timeout preserves
a usable deterministic briefing. No whole-customer-database query, embedding join or
external search is part of this contract.

## Constraints and evidence

The gate thresholds live in `app.decision_model` as named constants. Deterministic outputs
(`decide`, `assess`, proposals) do not depend on the shortlist or on Decision-1. Decision-1's label is
neither evidence nor a clinical decision; it is evidence about a note and about prose, not
about truth, source membership or a clinical judgment.

The offline synthetic checks cover fan-out parsing and fail-closed answers, the
risk-scaled threshold asymmetry, gate-refused coverage accounting, deterministic
shortlist ordering and the candidate bound, per-sentence verifier isolation with
cited-only evidence, membership-before-model, one bounded regeneration with ID-only
rejection feedback inside the shared deadline, rejected-sentence retention, and Decision-1
on/off invariance for deterministic outputs. These checks do not establish clinical
relevance quality. The pinned fault fixture
(`data/experimental/decision-faults-v1.json`) is unchanged. The v2 fixture remains test-only
with provisional, unadjudicated labels; it is not a live trial.

This ADR does not authorize future live calls or deployments; those require owner approval.
Revision `--0000017` and its owner-approved live Decision-1/Foundry evidence are recorded in
`.scratch/hackathon-readiness/results/deployment-record.md`. The F2 value evaluation is in
`.scratch/hackathon-readiness/results/decision-value-eval-v1.json`. Historical note: before the
first negation correction it read `addition_precision=null`, zero wrong omissions, and
`bound-note-011` abstained because a negation probability `0.31` exceeded the then-`0.30`
addition ceiling — a `p=1.00` relevant read refused by `0.01`. Under the current v7 contract
(source-wide negation question dropped, missed-cohort exploration ordering) the 11-case
inventory adds `bound-note-011`, `bound-note-017` and the authored keyless probe
`bound-note-scale-041`: correct additions 2/2, precision 1.0, wrong 0/2, missed expected
0/3. A five-repeat stability run added `bound-note-017` 5/5 with subject-binding margins
0.33–0.41 against the unchanged 0.25 threshold, so the case remains boundary-limited rather
than stable. An earlier run gate-refused `bound-note-017` at margin 0.24. The selection-only
omission check and stubbed generator-input test show the expected omission 1/1 while
preserving the source; the ambiguous note returned `unclear` 1/1 with `gate_refused=false`.
The scale fixture reconciles 42 candidates → 42 shortlisted, 41 assessed, 1 too long, 0
budget-exhausted, 0 not-shortlisted, 0 unavailable (at the shipped 80/80 cap; the earlier 40/12 cap
recorded 40 shortlisted, 11 assessed, 28 budget-exhausted, 2 not-shortlisted). The live-service sentence-order check found
that agent v7's sentence 1 cited the first `Records.priority` source in 4/7 comparable encounters,
so v8 was created from the committed prompt. The post-deploy v8 live-services recheck found
5/7 comparable (5/11 total; four had no priority source); two comparable encounters still did not
lead with the priority source. The recheck used the live services and current committed pipeline
locally, not an 11-encounter signed-in hosted trial. This is observed behavior, not a guarantee.
These are author-authored synthetic expectations, not Finnish relevance accuracy or clinical truth. No real-source integration,
50,000-profile working-set measurement or matched cost study exists.
