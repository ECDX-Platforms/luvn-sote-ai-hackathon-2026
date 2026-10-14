#!/usr/bin/env python
"""Check the hand-maintained documents' CURRENT-STATE claims against the code and the capture.

The failure this removes: revision `--0000017` sat in ADR-0009's status line two deploys after it
stopped being true. Derived documents are already guarded (`render_evidence.py`,
`render_deployment_state.py`); this covers the facts that a human retypes into prose.

It deliberately checks only CURRENT-STATE claims. Historical prose — HANDOFF's superseded snapshot,
the deployment record's dated deploys — is supposed to name old revisions, so scanning every
occurrence would be a false-positive machine. Each claim is extracted by the phrase that introduces
it, then compared with the single source of truth:

  * the live revision, from `.scratch/hackathon-readiness/results/deployment-state.json`
  * optional `Record` fields and the full `RecordView` field surface, documented across
    `docs/architecture-as-built.md` and `CONTEXT.md` (also tested with a synthetic missing field)

    .venv/bin/python scripts/check_doc_claims.py     # exit non-zero on any drift
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.picture import RecordView  # noqa: E402
from app.provenance import MARKING_VERSION  # noqa: E402
from app import provenance  # noqa: E402
from app.upm import Record  # noqa: E402

RESULTS = ROOT / ".scratch" / "hackathon-readiness" / "results"
STATE = RESULTS / "deployment-state.json"
MANIFEST = RESULTS / "evidence-manifest.json"


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read {path.relative_to(ROOT)}: {type(error).__name__}") from error


def _artifact(kind: str) -> dict:
    """The parsed artifact of `kind`, resolved from the manifest instead of a filename here."""
    document = _load_json(MANIFEST)
    for entry in document.get("artifacts") or []:
        if entry.get("kind") == kind:
            return _load_json(ROOT / entry["path"])
    raise SystemExit(f"{MANIFEST.relative_to(ROOT)} lists no artifact of kind {kind!r}")

# (document, the phrase that introduces its current-state revision claim)
REVISION_CLAIMS = (
    ("docs/architecture-as-built.md", r"currently deployed revision is `([^`]+)`"),
    ("docs/hosting-runbook.md", r"Current revision is `([^`]+)`"),
    ("README.md", r"Deployed revision `([^`]+)`"),
    ("HANDOFF.md", r"Current live state: revision `([^`]+)`"),
)
# A decision record must not carry deployment state at all: it goes stale on every deploy.
STATE_FREE = ("docs/adr/0009-decision-purpose-relevance-selection.md",)
# The selection contract is owned by ADR-0009 and by `app.selection.RULE_VERSION`. Another ADR naming a
# version goes stale on the next contract change — ADR-0010 said "v5" while v7 was live.
CONTRACT_FREE = ("docs/adr/0010-bound-source-identity-and-basics.md",
                 "docs/adr/0011-ai-architecture-layers.md")
# Numeric F2 results live in the generated evidence summary, derived from the artifact. Restating them
# in hand-written prose is what let the as-built claim "2/2" while the artifact said 3/3. The patterns
# require a FIGURE, so prose that merely says "additions are reported with a denominator" is fine.
F2_FIGURES = (
    ("docs/architecture-as-built.md", r"additions are\s*\*{0,2}\d|precision = 1\.0|\b\d/\d precision\b"),
    ("HANDOFF.md", r"additions are\s*\*{0,2}\d|precision = 1\.0"),
)
# The final-pass results are restated in the pitch narrative rather than banned from it the way
# F2_FIGURES bans the scorecard's own numbers. A checked restatement is stronger than a missing one:
# each figure below is compared with the artifact that produced it, so regenerating either artifact
# with different numbers fails this gate instead of leaving the pitch quietly wrong.
ABLATION_CLAIM = ("docs/decision-hackathon-fit.md", r"wrong omissions \*\*(\d+) → (\d+)\*\*")
DISAGREEMENT_CLAIM = ("docs/decision-hackathon-fit.md",
                      r"absent (\d+), not-comparable (\d+) — so \*\*(\d+)/(\d+)\*\*")
# Documents with an explicitly superseded section: everything from the marker onward may legitimately
# quote figures from that superseded snapshot, so only the text before it is current state.
HISTORICAL_MARKER = {"HANDOFF.md": "## Historical"}
# The AI marking's version and header names are copied into ADR-0017 from `app.provenance`. A copy of a
# code constant in prose is exactly what this whole checker exists to catch: bump `MARKING_VERSION` and
# the record would still say the old one, with every gate green. One claim per introducing phrase.
MARKING_CLAIMS = (
    ("docs/adr/0017-machine-readable-ai-marking.md", r"`MARKING_VERSION` is \*\*`([^`]+)`\*\*"),
    ("docs/adr/0017-machine-readable-ai-marking.md", r"\| `AI-Generated-Marking` \| `([^`]+)` \|"),
)
# Every header the marking emits must be named in its own record, so a renamed header cannot lose its
# documentation silently.
MARKING_HEADER_DOC = "docs/adr/0017-machine-readable-ai-marking.md"
# The arrival runbook is the only document an operator reads before touching the real delivery, and the one
# thing it must never lose is the distinction between "the importer is verified" and "this data may be sent
# to a model vendor". A boundary that lives only in prose is removed by the next tidy-up, so it is checked
# here rather than trusted: each phrase is the load-bearing half of one condition, and losing a condition
# would otherwise look like a shorter runbook.
ARRIVAL_RUNBOOK = ".scratch/challenge-1-1-alignment/arrival-runbook.md"
ARRIVAL_BOUNDARY = (
    ("the importer/egress distinction", "Verifying this importer is NOT verifying model egress"),
    ("the Art. 6 lawful basis", "Art. 6"),
    ("the Art. 9 condition for health data", "Art. 9"),
    ("the Art. 28 processor terms", "Art. 28"),
    ("the Art. 35 DPIA", "Art. 35 DPIA"),
    ("the instruction not to release anything to a vendor yet",
     "do not open a Picture for an imported encounter with narration or Decision-1 enabled"),
)

SURFACE_DOCUMENTS = ("docs/architecture-as-built.md", "CONTEXT.md", "docs/decision-protocol.md")


def _current_region(relative: str, text: str) -> str:
    marker = HISTORICAL_MARKER.get(relative)
    return text if marker is None else text.split(marker, 1)[0]


def _record_surface_fields() -> set[str]:
    """The model-owned, non-required Record fields plus the public RecordView contract."""
    optional_record_fields = {name for name, field in Record.model_fields.items() if not field.is_required()}
    return optional_record_fields | set(RecordView.model_fields)


def _assert_surface_coverage(fields: set[str], documentation: str) -> int:
    missing = sorted(name for name in fields if f"`{name}`" not in documentation)
    if missing:
        raise ValueError(f"Record/RecordView fields are undocumented: {', '.join(missing)}")
    return len(fields)


# A relative markdown link is resolved against the file that contains it, so the number of `../` segments is
# exactly the thing a document gets wrong when it moves. `docs/architecture-as-built.md` carried
# `../../.scratch/...` (one `..` too many, which resolves outside the repository) on two lines while a third
# line in the same file had it right, and nothing read the links. Only the surface documents are walked:
# `vendor/` holds a third-party template whose example rows are placeholders, not links.
#
# `docs/decision-protocol.md` is generated and was added here for the same reason: its renderer emitted
# `../../docs/adr/0024-...` (one `..` too many, so the link resolved outside the repository) while the two
# links beside it in the same renderer were already `adr/...`, and no check read a generated document.
LINK_IN_MARKDOWN = re.compile(r"(?<!!)\[[^\]]*\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
LINK_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
CODE_FENCE = re.compile(r"^\s*(```|~~~)")


def _markdown_links(text: str) -> list[tuple[int, str]]:
    """Every relative link target with its line number, skipping fenced blocks and inline code spans.

    A link written inside a fence or a code span is an example, not a reference — this very file's docstring
    carries link-shaped text — so both are blanked before the scan rather than reported.
    """
    found: list[tuple[int, str]] = []
    in_fence = False
    for number, line in enumerate(text.splitlines(), 1):
        if CODE_FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for target in LINK_IN_MARKDOWN.findall(re.sub(r"`[^`]*`", "", line)):
            if target.startswith(("#", "/")) or LINK_SCHEME.match(target):
                continue
            found.append((number, target))
    return found


def _assert_relative_links_resolve() -> tuple[list[str], int]:
    """Every relative link in a surface document must point at a path that exists.

    Returns the problems and how many links were tested, so the summary can show this check was not vacuous.
    """
    problems: list[str] = []
    tested = 0
    for relative in SURFACE_DOCUMENTS:
        path = ROOT / relative
        for number, target in _markdown_links(path.read_text(encoding="utf-8")):
            tested += 1
            clean = target.split("#", 1)[0].replace("%20", " ").strip("<>")
            if clean and not (path.parent / clean).resolve().exists():
                problems.append(f"{relative}:{number}: link [{target}] does not resolve to an existing path")
    return problems, tested


# Only these documents state which revision is current. Anywhere else, a revision claim is drift in
# waiting: an adversarial pass planted one in decision-hackathon-fit.md and every check still passed.
REVISION_OWNERS = {relative for relative, _ in REVISION_CLAIMS} | {
    ".scratch/hackathon-readiness/results/deployment-record.md",  # chronological evidence, names revisions in context
}
REVISION_CLAIM = re.compile(r"revision is `[^`]+`")
# `REVISION_CLAIM` and the four owner patterns are phrase-exact, so a present-tense claim phrased
# differently slipped through all of them: `docs/architecture-as-built.md` carried
# "**Current deployed revision `luvn-challenge11-product--0000038`**" for several revisions after that
# revision stopped being current, and nothing matched it. A present-tense claim is a revision name
# qualified by "current"/"currently" just before it, whatever the rest of the sentence says.
REVISION_NAME = re.compile(r"luvn-challenge11-product--\d{7}")
CURRENTLY_QUALIFIED = re.compile(r"current(?:ly)?[^.;:\n]{0,25}$", re.IGNORECASE)
# Dated work records under `.scratch/` legitimately say what was current *when they were written* — three
# do today — so the boundary is the documents that make present-tense claims about the system: `docs/` and
# the root files. That is a category, not an allowlist, and it is the same boundary `REVISION_OWNERS`
# draws in the other direction.
PRESENT_TENSE_EXCLUDED = (".scratch/",)
# The owner patterns capture the revision NAME only, so the digest and the source commit beside it were
# unguarded — and that is exactly how four documents came to name one revision with another revision's image
# digest after a re-point moved the name and the build-run id but not the digest. A claim sentence states
# three facts; all three are read from the capture and compared. The window starts at the claim and is short,
# so a digest mentioned later on the page as history is not caught up in it: the as-built carries eight more.
CLAIM_WINDOW = 400
DIGEST_IN_PROSE = re.compile(r"sha256:[0-9a-f]{64}")
# ANY backticked hash in the window is a commit, whatever the words in front of it: this file has carried
# "source commit `…`" and "built from commit `…`", and a 7-character hash is what went stale.
COMMIT_IN_PROSE = re.compile(r"`([0-9a-f]{7,40})`")


def _present_tense_documents() -> list[str]:
    """Tracked markdown that speaks about the system in the present tense."""
    listed = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True, check=True)
    return sorted(path for path in listed.stdout.split()
                  if not path.startswith(PRESENT_TENSE_EXCLUDED))


REVISION_MANIFEST = ".scratch/hackathon-readiness/results/deployment-manifest.md"
# "the preceding/previous revision `X`" — the precedence claim, as opposed to the currency claim above.
# `preceding` and `previous` assert ADJACENCY and so are checkable against the nearest named revision.
# `superseded` deliberately is not here: "the superseded revision X" only says a later revision exists,
# which is true of every revision but one, and it names no relation to the passage around it.
# How far back a precedence claim's antecedent may sit. Long enough for a sentence that names the
# revision and then comments on it, short enough not to reach into another paragraph's subject.
PRECEDENCE_WINDOW = 240
# For finding the ANTECEDENT, both spellings count: prose names the full form once and then says `--0000046`
# for the rest of the passage, and a claim's antecedent is whichever of them the reader last saw.
ANY_REVISION_NAME = re.compile(r"(?:luvn-challenge11-product)?--\d{7}")
PRECEDENCE_CLAIM = re.compile(
    r"(?:preceding|previous)\s+(?:revision\s+)?`(?:luvn-challenge11-product)?--(\d{7})`",
    re.IGNORECASE)


def _revision_number(revision: str) -> int | None:
    found = re.search(r"--(\d{7})\s*$", revision)
    return int(found.group(1)) if found else None


def _assert_deployed_posture() -> list[str]:
    """The deployment's security-shaped configuration, asserted rather than merely captured.

    Capturing a fact the gate never reads is decoration. These are the properties that would embarrass the
    project if they silently changed: platform authentication on every route, the removed classifier's key
    absent, and no per-role provider override re-introduced.
    """
    problems: list[str] = []
    state = json.loads(STATE.read_text(encoding="utf-8"))
    configuration = state.get("configuration")
    if not isinstance(configuration, dict):
        return ["the capture records no configuration block, so the deployed posture is unverified"]
    authentication = configuration.get("authentication") or {}
    if authentication.get("platform_enabled") is not True:
        problems.append("platform authentication is not enabled on the live revision")
    if not authentication.get("allowed_principal_count"):
        problems.append("no presenter principals are allowed, so either the policy or the capture is wrong")
    if configuration.get("classifier_key_present"):
        problems.append("the removed classifier's key is present on the live revision")
    if configuration.get("verifier_provider"):
        problems.append("a per-role verifier provider is configured again")
    deployments = configuration.get("foundry_deployments")
    # `Decision-1` and `gpt-6.1-sol` are the product's two model layers. `claude-fable-5-1` is not a product
    # route at all: it is the Anthropic-format oracle reviewer the owner deployed so the architecture decisions
    # could be argued against a second vendor's model -- ADR-0021 and ADR-0024 both record their acceptance as
    # resting on a `claude-fable-5.1` oracle review. It is listed here rather than removed because the check's
    # job is to make an addition visible, and "this one is expected, for this reason" is the answer a reader
    # needs; anything not on this list is still drift.
    # `text-embedding-3-large` is the knowledge-retrieval embedding deployment (E1, 2026-10-14; ADR-0027's seam).
    EXPECTED_DEPLOYMENTS = ("Decision-1", "gpt-6.1-sol", "claude-fable-5-1", "text-embedding-3-large")
    if isinstance(deployments, list) and any(name not in EXPECTED_DEPLOYMENTS for name in deployments):
        problems.append(f"unexpected model deployments on the account: {deployments}")
    return problems


def _captured_digest_and_commit() -> tuple[str, str]:
    """The image digest and source commit the capture names, for the claims that state them beside a revision."""
    doc = json.loads(STATE.read_text(encoding="utf-8"))
    return str(doc.get("image_digest") or ""), str(doc.get("source_commit") or "")


def _prior_revision() -> str | None:
    """The revision the manifest records as the current one's predecessor."""
    text = (ROOT / REVISION_MANIFEST).read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("- Prior image:"):
            found = re.search(r"revision `([^`]+)`", line)
            if found:
                return found.group(1)
    return None


