# ADR-0022 — No new Foundry capability on the runtime path; three capabilities are worth an offline trial

> **Superseded in part by [ADR-0027](0027-the-tool-using-assistant-supersedes-the-deterministic-arm.md) (2026-10-13):** the *Function calling / tools* rejection below no longer holds for the assistant, whose tools are closed over the permission cut's output and run on the Responses API. Every other rejection in the table stands.

Status: accepted by the owner's delegate, 2026-10-11, on the recommendation of a `gpt-6-sol` oracle review
that fetched Microsoft's own documentation. Records a decision **not** to add Foundry capability to the
running system before the event, and names the three capabilities that are worth a bounded offline trial
afterwards. It extends [ADR-0021](0021-decision-1-stays-a-typed-call.md) (Decision-1 stays a typed call) and
[ADR-0018](0018-microsoft-decision-1-replaces-the-vendor-classifier.md) (one model, both roles) by answering
the wider question: *are we using the wrong set of Foundry capabilities?*

## Context

The owner asked whether the system should use Foundry capabilities other than the two it already uses — a
versioned **prompt agent** for narration and **direct typed calls** to `Microsoft-Decision-1` — and whether
the decision model in particular should become a Foundry agent.

The question is worth recording rather than answering in conversation, because most of the plausible answers
are **substitutions that would weaken a mechanism this repository already has**, and a future reader will
otherwise ask again. Each capability below was assessed against what the system does today, with the
documentation fetched rather than recalled.

## Decision

**1. No capability is added to the runtime decision or narration path before the event.** The regression
risk is asymmetric: the deterministic core, the permission cut, the per-sentence verifier and the scrubbed
telemetry all currently hold, and none of the capabilities below buys a property the product is missing.

**2. Three capabilities are worth a bounded trial, in this order, all offline or annotate-only.** They are
recorded here so the reasoning is not lost, not as work authorised now.

