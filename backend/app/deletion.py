"""Delete a delivery run and everything this machine can still reach of it -- and say what it cannot.

The organiser requires the data and all copies deleted after the event. A deletion that is only a shell
script cannot be shown to have happened, and a deletion report that claims completeness it cannot verify is
worse than no report at all: it is the document somebody points at when they say the data is gone.

So `delete_delivery_run(run_id)` removes what it can reach, and then emits a **dated, sealed record** that
(a) lists every removal with its kind, target, file/byte/row counts and whether it was removed, already
absent, not reachable or failed, and (b) states in its first lines, in plain words, that the record is NOT
proof of total erasure and names exactly what it cannot reach:

* Azure SQL **automated backups and point-in-time-restore retention** -- deleting rows does not delete the
  copies the platform keeps; the retention window belongs to the database's configuration, which this job
  does not read and cannot shorten;
* the copies **narration sends to Microsoft Foundry** (and Decision-1): prompts and outputs leave this
  subscription under the vendor's processor terms, and this job has no deletion route into them;
* **container logs / Azure Log Analytics**: stdout lines, including refusal messages that carry values, are
  retained by the workspace's data-retention setting;
* this record's own copies, and any copy the operator has made (a spreadsheet, a screenshot, the vendor's
  own master file, an e-mail attachment).

The seal is a **content digest** (SHA-256 over the record's canonical JSON): it makes the record
tamper-evident and chains it to the run identity. It is deliberately not called a signature, because there
is no key here that would make it one, and it proves nothing about erasure.

Safety properties, each deliberate:

* **refuses without the confirmation flag** -- `DELETE_CONFIRMATION` is a string the operator has to type
  out, so a deletion is never one stray argument away;
* **refuses to guess at attribution** -- SQL rows are removed only for the people this run demonstrably
  indexed (its `index.sqlite`, or an explicit list). Without them the SQL step is reported as
  `not_attributable` naming the reason rather than wiping a shared table on a hunch;
* **never deletes a file it cannot attribute** -- landing bytes are only removed through the run's own
  `delivery_run` manifest, and every path is re-checked to be inside that landing zone before it is touched;
* **idempotent** -- a second run finds the same things absent, removes what reappeared, reports zero new
  removals and still writes a fresh record, noting the previous record's digest if one exists.

Offline honesty: the Blob-container and Log Analytics paths are WRITTEN BUT NOT EXERCISED by the Azure-free
gate (no `az`, no network, no SDK). When `blob_container` is not configured, the record says so; when it is
configured on a machine with the SDK absent, the step FAILS LOUDLY and is recorded as `failed` -- the record
never claims a container it did not delete.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from .landing import MANIFEST_NAME, LandingError, read_manifest

# The exact string an operator must type. A boolean flag is too easy to pass by accident from a script that
# meant to dry-run; a word is not.
DELETE_CONFIRMATION = "I_CONFIRM_DELETE"
# Batch size for the parameterised `IN (...)`. 400 keeps the statement under SQLite's 999-parameter limit and
# well under ODBC's ~2100, so one code path is testable against both.
SQL_BATCH = 400
# Working directories the arrival path creates. Only names matching this prefix inside the temp directory are
# ever considered, so a glob cannot walk out of it.
TMP_PREFIX = "luvn-"
# The prominent clause. It is rendered immediately after the header, before any removal list, because the
# reader who stops after three lines is the reader this text has to protect.
NOT_PROOF = (
    "THIS RECORD IS NOT PROOF THAT THE DATA IS GONE. It is an account of what THIS job removed from THIS "
    "machine, and from the SQL database and Blob container it was pointed at. Copies this job cannot reach "
    "-- Azure SQL backups and point-in-time-restore retention, the narration prompts and outputs sent to "
    "Microsoft Foundry, container logs in Azure Log Analytics, and every copy the operator or the vendor "
    "kept elsewhere -- are listed under NOT_REACHABLE below and are NOT deleted by this record. Do not "
    "describe the delivery as erased on the strength of this document alone."
)


class DeletionError(ValueError):
    """A deletion cannot be performed or cannot be attributed without guessing."""


@dataclass(frozen=True)
class Attribution:
    """The TWO identifiers a run's import index holds for one person, kept apart on purpose.

    `persona_id` and `source_person_id` are different strings in every shipped corpus
    (`data/experimental/delivery-demo/profiles.csv` maps `demo-source-01` to `demo-persona-01`), and the
    tables do not use the same one: `records.persona_id` and `people.persona_id` are persona ids, while
    `encounters.source_person_id` is a source id. A delete handed only one of them, or handed the source
    id for a persona column, changes no rows -- and an after-count built from the same wrong predicate
    cannot see that, which is how the receipt came to certify a half-deletion as `removed`.
    """

    persona_id: str
    source_person_id: str


@dataclass
class Removal:
    """One thing the job did, could not do, or deliberately did not do."""

    kind: str
    target: str
    status: str
    detail: str
    files_removed: int = 0
    bytes_removed: int = 0
    counts: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "target": self.target, "status": self.status, "detail": self.detail,
                "files_removed": self.files_removed, "bytes_removed": self.bytes_removed,
                "counts": self.counts}


@dataclass
class DeletionRecord:
    document: dict[str, Any]
    text: str
    json_path: Path | None = None
    text_path: Path | None = None

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [entry for entry in self.document["removed"] if entry["status"] == "failed"]

    def raises(self) -> bool:
        """Did anything fail that the operator has to know about? A `not_reached` is not a failure."""
        return bool(self.failed)


# --- the honest limitation list ---------------------------------------------------------------------

def _limitations() -> list[dict[str, str]]:
    """The copies this job cannot reach. Kept as data so the record and the rendered text agree."""
    return [
        {"copy": "Azure SQL automated backups and point-in-time restore",
         "why_not_reachable": (
             "deleting rows from people/records/encounters does not delete the copies the platform keeps. "
             "Automated backups and point-in-time-restore (PITR) retention are database configuration: this "
             "job does not read the window and cannot shorten it, so the deleted rows remain restorable for "
             "that period. Backups older than the window and any long-term-retention copy are outside this "
             "job entirely."),
         "action": ("read the database's backup/PITR retention configuration, record the window in the "
                    "event's deletion paperwork, and wait it out (or ask the platform owner to shorten it) "
                    "rather than claiming immediate erasure")},
        {"copy": "Microsoft Foundry (narration and Decision-1) prompts and outputs",
         "why_not_reachable": (
             "record-derived text sent to the model leaves this subscription and is retained by Microsoft "
             "under its processor terms. There is no deletion API call in this repository, and the "
             "processor terms for this use do not exist yet (issue 12), so there is no agreed deletion "
             "route either."),
         "action": ("raise a deletion request under the processor terms as soon as they exist, and record "
                    "that this copy was outstanding at the time of deletion")},
        {"copy": "Container logs and traces in Azure Log Analytics / Application Insights",
         "why_not_reachable": (
             "anything written to stdout/stderr, including refusal messages that quote offending values, is "
             "retained by the Log Analytics workspace under its data-retention setting. This job cannot "
             "purge a workspace."),
         "action": ("apply the workspace's retention setting and purge if the setting alone is not enough; "
                    "record which was done. The application's own telemetry deliberately sends no note "
                    "text, record ids or names, which bounds this copy but does not empty it")},
        {"copy": "The copies of this record itself, and operator copies",
         "why_not_reachable": (
             "this record is written to disk and may be printed, attached, e-mailed or committed. It "
             "contains counts and paths, never person ids or values, but it is still a copy of a document "
             "about the run. The vendor's own master file, screenshots, spreadsheets and e-mail attachments "
             "are also beyond this job."),
         "action": ("treat the receipt as part of the deletion paperwork: keep one authoritative copy, and "
                    "delete the ad-hoc copies made while the event ran")},
        {"copy": "Blob container deletion is API-only offline",
         "why_not_reachable": (
             "the Blob path is written and unit-tested against its own argument handling, but the Azure-free "
             "gate has no network, no `az` and no storage SDK, so no container is actually deleted by the "
             "gate. When the step is NOT configured the record says `not_reached`; when it is configured "
             "and fails, the record says `failed`."),
         "action": ("run the configured deletion on a machine with the storage SDK and credentials, then "
                    "check the container is gone; keep both records together")},
    ]


# --- removals ---------------------------------------------------------------------------------------

def _remove_file(path: Path, kind: str, root: Path | None = None) -> Removal:
    """Remove one file, refusing anything outside `root` and reporting an absent file as absent."""
    if root is not None:
        resolved = path.resolve()
        if not resolved.is_relative_to(root.resolve()):
            return Removal(kind=kind, target=str(path), status="not_attributable",
                           detail=f"{path} is outside {root}; refused rather than deleted")
        path = resolved
    if not path.exists() and not path.is_symlink():
        return Removal(kind=kind, target=str(path), status="already_absent",
                       detail="already gone; this is what makes a re-run safe")
    if path.is_dir():
        return _remove_tree(path, kind)
    size = path.stat().st_size if path.is_file() else 0
    path.unlink()
    return Removal(kind=kind, target=str(path), status="removed", detail="file removed",
                   files_removed=1, bytes_removed=size)


def _remove_tree(path: Path, kind: str) -> Removal:
    files = 0
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file() and not entry.is_symlink():
            files += 1
            total += entry.stat().st_size
    shutil.rmtree(path)
    return Removal(kind=kind, target=str(path), status="removed",
                   detail="directory tree removed", files_removed=files, bytes_removed=total)


def _landing_dir_for(run_id: str, landing_dir: Path | None) -> Path | None:
    """Resolve the run's landing directory, distinguishing 'a path that is gone' from 'not a path'."""
    candidate = Path(run_id).expanduser()
    if candidate.is_dir():
        return candidate
    if landing_dir is not None:
        return Path(landing_dir)
    # A run id carrying a path separator IS a path, and a path that no longer exists is the idempotent
    # re-run case (`already_absent`). A bare manifest hash is NOT a path: treating it as one would let a
    # re-run report a hash it never resolved as "already removed", which is exactly the false completeness
    # this module exists to prevent.
    if os.sep in run_id or "/" in run_id:
        return candidate
    return None


