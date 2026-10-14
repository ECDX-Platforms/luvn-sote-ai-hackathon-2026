#!/usr/bin/env bash
# The repository gates, in one place. The pre-commit hook runs this exact script, so a green local
# run and a green commit mean the same thing.
#
#   scripts/check.sh          fast: tests, derived documents, whitespace
#   scripts/check.sh --full   adds the frontend gate (test, lint, build)
#
# Setup, once per clone: git config core.hooksPath scripts/hooks
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
cd "$root"

python="$root/backend/.venv/bin/python"
if [[ ! -x "$python" ]]; then
  echo "check: backend/.venv/bin/python is missing; create the venv first" >&2
  exit 1
fi

echo "== backend tests =="
(cd backend && .venv/bin/pytest -q)

# Fails when a document no longer matches the artifacts it is rendered from. Numbers cannot drift
# here, and the window check refuses a trial artifact produced under a different window than the
# code declares.
echo "== derived documents match their sources =="
(cd backend && .venv/bin/python scripts/render_evidence.py --check)

# The endpoint list is rendered from the application's own route table, and the renderer refuses to run
# while the two disagree — so an added, renamed or removed route cannot leave the document describing an
# API that is not there, and a documented route that answers nothing cannot survive either.
(cd backend && .venv/bin/python scripts/render_api_endpoints.py --check)

# The architecture poster is rendered from the declarative diagram in the renderer, so the picture cannot
# drift from its source: a changed node, edge, label or role fails here until the SVG is regenerated. The
# rasteriser is deliberately not part of this — the gate verifies the SVG, which is what carries the
# content, and headless Chrome stays out of the gate.
echo "== architecture poster matches its declarative diagram =="
(cd backend && .venv/bin/python scripts/render_architecture_poster.py --check)

# The poster's illegibility guards, which a content comparison cannot cover. Two defects shipped in this
# picture and survived visual passes at reduced zoom: three edge labels printed on top of one another, and
# an edge drawn straight through a group heading. This re-introduces the heading crossing deliberately and
# asserts the render refuses, then hands the collision detector the exact geometry the shipped defect
# produced and asserts it fires. It also reads the PNG back, so a screenshot of an unloaded SVG cannot be
# accepted as the deliverable. Offline: it renders in-process and decodes the PNG with the stdlib.
echo "== architecture poster guards fire on planted faults =="
(cd backend && .venv/bin/python scripts/verify_architecture_poster.py)

# The poster draws the estate with the official Microsoft Azure icons, which are vendored under
# vendor/azure-icons/ and pinned by hash in PROVENANCE.md. This re-hashes every one of them and refuses a
# modified, missing or extra file, so the picture cannot quietly stop depicting the real artwork. Offline:
# it reads the working tree only.
echo "== vendored official Azure icons match their pin =="
(cd backend && .venv/bin/python scripts/verify_azure_icons.py --check)

# The deployment record's current-state table is rendered from the az readback, and the capture must
# name the checked-out commit. This never calls az; only the capture does.
echo "== deployment record matches the captured deployment state =="
(cd backend && .venv/bin/python scripts/render_deployment_state.py --check)

# The facts a human retypes into prose: which revision is current, how many product encounters exist,
# and that decision records carry no deployment state. Only current-state claims are checked, so the
# deliberately historical sections do not trip it.
echo "== document current-state claims match the code and the capture =="
(cd backend && .venv/bin/python scripts/check_doc_claims.py)

# The retrieval-and-knowledge spike artifacts are verified by the same scripts that produced them, so a
# finding cannot drift from its evidence and a stale artifact cannot ship quietly. All four are offline:
# none of them calls Azure, Decision-1, or the network.
echo "== guideline links: recorded metadata is complete =="
(cd backend && .venv/bin/python scripts/verify_guideline_links.py --offline)

echo "== spike artifacts match their scripts =="
for script in run_decision_eval fetch_knowledge run_search_spike run_pathway_demo run_decision_trace \
              run_decision_compare run_verifier_compare run_narration_compare \
              run_threshold_sweep run_oracle_review run_instability_study \
              run_verifier_adjudication run_verifier_language_ablation run_search_cap_spike \
              run_chat_evidence run_verifier_adversarial run_narration_agent_v8_eval run_prefill_eval; do
  (cd backend && .venv/bin/python "scripts/$script.py" --check)
done

echo "== whitespace =="
# The vendored library is hash-pinned and copied byte for byte (ADR-0026), so its whitespace is upstream's.
git diff --check -- . ":(exclude)frontend/vendor/comp-lib"
git diff --cached --check -- . ":(exclude)frontend/vendor/comp-lib"

if [[ "${1:-}" == "--full" ]]; then
  echo "== frontend tests (includes the accessibility gate) =="
  (cd frontend && npm test)
  echo "== frontend lint and build =="
  (cd frontend && npm run lint && npm run build)
fi

echo "check: ok"