- **PHI-free OpenTelemetry tracing.** Genuine additive value: one trace joining the permission cut, the
  Decision-1 fan-out, the narration agent run and the per-sentence verifier, where today App Insights holds
  only scrubbed app-side stage events. Cost and risk: instrumentation, provider integration, and the GenAI
  semantic conventions record prompts **by default**, which is exactly the leak
  [ADR-0021](0021-decision-1-stays-a-typed-call.md) already forbids. Proof: one synthetic encounter produces
  one trace with the stages and latencies visible and **zero** note text, record ids or prompts in any span.
  See [tracing concepts](https://learn.microsoft.com/en-us/azure/foundry/observability/concepts/trace-agent-concept)
  and [`backend/app/telemetry.py`](../../backend/app/telemetry.py).
- **One offline evaluation of the deployed `luvn-narration:8` — DONE, 2026-10-11.** Genuine additive
  value: it shows that the *deployed, versioned* agent behaves as the local prompt copy does, giving a
  reproducible baseline for `:9` — which the smoke harness, a direct-model call, does not do. It is not
  clinical evidence: its case expectations are structural and it judges with the same verifier the
  application uses, so agreement between the two is not independent.

  Run as [`backend/scripts/run_narration_agent_v8_eval.py`](../../backend/scripts/run_narration_agent_v8_eval.py),
  emitting [`data/narration-agent-v8-eval.json`](../../data/narration-agent-v8-eval.json), which is verified
  offline by `scripts/check.sh` so its numbers cannot drift from the corpus they were measured on. It calls
  the shipped entry point (`llm.generate_fresh_narration`) over five cases derived from the tracked synthetic
  corpus — four clinical shapes plus one injection proxy — and runs the shipped acceptance gate
  (`validate_fresh_candidate` → the per-sentence Decision-1 verifier → `accept_supported_candidate`).

  Result on agent version 8: **5/5 served by `agent_reference`** (no direct fallback), 5/5 schema-valid,
  5/5 citations inside the permitted scope with no uncited sentence, and **18 of 18 generated sentences
  approved**, p50 9.4 s. The injection proxy — an instruction string appended to a note's own text and
  recorded as forbidden — did not appear in any output.

  Two things this measured that are worth more than the rates. First, the agent is not returning prose this
  system has to repair: every sentence it wrote survived the verifier, so the fallback and the retry paths
  were never exercised — which is the state a `:9` regression run must be compared against. Second, running
  it exposed a **stale local `.env`**: the file named agent version **7** while the deployment serves **8**,
  so a developer testing locally was exercising a different agent than production. The artifact now records
  the version it evaluated, and the runbook tells the reader to take that value from the deployment.

  A third thing it caught was a defect in the evaluation itself, recorded because the same mistake is easy to
  repeat: an earlier revision of the script passed the verifier's raw rows to `accept_supported_candidate`,
  whose `SupportReview` model is `extra="forbid"`. Every review was therefore rejected as `invalid`, and
  because the rejected-sentence list was empty the script reported a **100 % approval rate** on a run the
  acceptance gate had refused outright. It now projects the three verdict fields exactly as `narration.py`
  does, and `--check` fails an artifact in which an `agent_reference` row is `invalid` — so a script that
  stops exercising the gate cannot ship a clean-looking result.
- **Annotate-only Prompt Shields trials against malicious note text.** Genuine but unproven value: record text
  is untrusted input and is already the subject of a copied-fragment injection proxy in the narration
  harness. Annotate-only, measuring true/false positives and latency before considering any block, and never
  as a substitute for the permission cut or the grounding check. See
  [prompt shields](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/jailbreak-detection).
  The current content-filter handling in [`backend/app/llm.py`](../../backend/app/llm.py) is **not** evidence
  that Prompt Shields is enabled.

**3. The remaining capabilities are rejected, each because it is a substitution for something better.**

| Capability | Why it is not adopted |
| --- | --- |
| Model router | Changes the fixed model identity that ADR-0018 chose deliberately, and does not document `Microsoft-Decision-1` as an eligible underlying model, so it cannot carry the typed protocol. Also a regression for the one-model-same-terms property. |
| Connected agents / multi-agent workflows | Puts a model in the control flow, which [ADR-0011](0011-ai-architecture-layers.md) forbids: permission is decided before any model sees a record. |
| Function calling / tools | Unnecessary model latitude over operations the permission cut already performs deterministically. A tool that can choose what to fetch is a permission decision by another name. |
| File search / knowledge indexes | Adds retrieval, hosted-toolbox isolation caveats and cost without improving coverage: the repository's own retrieval ablation found every authored note already reachable and no selection improvement in the frozen inventory. See [`.scratch/retrieval-and-knowledge/results/retrieval-ablation-v1.json`](../../.scratch/retrieval-and-knowledge/results/retrieval-ablation-v1.json). |
| Continuous evaluation / monitoring | Sampling production traffic would require sending content the telemetry deliberately excludes; the current design holds no text, so there is nothing to sample without changing the privacy position. |
| Fine-tuning | No clinician-adjudicated corpus exists, and fitting to the authored, unadjudicated keys would launder their weakness into a model. |
| Batch | Asynchronous with a 24-hour completion window, which contradicts the online decision budget. |
| Prompt optimizer | Rewrites system instructions with no version history, which is incompatible with a pinned protocol; usable only offline against the narrator as a candidate. |
| Red teaming | A synthetic adversarial complement, not clinical validation; the documentation notes English-only agentic probes and constraints on connected/tool scenarios. |
| Agent-wrapping Decision-1 | Rejected in [ADR-0021](0021-decision-1-stays-a-typed-call.md), with the added fact that provider-enforced structured output is unavailable to Agent Service calls, so the native calibrated `answers` contract cannot be reproduced by asking a chat model for JSON. |

## Consequences

- The runtime path is unchanged, so no new failure mode, identity or residency question is introduced during
  the delivery week.
- The three trials are recorded with their costs and their proofs, so starting one later is a matter of
  reading rather than re-arguing.
- The strongest evidence gap remains what it was: an **independently clinician-labelled, blinded
  sentence-and-citation benchmark**. No Foundry capability substitutes for it, and this ADR does not pretend
  otherwise.
