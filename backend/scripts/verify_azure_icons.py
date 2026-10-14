#!/usr/bin/env python
"""Re-hash the vendored official Azure icons and refuse any drift.

`vendor/azure-icons/` holds the official Microsoft Azure architecture icons that
`docs/assets/luvn-architecture.svg` draws with. `vendor/azure-icons/PROVENANCE.md` records the SHA-256 of
every one of them; this script is the enforcement half of that record, so the picture cannot silently
stop depicting the real Microsoft artwork.

It fails on:

  * a file whose bytes no longer hash to the recorded value (edited, truncated, or swapped);
  * a **missing** file — the record names it, the disk does not have it;
  * an **extra** file — something is vendored that the record does not pin;
  * a `PROVENANCE.md` whose table and whose recorded zip hash have been altered.

Extra-file detection is deliberate and not merely tidy: an unpinned icon in this directory is
indistinguishable from a pinned one to the poster generator, and the Do's require the icons to be used
as Microsoft shipped them. If a file is not in the table, it is not allowed to be here.

Offline: it reads the working tree only. That is why it is safe to register in `scripts/check.sh`.

    cd backend && .venv/bin/python scripts/verify_azure_icons.py --check     # verify (gate / pre-commit)
    cd backend && .venv/bin/python scripts/verify_azure_icons.py             # same, default is --check
    cd backend && .venv/bin/python scripts/verify_azure_icons.py --list      # print the pin table

To re-pin after a deliberate re-vendor, run `--print-table` and paste the result over the table in
`vendor/azure-icons/PROVENANCE.md`, then update `ZIP_SHA256` from the new download.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ICON_DIR = ROOT / "vendor" / "azure-icons"
PROVENANCE = ICON_DIR / "PROVENANCE.md"

# The SHA-256 of Azure_Public_Service_Icons_V24.zip as downloaded. Kept here as well as in
# PROVENANCE.md: this script asserts the two agree, so a re-pin that edits one but not the other fails.
ZIP_SHA256 = "921594ccd1bf3d9c0a1bd7b6d924e050551a59342f2b353bb74bdcf761c35141"
EXPECTED_COUNT = 18

# `| `name.svg` | `original/path.svg` | bytes | `sha256` |`
TABLE_ROW = re.compile(
    r"^\|\s*`(?P<name>[^`]+\.svg)`\s*\|\s*`(?P<origin>[^`]+)`\s*\|\s*(?P<size>\d+)\s*\|\s*`(?P<digest>[0-9a-f]{64})`\s*\|\s*$"
)
ZIP_HASH_IN_PROSE = re.compile(r"^\|\s*\*\*Downloaded zip SHA-256\*\*\s*\|\s*`([0-9a-f]{64})`\s*\|", re.M)


class VerifyError(RuntimeError):
    """The vendored set and its record disagree; the picture must not be rendered from it."""


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_provenance() -> dict[str, dict[str, str]]:
    """The pin table, keyed by vendored filename."""
    if not PROVENANCE.is_file():
        raise VerifyError(f"missing {PROVENANCE.relative_to(ROOT)}; the icons are unpinned without it")
    rows: dict[str, dict[str, str]] = {}
    for line in PROVENANCE.read_text(encoding="utf-8").splitlines():
        match = TABLE_ROW.match(line)
        if match:
            rows[match.group("name")] = {
                "origin": match.group("origin"),
                "size": match.group("size"),
                "digest": match.group("digest"),
            }
    if not rows:
        raise VerifyError(
            f"{PROVENANCE.relative_to(ROOT)} has no pin table rows; the icons are unpinned without them")
    return rows


def _provenance_zip_hash() -> str:
    found = ZIP_HASH_IN_PROSE.search(PROVENANCE.read_text(encoding="utf-8"))
    if found is None:
        raise VerifyError(f"{PROVENANCE.relative_to(ROOT)} does not record the downloaded zip's SHA-256")
    return found.group(1)


def verify() -> list[str]:
    """Return the failures. Empty means the vendored set matches its record exactly."""
    expected = parse_provenance()
    failures: list[str] = []

    recorded_zip = _provenance_zip_hash()
    if recorded_zip != ZIP_SHA256:
        failures.append(
            f"the zip hash recorded in PROVENANCE.md ({recorded_zip[:12]}…) does not match the hash "
            f"this check pins ({ZIP_SHA256[:12]}…): re-pin both together")

    if len(expected) != EXPECTED_COUNT:
        failures.append(
            f"PROVENANCE.md pins {len(expected)} files but this check expects {EXPECTED_COUNT}: the "
            f"record and the check drifted apart")

    if not ICON_DIR.is_dir():
        raise VerifyError(f"missing directory {ICON_DIR.relative_to(ROOT)}")

    present = {path.name for path in ICON_DIR.glob("*.svg")}

    for name in sorted(set(expected) - present):
        failures.append(f"missing: {name} is pinned in PROVENANCE.md but is not in the directory")

    for name in sorted(present - set(expected)):
        failures.append(
            f"extra: {name} is vendored but not pinned in PROVENANCE.md — an unpinned icon is "
            f"indistinguishable from a pinned one, so add it to the table or remove it")

    for name in sorted(set(expected) & present):
        path = ICON_DIR / name
        actual_digest = _digest(path)
        if actual_digest != expected[name]["digest"]:
            failures.append(
                f"modified: {name} hashes to {actual_digest[:16]}… but PROVENANCE.md pins "
                f"{expected[name]['digest'][:16]}…")
        actual_size = str(path.stat().st_size)
        if actual_size != expected[name]["size"]:
            failures.append(
                f"modified: {name} is {actual_size} bytes but PROVENANCE.md records "
                f"{expected[name]['size']}")

    return failures


def print_table() -> None:
    """Emit the pin table for PROVENANCE.md, so re-pinning is a paste rather than a retype."""
    for path in sorted(ICON_DIR.glob("*.svg")):
        print(f"| `{path.name}` | `<original path>` | {path.stat().st_size} | `{_digest(path)}` |")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the vendored icons match their pin")
    parser.add_argument("--list", action="store_true", help="print the pinned files and digests")
    parser.add_argument("--print-table", action="store_true", help="print the pin table for PROVENANCE.md")
    args = parser.parse_args()

    if args.print_table:
        print_table()
        return 0

    try:
        if args.list:
            for name, row in sorted(parse_provenance().items()):
                print(f"{row['digest']}  {name}")
            return 0

        failures = verify()
    except VerifyError as error:
        print(f"verify_azure_icons: {error}", file=sys.stderr)
        return 1

    if failures:
        print(f"verify_azure_icons: the vendored official Azure icons have drifted from their pin "
              f"({len(failures)} problem(s)):", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        print("\nRe-vendor from the source recorded in vendor/azure-icons/PROVENANCE.md and re-pin, "
              "rather than editing an icon in place.", file=sys.stderr)
        return 1

    count = len(parse_provenance())
    print(f"check: {count} official Azure icons match their pin (set V24, zip sha256 {ZIP_SHA256[:12]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
