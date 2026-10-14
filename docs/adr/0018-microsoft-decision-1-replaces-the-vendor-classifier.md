# ADR-0018 — Microsoft-Decision-1 replaces the vendor classifier, for both reading and verification

Status: accepted by owner, 2026-10-10. Supersedes [ADR-0009](0009-decision-purpose-relevance-selection.md)
(it replaces the classifier vendor); the *contract* ADR-0009 states is retained and unchanged —
permission first, one call per `Kirjaus`, shortlist-then-rerank, risk-scaled abstention with named
thresholds, urgency as ordering only, and a per-sentence grounding verifier that sees only the records a
sentence cites. (The cited-only restriction is **LUVN's own design decision** — the shipped contract performs
no whole-database query or embedding join, so the sentence's citations are the whole evidence the check can
honestly consult — and it is **not** a Microsoft requirement or recommendation: Microsoft documents one
Groundedness evaluator whose `context` input is the full retrieved set, concatenated, and documents no
per-sentence mode. See [`../references.md`](../references.md) §1 and §8. The contract and this decision are
unchanged.) This ADR records a vendor substitution and one **accepted risk**; it is not a finding that
the two models are equivalent.

## Context

ADR-0009 selected a vendor-calibrated System One classifier (TypeSafe, model `decision-1.13.0`, endpoint
`api.typesafe.ai/v1/systemone`) as the advisory reader and verifier. Microsoft-Decision-1 answers the same
protocol — `state` plus typed `questions` (`noul` / `choice` / `score`), an `answers` object back with
probabilities and usage — at
`<foundry-host>/providers/microsoft/v1/systemone`, so it is a transport substitution rather than a
re-architecture: the question sets, validators, thresholds and the `Scoped`-only boundary all apply
unchanged.

Three things motivated the substitution. **One fewer processor:** every other model call already goes to
Foundry, so the classifier was the only remaining inference vendor, the only extra set of terms, and the
only extra jurisdiction to assess. **Residency:** Microsoft-Decision-1 offers `DataZoneStandard`, and the
account is in swedencentral, so inference can be kept inside the EU data zone — the vendor classifier
offered no equivalent lever. **Measured behaviour:** on the authored keys the two are indistinguishable for
selection (3/3 expected additions, 0 wrong additions, hit rate 1.0 for both) while Decision-1 used 33 %
fewer input tokens at the same published rate and separated the classes far more sharply (0.98–1.00 on
expected-relevant notes against 0.70–0.91; 0.03–0.29 on non-relevant against 0.16–0.58).

## Decision

**Microsoft-Decision-1 serves both roles.** There is one classifier, configured by deployment name, and no
second vendor is retained behind configuration. The optional two-role configuration introduced while the
comparison was open is removed with it: with a single model it would be a setting that can only misconfigure
the system.

**The verifier question was adjudicated, and the answer is about our rule rather than the model.** With one
model answering both, the two-of-twenty-nine difference between the shipped rule and the evaluation rule is a
**discrepancy in our own code**, not a model property. `gpt-6.1-sol` labelled every accepted sentence from the
sentence and its cited records alone (35 sentences, one pass): the shipped runtime rule agrees with the
labeller on 33, the evaluation rule on 34, and the two rules differ on two sentences — both from the same
neuvola encounter, and on **both the shipped rule abstains (`unknown`) where the labeller says `supported`**.
On one of them the evaluation rule over-rejects as `unsupported` while the labeller says supported, which is
the single case where both rules disagree with it. So the honest reading is not "the model is more permissive
than the previous one" but **our verifier is over-conservative: it declines to publish sentences that are
supportable**. That costs recall, not safety, and it is fixable in the rule. Measurements:
[`verifier-adjudication-v1.json`](../../.scratch/retrieval-and-knowledge/results/verifier-adjudication-v1.json).
A third opinion is not ground truth, and this is recorded as one.

**Where the cited-only restriction comes from.** It is **ours, not the model's and not the vendor's**: this
repository's contract performs no whole-database query, embedding join or external search on the verifier path,
so a sentence's own citations are the whole evidence the check can honestly consult. Microsoft documents one
Groundedness evaluator whose `context` input is the full retrieved set, concatenated, and documents no
per-sentence mode and no context-restriction rule, so nothing about the cited-only scope is a vendor
requirement. Recorded because an earlier framing of this contract implied otherwise; the scope itself is
unchanged. See [`../references.md`](../references.md) §1.