def _delete_landing(run_id: str, landing_dir: Path) -> list[Removal]:
    """Remove the run's landed bytes and its `delivery_run` manifest, through the manifest itself."""
    removals: list[Removal] = []
    if not landing_dir.exists():
        removals.append(Removal(kind="landing_zone", target=str(landing_dir), status="already_absent",
                                detail="landing directory is already gone"))
        return removals
    manifest_path = landing_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        removals.append(Removal(
            kind="landing_bytes", target=str(landing_dir), status="not_attributable",
            detail=("no delivery_run manifest in this directory, so its files cannot be attributed to this "
                    "run; nothing under it was deleted")))
        return removals
    try:
        manifest = read_manifest(landing_dir)
    except LandingError as exc:
        removals.append(Removal(kind="landing_bytes", target=str(manifest_path), status="failed",
                                detail=f"unreadable manifest: {exc}"))
        return removals
    manifest_hash = str(manifest.get("manifest_hash") or "")
    # A run may be named by its landing directory (the CLI's natural argument), by the directory's name, or
    # by its manifest hash -- all three name the same run, and nothing else does.
    identities = {manifest_hash, landing_dir.name, str(landing_dir), str(landing_dir.resolve())}
    if run_id not in identities and not (len(run_id) >= 8 and manifest_hash.startswith(run_id)):
        raise DeletionError(
            f"refusing to delete: {landing_dir} holds run {manifest_hash!r}, which is not the run {run_id!r} "
            f"this deletion names; name the run by its directory or its manifest hash")
    for entry in manifest.get("files") or []:
        relative = str(entry.get("path") or "")
        if not relative:
            continue
        removals.append(_remove_file(landing_dir / relative, kind="landing_bytes", root=landing_dir))
    # The manifest's own rows, then the derived shape reports (column names, candidate vocabularies and
    # samples -- metadata about the same material), then any now-empty run directory.
    removals.append(_remove_file(manifest_path, kind="landing_manifest", root=landing_dir))
    results_dir = landing_dir / "results"
    if results_dir.exists():
        removals.append(_remove_tree(results_dir, "landing_reports"))
    files_dir = landing_dir / "files"
    if files_dir.is_dir():
        for parent in sorted(files_dir.glob("*")):
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        if not any(files_dir.iterdir()):
            files_dir.rmdir()
    if landing_dir.is_dir() and not any(landing_dir.iterdir()):
        landing_dir.rmdir()
        removals.append(Removal(kind="landing_zone", target=str(landing_dir), status="removed",
                                detail="empty run directory removed"))
    return removals


