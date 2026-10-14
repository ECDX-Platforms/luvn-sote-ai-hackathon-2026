"""Demo state behind one interface.

Personas and feedback used to be module-level globals in `main.py`, built at
import time. They live here now: one owner, injectable, and a test can hand the
app an in-memory store instead of paying for disk reads on import.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from time import monotonic
from functools import lru_cache
from pathlib import Path
from threading import Lock
from uuid import uuid4

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from .encounters import Encounter, EncounterMetadata
from .fixtures import load_personas
from .upm import Persona

log = logging.getLogger(__name__)

DEFAULT_FEEDBACK_PATH = Path(__file__).resolve().parent.parent / "data" / "feedback.jsonl"

# Records of different kinds share one container/namespace, so each stored record carries its kind explicitly
# and every read filters on it: an owner-scoped read for one kind can never return another kind's record.
KIND_PROPOSAL_FEEDBACK = "proposal_feedback"
KIND_CHAT_TURN = "chat_turn"
KIND_CHAT_REVIEW = "chat_review"
# A professional's verdict on one pre-filled form answer (`vahvistettu` / `muokattu` / `hylätty`). Append-only,
# keyed `prefill-review-<id>`; it is never written back to the form, the record or the briefing.
KIND_PREFILL_REVIEW = "prefill_review"
# A decision claim is the O(1) uniqueness marker for one subject; it carries no owner, so it never appears in
# an owner-scoped read.
KIND_DECISION_CLAIM = "decision_claim"
# One row per briefing read that was resolved and released. Metadata only: see `main.encounter_picture`.
KIND_READ_AUDIT = "briefing_read_audit"
# Read-audit rows live in their own physical namespace (`audit/<event_id>.json` on Blob, a separate file on
# JSONL), so no owner-scoped feedback or chat read can ever return one. Ownerless, like a claim.
READ_AUDIT_NAMESPACE = "audit"
# Decision claims live in their OWN key namespace inside the store, not beside the records. A record's key is
# a caller-supplied `event_id` over `[A-Za-z0-9_-]` (an alphabet that even contains `__`, so the owner
# separator is not enough), which means a caller could choose an `event_id` equal to another actor's
# deterministic `_decision_claim_key` and read that claim back as if it were their own record. `/` cannot
# occur in a validated event_id, so `claims/<key>` is a name space no request can spell: the two key spaces
# are disjoint by construction rather than by the caller declining to guess.
CLAIM_NAMESPACE = "claims"
# Record kinds that would otherwise share one name — a feedback `event_id` of `turn-<x>` and a chat turn
# whose id is `<x>` both spelled `<persona>__turn-<x>.json` — get a namespace segment of their own. The
# separator is `/`, the one character the event-id alphabet `[A-Za-z0-9_-]` excludes (both the route models
# and `_record_name` enforce it), so no caller-supplied id can spell a namespaced name. `_` is NOT enough: an
# event id may contain `_`, even `__`, so the owner separator alone leaves the two key spaces overlapping.
# The segment sits AFTER the owner separator (`<persona>__chat/<event_id>.json`) so the owner-prefix scan
# that keeps one persona's read off another persona's bytes still works, and a legacy owner-scoped name is
# still returned by the same listing — which is what keeps the earlier revisions' records readable.
_KEY_NAMESPACE_SEPARATOR = "/"
CHAT_TURN_KEY_NAMESPACE = "chat"
CHAT_REVIEW_KEY_NAMESPACE = "review"
# The owner segment of an owner-scoped blob name, `<persona_id>__<event_id>.json`. It is not a path
# separator, so the name stays flat and a by-id read can construct the exact name it wants without listing
# the container. (`_` is allowed inside an event id, so the separator is only unambiguous because the owner
# segment is constructed from the persona, never recovered by splitting a caller-supplied id.)
_OWNER_SEPARATOR = "__"
# How long a container listing (blob NAMES only, never entries) is reused by the blob backend before it is
# re-listed. Short, because another replica may have written a record; long enough that a burst of reads
# does not pay a listing each. `LUVN_BLOB_INDEX_TTL_SECONDS` exists for tests and for an operator.
BLOB_INDEX_TTL_SECONDS = float(os.environ.get("LUVN_BLOB_INDEX_TTL_SECONDS") or 30.0)
# The event_id -> owner write hints kept per storage instance before they are cleared. An optimisation for
# the write-then-read-back path, never a correctness dependency: a miss goes to the listing.
_OWNER_HINT_LIMIT = 4096


class DecisionConflict(ValueError):
    """A second, different decision was submitted for a subject that already has one."""


def _decision_claim_key(subject: str) -> str:
    """A short, safe storage key for one decision subject (an id pair, never raw text).

    Hashed because the subject's ids are attacker-controlled path/body values and the key is a blob name; the
    hash keeps the key inside the storage's name guard whatever the ids contain. The key is written only into
    the claim namespace (`CLAIM_NAMESPACE`), never into the record namespace: it is deterministic, so a
    caller could otherwise choose it as their own `event_id` and address another actor's claim.
    """
    return "decision-" + hashlib.sha256(subject.encode("utf-8")).hexdigest()[:32]


def _record_kind(entry: dict) -> str | None:
    """The record's explicit kind, or the legacy kind inferred from the fields it carries.

    Records written before the kind field existed are told apart by their own id fields: a chat turn carries
    `turn_id`, a review carries `review_id`, and proposal feedback carries neither. New records always carry
    `kind`, so a feedback read can never return a chat record.
    """
    kind = entry.get("kind")
    if kind:
        return kind
    if entry.get("turn_id"):
        return KIND_CHAT_TURN
    if entry.get("review_id"):
        return KIND_CHAT_REVIEW
    return KIND_PROPOSAL_FEEDBACK


def _event_content(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if key not in ("recorded_at", "event_id")}


class _JsonlRegistry:
    """One lock and one known-event-id set per resolved JSONL path, process-wide.

    Two `Store` instances on the same path must contend on the same lock, not the
    per-instance lock that made the old check-then-append racy. The id set is
    populated lazily and kept in memory, so the write path is O(1) instead of an
    O(file) read per POST; the file stays the append-only source of truth.
    """

    def __init__(self, path: Path):
        self.path = path
        self.lock = Lock()
        self.events: dict[str, dict] | None = None


_FEEDBACK_REGISTRIES: dict[str, _JsonlRegistry] = {}
_REGISTRIES_GUARD = Lock()


def _registry(path: Path) -> _JsonlRegistry:
    key = str(path.resolve())
    with _REGISTRIES_GUARD:
        return _FEEDBACK_REGISTRIES.setdefault(key, _JsonlRegistry(path))


def _quarantine(path: Path, bad_lines: list[bytes]) -> None:
    """Preserve corrupt bytes exactly in the quarantine sidecar."""
    sidecar = path.with_suffix(path.suffix + ".corrupt")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    with sidecar.open("ab") as stream:
        for line in bad_lines:
            stream.write(line + b"\n")
    log.warning("quarantined %d unreadable feedback line(s) to %s", len(bad_lines), sidecar.name)


def _load_jsonl(path: Path) -> list[dict]:
    """Parse the file, quarantining torn lines so the count cannot drift silently.

    Reads bytes and decodes per line: a torn multibyte UTF-8 character is a torn
    line too, and must be quarantined rather than abort the whole read.
    """
    if not path.exists():
        return []
    good: list[str] = []
    entries: list[dict] = []
    bad: list[bytes] = []
    for raw_line in path.read_bytes().split(b"\n"):
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            text = raw_line.decode("utf-8")
            entry = dict(json.loads(text))
            entry["recorded_at"] = datetime.fromisoformat(entry["recorded_at"])
        except (ValueError, TypeError, KeyError, UnicodeDecodeError, RecursionError):
            # `RecursionError` (`RuntimeError`, not `ValueError`) is a deeply nested feedback line, and it
            # must be quarantined like any other torn line: otherwise ONE such line makes every read of
            # that person's feedback raise forever, because the repair below never runs.
            bad.append(raw_line)
            continue
        good.append(text.strip())
        entries.append(entry)
    if bad:
        # Repair the main file so later loads are clean and the counter matches it.
        _quarantine(path, bad)
        repaired = path.with_suffix(path.suffix + ".tmp")
        repaired.write_text("".join(text + "\n" for text in good), encoding="utf-8")
        repaired.replace(path)
    return entries


class _JsonlFeedbackStorage:
    """JSONL persistence, or a process-local list when `path` is None."""

    def __init__(self, path: Path | None):
        self._path = path
        self._memory: list[dict] = []
        self._lock = Lock()
        self._registry = _registry(path) if path is not None else None

    def _entries_locked(self) -> list[dict]:
        """Caller holds the path registry lock (or owns the memory list)."""
        return _load_jsonl(self._path) if self._path is not None else self._memory

    def create_if_absent(self, event_id: str, entry: dict) -> bool:
        if self._path is None:
            with self._lock:
                if any(row.get("event_id") == event_id for row in self._memory):
                    return False
                self._memory.append(dict(entry))
                return True
        registry = self._registry
        path = self._path
        if registry is None or path is None:  # constructor guarantees both when path is set
            raise RuntimeError("jsonl feedback registry is not initialised")
        with registry.lock:
            # The file is the source of truth, not this process's cached id set. `registry.events` only
            # knows what this process has seen, and a second replica appends without updating it, so a
            # retry landing on another replica would append the same event a second time. Re-reading the
            # file before the create-if-absent decision is what stops that. It costs an O(file) parse per
            # write, which the JSONL backend accepts: it is the development/non-hosted backend, and a real
            # cross-process guarantee (no window at all between the read and the append) needs an atomic
            # create primitive — the same one the Blob backend has via `overwrite=False`.
            known = {row["event_id"]: row for row in _load_jsonl(path) if row.get("event_id")}
            registry.events = known
            if event_id in known:
                return False
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {**entry, "recorded_at": entry["recorded_at"].isoformat()}
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
            known[event_id] = dict(entry)
            return True

    def read_by_id(self, event_id: str) -> dict | None:
        if self._path is None:
            with self._lock:
                return next((dict(row) for row in self._memory if row.get("event_id") == event_id), None)
        registry = self._registry
        if registry is None:
            raise RuntimeError("jsonl feedback registry is not initialised")
        with registry.lock:
            if registry.events is None:
                registry.events = {row["event_id"]: row for row in _load_jsonl(self._path) if row.get("event_id")}
            entry = registry.events.get(event_id)
            return None if entry is None else dict(entry)

    def read_create_slot(self, event_id: str, persona_id: str | None = None) -> dict | None:
        """The row `create_if_absent(event_id, ...)` would collide with, or None.

        A JSONL record is keyed by `event_id` alone (unlike the Blob backend, whose key carries the owner
        too), so this mirrors that keying exactly: the row with this id, whoever owns it. Checked before a
        decision slot is reserved, so a conflicting id never consumes one.
        """
        return self.read_by_id(event_id)

    def read_ownerless(self, event_id: str) -> dict | None:
        """The exact row for one id; a legacy claim is the row at its own deterministic id.

        JSONL keys every row by `event_id` alone, so a legacy claim and a legacy record share this read's
        shape: the caller kind-verifies before treating it as a claim (`Store._claim_decision`).
        """
        return self.read_by_id(event_id)

    def read_owned(self, event_id: str, persona_id: str) -> dict | None:
        """The caller's own record by its key, verified against the owner this read is scoped to."""
        if not persona_id:
            return None
        row = self.read_by_id(event_id)
        if row is None or row.get("persona_id") != persona_id or row.get("event_id") != event_id:
            return None
        return row

    def enumerate_by_owner(self, persona_id: str) -> list[dict]:
        return [entry for entry in self.read_all() if entry.get("persona_id") == persona_id]

    def read_all(self) -> list[dict]:
        if self._path is None:
            with self._lock:
                return [dict(entry) for entry in self._memory]
        registry = self._registry
        if registry is None or self._path is None:  # constructor guarantee
            raise RuntimeError("jsonl feedback registry is not initialised")
        # Take the shared lock so a concurrent append cannot be read as a torn line.
        with registry.lock:
            return [dict(entry) for entry in _load_jsonl(self._path)]

    def delete_if_matching(self, event_id: str, predicate) -> bool:
        """Remove the row at `event_id` only when `predicate(row)` says it is still ours; True if removed.

        The JSONL backend is append-only, so this rewrites the file without the matching row. It is used
        to release a decision claim that this process took and then failed to complete (see
        `Store._release_claim`); the predicate is the guard that keeps it from removing a claim another
        attempt took in the meantime, or a decision record that really was written. Absent is False, and
        that is safe for the caller: a file that never held the row never held the claim either.
        """
        if self._path is None:
            with self._lock:
                row = next((entry for entry in self._memory if entry.get("event_id") == event_id), None)
                if row is None or not predicate(row):
                    return False
                self._memory = [entry for entry in self._memory if entry.get("event_id") != event_id]
                return True
        registry = self._registry
        path = self._path
        if registry is None or path is None:  # constructor guarantee
            raise RuntimeError("jsonl feedback registry is not initialised")
        with registry.lock:
            rows = _load_jsonl(path)
            row = next((entry for entry in rows if entry.get("event_id") == event_id), None)
            if row is None or not predicate(row):
                return False
            remaining = [entry for entry in rows if entry.get("event_id") != event_id]
            repaired = path.with_suffix(path.suffix + ".tmp")
            repaired.write_text(
                "".join(json.dumps({**entry, "recorded_at": entry["recorded_at"].isoformat()},
                                   ensure_ascii=False) + "\n"
                        for entry in remaining),
                encoding="utf-8")
            repaired.replace(path)
            # The cached id set is an optimisation the file re-reads below overwrite anyway; a rewrite is
            # exactly the change it cannot be trusted across, so it is dropped rather than patched.
            registry.events = None
            return True


