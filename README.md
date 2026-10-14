> **Confidential — view only, for hackathon evaluation.**
> © 2026 Tieto. All rights reserved. This is not open-source software.
> The SOTE AI Hackathon 2026 judges may view this source code to evaluate the entry, until 15 October 2026.
> Nothing else is permitted: do not copy, clone, fork, download, run or reuse any part of it. See [LICENSE](LICENSE).

# LUVN 1.1 — purpose-aware pre-visit briefing

Start with [`ARCHITECTURE.md`](ARCHITECTURE.md) — the architecture as built, explained with diagrams and in the order the system actually works. Current implementation and measured limits in full: [`docs/architecture-as-built.md`](docs/architecture-as-built.md). The decision model's typed protocol, as a pinned value: [`docs/decision-protocol.md`](docs/decision-protocol.md). Deployed revision `luvn-challenge11-product--night5` is live — built from commit `2aebda44606b77bfd1287c66a3be1ad8599bf100`, image `sha256:f01024b526caa5057926969cbbca03d4a557a10a12dfac68b3a19d9e3e0104b9`, with `minReplicas=2` / `maxReplicas=4`, and shipped by a targeted `az containerapp update` from a clean detached worktree. **`luvn-challenge11-product--night5` carries the night of 2026-10-14 and the owner's morning rulings (`--night1` to `--night4` were the same morning's earlier cuts, replaced within hours): Decision-1's findings as blue information rows in the attention panel (what it added, what it put first, what it called relevant and current), the ordering caveat off the chips; and: the doctor sees the whole clinical record, a result from the last year is baseline in every view, the generic fallback step and the catalogue-absence rows are gone, clinical appointments open the doctor lens on the day view, and the open-esitiedot question is answered from the picture; also: the assistant as a right-docked side pane on every destination, the diagnoses table folded to one row per code and status with the historical medications behind a disclosure, plain-Finnish lab labels, a five-to-seven-sentence source-grounded summary on narration agent version 12, a Käypä hoito question that publishes the guideline citation beside the verified sentence, and the doctor lens (`laakari`, mielenterveyden arvio) in the loaded corpus (20,650 encounters). Its predecessor `luvn-challenge11-product--closeout2` carries the close-out of 2026-10-14: the component library at the ADR-0028 pin with the library's own `AppShell`, tables and blocks (ADR-0028); the tool-using assistant as the only conversational surface, the keyword route deleted (ADR-0027) and the opening prompts derived from the attention summary; knowledge retrieval over Azure SQL `VECTOR(1536)` with `text-embedding-3-large` (514 chunks loaded, token-overlap fallback when unconfigured); a 30 s Decision-1 stage with 10 s calls inside a 160 s preparation window; same-day home-care visits folded in the social baseline; the dedicated Tekniset tiedot page, accept/edit on the pre-filled form, Decision-1's time-sensitivity on the day and the briefing, warm-up readiness on the day view, and the tester findings C1–C4 closed. Its predecessor `luvn-challenge11-product--responses` carries the browser-pass rounds on the final product: the assistant's tools run on the Responses API (`gpt-6.1-sol` refuses function tools on chat completions), any encounter opens from the day, from search or from a deep link beyond the catalogue page, the day keeps readiness honest across days, duplicates fold, decisions read as plain text, and the phone layout holds at 390 px; four tester passes closed with no open UI finding. Its predecessor `luvn-challenge11-product--0000102` carried the first browser pass: landing on the day, readiness kept honest across days, duplicate facts folded, and plain-text decisions. Its predecessor `luvn-challenge11-product--main` was the `main` of 2026-10-13 deployed, so the running revision and the repository agreed: it carries the enforced no-first-party-lint-warning invariant, the conversation reset as the mount key's job, and the provenance ledger that names every vendored file's source. Its predecessor `luvn-challenge11-product--nextsteps` stops the assistant refusing the question whose whole purpose is the engine's next steps: the words that make a question procedural -- `toimenpiteitä`, `suosittelet`, `seuraavat`, `mitkä` -- were missing from the vocabulary that decides it, so asking what to do next named a "topic", and because the engine's steps cite nothing by design the answer was refused. Its predecessor `luvn-challenge11-product--agent9` activates narration agent version 9, whose instructions are the corrected ones — the created version's canonical-prompt-sha256 matches the prompt file in this repository, so the hosted agent and the code now impose the same ordering. Its predecessor `luvn-challenge11-product--fix503` carried the HTTP probes and the readiness fix that ended the deployed-only 503 on the briefing path: readiness now probes the data layer while liveness deliberately does not, both probes are HTTP rather than TCP, and the corpus-generation lookup can no longer withhold a briefing it was only meant to enrich. Its predecessor `luvn-challenge11-product--0000095` carried the full-throttle decision model: every permitted record connected to the encounter — of every type and whatever its temporal status — reaches Decision-1, whose verdict now adds or emphasises a structured or historical record instead of being discarded. Measured live, the model is exercised on 14 of 14 sampled encounters, up from 13%. Its predecessor `luvn-challenge11-product--0000094` widened the pool to every permitted type, removed the shortlist cap and stopped a verdict being discarded by record type, and `luvn-challenge11-product--0000093` carried the single-encounter metadata route (ADR-0025 §4) and the refusal of a cursor parameter it does not implement. Its predecessor `luvn-challenge11-product--0000092` carried the loaded corpus itself: the full 2024-2026 cohort is live (14,803 encounters, 11,220 people with an encounter, 401,340 records) and the briefing read now requires an actor and writes one metadata-only audit row. Its predecessor `luvn-challenge11-product--0000090` carried the worklist projection, the lens gate and the chunked loader, and `luvn-challenge11-product--0000088` carried the knowledge-advice rounds, the design-system keyboard fix and the challenge-release corpus tooling, and `luvn-challenge11-product--0000087` before it carried the blood-pressure round. One revision exists between them and never served: `--0000089` was created from an image digest that was not in the registry, was never healthy, and was replaced within minutes (see the plan, §12).** — the engine can see a blood pressure at all for the first time (`systolic_max`/`diastolic_max` from the measurement records, and a `risk-verenpaine-korkea` rule at 140/90 marked `illustrative_threshold`), and the optional actions module is fed the core's matched **risk rule id** — still one of the core's own outputs and still never a record — so five step sets are keyed to what the core actually found. The owner's own 158/92 case now answers with four blood-pressure steps, each carrying `Riski: Verenpaine 140/90 tai yli (havainnollistava raja)` and the table's illustrative status. **One gap is recorded rather than closed:** the reason is traceable to the rule, not yet to the measurement record, because `decide._risk_reasons_by_record` does not yet key this rule. **Rounds, each named beside the revision that carried it beside the revision that carried it (demoted out of the current-state sentence, so no round description is left under a moving revision number):** `luvn-challenge11-product--0000086` carried the **next-steps round** — the assistant answers with the engine's own next steps whenever the situation has them, whatever the question, led by the block and each step carrying its applicability and the actions table's own status (*Havainnollistava sääntö, ei kliinisesti kalibroitu eikä hyväksytty käyttöön*); the turn reports how many sources it cited against the set the digest sealed; an answered turn ends with follow-ups so it does not dead-end; and **`LUVN_ACTIONS_MODULE` is on again** — it had been set to 0 since the Challenge 1.1 round, which is why the live answer to "Mitä toimenpiteitä suosittelet" carried no recommendation at all. Two product oracles reviewed the direction first and both refused model-authored clinical steps until the declared purpose is rewritten in the same change; that remains open.; `luvn-challenge11-product--0000085` carried the **workspace-column rollback** — the record row's columns were restored to the assignment the "Challenge 1.1 unmistakable" round changed: the attention panel, the medications card and the rule-based assessment in the side column, the proposal and the purpose-ordered record cards in the reading column, and the proposal and the assistant in the rail. Measured at 1540×862 before the rollback, the side column held one 155px card beside a 2164px row (93% empty) and the same material stacked in one 492px column made the row 872px taller than the previous UI. The rollback reverses that round's reading order, which is now stated in the code and pinned by test rather than left implicit.; `luvn-challenge11-product--0000084` carried the **assistant-presentation round** — a rule's *proposal* now reads as a proposal in the tag (`Sääntöpohjainen arvio (ehdotus)`, the engine's own label) instead of sharing the finding's tag with the distinction buried in a caption; each finding is labelled in the source's own words (the record's `otsikko`, `lääke`, `tutkimus`, `nimi`, `instrumentti` or measurement, or the engine's own em-dash leading clause) and the statement is rendered without the clause the label duplicates, so the row reads as a label and a sentence rather than a label over a sentence that repeats it; and `BUILD_REVISION` is baked into the bundle so the technical disclosure can say which build the tab is running. The refusal reasoning round 1 published is corrected in the same round: `CHAT_INSTRUCTIONS` rule 3 constrains the generator, not the surface, and the challenge asks for proposals the professional decides.; `luvn-challenge11-product--0000083` carried the **chat-transparency round** — a rule output on the assistant rail now carries the same `Havainnollistava sääntöraja, ei kliinisesti kalibroitu` qualification the briefing has always rendered, and the idempotent-retry path no longer loses it (`statement_rows` wrote `basis` into the stored turn and `turn_from_record` did not restore it); the reply states its own composition and says that a count of displayed statements says nothing about the extent of the record set; the four statement-class tags get a collapsed legend; and down-arrow in the composer completes the field from the encounter's own offered questions. This round also triaged the UpToDate scout item by item — `.scratch/chat-and-suggestions/uptodate-implementation.md` records what shipped, what was already implemented in the surface the scout would have changed, and what was refused with its reason.; `luvn-challenge11-product--0000082` carried the **Challenge 1.1 scope round** — the served corpus drops the two contact records the 1.2 exclusion refused (`demo-p2-visit-1`, `demo-p6-visit-1`, so the flagship contrast is 5 vs 6 and still differs by exactly `demo-p6-3`), the loader's `StorageStreamDownloader.chunks()` crash is fixed, and `LUVN_ACTIONS_MODULE=0` runs the core alone so the generic action strings are gone while source-cited proposals remain.; `luvn-challenge11-product--0000081` carried the **urgency-legend round** — the classifier's own leading urgency level is shown in words beside the distribution it already returns, under a label that names its own limit (*järjestysperuste, ei hoidon kiireellisyys*); the legend is rendered into `docs/decision-protocol.md` from `app.decision_model.URGENCY_LEVELS` and pinned from both the backend and the frontend, and ADR-0023's false claim that Decision-1 does not answer an urgency question is corrected (ADR-0024); this revision also carries the concurrent knowledge-source workstream's runtime changes — guidance/assess/knowledge registry and the risk-tier table; `luvn-challenge11-product--0000080` carried the **core-proposes-nothing round** — `decide` no longer evaluates the actions table and returns no `actions` key; suggested next steps moved to the optional `app.actions` module, which reads two scalars from the core's finished output and defaults on (`LUVN_ACTIONS_MODULE=0` runs the core alone); each risk reason now travels with the record that produced it as `risk_reasons_by_record`, rendered as *Riskihavainnon peruste* on the record; and the chat and guideline surfaces receive the built `Picture` instead of the post-cut permitted set, so they can no longer cite a record the briefing calls irrelevant (ADR-0023); `luvn-challenge11-product--0000079` carried the **greeting round** — the greeting derives a display name from the principal instead of printing the user principal name (it rendered `Hei juhis.jalopeura@ecdx.onmicrosoft.com,`), and the derivation moved into its own module; `luvn-challenge11-product--0000078` carried the **assistant-panel round** — answers became Finnish sentences instead of `key: value` field dumps, the matching Käypä hoito reference appears in the answer, and the rail was rebuilt as an assistant panel with a brand header, tabs, a context chip, a greeting and suggestion cards; `luvn-challenge11-product--0000077` carried the **authorization-hardening round** — the access policy's `version` is validated, the policy-fault `500` no longer discloses the mount path or the policy's own values, and the one-403-body promise is bounded to ids on the route's canonical path; `luvn-challenge11-product--0000076` carried the **review-contract round** — four adversarial findings closed on the production storage and deletion paths (a blob-listing cache that stayed stale after a write, a decision claim written without its record, an ambiguous-name deletion receipt that reported `removed` with the encounter still present, and the legacy flat-name blob ambiguity), per-actor authorization added as opt-in `LUVN_ACCESS_POLICY` / `LUVN_ACCESS_POLICY_FILE` configuration and deliberately left unset on the live deployment, the deployment-topology diagram, and the authenticated live journey verified through the real ingress; `luvn-challenge11-product--0000075` carried the **decisions-and-observability round**; `luvn-challenge11-product--0000074` carried the **data-layer round**; `luvn-challenge11-product--0000073` carried the **chat-generation round**; and `luvn-challenge11-product--0000072` carried the **chat/review-correctness round**; `luvn-challenge11-product--0000069` carried the **hardening round** — the App Insights connection string became a Key Vault reference, the `buildRevision` tag was corrected, four read-only audits' findings were closed, and the Decision-1 protocol was published and pinned. **History, and which source answers it:** the generated current-state block and the older revisions through `--0000052` are in [`.scratch/hackathon-readiness/results/deployment-record.md`](.scratch/hackathon-readiness/results/deployment-record.md) — its hand-written narrative ends at `--0000052`, so it has no entry for `--0000069`, `--0000071`–`--0000076`, or the gap between;  the live revision's commit, image, ACR run and rollback tag are in [`deployment-manifest.md`](.scratch/hackathon-readiness/results/deployment-manifest.md), each recent round is named in `git log`, and [`CAMPAIGN.md`](.scratch/final-touches/CAMPAIGN.md) narrates them. This paragraph is the current state.

