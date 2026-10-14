# ADR-0011 — Three-layer AI architecture, with the deterministic core in authority

Status: accepted by the owner. Records the layer split that outlived the earlier
readiness effort. ADR-0018 records the Decision-1 contract; this ADR records why the layers are
where they are.

## Context

The report's failure mode was arguing from precedent: prior ADRs were read as prohibitions,
so the AI stayed small, optional and invisible. The five acceptance criteria instead ask for a
measurable, visible, load-bearing AI that never exceeds the permission boundary. This
decision re-derives the architecture from those criteria rather than from earlier text.

## Decision

Three layers. The deterministic core keeps authority; both model layers are advisory and
either can be absent.

**Layer 1 — deterministic core (sole authority).** `app.policy.cut` decides visibility
before any model. `Päätös`, `Arviointi` and `Ehdotus` are deterministic and never
model-authored. The human decides. This layer is *why* the AI can be used ambitiously:
every model output is advisory, cited and reversible.

**Layer 2 — Decision-1 (the innovation).** A vendor-calibrated System One classifier rather than a
generator, shaped entirely by `state` + `criteria`, used as a bounded reader and as the
grounding verifier. Its contract, thresholds and coverage accounting are recorded in
[ADR-0018](0018-microsoft-decision-1-replaces-the-vendor-classifier.md).

**Layer 3 — Foundry (the platform).** The deployed configuration is single-shot structured
narration over post-`cut` evidence, a versioned project Prompt Agent with a direct-call fallback,
and scrubbed App Insights custom events. The agent and direct path both validate against the
Pydantic `FreshNarration` schema. The four-case scorecard and live encounter trial are
synthetic, not Agent Service batch evaluations or clinical accuracy results. The deployment
uses platform default RAI policy `Microsoft.DefaultV2`; no custom content-filter or Prompt
Shields policy is configured, and no block event has been observed. Evaluation results and
traces inform review; they never gate a request or establish clinical validity.

**Transport and configuration decision (verified against the live configuration).** One
configuration is used in local environment, Bicep, Container App and evidence: the Foundry
account and its default project, project-scoped route, model
`gpt-6.1-sol` (deployment version `2026-09-29`), Prompt Agent `luvn-narration:8`. On that
route/model, chat-completions with strict `json_schema`, plain Responses, and Responses with
`agent_reference` all returned 200; the agent call validated the real `FreshNarration` schema.
The deployed agent path is verified in an App Insights trace of the agent call
(1119 input / 460 output tokens). Earlier
HTTP 500s were the specific legacy model/project-route pairing; `_validate_route_model` now
blocks that pairing. App Insights is app-side custom-event correlation, not Foundry
service-side tracing or distributed spans. The Pydantic model remains the schema source of
truth.

**The boundary that makes this safe.** Every AI path reads only `Scoped` — the post-`cut`,
permitted set. An index or retrieval step may pre-filter by person, but `policy.cut`
remains the sole authority for visibility, and no AI component, agent or index may read
the corpus directly. A model never resolves identity, consent, `Sallittu`/`Poissuljettu`,
scores or actions. Retrieval before the cut is therefore not permitted: it would invert
the permission order. Eval datasets, traces and scorecards must not contain withheld
`Kirjaus` identities or counts, or other-person data; a trace sits outside the frontend's
disclosure discipline and is scrubbed before it is persisted.

## Consequences

- Discovery, authoring, and review prompts are written against this ADR's boundaries
  rather than against prior prohibition text.
- The approved agent is versioned and single-shot (`luvn-narration:8`), with no tools; its deployed
  invocation is verified, and a Foundry Agent Service batch evaluation suite is still open. Direct
  chat-completions with strict `json_schema` remains the fallback. **Amended:** the owner
  removed the "hosted-agent re-hosting and agent retrieval tools remain out of scope" clause. A
  conversational surface and an external knowledge layer are now in scope, and what bounds them is
  the permission boundary above — every AI path reads only `Scoped` — not a scope list.
- The scorecard, local environment, Bicep and deployed app now use the same Foundry account,
  project and model; do not reintroduce a cross-account configuration.
- Nothing in this ADR authorizes a deploy, a live trial or a clinical claim.