def people_from_index(index_dir: Path) -> list[Attribution]:
    """The people a run demonstrably indexed, read from its own `index.sqlite` BEFORE it is deleted.

    Both identifier columns are read. Returning only `source_person_id` was the defect: SQL was then told
    to delete `records.persona_id IN (source ids)`, which matched nothing, and the after-count repeated
    the same mistake, so the receipt said `removed` about rows that were still there.
    """
    index_path = Path(index_dir) / "index.sqlite"
    if not index_path.is_file():
        raise DeletionError(f"no import index at {index_path}; cannot attribute SQL rows to this run")
    connection = sqlite3.connect(f"file:{quote(str(index_path))}?mode=ro", uri=True)
    try:
        return [Attribution(persona_id=str(row[0]), source_person_id=str(row[1]))
                for row in connection.execute("SELECT persona_id, source_person_id FROM people")]
    except sqlite3.Error as exc:
        raise DeletionError(f"cannot read people from {index_path}: {exc}") from exc
    finally:
        connection.close()


def _attributions(people: set[Any]) -> tuple[list[Attribution], set[str]]:
    """Normalize whatever attribution the caller supplied into `Attribution` pairs, plus the AMBIGUOUS ids.

    An `Attribution` carries both id spaces and is unambiguous: each id is used only for the column it
    names. A bare string (the `--persons` file, or a caller of `delete_delivery_run`) names a value whose
    id SPACE the caller did not say -- an operator copying from a manifest has a source person id, but the
    same call is reachable with a persona id -- so it is applied to BOTH id spaces, and the value is
    returned in the second element so the delete can treat it as ambiguous.

    That second element is the fix for the receipts that said `removed` while an `encounters` row survived.
    `encounters` is bound by `source_person_id` and the shipped schema gives it no `persona_id` at all, so
    a bare persona id (say `P1`) was applied to a column it could not match, the encounter row survived,
    and the probe for the persona id space was skipped because the column does not exist -- a fabricated
    "nothing survived". The ambiguous value is therefore also probed against `encounters.persona_id` when
    that column exists (see `_delete_sql`), under the same strictly-wider predicate, so the case where the
    string really is a persona id is caught by the column that holds it.
    """
    out: list[Attribution] = []
    ambiguous: set[str] = set()
    for value in people:
        if isinstance(value, Attribution):
            out.append(value)
        else:
            text = str(value)
            ambiguous.add(text)
            out.append(Attribution(persona_id=text, source_person_id=text))
    return out, ambiguous


def _delete_import_indexes(indexes: Iterable[Path], people: set[Any]) -> list[Removal]:
    removals: list[Removal] = []
    for index_dir in indexes:
        index_dir = Path(index_dir)
        if not index_dir.exists():
            removals.append(Removal(kind="import_index", target=str(index_dir), status="already_absent",
                                    detail="import output directory is already gone"))
            continue
        try:
            found = people_from_index(index_dir)
        except DeletionError as exc:
            removals.append(Removal(kind="import_index", target=str(index_dir), status="failed",
                                    detail=str(exc)))
            continue
        # The people are read FIRST: they are the only attribution this run has for its SQL rows, and the
        # index is about to stop existing.
        people.update(found)
        entry = _remove_file(index_dir, kind="import_index")
        entry.detail = ("run's import output removed (index, one file per person, quarantine, reports); its "
                        f"{len(found)} indexed people are the SQL attribution for this run")
        entry.counts = {**entry.counts, "indexed_people": len(found)}
        removals.append(entry)
    return removals