**The verifier substitution is an accepted risk, and this is the sentence that records it.** As a
per-sentence grounding verifier the two models did **not** measure as equivalent: over 29 sentences,
Decision-1 marked `supported` two sentences that the shipped rule left `unknown`, and `unknown` is the
fail-closed verdict. That is conservatism lost, not accuracy gained. A `gpt-6.1-sol` adversarial review of
the evidence returned `no-go` on presenting the full substitution as settled, with exactly this as its
sharpest technical finding. The owner has decided to substitute anyway, on the grounds that one vendor and a
closeable residency claim outweigh a two-sentence difference on a small set that has no independent
sentence-level labels to adjudicate it. **Recording the disagreement is part of the decision**: it is why
this ADR exists rather than a silent rename, and it is the first thing to re-examine if the verifier's
behaviour is ever questioned.

**Where the conservatism sits, measured later.** A later adjudication labelled 35 accepted sentences
from their own text and cited records with a third model and scored both rules against it
(`verifier-adjudication-v1.json`). On the two sentences the two rules differ about, the **runtime
acceptance rule** leaves both `unknown` while the independent label supports both — so the conservatism
is in the acceptance rule, not only in the model, and it is a false refusal rather than a missed
endorsement. The evaluation rule is right about one of the two and wrong about the other. The two rules
are deliberately different and must stay so: making the evaluation rule identical to the runtime rule
would make them agree by construction and hide exactly this. See
`.scratch/retrieval-and-knowledge/issues/13-do-not-unify-the-two-verifier-rules.md`.

**The thresholds remain inherited and uncalibrated, and are labelled as such.** The floors
(`ADDITION_FLOOR = 0.60`, `OMISSION_FLOOR = 0.70`, `DECISION_MARGIN = 0.25`) come from the previous
model and were never calibrated on Finnish data, so this substitution does **not** change them. A sweep
over the recorded answers (`results/threshold-sweep-v1.json`) reports `safe: true, recall: 1.0` at every
floor from 0.60 to 0.98 — which is not a calibration: with ten candidates and three positives the grid is
degenerate, and the sweep's own `limits` say it finds a floor to calibrate *from*. Raising a floor to
match a number in this document would be fitting prose to a sample of three; leaving the floors where
they are, and saying they are uncalibrated, is the honest position. Calibrating them needs independent
labels on Finnish data, which do not exist yet.

**The selection contract version is bumped**, because the model that implements it changed:
`challenge-1.1-decision-selection-v8`. A contract identifier that names the vendor would be stale on the
next substitution, and the version is part of what a reader uses to know which behaviour was measured.

## Consequences

* The egress table in [the regulatory position](../../docs/regulatory-position.md) loses the classifier
  vendor. The remaining inference egress is **two** Foundry deployments, and they do not share a data zone:
  this ADR's `DataZoneStandard` guarantee is about `Decision-1` (the classification and verification calls)
  **only**, while the narration model `gpt-6.1-sol` is deployed `GlobalStandard`, so generated prose can be
  processed outside the region. An earlier revision of this sentence said "the remaining inference egress is
  Foundry under `DataZoneStandard`", which read as a guarantee for both paths and was false for the
  narration one; a live readback of the deployments is what caught it. This record deliberately makes no
  regional claim for the narration path — see `docs/hosting-runbook.md`, which states the trade-off and what
  production would need (a regional data zone or a private endpoint for both).
* `LUVN_DECISION_DEPLOYMENT` names the deployment. There is no classifier key, no fallback provider and no
  per-role provider setting.
* The measured evidence base is unchanged and remains small: nine scored notes, author-authored and
  unadjudicated keys, no independent sentence-level labels. Nothing in this ADR claims clinical validity,
  and the deterministic core keeps sole authority over what a person may see.

## Evidence

* `.scratch/retrieval-and-knowledge/issues/08-model-choices.md` — the comparison, the harness mistakes that
  were caught, and the oracle's verdict.
* `.scratch/retrieval-and-knowledge/results/decision-fanout-compare-v1.json` — the production contract.
* `.scratch/retrieval-and-knowledge/results/decision-verifier-compare-v1.json` — the verifier difference.
* `.scratch/retrieval-and-knowledge/results/oracle-review-v1.json` — the `no-go` and its conditions.
* `.scratch/retrieval-and-knowledge/results/threshold-sweep-v1.json` — the floor calibration.
