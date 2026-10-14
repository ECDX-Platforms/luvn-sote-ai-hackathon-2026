# ADR-0019 — The conversational surface: it explains, and the generator is an addition

> **Superseded in part by [ADR-0027](0027-the-tool-using-assistant-supersedes-the-deterministic-arm.md) (2026-10-13):** Decision 1 (the deterministic arm is the surface, and it makes no model call) no longer holds; the tool-using assistant is the surface. Decisions 2 and 3, the sealed digest, the one refusal and the actor rule are retained and re-pinned there.

Status: accepted by owner, 2026-10-10. Implements the design in
`.scratch/chat-and-suggestions/plan.md`, and this record resolves the fork that plan left open (§4) and the
reversal it flagged (§8).

## Context

The plan asked for one owner decision before implementation: **explainer only**, or **clinical suggestion**.
The second rewrites the product's purpose statement and puts MDR qualification in play (Art. 2(1) with a
clinical purpose → Art. 61, Annex I GSPRs, ISO 14971, clinical evaluation, PMCF, UDI, CE), while Art. 50(1)
applies either way the moment a conversational surface exists.

The plan also recorded a precondition: `risk.json` ships numeric cut-offs rendered as clinical findings, and a
conversational surface over undisclosed invented thresholds would launder them into conversational authority.
That precondition turned out to be **already satisfied** — rule outputs carry `basis: illustrative_threshold`
from the rule table to the wire, the frontend renders it on the claim, and `test_decide.py` plus
`challenge-demo.test.mjs` pin both which rules carry it and which do not.

## Decision

**The surface explains. It does not author clinical judgement, and the generator cannot make it.** Three
consequences, each enforced in code rather than promised in prose:

**1. The deterministic arm is the surface, and it makes no model call.** Permitted records are ranked and
restated from their own values; the deterministic engine's own outputs are restated with their basis visible.
With no generator configured, a turn still answers or refuses. This is the plan's sequencing ("ship the
refusal and ledger surface; the generator is optional and gated") made structural: `generate_sentences` is an
argument to `answer`, not a step inside it, so a generator failure arrives as `None` and degrades to the same
place a missing one does.

**2. When the generator runs, it restates and is verified per sentence by the briefing's own verifier.** Its
instructions forbid advice, recommendations, diagnoses, risk classifications and authority cues, and — more to
the point — a sentence it produces is published only if the verifier found it **supported** by the records it
cites, all of which must be members of the digest. A citation outside that digest's record set is dropped. Verified
generated prose is labelled `model_summary` rather than `source_fact`, because a machine-drafted sentence is
not a verbatim source fact and calling it one would be false. The plan's three statement classes therefore
become four, and the addition is recorded rather than smuggled. A sentence that cites nothing is rejected
before it reaches the verifier, so an answer the records cannot ground is an **empty** answer: the
instructions never ask for an uncited refusal sentence, and every turn carries which of three generator
states it is in — `not_configured`, `withheld`, `published` — so a model that was called and published
nothing is disclosed rather than read as a model-free answer.

**3. "Next steps" are the engine's, not the model's.** The `rule_output` class carries the action table's own
outputs — the same suggestions the briefing already makes. The authored pathway corpus is deliberately **not**
wired into chat: its steps need the pathway gate (one step in that corpus is authored to be refused when it
contradicts a record), and presenting them ungated would be exactly the laundering the plan warns about.

**The thread is bound to a sealed digest.** `sha256` over the **picture's own record set** — the records this view may read, after relevance selection, not the whole permitted set ([ADR-0023](0023-the-core-proposes-nothing.md)) — with a content hash per record,
plus encounter, role, purpose and rule version. A caller that presents a digest which no longer matches
receives `409` and the thread is sealed, rather than the conversation silently re-pointing at a changed
subject. The client never supplies history: prior-turn text would arrive as evidence and be treated as a claim
rather than as something the user said.

**One cause-neutral refusal, byte-identical.** `chat.ABSTENTION` is a single module constant, pinned by test,
and chosen so it cannot imply that something visible is being withheld — a refusal that varied with what the
hidden set contains would be a differential oracle over it.

**A review decision requires an authenticated actor.** The backend read no principal at all before this: Entra
authorised at the ingress and the application never learned who the professional was, so "who accepted this
suggestion" was unanswerable. It now reads the platform-asserted principal from the ingress headers, and a
review without one is refused. The label rendered is `Tallennettu palveluun`, never that anything was done.

## Consequences

* **Art. 50(1) now applies.** The surface is conversational, so the transparency duty is owed: the API returns
  an `answer_notice` ("Olet yhteydessä tekoälyyn. Vastaus on tarkistettava.") with every turn, and
  `docs/regulatory-position.md` §5's sentence that there is no Art. 50(1) duty *because no conversational
  surface exists* is superseded by this record. ADR-0017's machine-readable marking applies to the briefing's
  accepted prose; extending it to per-turn chat marking is named below as open, not claimed.
* **`.scratch/hackathon-readiness/spec.md`'s "Q&A is out of scope" is reversed** by this ADR, explicitly and in
  the same commit, rather than quietly superseded.
* **Persistence stays where proposal feedback is.** The application identity holds `db_datareader` on SQL by
  design, so persisting chat to SQL would require granting it write access to the corpus — the security
  property this repository keeps. Turns and reviews therefore use the same append-only create-if-absent
  JSONL/blob store as feedback, and the hosted DDL is recorded in `chat.py` for when that changes.
* **The audit is not wider than the source.** A turn records the question, the ids of the evidence it cited and
  a content projection; record content is referenced, never copied.

## What this does not do, and the residual limit

* No transcript is persisted, and no external evidence source is named — the `external_evidence` class exists
  with an empty allowlist, so naming a source later is a data change rather than a redesign.
* **Latent inference can survive every per-sentence check.** A visible medication can imply a diagnosis, and no
  amount of per-sentence grounding catches an implication the reader draws. Mitigated by inference review over
  accepted output; not eliminated.
* **The generator remains uninstructed by clinical content.** It has no guideline corpus, because Käypä hoito's
  terms prohibit ingesting it into any AI system (issue 12). Its answers are therefore only as good as the
  records it restates.
* PHQ-9 item 9 stays invisible to scoring and triage; this ADR does not fix that and should not be read as
  fixing it.