def _live_revision() -> str:
    try:
        doc = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read {STATE.relative_to(ROOT)}: {type(error).__name__}") from error
    revision = doc.get("revision")
    if not revision:
        raise SystemExit(f"{STATE.relative_to(ROOT)} has no revision field")
    return revision


def main() -> None:
    live = _live_revision()
    problems: list[str] = []
    checked = 0

    surface_documentation = "\n".join((ROOT / relative).read_text(encoding="utf-8")
                                      for relative in SURFACE_DOCUMENTS)
    surface_fields = _record_surface_fields()
    surface_covered = 0
    try:
        surface_covered = _assert_surface_coverage(surface_fields, surface_documentation)
    except ValueError as error:
        problems.append(f"{' and '.join(SURFACE_DOCUMENTS)}: {error}")

    for relative, pattern in REVISION_CLAIMS:
        text = (ROOT / relative).read_text(encoding="utf-8")
        found = re.findall(pattern, text)
        if len(found) != 1:
            problems.append(f"{relative}: expected one current-state revision claim, found {len(found)}")
            continue
        checked += 1
        if found[0] != live:
            problems.append(f"{relative}: claims current revision {found[0]}, but {live} is live — "
                            f"is this a superseded revision recorded as current?")

    for relative in STATE_FREE:
        text = (ROOT / relative).read_text(encoding="utf-8")
        stale = re.findall(r"luvn-challenge11-product--\d+", text)
        checked += 1
        if stale:
            problems.append(f"{relative}: a decision record must not name deployment state, found {sorted(set(stale))}")

    # One revision, one round. Every re-point in this repository appends a sentence naming the revision it
    # supersedes, and those sentences accumulate: the four owner documents each ended up claiming the same
    # revision twice with two *different* rounds, because a later edit replaced a label without replacing the
    # description under it. The gate did not notice, because it only inspects the first preceding revision.
    # A revision may be described as carrying a round at most once per document.
    for relative in (*[relative for relative, _ in REVISION_CLAIMS],):
        text = (ROOT / relative).read_text(encoding="utf-8")
        claimed = re.findall(r"luvn-challenge11-product--(\d{7})`\s*carried", text)
        repeated = sorted({number for number in claimed if claimed.count(number) > 1})
        checked += 1
        if not claimed:
            # Non-vacuity, and it is the shape three checks in this repository have already had: a pattern that
            # stops matching reports nothing and passes. Reword one sentence from "carried" to "introduced" and
            # this check would silently guard nothing, so it refuses to be vacuous instead.
            problems.append(
                f"{relative}: no revision is described as carrying a round, so this check is looking at "
                f"nothing — reword it or update the pattern, but do not let it pass empty")
        if repeated:
            problems.append(
                f"{relative}: revision(s) {repeated} are each described as carrying a round more than once, "
                f"so at least one of those sentences is wrong — a revision carries one round")

    for relative in CONTRACT_FREE:
        named = re.findall(r"challenge-1\.1-decision-selection-v[0-9a-z-]+",
                           (ROOT / relative).read_text(encoding="utf-8"))
        checked += 1
        if named:
            problems.append(f"{relative}: the selection contract is owned by ADR-0009 and RULE_VERSION, "
                            f"but this decision record names {sorted(set(named))}")

    for relative, pattern in F2_FIGURES:
        text = _current_region(relative, (ROOT / relative).read_text(encoding="utf-8"))
        found = re.findall(pattern, text)
        checked += 1
        if found:
            problems.append(f"{relative}: restates F2 figures ({sorted(set(found))}); they are derived "
                            f"into docs/decision-evidence-summary.md and must not be duplicated in prose")

    # The final-pass results restated in the pitch, compared with the artifacts that produced them.
    ablation = _artifact("decision-floor-ablation")
    wrong = ablation["result"]["totals"]["wrong_omissions"]
    relative, pattern = ABLATION_CLAIM
    found = re.findall(pattern, (ROOT / relative).read_text(encoding="utf-8"))
    if len(found) != 1:
        problems.append(f"{relative}: expected one ablation claim matching {pattern!r}, found {len(found)}")
    else:
        checked += 1
        claimed = tuple(int(value) for value in found[0])
        if claimed != (wrong["production"], wrong["zero_floor"]):
            problems.append(f"{relative}: claims wrong omissions {claimed}, but the ablation artifact says "
                            f"({wrong['production']}, {wrong['zero_floor']})")

    disagreement = _artifact("decision-structured-fact-disagreement")
    counts = disagreement["counts"]
    relative, pattern = DISAGREEMENT_CLAIM
    found = re.findall(pattern, (ROOT / relative).read_text(encoding="utf-8"))
    if len(found) != 1:
        problems.append(f"{relative}: expected one disagreement claim matching {pattern!r}, found {len(found)}")
    else:
        checked += 1
        absent, not_comparable, numerator, denominator = (int(value) for value in found[0])
        if (absent, not_comparable, numerator, denominator) != (
                counts["absent"], counts["not-comparable"], counts["disagreement"], counts["pairs"]):
            problems.append(f"{relative}: claims disagreement ({absent} absent, {not_comparable} not-comparable, "
                            f"{numerator}/{denominator}), but the artifact says ({counts['absent']} absent, "
                            f"{counts['not-comparable']} not-comparable, "
                            f"{counts['disagreement']}/{counts['pairs']})")

    # Every other markdown document must not claim which revision is current.
    for path in sorted(ROOT.glob("*.md")) + sorted((ROOT / "docs").rglob("*.md")):
        relative = str(path.relative_to(ROOT))
        if relative in REVISION_OWNERS or relative.startswith("docs/adr/"):
            continue
        found = REVISION_CLAIM.findall(path.read_text(encoding="utf-8"))
        checked += 1
        if found:
            problems.append(f"{relative}: states a current revision ({found}) but is not one of the "
                            f"documents that owns that claim: {sorted(REVISION_OWNERS)}")

    # The marking's version and header names, compared with `app.provenance` rather than trusted.
    for relative, pattern in MARKING_CLAIMS:
        found = re.findall(pattern, (ROOT / relative).read_text(encoding="utf-8"))
        checked += 1
        if len(found) != 1:
            problems.append(f"{relative}: expected one marking claim matching {pattern!r}, found {len(found)}")
        elif found[0] != MARKING_VERSION:
            problems.append(f"{relative}: says the marking is {found[0]}, but app.provenance has "
                            f"{MARKING_VERSION} — is one of them a leftover from the previous shape?")
    declared_headers = {name for name in dir(provenance) if name.startswith("HEADER_AI_GENERATED")}
    checked += 1
    marking_doc = (ROOT / MARKING_HEADER_DOC).read_text(encoding="utf-8")
    missing_headers = sorted(name for name in declared_headers
                             if getattr(provenance, name) not in marking_doc)
    if missing_headers:
        problems.append(f"{MARKING_HEADER_DOC}: does not name every emitted header — missing "
                        f"{', '.join(missing_headers)}; a renamed header would lose its documentation")

    # A present-tense revision claim, however it is phrased. The four owner patterns above require an
    # exact sentence; this requires only that the claim be present-tense, which is what the stale line was.
    for relative in _present_tense_documents():
        text = (ROOT / relative).read_text(encoding="utf-8")
        for match in REVISION_NAME.finditer(text):
            checked += 1
            window = text[max(0, match.start() - 30):match.start()]
            if CURRENTLY_QUALIFIED.search(window) and match.group(0) != live:
                problems.append(f"{relative}: calls {match.group(0)} current, but the capture says {live} — "
                                f"a present-tense claim has to name the revision that is deployed")
    # Non-vacuity: the qualifier matches the shape the defect had, and does not match a plain historical
    # mention — otherwise this check would either miss the case it exists for or flag every narrative.
    assert CURRENTLY_QUALIFIED.search("**Current deployed revision "), "the qualifier misses the defect shape"
    assert not CURRENTLY_QUALIFIED.search("The preceding revision "), "the qualifier flags plain history"

    # The digest and the source commit beside each owner's claim, against the capture. The revision NAME was
    # checked and these two were not, which is how four documents named the right revision with the wrong
    # image for two deploys.
    captured_digest, captured_commit = _captured_digest_and_commit()
    for relative, pattern in REVISION_CLAIMS:
        text = (ROOT / relative).read_text(encoding="utf-8")
        found = re.search(pattern, text)
        if found is None:
            continue
        window = text[found.end():found.end() + CLAIM_WINDOW]
        for digest in DIGEST_IN_PROSE.findall(window):
            checked += 1
            if digest != captured_digest:
                problems.append(f"{relative}: its claim carries image digest {digest}, but the capture says "
                                f"{captured_digest}")
        for commit in COMMIT_IN_PROSE.findall(window):
            checked += 1
            if not captured_commit.startswith(commit):
                problems.append(f"{relative}: its claim carries commit {commit}, which is not the captured "
                                f"{captured_commit}")
    # Non-vacuity: the window has to reach the digest the claim actually states, or this check would pass by
    # looking at nothing — which is what an earlier version of it did, at 400 characters from the wrong anchor.
    sample = re.search(REVISION_CLAIMS[2][1], (ROOT / REVISION_CLAIMS[2][0]).read_text(encoding="utf-8"))
    assert sample is not None, "the README claim pattern no longer matches"
    tail = (ROOT / REVISION_CLAIMS[2][0]).read_text(encoding="utf-8")[sample.end():sample.end() + CLAIM_WINDOW]
    assert DIGEST_IN_PROSE.search(tail), (
        "the claim window does not reach the README's image digest, so this check is looking at nothing")
    assert COMMIT_IN_PROSE.search(tail), "the claim window does not reach the README's source commit"

    # Precedence, not just currency. Re-pointing a revision claim used to prepend to the existing
    # paragraph, which left the tail sentences of the OLD paragraph reading as claims about the new
    # revision: the as-built called `--0000038` "the preceding" revision in a passage whose subject had
    # moved on to `--0000047`.
    #
    # The rule is the one place the subject is unambiguous — the FIRST precedence claim after the live
    # revision is named is that revision's lineage claim, and it must name the manifest's prior revision.
    #
    # The limit of this, stated because a check whose limit is unstated gets trusted past it: precedence
    # claims deeper inside these documents are NOT gated, because the four owners pack a dozen revisions
    # into one paragraph and by mid-paragraph the antecedent is genuinely ambiguous — `--0000038` may be
    # being compared with `--0000047` or with `--0000039`, and no window or regex can tell. Gating that
    # needs each revision's history in its own bounded block, which is an editorial restructuring of four
    # long-standing documents and is not a change to slip in beside a deploy. Until then the defence there
    # is the same as it was: a human reading the claim, which is how this one was found.
    prior = _prior_revision()
    if prior is not None:
        for relative in sorted(REVISION_OWNERS):
            text = (ROOT / relative).read_text(encoding="utf-8")
            at = text.find(live)
            if at < 0:
                continue
            checked += 1
            claimed = PRECEDENCE_CLAIM.search(text, at)
            if claimed is not None and f"--{claimed.group(1)}" != prior and claimed.group(1) not in prior:
                problems.append(
                    f"{relative}: the first revision it calls preceding after `{live}` is "
                    f"`--{claimed.group(1)}`, but {REVISION_MANIFEST} records `{prior}` as the prior revision")

    # The boundary an operator has to be told before the first real run (issue 12). Every phrase is checked
    # because every one of them is a condition, not an introduction.
    # Whitespace-normalised and case-insensitive: prose wraps, and a check that a paragraph break can
    # silently defeat is a check that reports a boundary missing when it is present. Every phrase is still
    # the load-bearing half of one condition.
    runbook = re.sub(r"\s+", " ", (ROOT / ARRIVAL_RUNBOOK).read_text(encoding="utf-8")).casefold()
    for description, phrase in ARRIVAL_BOUNDARY:
        checked += 1
        if re.sub(r"\s+", " ", phrase).casefold() not in runbook:
            problems.append(f"{ARRIVAL_RUNBOOK}: {description} is no longer stated ({phrase!r}); the "
                            f"real-data gate cannot be read out of the runbook it was moved into")
    # Non-vacuity: the check would pass on any document if the phrases were empty.
    assert all(phrase.strip() for _, phrase in ARRIVAL_BOUNDARY), "a boundary phrase is empty"

    # The deployed posture, asserted from the capture: authentication on, the removed classifier's key gone,
    # no per-role provider override, and no unexpected model deployments.
    problems.extend(_assert_deployed_posture())

    # Every relative link in the surface documents resolves. Two lines of the as-built pointed one `..`
    # too far and resolved outside the repository, and the generated decision-protocol did the same on the
    # one link its renderer got wrong; a link check is cheap and the failure is silent otherwise.
    broken, links_tested = _assert_relative_links_resolve()
    checked += links_tested
    if not links_tested:
        problems.append("no relative links were found in the surface documents, so this check is vacuous")
    problems.extend(broken)

    if problems:
        for problem in problems:
            print(f"doc-claims: {problem}", file=sys.stderr)
        raise SystemExit(1)
    print(f"document current-state claims match the code and the capture ({checked} claims; "
          f"{surface_covered} Record/RecordView fields covered)")


if __name__ == "__main__":
    main()