class _BlobFeedbackStorage:
    """Blob persistence using conditional create for event identity.

    **Names carry the owner, and owner-scoped reads use that.** Every record that names a persona is stored
    at `<persona_id>__<event_id>.json`; a record with no owner lives in `namespace` (a decision claim is at
    `claims/<key>.json`, see `CLAIM_NAMESPACE`). An owner-scoped read scans only that persona's names, so no
    other persona's feedback, chat turns, reviews or claims are downloaded to answer it. Before this,
    `enumerate_by_owner` listed and downloaded the WHOLE container and filtered after, which cost
    O(container) per request and passed every other persona's data through the process.

    A storage may also carry a `kind_namespace`: the record name becomes
    `<persona_id>__<namespace>/<event_id>.json`, so two kinds whose ids would otherwise spell the same name
    (a feedback `event_id` of `turn-<x>` and a chat turn id of `<x>`) are structurally disjoint. The
    namespace goes after the owner, so the owner-prefix scan is unchanged and a legacy un-namespaced name is
    still returned by the same listing.

    `__` is the owner separator. It is not a path separator and the owner segment is validated, so the name
    stays flat; note that `_` IS allowed in an event id, so a record id that itself contains `__` still
    belongs to the persona named by the FIRST segment (the owner is constructed, never parsed out of a
    caller-supplied id). A claim, and one kind's namespaced name, are kept out of another kind's read by
    their namespace, not by a name shape.

    A flat record written before the owner segment existed is still readable: `_flat` caches the flat names
    it listed for `BLOB_INDEX_TTL_SECONDS`, so one legacy read does not make every later request pay the
    drain. The cache holds blob NAMES only; entries are downloaded per persona.
    """

    def __init__(self, container, index_ttl: float | None = None, *, namespace: str = "",
                 kind_namespace: str = ""):
        self._container = container
        self._index_ttl = BLOB_INDEX_TTL_SECONDS if index_ttl is None else index_ttl
        # `namespace` is a physical name segment in front of the whole key (a claim is ownerless, so it leads
        # with `claims/`). `kind_namespace` is a segment after the owner separator, so two record kinds that
        # would otherwise share a name (`turn-<x>`) are still one owner-prefix scan apart. See
        # `_KEY_NAMESPACE_SEPARATOR`.
        self._prefix = f"{namespace}/" if namespace else ""
        self._kind_namespace = kind_namespace
        # Blob NAMES only (never entries), cached per listing prefix for `_index_ttl`: "" is the whole
        # container (ownerless/legacy names), "p1__" is one persona's owner-scoped names. A miss falls back
        # to a fresh listing, so a record another replica wrote is still found.
        self._names_cache: dict[str, tuple[tuple[str, ...], float]] = {}
        # event_id -> owner for records THIS process wrote. A retry in the same process (the common case:
        # write, then read back) resolves the owner-scoped name without listing at all. Cleared wholesale
        # past a bound, because it is an optimisation and must not grow without limit.
        self._owner_hints: dict[str, str] = {}
        self._index_lock = Lock()

    def _uri(self, name: str) -> str:
        """The container-level blob name for a key-relative name in this storage's namespace."""
        return f"{self._prefix}{name}"

    @staticmethod
    def _owner_of(entry: Mapping) -> str | None:
        owner = entry.get("persona_id")
        return str(owner) if owner else None

    def _record_name(self, event_id: str, persona_id: str | None = None) -> str:
        """The key-relative blob name one record is stored at, inside this storage's key namespace.

        A namespaced storage puts its segment after the owner separator, so `<persona>__chat/<event_id>.json`
        is a different key space from `<persona>__<event_id>.json`: a caller-supplied `event_id` cannot
        contain `/`, so the un-namespaced name can never spell the namespaced one. An ownerless name (a
        claim) leads with its namespace instead, because there is no owner prefix to sit behind.
        """
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event_id):
            raise ValueError("event_id must contain 1-128 letters, digits, '_' or '-'")
        namespace = f"{self._kind_namespace}{_KEY_NAMESPACE_SEPARATOR}" if self._kind_namespace else ""
        if persona_id is None:
            return f"{namespace}{event_id}.json"
        # The persona id comes from the corpus (validated at import), but it becomes a blob name here, so
        # it is checked against the same alphabet the event id uses rather than trusted.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", persona_id) or ".." in persona_id:
            raise ValueError("persona_id must be a safe single blob-name segment")
        return f"{persona_id}{_OWNER_SEPARATOR}{namespace}{event_id}.json"

    def _classify(self, name: str) -> tuple[str, str | None]:
        """`(event_id, persona_id or None)` for one key-relative blob name; `("", None)` for anything else.

        Supported shapes are `<event_id>.json` (ownerless), `<persona_id>__<event_id>.json` (owner-scoped),
        and, for a namespaced storage, `<persona_id>__<namespace>/<event_id>.json`. A name whose namespace
        segment is not THIS storage's is not this storage's record shape, so `claims/...` is never a record
        of a record storage and one chat kind's name is never another's. Anything unrecognised is ignored
        rather than guessed at, so a stray blob cannot be attributed to a person.

        KNOWN LIMITATION, deliberately not fixed here: a **legacy flat** name whose event id itself contains
        `__` is genuinely two-valued. `evt__1.json` is either persona `evt` with event `1` (a name this
        revision writes) or the ownerless record `evt__1` (a name a pre-owner-scoped revision wrote) -- the
        name alone cannot say which, and this method keeps the owner-scoped reading, so it reports
        `("1", "evt")`. The consequence is specific and bounded: `evt__1` is still readable by id
        (`feedback_event("evt__1")` finds it through the legacy candidate scan) but INVISIBLE to
        `enumerate_by_owner` / `feedback(persona)` for its real persona, and a read scoped to a persona named
        `evt` would filter it out by its own `persona_id` (it is never returned to, or leaked at, the wrong
        person). It affects only feedback blobs written before the owner segment existed.

        The only decider is what the record itself says, which means fetching it -- and the only names to
        fetch are the ones that would otherwise be attributed to an owner, i.e. a whole-container listing
        per owner-scoped read. That is exactly the O(container) drain and cross-persona read that L4 removed
        (`test_a_personas_blob_read_does_not_download_another_personas_records`), so the cheap-looking fixes
        all trade this invisible-and-unleaked legacy case for a live privacy/performance regression. A
        "does the owner segment name a written owner?" heuristic does not help either: a persona named `evt`
        that has not written yet has the same shape, which only moves the invisibility. Left as is, with
        this note, per the review's own "residual stated" option.
        """
        if not name.endswith(".json"):
            return "", None
        stem = name[:-5]
        if _OWNER_SEPARATOR not in stem:
            if _KEY_NAMESPACE_SEPARATOR in stem:
                return "", None
            return stem, None
        persona_id, _, rest = stem.partition(_OWNER_SEPARATOR)
        if _KEY_NAMESPACE_SEPARATOR not in rest:
            return rest, persona_id or None
        namespace, _, event_id = rest.partition(_KEY_NAMESPACE_SEPARATOR)
        if not self._kind_namespace or namespace != self._kind_namespace:
            return "", None
        return event_id, persona_id or None

    def _list_names(self, prefix: str | None = None) -> list[str]:
        full = f"{self._prefix}{prefix or ''}"
        try:
            blobs = (self._container.list_blobs(name_starts_with=full) if full
                     else self._container.list_blobs())
        except TypeError:  # a stand-in/older client without the keyword: list and filter here
            blobs = [blob for blob in self._container.list_blobs()
                     if not full or blob.name.startswith(full)]
        names = [str(blob.name) for blob in blobs]
        if not self._prefix:
            return names
        # Names are returned relative to this storage's namespace: the namespace is a container-level
        # implementation detail, not part of a record's key.
        return [name[len(self._prefix):] for name in names if name.startswith(self._prefix)]

    def _evict_names(self, *prefixes: str) -> None:
        """Drop the cached listings a write just invalidated. Caller holds `_index_lock`.

        The cache is what makes an owner-scoped read cheap, and it is also what made a read-write-read
        return the listing from BEFORE the write: `create_if_absent` updated `_owner_hints` (which serves
        `read_by_id`) but left `_names_cache` alone, so `enumerate_by_owner` kept answering from the stale
        listing for up to `_index_ttl` seconds. On the Blob backend production uses, the presenter who
        clicked *accept* then reloaded saw an empty decisions page. Only the prefixes this write could
        have changed are evicted: the owner-scoped prefix it wrote under, and (for an ownerless write) the
        whole-container prefix the flat listing is cached under. Another persona's cached listing is a
        different key and is deliberately left alone -- a write cannot change it, and that cache is what
        keeps a colleague's read from costing a listing.
        """
        for prefix in prefixes:
            self._names_cache.pop(prefix, None)

    def _names_for(self, prefix: str) -> list[str]:
        """Blob names under `prefix` (`""` = all), cached for `_index_ttl`.

        One bounded listing serves every read in the window, which is what keeps a legacy flat name and a
        by-id lookup from paying a container listing per call. The cache holds NAMES; entries are always
        downloaded per persona. A write under this storage evicts the prefixes it could have changed (see
        `_evict_names`), so a read that follows a successful write sees it rather than waiting out the TTL.
        """
        with self._index_lock:
            cached = self._names_cache.get(prefix)
            if cached is not None and monotonic() - cached[1] < self._index_ttl:
                return list(cached[0])
            names = tuple(self._list_names(prefix or None))
            self._names_cache[prefix] = (names, monotonic())
            return list(names)

    def _flat(self) -> list[str]:
        """The ownerless (legacy feedback) blob names; a namespaced or stray name is not a record shape.

        A legacy flat name whose id contains `__` is classified as owner-scoped and so does not appear here
        -- the two-valued case `_classify` documents, left as a stated residual rather than fixed by
        fetching every candidate.
        """
        return [name for name in self._names_for("")
                if self._classify(name)[0] and self._classify(name)[1] is None]

    @staticmethod
    def _decode(data: bytes) -> dict:
        try:
            entry = dict(json.loads(data))
            entry["recorded_at"] = datetime.fromisoformat(entry["recorded_at"])
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            # A deeply nested blob is "invalid stored feedback" like any other; letting `RecursionError`
            # escape here would make one planted blob 500 the whole read instead of being refused.
            raise ValueError("invalid stored feedback") from exc
        if entry["recorded_at"].tzinfo is None or not entry.get("event_id"):
            raise ValueError("stored feedback must have a timezone-aware timestamp and event_id")
        return entry

    def _download(self, name: str) -> dict:
        return self._decode(self._container.get_blob_client(self._uri(name)).download_blob().readall())

    def create_if_absent(self, event_id: str, entry: dict) -> bool:
        persona_id = self._owner_of(entry)
        blob = self._container.get_blob_client(self._uri(self._record_name(event_id, persona_id)))
        payload = {**entry, "recorded_at": entry["recorded_at"].isoformat()}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            blob.upload_blob(
                data,
                overwrite=False,
                content_settings=ContentSettings(content_type="application/json"),
            )
        except ResourceExistsError:
            return False
        with self._index_lock:
            # The listing cache is now wrong about this write in BOTH directions: an owner-scoped write
            # appears in that persona's prefix, and an ownerless one (a claim) in the whole-container
            # prefix the flat list is built from. Evict exactly those, so the first read after a
            # successful write sees it instead of the pre-write listing held for `_index_ttl`.
            self._evict_names(*self._prefixes_for(persona_id))
            if persona_id is not None:
                if len(self._owner_hints) >= _OWNER_HINT_LIMIT:
                    self._owner_hints.clear()
                self._owner_hints[event_id] = persona_id
        return True

    def _prefixes_for(self, persona_id: str | None) -> tuple[str, ...]:
        """The `_names_cache` prefixes one write by this owner can have changed.

        An owner-scoped record changes only `<persona>__`; an ownerless one (a claim) changes the
        whole-container prefix `""` the flat listing is cached under. Nothing else moves, which is why a
        different persona's cached listing -- and the point of the cache -- survives a write.
        """
        return (f"{persona_id}{_OWNER_SEPARATOR}",) if persona_id is not None else ("",)

    def read_by_id(self, event_id: str) -> dict | None:
        """Read one event by id inside THIS storage's namespace.

        The ownerless name is one `exists()` and is how a decision claim is read back (the claim storage is
        namespaced, so no record id can address it). An owner-scoped record's owner is recovered from the
        write hint when this process wrote it, else from the cached blob-name listing; only the matching
        name is fetched, and the fetched entry's `event_id` is checked in case two owners share an id. No
        other persona's bytes are downloaded.

        This read is deliberately NOT used to acknowledge a write: it is unscoped, so a legacy ownerless
        claim blob would still be a candidate. `Store.feedback_acknowledgement` reads the caller's own
        owner-scoped record exactly instead.
        """
        blob = self._container.get_blob_client(self._uri(self._record_name(event_id)))
        if blob.exists():
            return self._decode(blob.download_blob().readall())
        hinted = self._owner_hint(event_id)
        candidates = [self._record_name(event_id, hinted)] if hinted else self._candidates_for(event_id)
        for candidate in candidates:
            if not self._container.get_blob_client(self._uri(candidate)).exists():
                continue
            found = self._download(candidate)
            if found.get("event_id") == event_id:
                return found
        return None

    def read_create_slot(self, event_id: str, persona_id: str | None = None) -> dict | None:
        """The record this storage's own `create_if_absent(event_id, ...)` would collide with, or None.

        The name is constructed from the keying the write itself uses, so a caller can decide whether the
        write would conflict BEFORE it reserves anything (see `Store.add_feedback`). It never lists: the
        persona is known from the entry, so the exact owner-scoped name is available.
        """
        name = self._record_name(event_id, persona_id)
        blob = self._container.get_blob_client(self._uri(name))
        if not blob.exists():
            return None
        found = self._decode(blob.download_blob().readall())
        return found if found.get("event_id") == event_id else None

    def read_owned(self, event_id: str, persona_id: str) -> dict | None:
        """One record by its exact owner-scoped name, or None.

        This is the acknowledgement read: the name is built from the caller's own owner and no fallback is
        attempted, so it can only ever return that caller's record — never an ownerless claim and never
        another persona's record.
        """
        if not persona_id:
            return None
        name = self._record_name(event_id, persona_id)
        blob = self._container.get_blob_client(self._uri(name))
        if not blob.exists():
            return None
        found = self._decode(blob.download_blob().readall())
        if found.get("event_id") != event_id or found.get("persona_id") != persona_id:
            return None
        return found

    def _owner_hint(self, event_id: str) -> str | None:
        with self._index_lock:
            return self._owner_hints.get(event_id)

    def _candidates_for(self, event_id: str) -> list[str]:
        """Owner-scoped names for one event id, from the cached listing; none is downloaded here.

        This is the only read that must consider names it cannot construct, because an owner-scoped name
        needs the owner. The listing is bounded and cached, so the by-id path does not re-list per call.

        Both the namespaced form (`<persona>__chat/<event_id>.json`) and the legacy un-namespaced form
        (`<persona>__<event_id>.json`) are matched, so a record written by an earlier revision is still
        found by id after a restart. A namespaced storage's own namespace is the only one it claims; a
        legacy match is kind-verified by the caller before it can be treated as this kind's record.
        """
        wanted = [f"{_OWNER_SEPARATOR}{event_id}.json"]
        if self._kind_namespace:
            wanted.append(
                f"{_OWNER_SEPARATOR}{self._kind_namespace}{_KEY_NAMESPACE_SEPARATOR}{event_id}.json")
        return [name for name in self._names_for("")
                if any(name.endswith(suffix) for suffix in wanted)]

    def read_ownerless(self, event_id: str) -> dict | None:
        """The exact ownerless name for one id, or None. No listing, no owner fallback.

        This is how a pre-namespace decision claim is found: the old revision stored it at the ownerless
        `<claim_key>.json` (`Store._claim_decision` explains the kind check). It is deliberately narrower
        than `read_by_id`, which also considers owner-scoped names: a legacy claim is ownerless by
        construction, so a caller's own record at the same id must not be read here.
        """
        blob = self._container.get_blob_client(self._uri(self._record_name(event_id)))
        if not blob.exists():
            return None
        return self._decode(blob.download_blob().readall())

    def _entries_for(self, persona_id: str) -> list[dict]:
        """Entries for one persona: the owner-scoped names, plus any legacy ownerless name that says it.

        `name_starts_with` is a prefix of the persona id, so the listing itself returns only names that
        could belong to this persona; a legacy ownerless name is fetched only because it cannot be
        attributed from its name at all, and only when the cached flat list is non-empty.
        """
        entries: list[dict] = []
        for name in self._names_for(f"{persona_id}{_OWNER_SEPARATOR}"):
            entries.append(self._download(name))
        for name in self._flat():
            entries.append(self._download(name))
        return [entry for entry in entries if entry.get("persona_id") == persona_id]

    def enumerate_by_owner(self, persona_id: str) -> list[dict]:
        """One persona's entries, ordered by server time then event id.

        The ordering is applied HERE for both backends: `Store.feedback` relies on it, and the blob backend
        now scans owner-scoped names rather than one sorted container listing, so the order must not be
        left to the listing.
        """
        entries = [entry for entry in self._entries_for(persona_id)
                   if entry.get("persona_id") == persona_id]
        return sorted(entries, key=lambda entry: (entry["recorded_at"], entry["event_id"]))

    def read_all(self) -> list[dict]:
        entries = []
        for name in self._list_names():
            if not self._classify(name)[0]:
                continue  # another namespace's name (a claim) is not a record
            entries.append(self._download(name))
        return sorted(entries, key=lambda entry: (entry["recorded_at"], entry["event_id"]))

    def delete_if_matching(self, event_id: str, predicate) -> bool:
        """Delete the exact blob at `event_id` only when `predicate(entry)` still says it is ours.

        No listing and no owner fallback: a release may touch only the claim key this attempt took. The
        blob is read first and the predicate is checked against what it actually holds, so a claim another
        attempt has since taken (a different `decision_key`) or a record that really was written is never
        deleted. Absent is False, and that is safe for the caller: a name that does not exist is not a
        claim this attempt still holds.
        """
        blob = self._container.get_blob_client(self._uri(self._record_name(event_id)))
        try:
            entry = self._decode(blob.download_blob().readall())
        except ResourceNotFoundError:
            return False
        if not predicate(entry):
            return False
        try:
            blob.delete_blob()
        except ResourceNotFoundError:
            return False  # someone else already removed it: the slot is no longer ours either way
        return True


