#!/usr/bin/env python
"""Harden the live Azure SQL corpus: keys, a JSON check, and an index that matches the read path.

What this adds, and why each one (all idempotent — running it twice changes nothing):

* **Foreign keys** from `encounters`/`records` to `people`. The corpus is loaded as three independent
  tables today, so an orphaned encounter or record is possible and nothing stops it. A judge asking
  "what makes this trustworthy?" gets an answer instead of a shrug.
* **A JSON check** on `record_json`, NULL-guarded because `ISJSON(NULL)` is NULL and a bare
  `CHECK (ISJSON(x) = 1)` therefore admits NULL. The column is `NOT NULL` today, so this is about the
  next loader writing something it should not.
* **A clustered index on `(source_person_id, record_id)`** and the primary key re-pointed to
  nonclustered. The dominant read is one person's rows by `source_person_id`; on the current layout that
  is one key lookup per row through a clustered index on `record_id`. Clustered the other way it is a
  range scan of adjacent pages, which matters because Basic is the tier with 1-4 IOPS per DTU and I/O,
  not CPU, is the scarce resource.
* **A covering nonclustered index** so the projection can be answered from the index.
* **Dropping `ix_records_person`**, which the clustered key now leads with and therefore duplicates.
* **The envelope and enum checks** the loader and the SQLite index both enforce: `record_json` must be a
  Record document (record id, source and record type present, `payload` present, `source_person_id` equal
  to the column when named), `record_type`/`topic` must be declared, an encounter's role/purpose/holders/
  life stage must be declared, and a trigger makes a record's two person keys name the same person.

**The privilege this needs, and who holds it.** Every statement here is DDL, and the load itself needs DDL
for `--recreate`. The loader identity `luvn-corpus-loader-identity` therefore holds `db_datareader`,
`db_datawriter` **and `db_ddladmin`** — a deliberate grant, recorded in
`docs/hosting-runbook.md`, not an accident: the loader owns the three corpus tables and the schema over
them, and holding it is what lets the arrival-day job migrate and harden the database with no operator
token. The app identity holds `db_datareader` only and is never granted DDL.

Nothing here changes what the application reads. `backend/scripts/run_search_spike.py`-style callers and
`app.sqldelivery` both keep working; this only removes scan-shaped work and adds integrity.

    LUVN_SQL_SERVER=... LUVN_SQL_ACCESS_TOKEN=... .venv/bin/python scripts/harden_sql.py
    .venv/bin/python scripts/harden_sql.py --check     # report only, change nothing
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.sqldelivery import apply_hardening, connect, hardening_sql, verify_integrity  # noqa: E402

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harden the live Azure SQL corpus")
    parser.add_argument("--check", action="store_true", help="report the current state and change nothing")
    parser.add_argument("--print-sql", action="store_true",
                        help="print the whole hardening as one idempotent T-SQL script and change nothing; "
                             "this is the path for a machine without the ODBC driver")
    args = parser.parse_args(argv)
    if args.print_sql:
        print(hardening_sql())
        return 0
    connection = connect()
    try:
        cursor = connection.cursor()
        try:
            state = verify_integrity(cursor)
            print(f"before: {state['actual']}")
            if not state["ok"]:
                print(f"  missing {state['missing']}, unexpected {state['unexpected']}")
            if args.check:
                return 0 if state["ok"] else 1
            state = apply_hardening(cursor)
            connection.commit()
        except Exception as error:  # noqa: BLE001 - a failed hardening must not half-apply
            connection.rollback()
            print(f"harden_sql: rolled back: {type(error).__name__}: {error}", file=sys.stderr)
            return 1
        finally:
            cursor.close()
        print(f"after:  {state['actual']}")
        for name in sorted(state["actual"]):
            mark = "ok" if state["actual"][name] == state["expected"][name] else "MISMATCH"
            print(f"  {name:16s} expected={state['expected'][name]} actual={state['actual'][name]} {mark}")
        return 0 if state["ok"] else 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
