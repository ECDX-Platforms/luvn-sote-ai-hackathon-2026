#!/usr/bin/env python
"""Re-check that every guideline link this product would show still resolves.

Link rot in a clinical citation is a real failure mode: a professional follows a link from inside a care
briefing and must not land on a 404, a redirect to something unrelated, or a page that has been withdrawn.
`guidance.GUIDELINES` records the status each code returned when it was last checked, and this script is how
that record is kept true.

The check is deliberately **out of the solution's offline gate**, which is Azure-free and network-free: a
network test in `check.sh` would make the gate fail when the publisher's site is briefly down, which says
nothing about this code. So the offline tests assert the *metadata* is complete and self-consistent, and this
script, run deliberately, asserts the links are alive.

    .venv/bin/python scripts/verify_guideline_links.py            # probe every recorded code
    .venv/bin/python scripts/verify_guideline_links.py --check    # fail if the record is stale
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import guidance  # noqa: E402

RESULTS = ROOT / ".scratch" / "retrieval-and-knowledge" / "results"
TARGET = RESULTS / "guideline-links-v1.json"


def _probe(url: str, timeout_s: float = 25.0) -> dict:
    """Fetch one guideline URL and decide liveness from what the page *is*, not from its status code.

    This distinction is the whole point. Unknown `/hoiNNNNN` codes answer **HTTP 200** while serving the site's
    generic 404 page, so a status-code-only checker reports a dead clinical link as healthy -- and 36 such
    codes were found during enumeration. Liveness is therefore the absence of the site's own 404 marker plus
    the presence of a real heading. Only `<title>` and `<h1>` are read: that is the citation metadata the terms
    permit, and no recommendation body text is touched.
    """
    import httpx

    try:
        response = httpx.get(url, timeout=timeout_s, follow_redirects=False,
                             headers={"User-Agent": "LUVN-guideline-link-check"})
    except Exception as error:  # noqa: BLE001 - an unreachable publisher is the finding
        return {"http_status": None, "live": False, "location": None, "title": None,
                "error": f"{type(error).__name__}: {error}"[:200]}
    body = response.text if response.status_code < 400 else ""
    folded = body.casefold()
    soft_404 = any(marker in folded for marker in guidance.SOFT_404_MARKERS)
    title_match = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S | re.I)
    title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", title_match.group(1))).strip() if title_match else None
    return {"http_status": response.status_code,
            # A redirect breaks the prescribed one-code-one-guideline property, so it is not live either.
            "live": bool(response.status_code == 200 and not soft_404 and title),
            "soft_404": soft_404, "location": response.headers.get("location"),
            "title": title, "error": None}


def run() -> dict:
    entries = []
    for guideline in guidance.GUIDELINES:
        probe = _probe(guideline.url)
        entries.append({"code": guideline.code, "recorded_title": guideline.title,
                        "recorded_published": guideline.published, "url": guideline.url,
                        "recorded_status": guideline.http_status,
                        "recorded_verified_at": guideline.verified_at, **probe})
    return {"kind": "guideline-link-verification",
            "generated_by": "backend/scripts/verify_guideline_links.py",
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "citation_form": guidance.CITATION_HOST,
            "licence_position": ("Linking is permitted in the prescribed /hoiNNNNN form with the publisher "
                                 "cited; content is not reproduced. See app/guidance.py and issue 12."),
            "entries": entries}


def check_recorded(document: dict) -> list[str]:
    """Compare the map against the last live observation, without the network.

    The gate cannot call the publisher's site, so it cannot decide liveness -- but it can insist that the
    titles the map records are the titles the last probe saw. Editing a title without re-probing fails here,
    which is the only way a recorded-title drift becomes visible to a gate at all. Its weakness is honest:
    map and artifact can be stale together, and only the live probe settles that.
    """
    by_code = {entry["code"]: entry for entry in document.get("entries", [])}
    problems: list[str] = []
    for guideline in guidance.GUIDELINES:
        entry = by_code.get(guideline.code)
        if entry is None:
            problems.append(f"{guideline.code}: in the map but not in the last live observation")
            continue
        if entry.get("title") != guideline.title:
            problems.append(f"{guideline.code}: the map says {guideline.title!r} but the last observation "
                            f"saw {entry.get('title')!r} -- re-probe the links")
        if entry.get("recorded_published") != guideline.published:
            problems.append(f"{guideline.code}: the map records publication {guideline.published!r} but the "
                            f"last observation recorded {entry.get('recorded_published')!r}")
    for code in by_code:
        if code not in {g.code for g in guidance.GUIDELINES}:
            problems.append(f"{code}: in the last observation but no longer in the map")
    return problems


def check(document: dict) -> int:
    problems: list[str] = []
    for entry in document["entries"]:
        if not entry["live"]:
            detail = entry.get("error") or ("soft 404 page served with status 200" if entry.get("soft_404")
                                            else f"status {entry['http_status']}")
            problems.append(f"{entry['code']}: not live — {detail}")
        if not entry.get("title"):
            problems.append(f"{entry['code']}: no heading on the page, so it cannot be cited")
        elif entry["title"] != entry["title"]:
            pass  # the recorded title is checked just below, once the entry shape is known
        elif entry["title"] != entry.get("recorded_title", entry["title"]):
            problems.append(f"{entry['code']}: the page now calls itself {entry['title']!r} but the map "
                            f"records {entry.get('recorded_title')!r} — a wrong code, or a retitled guideline")
        if entry["recorded_status"] != 200:
            problems.append(f"{entry['code']}: recorded status is not 200")
    alive = sum(1 for entry in document["entries"] if entry["live"])
    print(f"check: {len(document['entries'])} guideline link(s) probed, {alive} live "
          f"(status 200 *and* a real guideline page, not the site's 404 page served as 200)")
    for entry in document["entries"]:
        state = "live" if entry["live"] else "DEAD"
        print(f"  {entry['code']:10s} {entry['http_status']} {state:5s} {entry['title'] or '-':58s} "
              f"({entry['recorded_published']})")
    for problem in problems:
        print(f"check: {problem}", file=sys.stderr)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the guideline deep links resolve")
    parser.add_argument("--check", action="store_true", help="re-check the committed artifact")
    parser.add_argument("--offline", action="store_true", help="verify only the recorded metadata")
    args = parser.parse_args(argv)
    if args.offline:
        problems = guidance.metadata_is_complete()
        if TARGET.exists():
            problems += check_recorded(json.loads(TARGET.read_text(encoding="utf-8")))
        for problem in problems:
            print(f"check: {problem}", file=sys.stderr)
        if not problems:
            print(f"check: {len(guidance.GUIDELINES)} guideline(s) recorded and mappable, metadata complete, "
                  f"{len(guidance.topics_without_guidance())} product topic(s) with no guideline recorded")
        return 1 if problems else 0
    if args.check:
        if not TARGET.exists():
            print(f"check: no artifact at {TARGET}", file=sys.stderr)
            return 1
        return check(json.loads(TARGET.read_text(encoding="utf-8")))
    document = run()
    RESULTS.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return check(document)


if __name__ == "__main__":
    raise SystemExit(main())