def _delete_sql(connection: Any, people: Iterable[Any]) -> Removal:
    """Remove the run's people, records and encounters -- only for people this run indexed.

    Each table is deleted **by the column it actually has**: `records.persona_id` and `people.persona_id`
    take persona ids, `encounters.source_person_id` takes source ids. The after-count then counts every
    table under its OWN delete column (with its own id list) AND the other identifier column it has, so the
    receipt can only say `removed` when every affected table was actually counted and no row survives under
    either space -- which is the property that makes the receipt evidence rather than a claim. A column a
    table does not have is a legitimate skip (an older schema shape); a table for which NO column could be
    counted is a `failed` naming that table and identifier space. The counts are read before the commit, and
    the commit's own failure is reported as a failure rather than rolled into a status decision taken
    earlier.

    A **bare-string** attribution (an id whose space the caller did not say) is applied to both spaces, and
    its value is also put in the `encounters` delete predicate and probed against `encounters.persona_id`
    when that column exists. Without that, a persona id arriving as a string matched nothing in the
    source-id-only encounters table and the row survived under a `removed` receipt -- the same
    half-deletion the previous round fixed on the index path, which survived here on the ambiguous one.
    """
    attributions, ambiguous = _attributions(list(people))
    if connection is None:
        return Removal(kind="sql_rows", target="Azure SQL", status="not_reached",
                       detail=("no SQL connection was supplied, so no Azure SQL row was removed or even "
                               "counted; run this step with a connection to remove them"))
    if not attributions:
        return Removal(kind="sql_rows", target="Azure SQL", status="not_attributable",
                       detail=("no person ids could be attributed to this run (no import index and no "
                               "explicit list), and this job does not delete a shared table on a hunch; "
                               "the run's rows remain"))
    persona_ids = sorted({attribution.persona_id for attribution in attributions})
    source_ids = sorted({attribution.source_person_id for attribution in attributions})
    # The values whose id space the caller did not state. They are already in both lists above; this set
    # exists so a table bound by ONE column can also be probed under the other when the value might belong
    # there (`_sql_id_spaces`). An explicit `Attribution` is never in it: its two ids are stated per space.
    ambiguous_ids = sorted(ambiguous)
    # Only the BEFORE keys are seeded. The `*_after` keys are created by `_count_rows` when a count actually
    # runs, so a table nothing counted has NO `*_after` entry (plus a visible `*_after_unmeasured` marker)
    # instead of the initialised zero the old code reported as if it were a measurement.
    counts: dict[str, int] = {"people_before": 0, "records_before": 0, "encounters_before": 0,
                              "rows_matched_by_persona_id": 0, "rows_matched_by_source_person_id": 0}
    cursor = connection.cursor()
    try:
        # BEFORE: the rows this run is about to touch, counted by the column each table actually has. The
        # people and encounters counts are kept because they are also how the after-count tells a space an
        # ambiguous value actually occupies from one it cannot reach (see `required_columns` below).
        _count_rows(cursor, counts, "before", "records", "persona_id", persona_ids)
        before_encounters_source = _count_rows(cursor, counts, "before", "encounters", "source_person_id",
                                               source_ids)
        before_people_persona = _count_rows(cursor, counts, "before", "people", "persona_id", persona_ids)
        # THE DELETE, by the column each table has. `records` is deleted by `persona_id` ONLY: the
        # attribution's persona ids name exactly this run's people, while `records.source_person_id`
        # would delete a DIFFERENT person's records if the index ever bound one source key to two
        # personas. `people` is removed under both keys, so a string-only attribution (which sets both id
        # spaces to the same value) cannot leave a person behind; `encounters` uses its own column, widened
        # by the ambiguous values so a persona id supplied as a bare string cannot leave an encounter
        # behind either (the delete and the after-count widen together, so neither can hide the other).
        # Which columns each table actually has is read FIRST and the ambiguous widening is applied only to
        # the columns that exist: `DELETE FROM encounters WHERE persona_id ...` on the shipped schema (no
        # such column) would raise, turning a legitimate source-id run into a false `failed`.
        encounters_ids = sorted(set(source_ids) | set(ambiguous_ids))
        for start in range(0, len(persona_ids), SQL_BATCH):
            batch = persona_ids[start:start + SQL_BATCH]
            placeholders = ",".join("?" for _ in batch)
            cursor.execute(f"DELETE FROM records WHERE persona_id IN ({placeholders})", batch)
        encounters_columns = _table_columns(cursor, "encounters")
        for column in ("source_person_id", "persona_id"):
            if column not in encounters_columns:
                continue
            for start in range(0, len(encounters_ids), SQL_BATCH):
                batch = encounters_ids[start:start + SQL_BATCH]
                placeholders = ",".join("?" for _ in batch)
                cursor.execute(f"DELETE FROM encounters WHERE {column} IN ({placeholders})", batch)
        for start in range(0, len(source_ids), SQL_BATCH):
            batch = source_ids[start:start + SQL_BATCH]
            placeholders = ",".join("?" for _ in batch)
            cursor.execute(f"DELETE FROM people WHERE source_person_id IN ({placeholders})", batch)
        for start in range(0, len(persona_ids), SQL_BATCH):
            batch = persona_ids[start:start + SQL_BATCH]
            placeholders = ",".join("?" for _ in batch)
            cursor.execute(f"DELETE FROM people WHERE persona_id IN ({placeholders})", batch)
        # AFTER: every table under BOTH id spaces, using the table's OWN delete column and id list (what
        # `_sql_id_spaces` returns and the earlier loop threw away). `encounters` is bound by
        # `source_person_id` and the shipped schema gives it no `persona_id` at all, so a loop that hardcoded
        # `persona_id` found no column, skipped BOTH probes, and left `encounters_after` at an initialised
        # zero -- a fabricated count under a receipt that said `removed`. A survivor under either space is a
        # failed delete, named; a COLUMN that is absent is a legitimate skip (an older schema shape), but a
        # TABLE for which no column was counted is unverified and must fail by name. The receipt is evidence,
        # so `removed` is only reachable when every affected table was actually counted.
        survivors: list[str] = []
        unverified: list[str] = []
        # Which id list belongs to which identifier column, and the label used in the survivor text and the
        # `rows_matched_by_*` counters. The ambiguous values are in BOTH lists: that is what makes a bare
        # string probed under whichever column a table has, instead of only under `source_person_id`.
        id_spaces = {
            "persona_id": ("persona", sorted(set(persona_ids) | set(ambiguous_ids))),
            "source_person_id": ("source_person", sorted(set(source_ids) | set(ambiguous_ids))),
        }
        # A probe whose COLUMN is absent is normally a legitimate skip (an older schema shape). It is not a
        # skip when this run carried an AMBIGUOUS id that the table's OWN column cannot account for: a bare
        # string might be a persona id, and a table with no `persona_id` column holds no evidence either way,
        # so "no row survived under the persona space" cannot be measured on it. `required_columns` names the
        # probes a table must satisfy for an ambiguous run; an absent one becomes unverified and the receipt
        # fails rather than saying `removed`.
        #
        # `matched_*` inputs keep that from over-firing: a probe is required only when the ambiguous value
        # actually OCCUPIED that space pre-delete (the table's own before-count saw it). A value the table
        # never matched under either space (the encounters row owned by H1 when the string is the persona id
        # P1, say) is not occupying that space, so demanding a column that does not exist would only
        # manufacture a failure. This keeps the shipped three-table clean case `removed` (an import index
        # supplies explicit `Attribution`s, so nothing is ambiguous) AND keeps a bare SOURCE id honest (its
        # record really does survive under `records.persona_id`, and that count is what fails the receipt).
        occupied_by_space = {"persona_id": before_people_persona, "source_person_id": before_encounters_source}
        required_columns = {}
        if ambiguous_ids:
            for table, own_column, _key, _ids, fallback_column in _sql_id_spaces(
                    id_spaces["persona_id"][1], id_spaces["source_person_id"][1]):
                spans = id_spaces[own_column][1], id_spaces[fallback_column][1]
                required_columns[table] = tuple(
                    column for column, ids in zip((own_column, fallback_column), spans)
                    if set(ids) & set(ambiguous_ids) and occupied_by_space[column])
        for table, own_column, key, own_ids, fallback_column in _sql_id_spaces(
                id_spaces["persona_id"][1], id_spaces["source_person_id"][1]):
            columns = _table_columns(cursor, table)
            counted = 0
            required = required_columns.get(table, ())
            for column, value_ids in ((own_column, own_ids), (fallback_column, id_spaces[fallback_column][1])):
                label = id_spaces[column][0]
                if column not in columns:
                    counts[f"columns_absent_{table}_{column}"] = 1
                    continue
                counted += 1
                found = _count_rows(cursor, counts, "after", table, column, value_ids)
                counts[f"rows_matched_by_{label}_id"] += found
                if found:
                    survivors.append(f"{table}.{column} holds {found} row(s) still named by the run's "
                                     f"{label}s")
            missing = [column for column in required if column not in columns]
            if not counted or missing:
                # No count ran for this table, so nothing measured its rows -- or a probe this run NEEDED
                # (because it carried an id whose space is unknown) could not run at all. `key` is this
                # table's counter key; the `*_after_unmeasured` marker keeps `*_after` absent rather than a
                # false zero. The text names exactly which columns could not be counted, so the operator is
                # not told to go looking at a column that was probed and came back clean.
                counts[f"{key}_after_unmeasured"] = 1
                unmeasured = ", ".join(f"{table}.{column}" for column in missing) or f"{table}.{own_column}"
                unverified.append(f"{unmeasured} (the run's {id_spaces[own_column][0]} ids could not be "
                                  f"counted under {'every column this run needs' if missing else 'any column this table has'})")
        connection.commit()
    except Exception as exc:  # noqa: BLE001 - a failed deletion must be reported, never swallowed
        try:
            connection.rollback()
        except Exception:  # noqa: BLE001 - a rollback failure must not mask the original error
            pass
        return Removal(kind="sql_rows", target="Azure SQL", status="failed",
                       detail=f"SQL deletion failed and was rolled back: {exc}", counts=counts)
    if survivors or unverified:
        problems = list(survivors) + [f"{label} was NOT counted, so its absence is unverified"
                                      for label in unverified]
        return Removal(
            kind="sql_rows", target="Azure SQL", status="failed", counts=counts,
            detail=("the delete committed but data this run indexed could not be shown gone, so this is NOT "
                    "a removal: " + "; ".join(problems)))
    detail = (f"deleted {counts['records_before']} records, {counts['encounters_before']} encounters and "
              f"{counts['people_before']} people attributable to this run (both id columns come from the "
              f"run's own import index), and every one of the three tables was counted under both of its id "
              f"columns with no row surviving")
    return Removal(kind="sql_rows", target="Azure SQL", status="removed", detail=detail, counts=counts)