## The API

Every date, article, licence and vendor claim this repository repeats — and the list of claims its sources do
**not** support — is in [`docs/references.md`](docs/references.md). Read it before quoting one.

[`docs/api-endpoints.md`](docs/api-endpoints.md) lists every endpoint with what it returns and when it
refuses, and states where the authentication boundary really is — at the Container Apps ingress, not in the
application. It is rendered from the route table and `scripts/check.sh` fails if the two disagree, so it
cannot quietly describe an API that no longer exists.

## Try it

**Hosted demo:** shown to the judges in the presentation; the link is not part of this copy.

A browser request redirects to Entra sign-in — a presenter account is required, and the API answers `401`
without one. All nine bound encounters are synthetic. To run it locally instead, see [Run](#run) below.

## Run

**Easiest — one command starts both** (picks the first free ports and wires the
UI to the backend, so a stale server can't break it):

```bash
./dev.sh          # backend + frontend; prints the URLs to open
./dev.sh --stop   # stop them
```

Or run them separately:

Backend (FastAPI, port 8000). Credentials live in gitignored `backend/.env`
(`LUNA_FOUNDRY_BASE`, `LUNA_FOUNDRY_KEY`, `DECISION_KEY`); without them the
encounter route still returns selected source records, assessments and review
proposals, but no model-generated narration or Decision-1-only additions.

```
cd backend
uv run --env-file .env uvicorn app.main:app --port 8000
```

Frontend (Vite + React, port 5173):

```
cd frontend
npm install
npm run dev
```

### Where the data comes from

Three corpus sources, selected by environment and otherwise identical from the API up:

- **Azure SQL** — set `LUVN_SQL_SERVER` (and `LUVN_SQL_DATABASE`, `LUVN_SQL_CLIENT_ID`). The app reads one
  person's rows per request with its managed identity's Entra token. Load a corpus with
  `python -m app.load_sql <corpus>`.
- **An imported delivery directory** — `backend/scripts/reconcile_delivery.py import <corpus> --out <dir>`,
  then `LUVN_DELIVERY_DIR=<dir>`. For a delivery whose shape is not known ahead of time, start with
  `reconcile_delivery.py land <delivery> --out <dir>` and `profile <dir>` (bytes stored verbatim behind a
  `delivery_run` manifest, then a shape report with encodings, delimiters, candidate vocabularies and
  candidate roles), and add `--on-error=quarantine` to the import so one bad row stages itself instead of
  rolling back the corpus. `reconcile_delivery.py --help` lists every step.
- **The fixture corpus in the image** — the default, `data/experimental/bound-personas`.

Corpora in the delivery's declared shape live under `data/experimental/`: `delivery-demo/` is the one that
covers every capability the curated fixtures demonstrate, and `delivery-sample/` and `delivery-rehearsal/`
are the shape and scale cases. Reconcile any of them with `--dry-run` before importing: it names every
unknown record type and payload key, which is where a vendor mapping gets revised.

## Test

```
cd backend
uv run pytest -q                  # offline; the live Foundry test is skipped
uv run --env-file .env pytest -q  # includes the live Foundry round-trip; fails loudly if Foundry is unreachable
```

```
cd frontend
npm run lint
npm test          # reducer/Picture behaviour + axe-core accessibility gate
npm run build
```

## Contract

The API returns a typed `Picture` (`backend/app/picture.py`) — the one definition
of what the frontend consumes. FastAPI publishes it as OpenAPI at `/docs`, and
`backend/tests/test_contract.py` pins the keys `App.jsx` reads, so a rename fails
in tests instead of rendering `undefined` in the UI.

## Decision-1 purpose-aware selection (synthetic demo)

An encounter resolves the person, professional role and service purpose in code.
`app.policy.cut` limits the visible records **before** Decision-1 sees anything.
`app.selection.baseline` protects essential records; `app.decision_model` first
builds a deterministic shortlist over permitted current free-text notes, then asks
four questions per note in one isolated call: relevance, time-sensitivity,
subject-binding, and a five-level urgency Score (levels 0–4).
The Score distribution is shown in the existing gate-probabilities map and only orders
already-selected priority sources; it never changes inclusion or omission. The shortlist and the assessment cap are both 80; up to 80 candidates
are assessed concurrently within a 30-second stage budget (10 s per call), and a deadline or HTTP failure is
reported per row as `unavailable`. Addition requires
relevance ≥ 0.6 plus confident current/person auxiliary gates; omitting a baseline
note from generation requires relevance ≥ 0.7. The source-wide negation question was
dropped because a note can mix present findings and absences; the prose-inversion risk
remains with the grounding verifier's per-sentence `contradicts` check (ADR-0009). That verifier sees only the
records a sentence cites, which is **this repository's own design decision** — the path performs no
whole-database query or embedding join, so the sentence's citations are the whole evidence it can honestly
consult — and **not a Microsoft requirement**: Microsoft documents one Groundedness evaluator whose `context`
input is the full retrieved set, with no per-sentence mode ([`docs/references.md`](docs/references.md) §1).
Below
threshold, Decision-1 abstains.
Only a valid `relevant` result can add an original source record before Foundry
narration. `irrelevant`, `unclear`, malformed output, a missing key and timeouts
leave the baseline untouched. Decision-1 does not authorize a customer or record,
create a clinical fact, or influence decisions, assessments or proposals.
Never send an entire database to Decision-1; the model sees only permitted candidates
for one resolved encounter. Credentials must be supplied server-side as `DECISION_KEY`
(never in React). Decision-1 relevance calls are optional; without a Decision-1 grounding-verifier pass, generated prose is withheld and the deterministic source-backed briefing remains.

[ADR-0009](docs/adr/0009-decision-purpose-relevance-selection.md) records the owner-approved
pivot from FinBERT to a typed-question classifier, and [ADR-0018](docs/adr/0018-microsoft-decision-1-replaces-the-vendor-classifier.md) records the substitution of Microsoft-Decision-1 for the third-party classifier that first implemented it. The earlier purpose probe — run against the classifier this revision replaces — was a partial, synthetic
set (30 valid responses, 28/30 provisional-label agreement, unattempted cases) —
not clinical validation or a release gate, and its historical files are no longer
kept in the repository. Microsoft's [Decision-1 documentation](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision) and
the Azure retail price feed are not an end-to-end LUVN cost or time-saving measurement. Benchmark the actual
encounter flow before making those claims.

## Notes

- **Sandbox TLS:** behind a MITM proxy, `SSL_CERT_FILE` must point at the CA
  bundle (set in `backend/.env`). Not needed on a normal machine.
- A role that sees **zero** records gets `arvioimaton` / `ei_arvioitavissa`, never
  `matala`, and the grounding checks (`validate_fresh_candidate` +
  `accept_supported_candidate`) drop any narration that cites no permitted,
  selected source — so an empty picture can never render a green badge (ADR-0003).
- Decision/visibility tables are **illustrative synthetic demo policy, not law**
  (see the `_meta` note in `backend/app/tables/`).
- Fixtures are JSON today. A real Veil.AI integration still needs stable
  per-record source-person keys and encounter-owner mapping from the source
  system; the bound demo identifiers are not real provenance. A streaming
  CSV/JSONL importer with a per-person SQLite index exists
  (`backend/app/delivery.py`) against an **assumed** delivery shape; the
  operator-owned `encounters.csv` is capped at 128 mappings. Set
  `LUVN_DELIVERY_DIR` to the imported output to enable lazy indexed lookups; the
  real column names/code systems and 50,000-profile working set are unverified.
- The role-visibility cut lives in one place, `backend/app/policy.py`; it runs
  once per picture and the tables are parsed once per process.
