"""Load a corpus in the declared shape into Azure SQL: `python -m app.load_sql [dir] [--recreate]`.

Run it wherever the driver and an identity are: inside the cluster as a Container Apps Job, where the image
already carries the ODBC driver and the app's managed identity is the credential, or on a machine that has
the driver and a token in `LUVN_SQL_ACCESS_TOKEN`. The second form is for development; the first is how the
real delivery is loaded, because it needs no laptop, no local driver and no secret.

`--grant-app-user` creates the contained database user for the app's managed identity and gives it read
access. That statement needs the server's Entra **administrator**, which the app's identity is not — so it is
here as an explicit, opt-in step rather than something the loader quietly attempts on every run.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from .sqldelivery import (LoadRejected, connect, load_corpus, log_rate_readings, stored_counts,
                         verify_integrity)

_CONTAINER_CORPUS = Path("/app/data/experimental/delivery-demo")
# On a dev host the container path is not a directory, so resolve the same corpus inside the checkout
# instead of letting the caller fall through to a cwd-relative path that only works from the repo root.
DEFAULT_CORPUS = (_CONTAINER_CORPUS if _CONTAINER_CORPUS.is_dir()
                  else Path(__file__).resolve().parents[2] / "data" / "experimental" / "delivery-demo")
# What an Entra principal (managed-identity name, app registration name or UPN) can contain. The value is
# interpolated into BOTH a T-SQL string literal and a bracketed identifier, whose escapes differ (`''`
# vs `]]`), so this is validated FIRST and refused outright: a value that is not a principal name is a
# mistake at the CLI, not something to escape and execute. The payload would otherwise run with the
# loader's `db_ddladmin`, or the Entra administrator's rights when combined with `--harden`.
_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._@()'\-]{0,255}$")


class GrantRefused(ValueError):
    """A `--grant-*` identity that is not a principal name, so no statement was built from it."""


def _validate_identity(identity: str) -> str:
    if not _IDENTITY_PATTERN.fullmatch(identity) or "  " in identity:
        raise GrantRefused(
            f"refusing to grant to {identity!r}: an Entra principal name is 1-256 characters of letters, "
            f"digits, space, . _ @ ( ) ' or -; this value would have to be escaped into T-SQL, which is a "
            f"sign it is not a principal name")
    return identity


def _quote_literal(value: str) -> str:
    """The body of a T-SQL string literal: a doubled apostrophe is the only escape."""
    return value.replace("'", "''")


def _quote_identifier(value: str) -> str:
    """The body of a T-SQL bracketed identifier: a doubled `]` is the only escape."""
    return value.replace("]", "]]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load a delivery-shaped corpus into Azure SQL")
    parser.add_argument("corpus", nargs="?", default=None,
                        help="corpus directory (default: LUVN_DELIVERY_DIR, else the demo corpus)")
    parser.add_argument("--server", default=None, help="default: LUVN_SQL_SERVER")
    parser.add_argument("--database", default=None, help="default: LUVN_SQL_DATABASE")
    parser.add_argument("--client-id", default=None, help="default: LUVN_SQL_CLIENT_ID")
    parser.add_argument("--recreate", action="store_true",
                        help="drop and recreate the three tables, for a genuine schema change; a plain load "
                             "already replaces the corpus, by DELETE inside one transaction")
    parser.add_argument("--verify", action="store_true",
                        help="print what the database holds and change nothing; the check to run after an "
                             "upload, and the one that tells a failed load from a stale container")
    parser.add_argument("--append", action="store_true",
                        help="add to the stored corpus instead of replacing it; the default replaces, so a "
                             "smaller upload cannot leave the previous corpus' rows behind")
    parser.add_argument("--from-blob", action="store_true",
                        help="read the corpus from the delivery container instead of a directory; the "
                             "hackathon-day path (LUVN_CORPUS_CONTAINER / LUVN_CORPUS_PREFIX)")
    parser.add_argument("--grant-app-user", default=None, metavar="IDENTITY_NAME",
                        help="also create this contained user and grant it READ access; needs the server's "
                             "Entra administrator")
    parser.add_argument("--grant-loader-user", default=None, metavar="IDENTITY_NAME",
                        help="create this contained user with read, write AND DDL access (db_datareader, "
                             "db_datawriter, db_ddladmin), for a loader identity that reloads and can "
                             "--recreate/--harden without the operator's token. db_ddladmin is a DELIBERATE "
                             "grant: the loader owns the three corpus tables and the schema they need. The "
                             "app identity is never granted it")
    parser.add_argument("--harden", action="store_true",
                        help="apply the integrity and index hardening, BEFORE the load (the column upgrade "
                             "the records insert needs) and again after it (idempotent). It needs ALTER "
                             "rights on the three tables, which the loader identity holds deliberately as "
                             "db_ddladmin, so the job can harden without the operator's admin token")
    args = parser.parse_args(argv)

    if args.verify:

        connection = connect(server=args.server, database=args.database, client_id=args.client_id)
        try:
            counts = stored_counts(connection)
            cursor = connection.cursor()
            try:
                state = verify_integrity(cursor)
            finally:
                cursor.close()
        finally:
            connection.close()
        print(f"load_sql: database holds {counts['people']} people, {counts['encounters']} encounters, "
              f"{counts['records']} records")
        print(f"load_sql: integrity {state['actual']} (expected {state['expected']})")
        if state["ok"]:
            print("load_sql: hardened objects present")
            return 0
        print(f"load_sql: integrity NOT satisfied — missing {state['missing']}, "
              f"unexpected {state['unexpected']}; apply with --harden", file=sys.stderr)
        return 1
    if args.from_blob:
        import tempfile

        from .sqldelivery import fetch_corpus_blob

        try:
            corpus = fetch_corpus_blob(Path(tempfile.mkdtemp(prefix="luvn-corpus-")))
        except Exception as error:  # noqa: BLE001 - any download failure is the operator's next question
            print(f"load_sql: could not read the corpus from Blob: {type(error).__name__}: {error}",
                  file=sys.stderr)
            return 2
        print(f"load_sql: corpus read from Blob into {corpus}")
    else:
        corpus = Path(args.corpus) if args.corpus else Path(
            os.environ.get("LUVN_DELIVERY_DIR") or (DEFAULT_CORPUS if DEFAULT_CORPUS.is_dir() else
                                                    Path("data/experimental/delivery-demo")))
        if not corpus.is_dir():
            print(f"load_sql: no corpus directory at {corpus}", file=sys.stderr)
            return 2
    # The grants are validated BEFORE the connection is opened, so a hostile or mistyped `--grant-*`
    # identity cannot reach any statement (and cannot even be used to prove a connection exists).
    try:
        grants = [(identity, ("db_datareader",)) for identity in
                  (_validate_identity(name) for name in (args.grant_app_user,) if name)]
        grants += [(identity, ("db_datareader", "db_datawriter", "db_ddladmin"))
                   for identity in
                   (_validate_identity(name) for name in (args.grant_loader_user,) if name)]
    except GrantRefused as exc:
        print(f"load_sql: {exc}", file=sys.stderr)
        return 2
    connection = connect(server=args.server, database=args.database, client_id=args.client_id)
    try:
        for identity, roles in grants:
            cursor = connection.cursor()
            try:
                # `FROM EXTERNAL PROVIDER` maps the Entra identity to a database user. Read-only, and only
                # on the three tables this loader owns — the app never writes a corpus. The value is both a
                # T-SQL string literal and a bracketed identifier: escape for BOTH contexts even though
                # `_validate_identity` already refused anything that needs it, so the two cannot drift.
                literal = _quote_literal(identity)
                identifier = _quote_identifier(identity)
                cursor.execute(f"IF NOT EXISTS (SELECT 1 FROM sys.database_principals "
                               f"WHERE name = N'{literal}') "
                               f"CREATE USER [{identifier}] FROM EXTERNAL PROVIDER")
                for role in roles:
                    # Parameterised: the role is a fixed name from this file, but a statement built by
                    # interpolation is one edit away from not being one, so the engine receives both values.
                    cursor.execute("EXEC sp_addrolemember @rolename = ?, @membername = ?", role, identity)
                connection.commit()
                print(f"load_sql: granted {', '.join(roles)} to {identity}")
            finally:
                cursor.close()
        if args.harden:
            # BEFORE the load. A database created by an earlier revision has no `records.encounter_id` or
            # `records.case_id`, and the loader's records insert now names both, so the migration has to run
            # first. This subset is idempotent and a no-op on a database whose tables do not exist yet, so a
            # first `--recreate` load is unaffected; the FULL hardening runs again below, when the rows exist
            # for the `WITH CHECK` validation.
            from .sqldelivery import apply_schema_upgrade

            cursor = connection.cursor()
            try:
                apply_schema_upgrade(cursor)
                connection.commit()
            except Exception as error:  # noqa: BLE001 - a failed upgrade must not pretend to have run
                connection.rollback()
                print(f"load_sql: schema upgrade rolled back: {type(error).__name__}: {error}",
                      file=sys.stderr)
                return 1
            finally:
                cursor.close()
        try:
            counts = load_corpus(corpus, connection, recreate=args.recreate, replace=not args.append)
        except LoadRejected as error:
            # A hard failure, by design: the rejects are on disk, the committed batches stay, and the next run
            # resumes from the checkpoint once the rows are fixed. Nothing is skipped to make a number look right.
            print(f"load_sql: NOT LOADED: {error}", file=sys.stderr)
            return 1
        print(f"load_sql: generation {counts['generation']}, {counts['batches']} committed batch(es)"
              + (f", resumed after item {counts['resumed_after']}" if counts["resumed_after"] is not None else ""))
        # Verified by counting, not by the exit code of the load: the manifest the loader read against what the
        # database now holds. An append is a sum with rows this load did not write, so it is not compared.
        if not args.append:
            held = stored_counts(connection)
            manifest = {"people": counts["profiles"], "encounters": counts["encounters"],
                        "appointments": counts["appointments"], "records": counts["records"]}
            if held != manifest:
                print(f"load_sql: COUNT MISMATCH: manifest {manifest}, database holds {held}", file=sys.stderr)
                return 1
            print(f"load_sql: database holds {held}, equal to the manifest")
        if not args.harden:
            cursor = connection.cursor()
            try:
                state = verify_integrity(cursor)
            finally:
                cursor.close()
            if not state["ok"]:
                print(f"load_sql: integrity NOT satisfied — missing {state['missing']}, "
                      f"unexpected {state['unexpected']}", file=sys.stderr)
                return 1
            print("load_sql: integrity satisfied")
        print(f"load_sql: log rate and waits {log_rate_readings(connection)}")
        if args.harden:
            from .sqldelivery import apply_hardening

            cursor = connection.cursor()
            try:
                state = apply_hardening(cursor)
                connection.commit()
            except Exception as error:  # noqa: BLE001 - report the real reason, do not half-apply
                connection.rollback()
                print(f"load_sql: hardening rolled back: {type(error).__name__}: {error}", file=sys.stderr)
                return 1
            finally:
                cursor.close()
            print(f"load_sql: hardened; catalogue now holds {state['actual']}")
            if not state["ok"]:
                print(f"load_sql: hardening incomplete — missing {state['missing']}, "
                      f"unexpected {state['unexpected']}", file=sys.stderr)
                return 1
    finally:
        connection.close()
    print(f"load_sql: {corpus} -> {counts['profiles']} people, {counts['encounters']} encounters, "
          f"{counts['records']} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