def _sql_id_spaces(persona_ids: list[str], source_ids: list[str]):
    """`(table, column, key, ids, fallback_column)` for each table's own id space.

    `column` is the column the DELETE uses, with `ids` the id list that belongs to it; `fallback_column` is
    the OTHER identifier column the after-count must also check, with the other id list; `key` is the counter
    key `_count_rows` writes. Returns each table's own space first, because that is the probe the earlier
    loop discarded -- which is how `encounters` (own column `source_person_id`, no `persona_id` at all) went
    uncounted under a receipt that said `removed`.

    The id lists handed in already carry the AMBIGUOUS values (an id supplied as a bare string, whose space
    the caller did not state) in both, so `encounters.source_person_id` and `encounters.persona_id` are each
    probed for a value that could be either. That is what keeps the string case honest in both directions:
    a bare persona id is counted by the column that holds it, and a bare source id is still counted by the
    column the shipped schema has (so the mirror case cannot become a false `failed`).
    """
    return (("records", "persona_id", "records", persona_ids, "source_person_id"),
            ("encounters", "source_person_id", "encounters", source_ids, "persona_id"),
            ("people", "persona_id", "people", persona_ids, "source_person_id"))


def _table_columns(cursor: Any, table: str) -> set[str]:
    """The column names `cursor`'s connection holds for `table`, across the stand-in and SQL Server.

    Both engines expose a DB-API `cursor.description` after a zero-row probe, but the probe differs
    (`PRAGMA table_info` has no SQL Server equivalent). The honest test is the one both agree on: run a
    parameterised `SELECT * ... WHERE 1 = 0` and read the names back. A table that does not exist returns
    the empty set -- and the caller treats that as UNVERIFIABLE and fails, because a table it cannot list is
    a table whose rows it cannot prove absent.
    """
    try:
        cursor.execute(f"SELECT * FROM {table} WHERE 1 = 0")
        return {str(column[0]) for column in (cursor.description or [])}
    except Exception:  # noqa: BLE001 - a table that cannot be probed cannot be counted; caller skips it
        return set()


