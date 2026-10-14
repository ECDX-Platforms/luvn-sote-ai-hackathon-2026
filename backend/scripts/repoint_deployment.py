"""Point the current-state documents at what is actually deployed.

This exists because the same job done by hand has broken a deploy three times running:

1. a revision was dropped from the chain, because each round inserted the new one without demoting the old;
2. a digest and a commit went stale in two documents for four deploys, because the hand sweep only replaced
   the revision string and nothing read those values back;
3. an over-broad sweep rewrote twenty-three documents including historical issue files that legitimately
   record the digests of the deploys they describe -- destroying the record of what happened when, which is
   the same mistake as overwriting the canonical oracle artifact.

So the rules are explicit here rather than remembered:

* The **capture** is the only source of truth. Every value written comes from it, never from a literal in this
  file. A revision string, a digest or a commit found in a listed document is replaced with what the capture
  says is live.
* The document list is an **allow-list of current-state documents**. The repository's history and its issue
  files are not in it and cannot be reached from here, so no future edit can repeat failure 3.
* Running it twice changes nothing the second time, which is what makes it safe to run after every deploy.

The one thing it cannot write is the prose that describes what a revision carries; that stays the author's.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The claim patterns live in the gate, so this script and the gate agree on what "the current-state claim"
# is. Duplicating them here is how they drift apart.
# pi-lens-ignore: reportMissingImports
from check_doc_claims import REVISION_CLAIMS  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CAPTURE = ROOT / ".scratch/hackathon-readiness/results/deployment-state.json"

# Current-state documents only. History and issue files are deliberately absent: they record what was true
# when they were written, and rewriting them would make the repository lie about its own past.
DOCUMENTS = (
    "README.md",
    "HANDOFF.md",
    "docs/hosting-runbook.md",
    "docs/architecture-as-built.md",
    ".scratch/hackathon-readiness/results/deployment-manifest.md",
)

# Revision strings are deliberately NOT rewritten here. The documents describe a *chain* -- each revision
# naming the one before it -- so a blanket replacement flattens every link to the current revision, which is
# the very failure this script was written to end. Inserting this round's revision and demoting the previous
# one is prose, and stays the author's job.
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT = re.compile(r"\b[0-9a-f]{40}\b")

# Digests and commits are rewritten ONLY inside the paragraph that carries the current-state revision claim.
#
# Rewriting them document-wide was a defect, and a quiet one: the same documents record the digest and commit
# of every *earlier* revision too -- "Previous revision `--0000027` was built from commit ... (image ...)" --
# and a global substitution rewrote those into the current pair. The history then read as though every past
# round had been built from the newest commit, which is the opposite of a record. The window here is the same
# one the gate measures (400 characters from the claim) with room for a long claim sentence, and it stops at
# the first blank line or heading so it cannot walk into the next paragraph.
CLAIM_WINDOW = 2000


def _first(document: object, keys: set[str]) -> str | None:
    """The first string under any of `keys`, wherever the capture nests it."""
    if isinstance(document, dict):
        for key, value in document.items():
            if key in keys and isinstance(value, str):
                return value
            found = _first(value, keys)
            if found:
                return found
    elif isinstance(document, list):
        for value in document:
            found = _first(value, keys)
            if found:
                return found
    return None


def read_capture(path: Path = CAPTURE) -> dict[str, str]:
    if not path.exists():
        raise SystemExit(f"no capture at {path}: run capture_deployment_state.py first")
    document = json.loads(path.read_text(encoding="utf-8"))
    values = {
        "revision": _first(document, {"revision", "latest_revision", "name"}),
        "digest": _first(document, {"image_digest", "digest"}),
        "commit": _first(document, {"source_commit", "commit", "build_revision"}),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise SystemExit(f"the capture does not name {', '.join(missing)}; refusing to write a partial point")
    # The digest must be the form the documents use, or the replacement would write something the doc-claims
    # check reads differently from what it expects.
    if not values["digest"].startswith("sha256:"):
        values["digest"] = f"sha256:{values['digest']}"
    return values


def _current_state_window(text: str) -> tuple[int, int] | None:
    """The span of the paragraph carrying the current-state revision claim, or None."""
    for name, pattern in REVISION_CLAIMS:
        if name != CURRENT_DOCUMENT:
            continue
        match = re.search(pattern, text)
        if match is None:
            return None
        start = match.start()
        end = min(len(text), start + CLAIM_WINDOW)
        for stop in ("\n\n", "\n#"):
            at = text.find(stop, match.end())
            if at != -1:
                end = min(end, at)
        return start, end
    return None


def repoint_document(text: str, values: dict[str, str], document: str) -> str:
    """Rewrite the digests and commits in `document`'s current-state paragraph, and nowhere else."""
    global CURRENT_DOCUMENT
    CURRENT_DOCUMENT = document
    window = _current_state_window(text)
    if window is None:
        return text
    start, end = window
    region = text[start:end]
    region = DIGEST.sub(values["digest"], region)
    region = COMMIT.sub(values["commit"], region)
    healed = text[:start] + region + text[end:]

    # The window above rewrites the paragraph matching the FIRST current-state claim, and that was enough
    # while only one paragraph carried the claim. Then a document grew a second one -- a summary bullet that
    # also names the current revision -- and the window walked straight past it: the bullet's revision number
    # had already been advanced, so it kept the PREVIOUS revision's commit and digest and the document
    # contradicted itself (captured, not hypothetical: `docs/architecture-as-built.md` claimed `--0000074`
    # with `--0000073`'s commit for one deploy, and nothing failed). Repair every other paragraph that names
    # the CURRENT revision and still carries a commit or a digest. Paragraphs naming older revisions do not
    # match, so their recorded history stays intact -- the reason the window exists in the first place.
    revision = values.get("revision") or ""
    if revision:
        for match in list(re.finditer(re.escape(f"`{revision}`"), healed)):
            claim_end = min(len(healed), match.end() + CLAIM_WINDOW)
            for stop in ("\n\n", "\n#"):
                at = healed.find(stop, match.end())
                if at != -1:
                    claim_end = min(claim_end, at)
            claim_start = max(0, match.start() - 80)
            if claim_start <= start < claim_end:
                continue  # the window above already rewrote this one
            region = healed[claim_start:claim_end]
            rewritten = COMMIT.sub(values["commit"], DIGEST.sub(values["digest"], region))
            healed = healed[:claim_start] + rewritten + healed[claim_end:]
    return healed


CURRENT_DOCUMENT = ""


def repoint(values: dict[str, str], *, dry_run: bool = False) -> list[str]:
    changed: list[str] = []
    for name in DOCUMENTS:
        path = ROOT / name
        if not path.exists():
            raise SystemExit(f"listed document is missing: {name}")
        before = path.read_text(encoding="utf-8")
        after = repoint_document(before, values, name)
        if after == before:
            continue
        changed.append(name)
        if not dry_run:
            path.write_text(after, encoding="utf-8")
    return changed


def main() -> int:
    values = read_capture()
    changed = repoint(values, dry_run="--dry-run" in sys.argv)
    print(f"capture: revision={values['revision']} digest={values['digest'][:23]}... commit={values['commit'][:7]}")
    if changed:
        verb = "would change" if "--dry-run" in sys.argv else "updated"
        for name in changed:
            print(f"  {verb} {name}")
    else:
        print("  every listed document already matches the capture")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
