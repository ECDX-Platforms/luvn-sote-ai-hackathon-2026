#!/usr/bin/env python
"""Host the built SPA and the API on the seeded delivery corpus, for the local end-to-end checks.

This is the shape the deployed revision actually has — one origin serving `/api/*` and the built
frontend — with the corpus coming from a local delivery import instead of Azure SQL, so a browser
session exercises the real pipeline without reaching a database.

    cd backend
    .venv/bin/python scripts/run_local_hosted.py                     # defaults to port 8099
    .venv/bin/python scripts/run_local_hosted.py --port 8123
    .venv/bin/python scripts/run_local_hosted.py --delivery /tmp/luvn-delivery

Build the frontend first (`cd frontend && npm run build`); the script refuses to start without
`frontend/dist/index.html`, because serving an empty directory silently produces the API-only shape and
every 404 looks like an application bug. Foundry credentials are read from `backend/.env` when present,
so narration and Decision-1 run the same way they do in the container; without them the deterministic
briefing still renders and the Picture reports the fail-closed status.

It sets two things that are local-only and must never be carried into a deployment:
`LUVN_DELIVERY_DIR` (this directory) and `LUVN_STATIC_DIR` (the local build).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent


def load_env_file(path: Path) -> None:
    """Load `KEY=VALUE` lines without `set -a`-style shell evaluation of the values.

    `dev.sh` sources `.env` with the shell, which treats a value as code. A `.env` is data; this parses
    it as data. Comments and blank lines are skipped, and an existing environment variable wins.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--delivery", default="/tmp/luvn-delivery")
    parser.add_argument("--dist", default=str(ROOT / "frontend" / "dist"))
    parser.add_argument("--no-models", action="store_true",
                        help="do not read .env: run the deterministic path only")
    args = parser.parse_args()

    dist = Path(args.dist)
    if not (dist / "index.html").is_file():
        print(f"refusing to start: no built frontend at {dist}/index.html\n"
              f"  run `cd frontend && npm run build` first", file=sys.stderr)
        return 1
    if not Path(args.delivery).is_dir():
        print(f"refusing to start: no delivery import at {args.delivery}\n"
              f"  create one with scripts/reconcile_delivery.py import <corpus> --out {args.delivery}",
              file=sys.stderr)
        return 1

    if not args.no_models:
        load_env_file(BACKEND / ".env")
        # A SQL configuration would take precedence over the delivery directory; this script is about the
        # delivery shape, so it removes it rather than letting the two disagree about the corpus.
        for key in ("LUVN_SQL_SERVER", "LUVN_SQL_DATABASE", "LUVN_SQL_CLIENT_ID", "LUVN_SQL_ACCESS_TOKEN"):
            os.environ.pop(key, None)

    os.environ["LUVN_DELIVERY_DIR"] = args.delivery
    os.environ["LUVN_STATIC_DIR"] = str(dist)
    os.environ.setdefault("LUVN_HOSTED", "")

    sys.path.insert(0, str(BACKEND))
    import uvicorn  # noqa: PLC0415

    print(f"serving the API and {dist} on http://{args.host}:{args.port}/")
    print(f"corpus: {args.delivery}"
          f"{' (deterministic only, no .env read)' if args.no_models else ''}")
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
