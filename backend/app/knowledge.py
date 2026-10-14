"""Deterministic lookup over pinned open reference data, carrying the provenance that makes it citable.

This is the `ref:` seam the chat plan assumed already existed in the type system. It did not: the plan
is corrected in this workstream. A lookup returns a `KnowledgeRef` that names its source, that source's
version, its licence and its attribution, so any label rendered from it can say where it came from and
under what terms — which is the whole reason to have an external-evidence class at all.

**Nothing here calls a model, and nothing here fetches.** Labels come from a snapshot under the
gitignored `backend/data/knowledge/`, produced by `scripts/fetch_knowledge.py`; a source the registry
marks `machine_readable: false` is not loadable at all. A missing snapshot resolves nothing rather than
inventing a label, and a snapshot whose bytes do not match its recorded SHA-256 is refused.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REGISTRY_PATH = Path(__file__).with_name("knowledge_registry.json")
ALIASES_PATH = Path(__file__).with_name("knowledge_aliases.json")
SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "data" / "knowledge"


class SnapshotError(RuntimeError):
    """A snapshot is missing, unreadable, or does not match its recorded hash."""


@dataclass(frozen=True)
class KnowledgeRef:
    """One label, with everything a citation needs to be honest."""

    source_id: str
    system: str
    code: str
    label: str
    version: str
    licence: str
    attribution: str
    alias: str | None = None

    def as_ref(self) -> str:
        """The citation form the chat plan proposed: `ref:<doc>@<version>#<system>:<code>`.
        When resolved via an alias, the system:code fragment records both: `#<system>:<alias>→<code>`.
        """
        code_fragment = f"{self.alias}→{self.code}" if self.alias else self.code
        return f"ref:{self.source_id}@{self.version}#{self.system}:{code_fragment}"

    def as_dict(self) -> dict:
        result = {"ref": self.as_ref(), "system": self.system, "code": self.code, "label": self.label,
                  "source_id": self.source_id, "version": self.version, "licence": self.licence,
                  "attribution": self.attribution}
        if self.alias is not None:
            result["alias"] = self.alias
            result["via_alias"] = True
        else:
            result["via_alias"] = False
        return result


@lru_cache(maxsize=1)
def load_aliases() -> dict:
    """The committed aliases map. Cached because it is a constant of the build."""
    if not ALIASES_PATH.exists():
        return {}
    return json.loads(ALIASES_PATH.read_text(encoding="utf-8"))


def resolve_alias(system: str, code: str) -> tuple[str, str | None]:
    """Return (target_code, alias) if an alias exists, or (code, None) if not."""
    aliases = load_aliases().get("aliases", {}).get(system, {})
    if code in aliases:
        return str(aliases[code]), code
    return code, None


@lru_cache(maxsize=1)
def load_registry() -> dict:
    """The committed registry. Cached because it is a constant of the build."""
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def sources() -> list[dict]:
    return list(load_registry()["sources"])


def source(source_id: str) -> dict | None:
    return next((row for row in sources() if row["id"] == source_id), None)


def national_term_attribution(term: str) -> str | None:
    """The attribution string for a national term/heading the registry declares it sourced.

    Returns `None` when no registered source claims the term, so a caller cannot accidentally cite a
    source for a word nobody actually checked. This is a real read of `national_terms_sourced` /
    `national_headings_sourced`, not decoration: `test_contract.py` exercises this function directly
    against the term the shipped frontend label ('Toimintakykymittari') actually uses.
    """
    for row in sources():
        if term in row.get("national_terms_sourced", ()) or term in row.get("national_headings_sourced", ()):
            return row.get("attribution")
    return None


def load_snapshot(source_id: str) -> dict:
    """Read one snapshot and verify it against its recorded SHA-256 before trusting a single label."""
    row = source(source_id)
    if row is None:
        raise SnapshotError(f"unknown source {source_id!r}")
    if not row.get("machine_readable"):
        raise SnapshotError(f"{source_id} has no machine-readable interface; nothing to load")
    path = SNAPSHOT_DIR / f"{source_id}.json"
    if not path.exists():
        raise SnapshotError(f"no snapshot at {path}")
    raw = path.read_bytes()
    sidecar = path.with_suffix(".sha256")
    if sidecar.exists():
        recorded = sidecar.read_text(encoding="utf-8").split()[0].strip()
        actual = hashlib.sha256(raw).hexdigest()
        if recorded != actual:
            raise SnapshotError(f"{source_id} snapshot hash {actual} does not match the recorded {recorded}")
    document = json.loads(raw)
    if document.get("source_id") != source_id:
        raise SnapshotError(f"{path} declares source {document.get('source_id')!r}")
    if not isinstance(document.get("labels"), dict):
        raise SnapshotError(f"{path} carries no labels map")
    return document


def _available_snapshots() -> dict[str, dict]:
    available: dict[str, dict] = {}
    for row in sources():
        try:
            available[row["id"]] = load_snapshot(row["id"])
        except SnapshotError:
            continue
    return available


def lookup(system: str, code: str, snapshots: Mapping[str, dict] | None = None) -> KnowledgeRef | None:
    """Resolve one code to a label, or `None`. Later sources do not override earlier ones."""
    target_code, alias = resolve_alias(system, code)
    available = _available_snapshots() if snapshots is None else snapshots
    for source_id, document in available.items():
        labels = (document.get("labels") or {}).get(system) or {}
        label = labels.get(target_code)
        if not isinstance(label, str) or not label:
            continue
        row = source(source_id) or {}
        return KnowledgeRef(source_id=source_id, system=system, code=target_code, label=label,
                            version=str(document.get("version") or "unversioned"),
                            licence=str(row.get("licence") or "unrecorded"),
                            attribution=str(row.get("attribution") or ""),
                            alias=alias)
    return None


def coverage(requests: Iterable[tuple[str, str]], snapshots: Mapping[str, dict] | None = None) -> dict:
    """Resolved/unresolved counts for a set of (system, code) pairs. Misses are named, not hidden."""
    available = _available_snapshots() if snapshots is None else snapshots
    resolved: list[dict] = []
    unresolved: list[dict] = []
    direct_count = 0
    alias_count = 0
    for system, code in requests:
        reference = lookup(system, code, available)
        if reference:
            ref_dict = reference.as_dict()
            if ref_dict.get("via_alias"):
                alias_count += 1
            else:
                direct_count += 1
            # Ensure the original fixture code/system requested is recorded in the coverage entry
            resolved.append({"system": system, "fixture_code": code, **ref_dict})
        else:
            unresolved.append({"system": system, "code": code})
    return {"requested": len(resolved) + len(unresolved), "resolved": len(resolved),
            "resolved_direct": direct_count, "resolved_via_alias": alias_count,
            "unresolved": len(unresolved), "references": resolved, "misses": unresolved}


# --- External evidence: an operator-supplied (or API-supplied) knowledge answer ----------------------
# UpToDate Expert AI is the event's knowledge layer (owner decision 5). During the event its access is
# manual prompting in a browser, so the answer arrives as a **capture**: the question, the audience it was
# asked for, when it was captured, and a hash over the whole record. The production API, when it exists,
# fills the same record shape, so nothing above this seam changes.
#
# The audience is a required field rather than a prompt detail, because an answer written for a physician
# and one written for a family member are different artefacts and must never be confusable on screen.

CAPTURE_DIR = Path(__file__).resolve().parent.parent / "data" / "knowledge" / "captures"
AUDIENCES = ("professional", "patient", "family", "carer")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class ExternalEvidence:
    """One attributed external knowledge answer: optional to have, never optional to attribute."""

    capture_id: str
    source_id: str
    question: str
    audience: str
    answer: str
    captured_at: str
    surface: str
    sha256: str
    licence: str
    attribution: str

    def as_ref(self) -> str:
        """`ref:<source>@<date>#<capture>`, the form the chat plan proposed for external evidence."""
        return f"ref:{self.source_id}@{self.captured_at[:10]}#{self.capture_id}"

    def as_dict(self) -> dict:
        return {"ref": self.as_ref(), "capture_id": self.capture_id, "source_id": self.source_id,
                "question": self.question, "audience": self.audience, "answer": self.answer,
                "captured_at": self.captured_at, "surface": self.surface, "sha256": self.sha256,
                "licence": self.licence, "attribution": self.attribution}


def _capture_dir(directory: Path | None) -> Path:
    return CAPTURE_DIR if directory is None else directory


def capture_ids(directory: Path | None = None) -> list[str]:
    """Every capture file present, valid or not. Validity is decided by `load_capture`."""
    base = _capture_dir(directory)
    if not base.is_dir():
        return []
    return sorted(path.stem for path in base.glob("*.json"))


def load_capture(capture_id: str, directory: Path | None = None) -> ExternalEvidence:
    """Read one capture and refuse it unless it carries everything a citation needs.

    A capture without a matching hash is not evidence, so this raises rather than returning something
    that would render as an attributed claim.
    """
    base = _capture_dir(directory)
    path = base / f"{capture_id}.json"
    if not path.exists():
        raise SnapshotError(f"no capture at {path}")
    raw = path.read_bytes()
    sidecar = path.with_suffix(".sha256")
    if not sidecar.exists():
        raise SnapshotError(f"{capture_id} has no hash sidecar, so it cannot be verified")
    recorded = sidecar.read_text(encoding="utf-8").split()[0].strip()
    actual = hashlib.sha256(raw).hexdigest()
    if recorded != actual:
        raise SnapshotError(f"{capture_id} hash {actual} does not match the recorded {recorded}")
    document = json.loads(raw)
    if document.get("capture_id") != capture_id:
        raise SnapshotError(f"{path} declares capture {document.get('capture_id')!r}")
    source_id = str(document.get("source_id") or "")
    row = source(source_id)
    if row is None:
        raise SnapshotError(f"capture {capture_id} names unknown source {source_id!r}")
    audience = str(document.get("audience") or "")
    if audience not in AUDIENCES:
        raise SnapshotError(f"capture {capture_id} audience {audience!r} is not one of {AUDIENCES}")
    question = str(document.get("question") or "").strip()
    answer = str(document.get("answer") or "").strip()
    if not question or not answer:
        raise SnapshotError(f"capture {capture_id} needs both a question and an answer")
    captured_at = str(document.get("captured_at") or "")
    if not _DATE.match(captured_at):
        raise SnapshotError(f"capture {capture_id} captured_at {captured_at!r} is not an ISO date-time")
    return ExternalEvidence(capture_id=capture_id, source_id=source_id, question=question,
                            audience=audience, answer=answer, captured_at=captured_at,
                            surface=str(document.get("surface") or row["title"]),
                            sha256=actual, licence=str(row.get("licence") or "unrecorded"),
                            attribution=str(row.get("attribution") or ""))


def capture_refs(directory: Path | None = None) -> dict:
    """Every valid capture, plus the refused ones named with their reason. Refusals are never silent."""
    valid: list[dict] = []
    refused: list[dict] = []
    for capture_id in capture_ids(directory):
        try:
            valid.append(load_capture(capture_id, directory).as_dict())
        except SnapshotError as error:
            refused.append({"capture_id": capture_id, "reason": str(error)})
    return {"captures": len(valid) + len(refused), "valid": len(valid), "refused": len(refused),
            "references": valid, "refusals": refused}
