"""Warm the briefings cache for the coming days' appointments.

    python -m app.warmup --days 3 [--concurrency 2] [--role sairaanhoitaja]

Runs as the `luvn-warm-briefings` Container Apps job on the app's own image and identity, and after every
corpus load (the generation is part of the cache key, so a reload empties the cache in effect). It reads the
appointments for each day from today onward, keeps the ones that open a lens, and builds every briefing
that has no fresh row. It never deletes a row and never touches the corpus tables.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

from . import briefings
from .pipeline import build_picture_for_encounter
from .store import Store


def encounter_ids_for_days(store: Store, start: date, days: int, role: str | None = None) -> list[str]:
    ids: list[str] = []
    for offset in range(max(1, days)):
        for appointment in store.appointments_for_day(start + timedelta(days=offset)):
            if appointment.encounter_id and (role is None or appointment.role == role):
                ids.append(appointment.encounter_id)
    return list(dict.fromkeys(ids))


async def run(store: Store, start: date, days: int, concurrency: int, role: str | None) -> dict[str, int]:
    ids = encounter_ids_for_days(store, start, days, role)
    print(f"warmup: {len(ids)} encounter(s) scheduled over {days} day(s) from {start.isoformat()}", flush=True)
    counts = await briefings.warm(store, ids, build=build_picture_for_encounter, today=start,
                                  concurrency=concurrency, log=lambda line: print(line, flush=True))
    print(f"warmup: {counts}", flush=True)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--role", default=None)
    parser.add_argument("--start", default=None, help="ISO date; default today")
    args = parser.parse_args(argv)
    start = date.fromisoformat(args.start) if args.start else date.today()
    store = Store()
    counts = asyncio.run(run(store, start, args.days, args.concurrency, args.role))
    return 1 if counts["failed"] and not counts["built"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