def _count_rows(cursor: Any, counts: dict[str, int], phase: str, table: str, column: str,
                ids: list[str]) -> int:
    """Count rows matching the run's ids in `table.column` over batches into `counts[f"{key}_{phase}"]`.

    The predicate is deliberately strictly WIDER than the delete's: `UPPER(TRIM(column)) IN (UPPER(TRIM(?)))`
    plus an explicit `IS NULL`. A count built from the *same* exact `IN` as the DELETE could never contradict
    it, so a row surviving because its identifier is not byte-identical to an indexed id -- trailing or
    leading whitespace, an ASCII case difference, or a NULL -- would stay invisible and the receipt would
    certify a half-deletion. `TRIM`/`UPPER` is chosen over an unbounded whole-table scan because a per-run
    delete legitimately leaves OTHER people's rows in a shared table (test
    `..._removes_only_the_people_this_run_indexed`), so "any row remains" cannot be the failure condition; the
    predicate still has to name the run's ids, and normalising both sides is the widest form that does.
    What it still cannot catch: whitespace `TRIM` does not remove (tab/newline/NBSP), non-ASCII case folding
    (SQLite's `UPPER` is ASCII-only; SQL Server's follows the column collation), and an identifier that is
    genuinely a different string rather than a variant of one (that row is a different person, and no
    predicate over these ids could claim it).

    `key` is derived from the table here so the two counter namespaces (`*_before`, `*_after`) cannot be
    confused with the per-id-space survivor counters the caller adds, and so the `*_after` key exists only
    when this function actually ran. The after-phase caller invokes this once per identifier column, so a
    `*_after` value is the total number of (row, column) matches: it is exactly zero when nothing matched
    under any column (which is the only case a `removed` receipt reaches), and on a failure it can count one
    surviving row once per space it matched; the per-space `rows_matched_by_*` counters and the per-column
    survivor lines are the exact measures there.
    """
    key = {"records": "records", "encounters": "encounters", "people": "people"}[table]
    total = 0
    for start in range(0, len(ids), SQL_BATCH):
        batch = ids[start:start + SQL_BATCH]
        if not batch:
            continue
        placeholders = ",".join("UPPER(TRIM(?))" for _ in batch)
        cursor.execute(f"SELECT COUNT(*) FROM {table} "
                       f"WHERE {column} IS NULL OR UPPER(TRIM({column})) IN ({placeholders})", batch)
        total += int(cursor.fetchone()[0])
    counts[f"{key}_{phase}"] = counts.get(f"{key}_{phase}", 0) + total
    return total


def delete_blob_container(container: str, *, account_url: str | None = None,
                          client_id: str | None = None) -> Removal:
    """Delete a Blob container. WRITTEN, AND NOT EXERCISED OFFLINE: the gate has no network or SDK.

    Kept as a module-level function so a configured run calls exactly this code, and so the offline test can
    pin that the SDK import failure is reported loudly instead of silently skipping the container.
    """
    from azure.identity import ManagedIdentityCredential
    from azure.storage.blob import BlobServiceClient

    account_url = account_url or os.environ.get("LUVN_STORAGE_ACCOUNT_URL") or ""
    if not account_url:
        raise DeletionError("deleting a Blob container needs LUVN_STORAGE_ACCOUNT_URL")
    service = BlobServiceClient(
        account_url=account_url,
        credential=ManagedIdentityCredential(client_id=client_id or os.environ.get("LUVN_STORAGE_CLIENT_ID")
                                             or None))
    container_client = service.get_container_client(container)
    existed = container_client.exists()
    container_client.delete_container()
    return Removal(kind="blob_container", target=f"{account_url}/{container}",
                   status="removed" if existed else "already_absent",
                   detail=("container deleted (deleteContainer is idempotent; Blob soft delete is disabled "
                           "on this account, so the delete is immediate)") if existed else
                          "container did not exist or was already deleted")


