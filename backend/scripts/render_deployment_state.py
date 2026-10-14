#!/usr/bin/env python
"""Render the current-deployment-state table in the deployment record from a captured az readback.

The failure this removes: the record named revision `--0000013` while `--0000014` was live, because
the revision number was typed from memory instead of read back. Everything in the generated table
below comes from a single artifact, and nothing in it is retyped:

  .scratch/hackathon-readiness/results/deployment-state.json   (the read-only az capture)

The capture is the only step allowed to touch the network. This renderer reads the artifact and runs
`git rev-parse HEAD`; it never calls az.

`--check` exits non-zero when the committed table disagrees with a fresh render, when the image string
does not embed the recorded digest, and when the artifact's `source_commit` is not an ancestor of the
current `git rev-parse HEAD`.

It deliberately does NOT require `source_commit == HEAD`. A deployment record is history: the commit
named there is the one baked into the running image, and committing this record necessarily advances
HEAD. Requiring equality would mean **no committed state could ever satisfy the check** — the capture
would invalidate itself the moment its own commit landed, and every later commit would be blocked.
Ancestry still catches a capture from a rewritten or foreign history. Whether the app is running
something *newer* than recorded cannot be answered without az, so the table states when it was captured
and freshness is a re-capture, not a guess.

    .venv/bin/python scripts/render_deployment_state.py           # write
    .venv/bin/python scripts/render_deployment_state.py --check   # verify (CI / pre-commit)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / ".scratch" / "hackathon-readiness" / "results" / "deployment-state.json"
TARGET = ROOT / ".scratch" / "hackathon-readiness" / "results" / "deployment-record.md"

BEGIN = "<!-- generated:deployment-state:begin -->"
END = "<!-- generated:deployment-state:end -->"

REQUIRED = (
    "revision", "image", "image_digest", "provisioning_state", "health_state", "running_state",
    "traffic_weight", "revision_created_at", "source_commit", "captured_at",
    "foundry_model", "foundry_agent_name", "foundry_agent_version", "min_replicas", "max_replicas",
)


class RenderError(RuntimeError):
    """A source is missing or malformed; the record must not be guessed."""


def _load() -> dict:
    if not ARTIFACT.is_file():
        raise RenderError(
            f"missing artifact {ARTIFACT.relative_to(ROOT)}; run the read-only az capture first")
    try:
        doc = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RenderError(
            f"cannot read {ARTIFACT.relative_to(ROOT)}: {type(error).__name__}") from error
    missing = [key for key in REQUIRED if doc.get(key) in (None, "")]
    if missing:
        raise RenderError(f"{ARTIFACT.relative_to(ROOT)} is missing fields: {', '.join(missing)}")
    return doc


def _head_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RenderError("git rev-parse HEAD failed; cannot verify the captured source commit") from error
    return result.stdout.strip()


def _deployed_commit_is_known(commit: str, head: str) -> bool:
    """True when the recorded deployment commit is an ancestor of HEAD.

    Ancestry, not equality: the record is history, so committing it must not invalidate it. A commit
    that is missing from this repository (shallow clone, foreign capture, rewritten history) returns
    128 rather than 1 and is reported as an error, never silently accepted.
    """
    result = subprocess.run(["git", "merge-base", "--is-ancestor", commit, head],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise RenderError(f"git merge-base could not resolve {commit!r}; is the deployment commit in this "
                      f"repository's history?")


def render(doc: dict) -> str:
    rows = [
        ("Revision", f"`{doc['revision']}`"),
        ("Image", f"`{doc['image']}`"),
        ("Image digest", f"`{doc['image_digest']}`"),
        ("Provisioning / health", f"`{doc['provisioning_state']}` / `{doc['health_state']}`"),
        ("Running state", f"`{doc['running_state']}`"),
        ("Traffic weight", f"`{doc['traffic_weight']}`%"),
        ("Minimum / maximum replicas", f"`{doc['min_replicas']}` / `{doc['max_replicas']}`"),
        ("Revision created", f"`{doc['revision_created_at']}`"),
        ("Deployed source commit", f"`{doc['source_commit']}`"),
        ("Captured at (UTC; re-capture to confirm freshness)", f"`{doc['captured_at']}`"),
        ("Foundry model", f"`{doc['foundry_model']}`"),
        ("Foundry agent name", f"`{doc['foundry_agent_name']}`"),
        ("Foundry agent version", f"`{doc['foundry_agent_version']}`"),
    ]
    lines = ["| Field | Value |", "| --- | --- |"]
    lines += [f"| {label} | {value} |" for label, value in rows]
    return "\n".join(lines) + "\n"


def _extract(text: str) -> str:
    if BEGIN not in text or END not in text:
        raise RenderError(f"the generated markers {BEGIN!r} / {END!r} are missing from the record")
    inner = text.split(BEGIN, 1)[1].split(END, 1)[0]
    return inner.lstrip("\n").rstrip("\n") + "\n"


def _write(text: str, rendered: str) -> str:
    block = f"{BEGIN}\n{rendered}{END}"
    if BEGIN in text and END in text:
        before = text.split(BEGIN, 1)[0]
        after = text.split(END, 1)[1]
        return f"{before}{block}{after}"
    if text and not text.endswith("\n"):
        text += "\n"
    return f"{text}{block if not text else '\n' + block}\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if the committed table or captured commit is stale")
    args = parser.parse_args()
    try:
        doc = _load()
        rendered = render(doc)
        if args.check:
            head = _head_commit()
            if not _deployed_commit_is_known(doc["source_commit"], head):
                print(f"{ARTIFACT.relative_to(ROOT)} names deployment commit {doc['source_commit']}, "
                      f"which is not an ancestor of HEAD ({head}): re-run the az capture", file=sys.stderr)
                raise SystemExit(1)
            if not doc["image"].endswith(doc["image_digest"]):
                print(f"{ARTIFACT.relative_to(ROOT)}'s image string does not embed its image_digest, "
                      f"so the capture is internally inconsistent", file=sys.stderr)
                raise SystemExit(1)
            if not TARGET.is_file():
                print(f"{TARGET.relative_to(ROOT)} is missing", file=sys.stderr)
                raise SystemExit(1)
            current = _extract(TARGET.read_text(encoding="utf-8"))
            if current != rendered:
                print(f"{TARGET.relative_to(ROOT)} is stale: re-run "
                      f"`cd backend && .venv/bin/python scripts/render_deployment_state.py`",
                      file=sys.stderr)
                raise SystemExit(1)
            print(f"{TARGET.relative_to(ROOT)} matches its captured deployment state")
            return
        text = TARGET.read_text(encoding="utf-8") if TARGET.is_file() else ""
        TARGET.write_text(_write(text, rendered), encoding="utf-8")
        print(f"wrote {TARGET.relative_to(ROOT)} ({doc['revision']})")
    except RenderError as error:
        print(f"render_deployment_state: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
