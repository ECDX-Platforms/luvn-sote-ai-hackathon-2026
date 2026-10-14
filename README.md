> **Confidential — view only, for hackathon evaluation.**
> © 2026 Tieto. All rights reserved. This is not open-source software.
> The SOTE AI Hackathon 2026 judges may view this source code to evaluate the entry, until 15 October 2026.
> Nothing else is permitted: do not copy, clone, fork, download, run or reuse any part of it. See [LICENSE](LICENSE).

# LUVN 1.1 — purpose-aware pre-visit briefing

LUVN assembles a ready pre-visit briefing for a professional. It resolves the person, the professional's role and the service purpose in code; `app.policy.cut` applies the role's access cut **before** any model sees anything; and every generated sentence must cite a record the professional was already permitted to see, or the sentence is withheld. A deterministic core holds authority — the model layers advise, and the briefing is complete without them.

Start with [`ARCHITECTURE.md`](ARCHITECTURE.md) — the architecture as built, explained with diagrams and in the order the system actually works. Current implementation and measured limits in full: [`docs/architecture-as-built.md`](docs/architecture-as-built.md). The decision model's typed protocol, as a pinned value: [`docs/decision-protocol.md`](docs/decision-protocol.md). The domain glossary is [`CONTEXT.md`](CONTEXT.md).

## The API

Every date, article, licence and vendor claim this repository repeats — and the list of claims its sources do
**not** support — is in [`docs/references.md`](docs/references.md). Read it before quoting one.

[`docs/api-endpoints.md`](docs/api-endpoints.md) lists every endpoint with what it returns and when it
refuses, and states where the authentication boundary really is — at the Container Apps ingress, not in the
application. It is rendered from the route table and `scripts/check.sh` fails if the two disagree, so it
cannot quietly describe an API that no longer exists.

## Try it

**Hosted demo:** <https://luvn-challenge11-product.kindplant-ccd82e27.swedencentral.azurecontainerapps.io>

A browser request redirects to Entra sign-in, so the demo is shown from a presenter account; the API answers `401` without one.

All nine bound encounters are synthetic. To run it locally instead, see [Run](#run) below.

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
remains with the grounding verifier's per-sentence `contradicts` check (ADR-0018). That verifier sees only the
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

[ADR-0018](docs/adr/0018-microsoft-decision-1-replaces-the-vendor-classifier.md) records the substitution of
Microsoft-Decision-1 for the third-party classifier that first implemented the selection contract. The earlier purpose probe — run against the classifier this revision replaces — was a partial, synthetic
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