def _delete_tmp(tmp_dir: Path, prefix: str = TMP_PREFIX) -> list[Removal]:
    """Remove this repository's working directories under the temp directory -- and nothing else."""
    removals: list[Removal] = []
    tmp_dir = Path(tmp_dir)
    if not tmp_dir.is_dir():
        return [Removal(kind="tmp_working_copies", target=str(tmp_dir), status="already_absent",
                        detail="temp directory does not exist")]
    for entry in sorted(tmp_dir.glob(f"{prefix}*")):
        if entry.resolve().parent != tmp_dir.resolve():
            removals.append(Removal(kind="tmp_working_copies", target=str(entry), status="not_attributable",
                                    detail="resolved outside the temp directory; refused"))
            continue
        if entry.is_symlink():
            removals.append(Removal(kind="tmp_working_copies", target=str(entry), status="not_attributable",
                                    detail="symlink; refused rather than followed"))
            continue
        removals.append(_remove_file(entry, kind="tmp_working_copies", root=tmp_dir))
    if not removals:
        removals.append(Removal(kind="tmp_working_copies", target=str(tmp_dir / f"{prefix}*"),
                                status="already_absent", detail="no matching working directory"))
    return removals


# --- the receipt ------------------------------------------------------------------------------------

def _seal(document: dict[str, Any]) -> dict[str, Any]:
    body = {key: value for key, value in document.items() if key != "seal"}
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {"algorithm": "sha256", "digest": digest,
            "covers": "the canonical JSON of this record with this object removed",
            "note": ("a content digest that makes the record tamper-evident and chains it to the run's "
                     "manifest hash; it is NOT a signature by a person and NOT evidence that any copy was "
                     "erased")}


def render_deletion_record(document: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"DELIVERY RUN DELETION RECORD   run: {document['run_id']}")
    lines.append(f"deleted_at: {document['deleted_at']}   "
                 f"landing manifest: {document['run_manifest_hash'] or '(not available)'}")
    lines.append(f"confirmation: {document['confirmation']}"
                 + (f"   confirmed_by: {document['confirmed_by']}" if document.get("confirmed_by") else ""))
    lines.append("")
    lines.append(">>> " + NOT_PROOF)
    lines.append("")
    lines.append("WHAT THIS JOB REMOVED / FOUND")
    for entry in document["removed"]:
        counted = ""
        if entry["files_removed"] or entry["bytes_removed"]:
            counted = f" [{entry['files_removed']} file(s), {entry['bytes_removed']} bytes]"
        lines.append(f"  {entry['status']:16s} {entry['kind']:20s} {entry['target']}{counted}")
        lines.append(f"      {entry['detail']}")
        if entry["counts"]:
            lines.append(f"      counts: {entry['counts']}")
    lines.append("")
    lines.append("NOT_REACHABLE -- copies this record does NOT claim to have deleted")
    for entry in document["not_reachable"]:
        lines.append(f"  [{entry['cause']}] {entry['copy']}")
        lines.append(f"      why: {entry['why_not_reachable']}")
        lines.append(f"      action: {entry['action']}")
    lines.append("")
    lines.append(f"seal: sha256:{document['seal']['digest']}")
    lines.append(f"      {document['seal']['note']}")
    if document.get("previous_record"):
        lines.append("")
        lines.append(f"replay: a deletion record for this run already existed at "
                     f"{document['previous_record']['path']} "
                     f"(seal sha256:{document['previous_record']['digest']}); this run removed only what was "
                     f"still there, which is what makes the job safe to re-run")
    return "\n".join(lines) + "\n"


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "_" for character in value)[:96]


def _receipt_stem(run_id: str) -> str:
    """A receipt filename a human can find again: the run's directory name, not a mangled path."""
    return _safe_name(Path(run_id).name if ("/" in run_id or os.sep in run_id) else run_id)