# How long an indexed corpus' encounter catalogue is trusted before it is re-read. Short enough that an
# operator's encounter change appears without a restart, long enough that a busy presenter's requests do not
# re-read it per call. `LUVN_CATALOGUE_TTL_SECONDS` exists for tests and for an operator who wants it instant.
CATALOGUE_TTL_SECONDS = float(os.environ.get("LUVN_CATALOGUE_TTL_SECONDS") or 30.0)


class Store:
    def __init__(
        self,
        personas: list[Persona] | None = None,
        path: Path | None = None,
        blob_container=None,
        encounters: Mapping[str, Encounter] | None = None,
        example_labels: Mapping[str, str] | None = None,
    ):
        """`personas` and `encounters` are the JSON-backed corpus: the people, and the catalogue of lenses over
        them. Neither has a compiled default. A store built from `personas` without `encounters` has an EMPTY
        catalogue (`Store([])` for feedback-only tests), and `example_labels` is the only place such a store
        gets a display name per encounter from, because a persona list carries none; an indexed corpus
        ignores both and reads its own `encounters` table."""
        if path is not None and blob_container is not None:
            raise ValueError("choose either JSONL or Blob feedback storage")
        if personas is None and (encounters is not None or example_labels is not None):
            raise ValueError("encounters and example_labels describe a personas corpus; an indexed corpus carries its own")
        self._delivery_index = None
        # Where the corpus comes from, decided once, here: a configured Azure SQL database, else an
        # imported delivery directory, else the fixture corpus compiled into the image. Both indexed
        # sources resolve ONE person per lookup and expose the same methods, so nothing above this line
        # can tell which it got — which is what makes the source a deployment choice rather than a fork.
        index = None
        if personas is None and os.environ.get("LUVN_SQL_SERVER"):
            from .sqldelivery import open_sql_index  # type: ignore[reportMissingImports]

            index = open_sql_index()
        elif personas is None and os.environ.get("LUVN_DELIVERY_DIR"):
            from .delivery import DeliveryIndex  # type: ignore[reportMissingImports]

            index = DeliveryIndex(Path(os.environ["LUVN_DELIVERY_DIR"]))
        # The operator catalogue (encounter -> person binding) is the ONE part of an indexed corpus that is
        # cached: the picture route resolves an id through it. It is re-read when a short TTL expires, so a
        # new encounter needs no restart. The worklist does not use this cache; see `encounter_metadata`.
        self._catalogue_lock = Lock()
        self._catalogue_read_at = 0.0
        # The briefings cache (`app/briefings.py`): the SQL backend's table, a local backend's memory or none.
        from .briefings import cache_for_local_backend

        self.briefing_cache = index.briefing_cache() if index is not None else cache_for_local_backend()
        if index is not None:
            self._delivery_index = index
            self._personas: Mapping[str, Persona] = index
            self._encounters = index.encounter_index()
            self._example_labels: dict[str, str] = {}
            if not self._encounters:
                index.close()
                raise RuntimeError("indexed delivery requires an encounters.csv owner mapping")
        else:
            source = load_personas() if personas is None else personas
            if not source and personas is None:
                # The image ships no corpus, so this is the state of a deployment that was never pointed at
                # one. Failing here names the two ways to fix it; failing later would be a 503 per request
                # with a message about an encounter id, which is a much longer walk to the same answer.
                raise RuntimeError(
                    "no corpus is configured: set LUVN_SQL_SERVER (and load it with "
                    "`python -m app.load_sql --from-blob`), or set LUVN_DELIVERY_DIR to an imported "
                    "delivery directory. This image intentionally carries no corpus of its own.")
            if len({persona.persona_id for persona in source}) != len(source):
                raise ValueError("duplicate persona ID")
            record_ids = [record.record_id for persona in source for record in persona.records]
            if len(record_ids) != len(set(record_ids)):
                raise ValueError("duplicate record ID")
            source_owners: dict[str, set[str]] = {}
            for persona in source:
                if persona.source_person_id:
                    source_owners.setdefault(persona.source_person_id, set()).add(persona.persona_id)
            if any(len(owners) > 1 for owners in source_owners.values()):
                raise ValueError("source person key maps to multiple people")
            self._personas = {persona.persona_id: persona for persona in source}
            self._encounters = {} if encounters is None else dict(encounters)
            self._example_labels = {} if example_labels is None else dict(example_labels)
        self._feedback_storage = (
            _BlobFeedbackStorage(blob_container)
            if blob_container is not None
            else _JsonlFeedbackStorage(path)
        )
        # Chat turns and reviews are append-only create-if-absent records too, in their own key namespaces.
        # On Blob a feedback `event_id` of `turn-<x>` and a chat turn id of `<x>` used to name the same blob
        # (`<persona>__turn-<x>.json`), so the second write was refused as if the id were its own; each chat
        # kind now carries a namespace segment (`<persona>__chat/<event_id>.json`) that no caller-supplied id
        # can spell (`/` is outside the id alphabet). On JSONL the file separation already makes the key
        # spaces disjoint, and turns and reviews are separated by their `turn-`/`review-` key prefixes, so
        # the two share the one chat file — which is also where records from earlier revisions live.
        chat_path = None if path is None else path.with_name(path.stem + "-chat" + path.suffix)
        self._chat_storage = (
            _BlobFeedbackStorage(blob_container, kind_namespace=CHAT_TURN_KEY_NAMESPACE)
            if blob_container is not None
            else _JsonlFeedbackStorage(chat_path)
        )
        self._review_storage = (
            _BlobFeedbackStorage(blob_container, kind_namespace=CHAT_REVIEW_KEY_NAMESPACE)
            if blob_container is not None
            else self._chat_storage
        )
        # Decision claims get their OWN key space, disjoint from every record key (see CLAIM_NAMESPACE): on
        # Blob a `claims/` name segment no event id can spell, on JSONL a separate file. A claim is an
        # internal uniqueness marker; nothing a caller writes or reads can address one.
        claim_path = None if path is None else path.with_name(path.stem + "-claims" + path.suffix)
        self._claim_storage = (
            _BlobFeedbackStorage(blob_container, namespace=CLAIM_NAMESPACE)
            if blob_container is not None
            else _JsonlFeedbackStorage(claim_path)
        )
        audit_path = None if path is None else path.with_name(path.stem + "-reads" + path.suffix)
        self._audit_storage = (
            _BlobFeedbackStorage(blob_container, namespace=READ_AUDIT_NAMESPACE)
            if blob_container is not None
            else _JsonlFeedbackStorage(audit_path)
        )
        initial_entries = ([entry for entry in self._feedback_storage.read_all()
                            if _record_kind(entry) == KIND_PROPOSAL_FEEDBACK] if path is not None else [])
        self._feedback_count = len(initial_entries)
        self._acknowledged_event_ids = {
            entry["event_id"] for entry in initial_entries if entry.get("event_id")
        }
        self._acknowledgement_lock = Lock()
        # Per-person chat turn sequence, initialised once per process in `next_chat_seq` so a ask does not
        # pay a container listing to find the next number.
        self._chat_seq: dict[str, int] = {}
        self._seq_lock = Lock()

    def personas(self) -> list[Persona]:
        if self._delivery_index is not None:
            raise RuntimeError("whole-delivery enumeration is disabled; resolve one indexed person")
        return list(self._personas.values())

    def persona(self, persona_id: str) -> Persona | None:
        return self._personas.get(persona_id)

    def persona_index(self) -> Mapping[str, Persona]:
        """Validated mapping; imported deliveries resolve one person lazily."""
        return self._personas

    def latest_briefing_built_at(self) -> datetime | None:
        """When the briefings cache was last written to, or `None` when it holds nothing (or cannot hold).

        The day view's honesty line: "prepared last at ..." is only true of a cache that persists. The
        no-op cache answers `None`; the in-process cache answers the newest of its rows; the SQL table
        answers `MAX(built_at)` through the index's own connection. The cache object is asked first, so a
        cache that grows a `latest_built_at()` of its own is used as is.
        """
        cache = self.briefing_cache
        own = getattr(cache, "latest_built_at", None)
        if callable(own):
            return own()
        if not getattr(cache, "persists", False):
            return None
        rows = getattr(cache, "_rows", None)
        if isinstance(rows, dict):
            built = [stored.built_at for stored in rows.values()]
            return max(built) if built else None
        query = getattr(self._delivery_index, "_rows", None)
        if query is None:
            return None
        found = query("SELECT MAX(built_at) FROM briefings")
        value = found[0][0] if found and found[0] else None
        if value is None:
            return None
        return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))

    def appointments_for_day(self, day):
        """The day's appointments from the indexed corpus; a fixture corpus has none."""
        get = getattr(self._delivery_index, "appointments_for_day", None)
        return get(day) if callable(get) else []

    def appointment_days(self, start, end, role=None) -> dict[str, int]:
        get = getattr(self._delivery_index, "appointment_days", None)
        return get(start, end, role) if callable(get) else {}

    def corpus_generation(self) -> str | None:
        """The generation the runtime's data actually came from, when the backing index can name one.

        Only a SQL-backed index can: it exposes the `load_state.generation` the loader stamped when it loaded
        the corpus this index reads (ADR-0025 §5), queried back from the same database — not re-derived from
        the application image. The delivery-directory and compiled-fixture backends carry no independent
        generation, so this is `None` for them: an honest "not reachable", not a proxy value.
        """
        get_generation = getattr(self._delivery_index, "corpus_generation", None)
        if not callable(get_generation):
            return None
        generation = get_generation()
        return str(generation) if generation is not None else None

    def data_layer_ready(self) -> bool | None:
        """Whether the data layer this store reads can answer right now.

        Three answers, and the difference matters: `True` when a probe exists and succeeded, `False` when a
        probe exists and failed (the database is unreachable), and `None` when there is nothing to probe
        because the runtime reads a local delivery directory or compiled fixtures.

        `None` must not be treated as a failure. The readiness route turns only `False` into a 503, so a
        fixture-backed deployment is not made unhealthy by a probe that does not apply to it, while a
        SQL-backed one whose database is down stops receiving traffic instead of serving briefings it cannot
        build. Before this existed, `/health/ready` checked only that the SPA's `index.html` was present, so it
        reported ready with the database unreachable.
        """
        probe = getattr(self._delivery_index, "probe", None)
        if not callable(probe):
            return None
        try:
            probe()
        except Exception:  # noqa: BLE001 -- any failure to answer is "not ready"; the route never sees why
            return False
        return True

    def _refresh_catalogue(self) -> None:
        """Re-read the encounter binding when its TTL has expired, for an INDEXED corpus only.

        A failure keeps the binding it already has: a database hiccup must not break the picture route, where
        a corpus that genuinely has no encounters is a fact and is served as one. The two are told apart by
        whether the read raised.
        """
        if self._delivery_index is None:
            return
        now = monotonic()
        if now - self._catalogue_read_at < CATALOGUE_TTL_SECONDS:
            return
        with self._catalogue_lock:
            if monotonic() - self._catalogue_read_at < CATALOGUE_TTL_SECONDS:
                return
            try:
                encounters = self._delivery_index.encounter_index()
            except Exception:  # noqa: BLE001 - any read failure keeps the binding the app already serves
                self._catalogue_read_at = monotonic()
                return
            self._encounters = encounters
            self._catalogue_read_at = monotonic()

    def encounter_index(self):
        self._refresh_catalogue()
        return self._encounters

    def encounter_order(self) -> tuple[str, ...]:
        self._refresh_catalogue()
        return tuple(self._encounters)

    def encounter_metadata(self) -> list[EncounterMetadata]:
        """Every encounter's worklist metadata, in id order. Reads no person and no record.

        An indexed corpus answers from its own `encounters JOIN people` table on every call, so the worklist
        is current without waiting for the binding's TTL and never depends on the person cache. The fixture
        corpus answers from the catalogue it was built with, and the only labels it has are the
        `example_labels` it was given; an encounter without one is served with `example_label=None`.
        """
        if self._delivery_index is not None:
            return self._delivery_index.encounter_metadata()
        return [EncounterMetadata(
            encounter_id=encounter.encounter_id, source_person_id=encounter.source_person_id,
            role=encounter.role, purpose=encounter.purpose,
            example_label=self._example_labels.get(encounter.encounter_id), demo_name=None,
            consent_holders=encounter.consent_holders, life_stage=encounter.life_stage)
            for _key, encounter in sorted(self._encounters.items())]

    def _legacy_claim(self, claim_key: str) -> dict | None:
        """The pre-namespace claim at the ownerless record name, or None if that name is not a claim.

        Before claims got their own key space the claim was written into a RECORD storage at its
        deterministic ownerless key (`<claim_key>.json` on Blob), so records written by an earlier revision
        have no marker in `claims/` and the one-decision rule would silently weaken for them. Both record
        storages are consulted, because the old code claimed a proposal through the feedback storage and a
        suggestion through the chat storage — one shared container on Blob, two files on JSONL. That
        ownerless name is shared with legacy ownerless FEEDBACK records, which is why this is kind-verified
        rather than shape-verified: only a record whose own `kind` is the claim kind is a claim here, and a
        legacy feedback record at the same name is not mistaken for one (and is never returned as one — this
        method is used only for the yes/no decision, never by a read that yields bytes to a caller).

        The claim's stored `decision_key` binds it to its subject and actor exactly as the new claim's does,
        so a claim found at the name is only THIS subject's claim when the keys match; `_claim_decision`
        treats a mismatch as "another decision already owns the subject".
        """
        for storage in (self._feedback_storage, self._chat_storage):
            entry = storage.read_ownerless(claim_key)
            if entry is not None and _record_kind(entry) == KIND_DECISION_CLAIM:
                return entry
        return None

    def _decision_record_exists(self, decision_key: str, persona_id: str | None) -> bool:
        """Is there a real decision record this attempt's `decision_key` refers to?

        Asked before a claim is RELEASED and never to decide a write. Both record storages are consulted,
        because the key is one half of an `event_id`/`review_id` pair whose kind is not known here (a
        proposal claim's key is a raw `event_id`, a suggestion claim's key is `review-<id>`), and a record
        the caller did not write may be ownerless (legacy). It is the guard that keeps a failed attempt's
        release from ever removing a slot a successful write already filled.

        Consequential on Blob, where the owner is part of the key: an owner-scoped name is built from the
        owner this attempt wrote under, so a same-provider retry under a different persona cannot be
        mistaken for this one's record. `read_ownerless` is the legacy shape (`<event_id>.json`).
        """
        if persona_id is not None and self._feedback_storage.read_owned(decision_key, persona_id) is not None:
            return True
        if persona_id is not None and self._chat_storage.read_owned(decision_key, persona_id) is not None:
            return True
        return (self._feedback_storage.read_ownerless(decision_key) is not None
                or self._chat_storage.read_ownerless(decision_key) is not None)

    def _release_claim(self, claim_key: str, decision_key: str, persona_id: str | None) -> None:
        """Give back a decision claim this attempt took and could not turn into a decision record.

        A claim whose record was never written used to keep the slot closed, and that fails CLOSED rather
        than double-deciding — but it also made the refusal a lie: a retry under a fresh `event_id` was
        told "this proposal already has a decision" while `proposal_decisions` returned nothing. The claim
        is therefore released, but only on the narrowest terms:

        * only after a record write that RAISED (a create that returned False means a record is there);
        * only when neither record storage holds a record for `decision_key` (see
          `_decision_record_exists`), so a successful concurrent write can never lose its slot;
        * only when the claim still carries THIS attempt's `decision_key` (the predicate), so a claim
          another attempt has taken since is not deleted;
        * and the legacy claim is left alone entirely: it was written by an earlier revision, this attempt
          never took it, and a release that acknowledged a decision nobody can find would be worse than the
          closed slot.

        A failed conditional delete is a no-op, not an error: the only thing being given up is a slot this
        attempt already failed to use, and the caller has already had the write's own exception.
        """
        existing = self._claim_storage.read_by_id(claim_key)
        if existing is None:
            return  # never took a new claim, or another attempt already released it
        if existing.get("decision_key") != decision_key:
            return  # another attempt has taken the slot; it is not ours to release
        if self._decision_record_exists(decision_key, persona_id):
            return
        try:
            released = self._claim_storage.delete_if_matching(
                claim_key, lambda row: row.get("decision_key") == decision_key)
        except Exception:  # noqa: BLE001 - the write's own exception is the one the caller must see
            return
        if released:
            log.warning("released a decision claim whose record was not written")

    def _claim_decision(self, claim_key: str, decision_key: str, persona_id: str | None,
                        conflict_message: str) -> None:
        """Claim the one decision slot for a subject, O(1), before the decision record is written.

        The claim is a conditional create in the claim namespace (see `CLAIM_NAMESPACE`), so two concurrent
        decisions for the same subject cannot both succeed. A retry under the same `decision_key` is let
        through (the caller's own idempotency check decides whether the content matches); a different one is
        refused. A claim whose decision record was never written is released by its own writer on the
        failure path (`Store._release_claim`), so a transient storage failure does not strand the actor —
        while a claim that HAS a record keeps the slot closed, which is what keeps one decision one
        decision. That is exactly why the event-identity check runs BEFORE this call: a request that is
        going to be refused for a conflicting id must not take the slot first.

        `decision_key` is the caller's `event_id` (or review id): it is what makes an exact retry idempotent
        while a second, different decision under a fresh id is refused. The actor's `persona_id` is carried
        only so the record-existence guard can build an owner-scoped name on the Blob backend; the subject
        already binds the actor.

        The legacy name is consulted FIRST, so a subject already decided by an earlier revision refuses a
        second decision without taking a new claim: a request that is going to be refused costs the caller
        nothing, exactly as a conflicting `event_id` does. The two key spaces and the kind check together
        keep a claim from ever being read as a record: the new claim lives at `claims/<key>.json` (a `/`
        segment no caller id can spell), a legacy claim lives ownerless at `<key>.json` and is only ever
        treated as a claim when its `kind` says so, and every record read either filters on kind
        (`feedback`, `feedback_event`, `chat_turns`, `chat_reviews`) or builds the caller's own exact
        owner-scoped name (`read_owned`), so neither a new nor a legacy claim can be returned as a record.
        """
        legacy = self._legacy_claim(claim_key)
        if legacy is not None and legacy.get("decision_key") != decision_key:
            raise DecisionConflict(conflict_message)
        claim = {"event_id": claim_key, "kind": KIND_DECISION_CLAIM, "decision_key": decision_key,
                 "recorded_at": datetime.now(timezone.utc)}
        if self._claim_storage.create_if_absent(claim_key, claim):
            return
        existing = self._claim_storage.read_by_id(claim_key)
        if existing is None or existing.get("decision_key") != decision_key:
            raise DecisionConflict(conflict_message)

    def _take_decision_or_proceed(self, claim_key: str, decision_key: str, persona_id: str | None,
                                  conflict_message: str) -> None:
        """`_claim_decision`, but a claim with NO record behind it is released and RETRIED, not lied about.

        The failure mode this closes: claim, then the decision-record upload fails transiently, so the
        route answers 503 "not saved" — while the claim keeps the slot. The actor retries with a new
        `event_id` and is told "already decided" although no decision exists anywhere, and the decisions
        read agrees there is none: the route and the read disagreed about the state of the world.

        The order is deliberate. The retry is attempted only after a claim is shown to be bound to a
        DIFFERENT `decision_key` than the one being written — i.e. only the failed-attempt shape can reach
        it — and only `_release_claim` (read, guard for an existing record, then a conditional delete) may
        remove the claim. So:

        * a genuine second decision under a fresh id DOES find a record for its key, is NOT released, and
          is refused with `conflict_message` as before;
        * a fresh id after a failed write finds no record, releases the orphaned claim, and succeeds;
        * no path releases a claim whose record exists, so a decision cannot be double-written.
        """
        try:
            self._claim_decision(claim_key, decision_key, persona_id, conflict_message)
        except DecisionConflict:
            if self._decision_record_exists(decision_key, persona_id):
                raise
            self._release_claim(claim_key, decision_key, persona_id)
            self._claim_decision(claim_key, decision_key, persona_id, conflict_message)

    def add_feedback(self, entry: dict, event_id: str | None = None) -> int:
        stable_id = event_id or uuid4().hex
        # One decision per proposal *per actor*, enforced by an O(1) claim rather than by enumerating the
        # container: the write path must stay a read-by-id path (a full read is not affordable on the hosted
        # backend). The actor is part of the subject because several professional roles legitimately see the
        # same proposal through their own lens, and the audit records who decided: one professional's
        # decision must not consume the only slot another professional still needs. A shared slot used to
        # make whichever lens clicked first the only one that could ever decide.
        encounter_id = entry.get("encounter_id")
        proposal_id = entry.get("proposal_id")
        actor_id = entry.get("actor_id") or ""
        persona_id = entry.get("persona_id")
        stamped = {**entry, "kind": KIND_PROPOSAL_FEEDBACK, "event_id": stable_id,
                   "recorded_at": datetime.now(timezone.utc)}
        # Validate the event identity BEFORE the slot is reserved. The old order claimed first, so a caller
        # reusing an id already written with different content had their one slot consumed by a request that
        # was then refused: their next attempt under a fresh id answered "already has a decision" although
        # they had recorded nothing, and a used id and a free id answering differently turned the route into
        # an existence oracle for the caller's own burned state. A conflicting id must record nothing and
        # lose nothing. The slot read mirrors the write's own keying (`read_create_slot`), so it is a hit
        # exactly when the atomic create below would be refused.
        existing = self._feedback_storage.read_create_slot(stable_id, persona_id)
        if existing is not None and _event_content(existing) != _event_content(stamped):
            raise ValueError("event_id already exists with different feedback")
        if encounter_id and proposal_id:
            self._take_decision_or_proceed(
                _decision_claim_key(f"proposal\0{encounter_id}\0{proposal_id}\0{actor_id}"),
                stable_id, persona_id, "this proposal already has a decision")
        try:
            created = self._feedback_storage.create_if_absent(stable_id, stamped)
        except Exception:
            # The record was not written, so the claim just taken must not outlive it: otherwise the
            # caller's next attempt is told a decision exists that does not (`_release_claim` releases it
            # only when nothing is stored for `stable_id`). The exception is re-raised unchanged, so the
            # route still answers 503 and nothing is acknowledged.
            if encounter_id and proposal_id:
                self._release_claim(
                    _decision_claim_key(f"proposal\0{encounter_id}\0{proposal_id}\0{actor_id}"),
                    stable_id, persona_id)
            raise
        if not created:
            # The pre-check said the slot was free but the atomic create lost a race with another writer.
            existing = self._feedback_storage.read_create_slot(stable_id, persona_id)
            if existing is None:
                raise RuntimeError("feedback event disappeared after create-if-absent")
            if _event_content(existing) != _event_content(stamped):
                raise ValueError("event_id already exists with different feedback")
        with self._acknowledgement_lock:
            if stable_id not in self._acknowledged_event_ids:
                self._acknowledged_event_ids.add(stable_id)
                self._feedback_count += 1
            return self._feedback_count

    def feedback_event(self, event_id: str) -> dict | None:
        """Read one acknowledged event by id, filtered to proposal feedback, without enumerating.

        This is the unscoped by-id read, kept for legacy ownerless feedback (a real record) and for callers
        that only have an id. It is filtered on the record's own kind, so a claim can never be returned as
        if it were feedback — legacy claim blobs are ownerless and share this read's shape. The write ROUTE
        does not acknowledge through this: `feedback_acknowledgement` reads the caller's own owner-scoped
        record exactly.
        """
        entry = self._feedback_storage.read_by_id(event_id)
        if entry is None or _record_kind(entry) != KIND_PROPOSAL_FEEDBACK:
            return None
        return entry

    def feedback_acknowledgement(self, event_id: str, persona_id: str, actor_id: str,
                                 encounter_id: str) -> dict | None:
        """The caller's OWN just-written feedback record, read by its exact owner-scoped key.

        An acknowledgement must never be produced by an unscoped by-id read: `event_id` is caller-supplied
        and can equal another actor's deterministic claim key, and the old acknowledgement returned that
        claim's `recorded_at` as the caller's own. This read builds the one name the caller's write could
        have used and verifies the record is proposal feedback for this persona, this actor and this
        encounter before it can become an acknowledgement. A read that does not verify can disclose.
        """
        record = self._feedback_storage.read_owned(event_id, persona_id)
        if record is None or _record_kind(record) != KIND_PROPOSAL_FEEDBACK:
            return None
        if record.get("actor_id") != actor_id or record.get("encounter_id") != encounter_id:
            return None
        return record

    def feedback(self, persona_id: str) -> list[dict]:
        """Explicitly enumerate feedback for one persona, ordered by server time then event ID.

        The result is filtered to proposal feedback by the record's own kind, so a chat turn or review that
        happens to carry the same `persona_id` can never be read back as feedback.
        """
        return [entry for entry in self._feedback_storage.enumerate_by_owner(persona_id)
                if _record_kind(entry) == KIND_PROPOSAL_FEEDBACK]

    # --- chat ---------------------------------------------------------------------------------------
    # A turn is stored with the question the professional typed and the *ids* of the evidence it cited, never
    # the statement text; the record content is referenced, never copied, so the audit is not wider than the
    # source. The same create-if-absent semantics make a retried request idempotent rather than a second turn.
    def add_chat_turn(self, entry: dict, turn_id: str, *, content: dict) -> bool:
        """Record a turn; `content` is the semantic identity used to detect a conflicting retry.

        Returns `True` when this call created the turn and `False` when an identical turn already existed, so
        the route can return the *stored* turn on the idempotent path rather than one it just recomputed.

        The stored entry keeps the turn's ids, classes, labels, digest, actor and outcome so a review can be
        validated later. A turn carries its own timestamp, so comparing the entry wholesale would make every
        retry look like a conflict; the comparison is therefore over `content`, which the caller derives from
        the stable part of the request (question, digest, turn id, actor) and not from any generated prose.
        """
        key = f"turn-{turn_id}"
        stamped = {**entry, "kind": KIND_CHAT_TURN, "turn_id": turn_id, "event_id": key, "content": content,
                   "recorded_at": datetime.now(timezone.utc)}
        if self._chat_storage.create_if_absent(key, stamped):
            return True
        existing = self._chat_storage.read_by_id(key)
        if existing is None or existing.get("content") != content:
            raise ValueError("turn_id already exists with different content")
        return False

    def add_read_audit(self, entry: dict, event_id: str) -> None:
        """Append one briefing-read audit row. Create-if-absent on a fresh id: a row is never overwritten.

        Raises if the write cannot be made durable. The caller must then withhold the read, because an
        unaudited release of a person's briefing is not an outcome the route may produce.
        """
        stamped = {**entry, "kind": KIND_READ_AUDIT, "event_id": event_id,
                   "recorded_at": datetime.now(timezone.utc)}
        if not self._audit_storage.create_if_absent(event_id, stamped):
            raise ValueError("audit event id already exists")

    def read_audit(self) -> list[dict]:
        return self._audit_storage.read_all()

    def chat_turn(self, turn_id: str) -> dict | None:
        """The stored turn, kind-verified.

        `read_by_id` also considers the legacy un-namespaced name (a record an earlier revision wrote), and
        that name space is shared with proposal feedback: a feedback `event_id` of `turn-<x>` can sit at
        `<persona>__turn-<x>.json`. The kind check is what keeps such a record from being returned as a turn.
        """
        entry = self._chat_storage.read_by_id(f"turn-{turn_id}")
        return entry if entry is not None and _record_kind(entry) == KIND_CHAT_TURN else None

    def next_chat_seq(self, persona_id: str) -> int:
        """The next turn sequence for one person, without enumerating the container per call.

        The counter is initialised once per process from the person's stored turns and then advanced under a
        lock, so two concurrent asks in one process get distinct numbers and no ask after the first pays a
        container listing.

        `seq` is display ordering, not identity: a turn is keyed by `turn_id`. Across replicas the counters
        are independent, so two turns can and will share a `seq`. Making it globally unique needs an atomic
        per-person counter in the shared store (an increment that the storage returns), which this demo's
        storage seam does not provide — so the limitation is stated here rather than silently wrong.
        """
        with self._seq_lock:
            if persona_id not in self._chat_seq:
                self._chat_seq[persona_id] = len(self.chat_turns(persona_id))
            self._chat_seq[persona_id] += 1
            return self._chat_seq[persona_id]

    def add_chat_review(self, entry: dict, review_id: str) -> None:
        key = f"review-{review_id}"
        # One decision per suggestion, enforced by an O(1) claim (see `_claim_decision`): a second, different
        # decision for the same (turn, suggestion) is refused, while a retry under the same review id stays
        # idempotent below. As on the proposal path, the review identity is checked BEFORE the claim, so a
        # `review_id` already used with different content is refused without consuming the caller's slot.
        turn_id = entry.get("turn_id")
        suggestion_id = entry.get("suggestion_id")
        persona_id = entry.get("persona_id")
        stamped = {**entry, "kind": KIND_CHAT_REVIEW, "review_id": review_id, "event_id": key,
                   "recorded_at": datetime.now(timezone.utc)}
        existing = self._review_storage.read_create_slot(key, persona_id)
        if existing is not None and _event_content(existing) != _event_content(stamped):
            raise ValueError("review_id already exists with different content")
        if turn_id and suggestion_id:
            self._take_decision_or_proceed(
                _decision_claim_key(f"suggestion\0{turn_id}\0{suggestion_id}"),
                review_id, persona_id, "this suggestion already has a decision")
        try:
            created = self._review_storage.create_if_absent(key, stamped)
        except Exception:
            # Same contract as `add_feedback`: a review record that was never written must not leave its
            # claim behind to refuse the caller's next attempt with a decision that does not exist.
            if turn_id and suggestion_id:
                self._release_claim(
                    _decision_claim_key(f"suggestion\0{turn_id}\0{suggestion_id}"),
                    review_id, persona_id)
            raise
        if not created:
            existing = self._review_storage.read_create_slot(key, persona_id)
            if existing is None or _event_content(existing) != _event_content(stamped):
                raise ValueError("review_id already exists with different content")

    def add_prefill_review(self, entry: dict, review_id: str) -> None:
        """Append one verdict on a pre-filled form answer. Create-if-absent under `prefill-review-<id>`.

        The same retry contract as `add_chat_review`: the same id with the same content is idempotent, the
        same id with different content is refused. There is no one-decision claim here on purpose -- a
        professional may confirm an answer, then edit it, and both verdicts stay on the record in order; the
        read side returns them all, newest last. The entry is the audit row: actor, encounter, form, question
        and verdict, and never the record the answer was prefilled from.
        """
        key = f"prefill-review-{review_id}"
        persona_id = entry.get("persona_id")
        stamped = {**entry, "kind": KIND_PREFILL_REVIEW, "review_id": review_id, "event_id": key,
                   "recorded_at": datetime.now(timezone.utc)}
        existing = self._review_storage.read_create_slot(key, persona_id)
        if existing is not None and _event_content(existing) != _event_content(stamped):
            raise ValueError("review_id already exists with different content")
        if not self._review_storage.create_if_absent(key, stamped):
            existing = self._review_storage.read_create_slot(key, persona_id)
            if existing is None or _event_content(existing) != _event_content(stamped):
                raise ValueError("review_id already exists with different content")

    def prefill_review(self, review_id: str) -> dict | None:
        """The stored pre-fill verdict, kind-verified."""
        entry = self._review_storage.read_by_id(f"prefill-review-{review_id}")
        return entry if entry is not None and _record_kind(entry) == KIND_PREFILL_REVIEW else None

    def prefill_reviews(self, persona_id: str, encounter_id: str, actor_id: str) -> list[dict]:
        """The caller's own pre-fill verdicts on one encounter, and no other actor's.

        Owner-scoped by persona in storage, then filtered to the encounter and the actor here, as
        `proposal_decisions` is: another professional's verdict is dropped before it can reach a response,
        and the caller's answer carries no count or hint that it exists.
        """
        return [entry for entry in self._review_storage.enumerate_by_owner(persona_id)
                if _record_kind(entry) == KIND_PREFILL_REVIEW and entry.get("review_id")
                and entry.get("encounter_id") == encounter_id and entry.get("actor_id") == actor_id]

    def chat_review(self, review_id: str) -> dict | None:
        """The stored review, kind-verified, so a feedback record at `review-<x>` is not returned as one."""
        entry = self._review_storage.read_by_id(f"review-{review_id}")
        return entry if entry is not None and _record_kind(entry) == KIND_CHAT_REVIEW else None

    def chat_turns(self, persona_id: str) -> list[dict]:
        """Every turn recorded for one person, so the next sequence number is derived, not supplied."""
        return [entry for entry in self._chat_storage.enumerate_by_owner(persona_id)
                if _record_kind(entry) == KIND_CHAT_TURN and entry.get("turn_id")]

    def chat_reviews(self, persona_id: str) -> list[dict]:
        """Every review recorded for one person, through the review kind's own storage and enumeration."""
        return [entry for entry in self._review_storage.enumerate_by_owner(persona_id)
                if _record_kind(entry) == KIND_CHAT_REVIEW and entry.get("review_id")]

    def proposal_decisions(self, persona_id: str, encounter_id: str, actor_id: str) -> list[dict]:
        """The caller's own proposal decisions for one encounter, and no other actor's.

        Storage is owner-scoped by persona, so this is where the actor filter runs: another professional's
        decision is filtered out before it can reach a response, and the caller's answer never carries a
        count or a hint that it exists. `encounter_id` is part of the filter because one persona
        legitimately appears in several encounters (the role lenses), and a decision belongs to the
        encounter it was made on, not to every encounter that happens to show the same person.
        """
        return [entry for entry in self.feedback(persona_id)
                if entry.get("encounter_id") == encounter_id and entry.get("actor_id") == actor_id]

    def chat_decisions(self, persona_id: str, encounter_id: str, actor_id: str) -> list[dict]:
        """The caller's own chat-suggestion decisions for one encounter, and no other actor's.

        A review does not store its encounter, so the encounter is recovered from the turn it decided:
        only a turn **this actor** asked **on this encounter** can own a review this method returns. That
        restores the write route's turn-ownership guard on the read side as well — a review inside someone
        else's turn is not this caller's decision, even if a record for it somehow exists.
        """
        turn_ids = {
            entry["turn_id"] for entry in self.chat_turns(persona_id)
            if entry.get("encounter_id") == encounter_id and entry.get("actor_id") == actor_id
        }
        return [entry for entry in self.chat_reviews(persona_id)
                if entry.get("actor_id") == actor_id and entry.get("turn_id") in turn_ids]


@lru_cache(maxsize=1)
def get_store() -> Store:
    backend = os.environ.get("LUVN_STORE_BACKEND")
    if os.environ.get("LUVN_HOSTED") == "1" and backend != "blob":
        raise RuntimeError("hosted feedback storage must use LUVN_STORE_BACKEND=blob")
    if backend == "memory":
        return Store(path=None)
    if backend == "blob":
        account_url = os.environ.get("LUVN_STORAGE_ACCOUNT_URL")
        container_name = os.environ.get("LUVN_FEEDBACK_CONTAINER")
        client_id = os.environ.get("LUVN_STORAGE_CLIENT_ID")
        if not account_url or not container_name:
            raise RuntimeError("Blob feedback storage requires account URL and container")
        credential = ManagedIdentityCredential(client_id=client_id or None)
        service = BlobServiceClient(account_url=account_url, credential=credential)
        return Store(blob_container=service.get_container_client(container_name))
    if backend not in (None, "jsonl"):
        raise RuntimeError(f"unsupported LUVN_STORE_BACKEND: {backend}")
    raw = os.environ.get("LUVN_FEEDBACK_PATH")
    path = Path(raw) if raw else DEFAULT_FEEDBACK_PATH
    return Store(path=path)
