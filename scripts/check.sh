#!/usr/bin/env bash
# The repository gates, in one place.
#
#   scripts/check.sh          backend tests, derived documents, guideline metadata, whitespace
#   scripts/check.sh --full   adds the frontend gate (test, lint, build)
#
# Setup, once per clone:
#   cd backend && uv sync
#   cd ../frontend && npm ci
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
cd "$root"

python="$root/backend/.venv/bin/python"
if [[ ! -x "$python" ]]; then
  echo "check: backend/.venv/bin/python is missing; run 'cd backend && uv sync' first" >&2
  exit 1
fi

echo "== backend tests =="
(cd backend && .venv/bin/pytest -q)

# The endpoint list is rendered from the application's own route table, and the renderer refuses to run
# while the two disagree — so an added, renamed or removed route cannot leave the document describing an
# API that is not there, and a documented route that answers nothing cannot survive either.
echo "== API endpoint document matches the route table =="
(cd backend && .venv/bin/python scripts/render_api_endpoints.py --check)

# The decision protocol document is rendered from the decision model's own declarations, so the published
# protocol cannot drift from the code that implements it.
echo "== decision protocol document matches the code =="
(cd backend && .venv/bin/python scripts/render_decision_protocol.py --check)

# Every guideline the application can cite must carry its title, publisher, publication date and link, and
# the recorded publisher dates must still match the code. Offline: it reads the working tree only.
echo "== guideline metadata is complete and matches the code =="
(cd backend && .venv/bin/python scripts/verify_guideline_links.py --offline)

echo "== whitespace =="
# The vendored library is hash-pinned and copied byte for byte, so its whitespace is upstream's.
git diff --check -- . ":(exclude)frontend/vendor/comp-lib"
git diff --cached --check -- . ":(exclude)frontend/vendor/comp-lib"

if [[ "${1:-}" == "--full" ]]; then
  echo "== frontend tests (includes the design-system and accessibility gates) =="
  (cd frontend && npm test)
  echo "== frontend lint and build =="
  (cd frontend && npm run lint && npm run build)
fi

echo "check: ok"