def delete_delivery_run(run_id: str, *, confirm: str | None = None, landing_dir: str | Path | None = None,
                        connection: Any = None, person_ids: Iterable[str] | None = None,
                        indexes: Iterable[str | Path] = (), blob_container: str | None = None,
                        blob_account_url: str | None = None, tmp_dir: str | Path | None = None,
                        delivery_dir: str | Path | None = None, receipt_dir: str | Path | None = None,
                        confirmed_by: str | None = None, keep_tmp: bool = False,
                        now: datetime | None = None) -> DeletionRecord:
    """Delete one delivery run's reachable copies and emit the dated, sealed, honest record.

    Refuses without `confirm=DELETE_CONFIRMATION`. Every step is idempotent: a second run reports what is
    already absent, removes only what reappeared, and still writes its own record.
    """
    if confirm != DELETE_CONFIRMATION:
        raise DeletionError(
            f"refusing to delete without explicit confirmation: pass confirm={DELETE_CONFIRMATION!r} "
            f"(nothing was removed)")
    deleted_at = (now or datetime.now(timezone.utc)).isoformat()
    resolved_landing = _landing_dir_for(run_id, Path(landing_dir) if landing_dir is not None else None)
    removals: list[Removal] = []
    run_manifest_hash = ""
    if resolved_landing is not None and (resolved_landing / MANIFEST_NAME).is_file():
        try:
            run_manifest_hash = str(read_manifest(resolved_landing).get("manifest_hash") or "")
        except LandingError:
            run_manifest_hash = ""
        removals.extend(_delete_landing(run_id, resolved_landing))
    elif resolved_landing is not None:
        removals.append(Removal(
            kind="landing_bytes", target=str(resolved_landing), status="already_absent",
            detail=("no landing directory (or no delivery_run manifest) at this path, so there were no bytes "
                    "to attribute and none were deleted; a re-run finds this and nothing else, which is what "
                    "makes the job safe to repeat")))
    else:
        removals.append(Removal(
            kind="landing_bytes", target=run_id, status="not_attributable",
            detail=("the run id is neither an existing landing directory nor resolved by --landing-dir, so "
                    "no landed bytes could be found or attributed")))

    # Attribution is a SET of `Attribution`s (both id columns, from the index) plus any explicit source
    # ids from `--persons`. A `str` here means "explicit source id, use it for both id spaces" -- see
    # `_attributions`, which normalizes before the delete.
    people: set[Any] = set()
    if person_ids:
        people.update(str(value) for value in person_ids)
    for index_dir in indexes:
        removals.extend(_delete_import_indexes([Path(index_dir)], people))
    removals.append(_delete_sql(connection, people))

    if blob_container:
        try:
            removals.append(delete_blob_container(blob_container, account_url=blob_account_url))
        except Exception as exc:  # noqa: BLE001 - a Blob failure must be in the record, not only on stderr
            removals.append(Removal(
                kind="blob_container", target=blob_container, status="failed",
                detail=(f"Blob container deletion FAILED: {exc}. This path is written but not exercised by "
                        f"the Azure-free gate; run it on a machine with the storage SDK and credentials")))
    else:
        removals.append(Removal(
            kind="blob_container", target="(not configured)", status="not_reached",
            detail=("no Blob container was configured (--blob-container / LUVN_CORPUS_CONTAINER). If the run "
                    "put a corpus in Blob, that copy is NOT deleted by this run")))

    if not keep_tmp:
        removals.extend(_delete_tmp(Path(tmp_dir) if tmp_dir is not None else Path(tempfile.gettempdir())))
    else:
        removals.append(Removal(
            kind="tmp_working_copies", target=str(tmp_dir or tempfile.gettempdir()), status="not_reached",
            detail=("temp cleanup was SKIPPED on request (--keep-tmp); any /tmp/luvn-* working copy is still "
                    "on disk")))
    working = Path(delivery_dir) if delivery_dir is not None else (
        Path(os.environ["LUVN_DELIVERY_DIR"]) if os.environ.get("LUVN_DELIVERY_DIR") else None)
    if working is not None:
        removals.append(_remove_file(working, kind="delivery_dir"))
    else:
        removals.append(Removal(
            kind="delivery_dir", target="LUVN_DELIVERY_DIR (unset)", status="not_reached",
            detail=("no LUVN_DELIVERY_DIR working copy was named, and no import directory was passed, so no "
                    "serving copy was removed by this step")))

    not_reachable = [dict(entry, cause="cannot reach") for entry in _limitations()]
    for entry in removals:
        if entry.status in ("failed", "not_attributable", "not_reached"):
            not_reachable.append({
                "cause": entry.status, "copy": f"{entry.kind}: {entry.target}",
                "why_not_reachable": entry.detail,
                "action": "resolve this step and re-run; the job is idempotent, so a re-run only removes "
                          "what is still there"})
    document: dict[str, Any] = {
        "kind": "delivery-run-deletion-record",
        "run_id": run_id,
        "run_manifest_hash": run_manifest_hash,
        "deleted_at": deleted_at,
        "confirmation": confirm,
        "confirmed_by": confirmed_by,
        "not_proof_of_erasure": NOT_PROOF,
        "removed": [entry.as_dict() for entry in removals],
        "not_reachable": not_reachable,
        "totals": {
            "removed": sum(1 for entry in removals if entry.status == "removed"),
            "already_absent": sum(1 for entry in removals if entry.status == "already_absent"),
            "failed": sum(1 for entry in removals if entry.status == "failed"),
            "not_reached": sum(1 for entry in removals if entry.status == "not_reached"),
            "not_attributable": sum(1 for entry in removals if entry.status == "not_attributable"),
            "files_removed": sum(entry.files_removed for entry in removals),
            "bytes_removed": sum(entry.bytes_removed for entry in removals),
        },
        "limitations_note": ("every entry under not_reachable is a copy this job did NOT delete; a reader "
                             "must not read this record as a statement that the delivery is erased"),
    }
    if receipt_dir is not None:
        receipt_dir = Path(receipt_dir)
        receipt_dir.mkdir(parents=True, exist_ok=True)
        previous = receipt_dir / f"delivery-run-deletion-{_receipt_stem(run_id)}.json"
        if previous.is_file():
            try:
                prior = json.loads(previous.read_text(encoding="utf-8"))
                document["previous_record"] = {"path": str(previous),
                                               "digest": (prior.get("seal") or {}).get("digest")}
            except (OSError, json.JSONDecodeError):
                document["previous_record"] = {"path": str(previous), "digest": None}
    document["seal"] = _seal(document)
    text = render_deletion_record(document)
    record = DeletionRecord(document=document, text=text)
    if receipt_dir is not None:
        json_path = receipt_dir / f"delivery-run-deletion-{_receipt_stem(run_id)}.json"
        text_path = receipt_dir / f"delivery-run-deletion-{_receipt_stem(run_id)}.txt"
        json_path.write_text(json.dumps(document, ensure_ascii=False, indent=2, default=str) + "\n",
                             encoding="utf-8")
        text_path.write_text(text, encoding="utf-8")
        record.json_path, record.text_path = json_path, text_path
    return record
