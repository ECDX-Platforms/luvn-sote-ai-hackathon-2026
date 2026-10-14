"""Precomputed briefings: the cache the day view, the picture route and the assistant read.

"Zero touch" means the briefing is ready before the professional clicks. The pipeline takes seconds per
encounter (Decision-1 over every permitted record, then narration and its verification), so a briefing is
built once -- by the warm-up job for the coming days' appointments, or by the first request that misses --
and every later read is served from here.

The key is everything a stored picture depends on: the encounter, the corpus generation the loader stamped,
the selection rule version, and the model-side versions (the narration agent, the decision model, the
pre-fill version). A build under any other combination is a different briefing, never served for this one.
`today` is stored beside the row rather than in the key: a picture built for another date is stale, because
temporal status and the form catalogue depend on it, and `is_fresh` says so.

The cache holds pictures only for encounters the pipeline already decided on. It never widens what a role
may see: the stored object IS the Picture the route would have built, after `policy.cut`, and the route
still applies its own authorization and writes its own audit row on every read.

Two backends. The SQL-backed index stores rows in the `briefings` table beside the corpus (the loader creates
it; the app writes it). The fixture and delivery-directory backends get a no-op cache unless
`LUVN_BRIEFING_CACHE=memory` is set, so tests and local runs keep building fresh pictures per request.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable, Awaitable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from threading import RLock
from typing import Any

from . import decision_model
from .picture import Picture, RecordView
from .selection import BASELINE_VERSION, RULE_VERSION

from .prefill import PREFILL_VERSION


def agent_version() -> str:
    """The model-side part of the key: narration agent, decision model, pre-fill version, baseline version."""
    agent = f"{os.environ.get('LUNA_FOUNDRY_AGENT_NAME', '') or '-'}:{os.environ.get('LUNA_FOUNDRY_AGENT_VERSION', '') or '-'}"
    return f"{agent}|{decision_model.MODEL}|{PREFILL_VERSION}|{BASELINE_VERSION}"


@dataclass(frozen=True)
class BriefingKey:
    encounter_id: str
    corpus_generation: str
    rule_version: str
    agent_version: str

    @classmethod
    def for_encounter(cls, encounter_id: str, corpus_generation: str | None) -> "BriefingKey":
        return cls(encounter_id=encounter_id, corpus_generation=corpus_generation or "unknown",
                   rule_version=RULE_VERSION, agent_version=agent_version())


@dataclass(frozen=True)
class StoredBriefing:
    key: BriefingKey
    today: date
    built_at: datetime
    status: str
    picture_json: str
    attention: dict[str, Any]

    def picture(self) -> Picture:
        return Picture.model_validate_json(self.picture_json)


def is_fresh(stored: StoredBriefing | None, today: date) -> bool:
    return stored is not None and stored.status == "ready" and stored.today == today


def _title(view: RecordView) -> str:
    payload = view.payload or {}
    for key in ("otsikko", "lääke", "nimi", "tutkimus", "päätös", "aihe", "instrumentti"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return view.record_type


def attention_summary(picture: Picture) -> dict[str, Any]:
    """What the day view shows before the click, computed over the picture's own rendered rows only.

    Every number here is a count of rows the same picture renders, so nothing withheld enters it
    (CONTEXT.md: no derived number may require a withheld denominator). The `lead` is the record Decision-1
    gave the highest expected urgency, with the legend's own wording; it is an ordering signal, never a
    care-urgency claim, and the UI says so.
    """
    views = [*picture.records.priority, *picture.records.accessible_other]
    by_id = {view.record_id: view for view in views}
    risk_rule = next((entry.rule_id for entry in picture.trace if entry.table == "risk"), None)
    active_medications = sum(1 for view in views
                             if view.record_type == "lääkitys" and (view.payload or {}).get("tila") == "aktiivinen")
    lead: dict[str, Any] | None = None
    if picture.selection is not None:
        best: tuple[float, str] | None = None
        for note in picture.selection.decision_notes:
            distribution = (note.gate_probabilities or {}).get("urgency") or {}
            expected = 0.0
            weight = 0.0
            for level, probability in distribution.items():
                try:
                    expected += int(level) * float(probability)
                    weight += float(probability)
                except (TypeError, ValueError):
                    continue
            if weight <= 0 or note.record_id not in by_id:
                continue
            if best is None or expected > best[0]:
                best = (expected, note.record_id)
        if best is not None:
            level = max(0, min(len(decision_model.URGENCY_LEVELS) - 1, round(best[0])))
            lead = {"record_id": best[1], "title": _title(by_id[best[1]]), "urgency_level": level,
                    "urgency_text": decision_model.URGENCY_LEVELS[level]}
    # Decision-1's time-sensitivity, counted over rendered rows only: a note it called `current` that the same
    # picture shows. None unless Decision-1 answered (`complete` or `partial`), so a chip is never a zero that
    # means "not asked" or "could not be asked".
    current_by_model: int | None = None
    if picture.selection is not None and picture.selection.decision_status in {"complete", "partial"}:
        current_by_model = len({note.record_id for note in picture.selection.decision_notes
                                if note.time_sensitivity == "current" and note.record_id in by_id})
    return {
        "risk_level": picture.risk.risk_level,
        "risk_reason": picture.risk.reason,
        "risk_rule_id": risk_rule,
        "conflicts": len(picture.conflicts),
        "active_medications": active_medications,
        "record_count": len(views),
        "narration_accepted": bool(picture.grounded and picture.narrative),
        "proposals": len(picture.proposals),
        "actions": len(picture.actions),
        "lead": lead,
        "current_by_model": current_by_model,
    }


class NullBriefingCache:
    """No cache: every read misses and every write is dropped. The fixture and delivery backends' default."""

    persists = False  # a build is never reused, so a caller may skip the work only a reuse would pay for

    def read(self, key: BriefingKey) -> StoredBriefing | None:
        return None

    def write(self, key: BriefingKey, today: date, picture: Picture, attention: dict[str, Any],
              status: str = "ready") -> None:
        return None


class MemoryBriefingCache:
    """An in-process cache, for local runs and tests (`LUVN_BRIEFING_CACHE=memory`)."""

    persists = True

    def __init__(self) -> None:
        self._rows: dict[BriefingKey, StoredBriefing] = {}
        self._lock = RLock()

    def read(self, key: BriefingKey) -> StoredBriefing | None:
        with self._lock:
            return self._rows.get(key)

    def write(self, key: BriefingKey, today: date, picture: Picture, attention: dict[str, Any],
              status: str = "ready") -> None:
        with self._lock:
            self._rows[key] = StoredBriefing(key=key, today=today, built_at=datetime.now(timezone.utc),
                                             status=status, picture_json=picture.model_dump_json(),
                                             attention=dict(attention))

    def __len__(self) -> int:
        return len(self._rows)


class SqlBriefingCache:
    """The `briefings` table beside the corpus. Shares the index's connection and lock; one row per key."""

    persists = True

    READ = ("SELECT today, built_at, status, picture_json, attention_json FROM briefings "
            "WHERE encounter_id = ? AND corpus_generation = ? AND rule_version = ? AND agent_version = ?")
    DELETE = ("DELETE FROM briefings WHERE encounter_id = ? AND corpus_generation = ? AND rule_version = ? "
              "AND agent_version = ?")
    INSERT = ("INSERT INTO briefings (encounter_id, corpus_generation, rule_version, agent_version, built_at, "
              "today, status, picture_json, attention_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")

    def __init__(self, connection: Any, lock: RLock) -> None:
        self._connection = connection
        self._lock = lock

    def read(self, key: BriefingKey) -> StoredBriefing | None:
        with self._lock:
            cursor = self._connection.cursor()
            try:
                cursor.execute(self.READ, key.encounter_id, key.corpus_generation, key.rule_version,
                               key.agent_version)
                row = cursor.fetchone()
            finally:
                cursor.close()
        if row is None:
            return None
        today = row[0] if isinstance(row[0], date) else date.fromisoformat(str(row[0])[:10])
        built = row[1] if isinstance(row[1], datetime) else datetime.fromisoformat(str(row[1]))
        return StoredBriefing(key=key, today=today, built_at=built, status=str(row[2]),
                              picture_json=str(row[3]), attention=json.loads(row[4] or "{}"))

    def write(self, key: BriefingKey, today: date, picture: Picture, attention: dict[str, Any],
              status: str = "ready") -> None:
        params = (key.encounter_id, key.corpus_generation, key.rule_version, key.agent_version)
        with self._lock:
            cursor = self._connection.cursor()
            try:
                cursor.execute(self.DELETE, *params)
                cursor.execute(self.INSERT, *params, datetime.now(timezone.utc), today, status,
                               picture.model_dump_json(), json.dumps(attention, ensure_ascii=False))
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
            finally:
                cursor.close()


def cache_for_local_backend():
    """The cache a non-SQL backend gets: memory when opted in, otherwise none."""
    return MemoryBriefingCache() if os.environ.get("LUVN_BRIEFING_CACHE") == "memory" else NullBriefingCache()


Builder = Callable[..., Awaitable[Picture]]


async def build_and_cache(store, encounter_id: str, *, build: Builder, today: date | None = None,
                          started_at: float | None = None, preparation_deadline: float | None = None,
                          foundry_enabled: bool = True) -> Picture:
    """Build the encounter's picture through `build` (the pipeline's entry point) and store it under its key.

    `build` is injected rather than imported so the picture route keeps calling the function it always
    called -- and so a test can observe the call. Decision-1 is always on: the stored picture's record
    selection is the evidence contract every reader (the briefing, the assistant) relies on. The write is
    best-effort: a cache that cannot be written never withholds the briefing that was just built.
    """
    today = today or date.today()
    picture = await build(encounter_id, personas=store.persona_index(), encounters=store.encounter_index(),
                          today=today, started_at=started_at, preparation_deadline=preparation_deadline,
                          decision_enabled=True, foundry_enabled=foundry_enabled)
    key = BriefingKey.for_encounter(encounter_id, _generation(store))
    try:
        store.briefing_cache.write(key, today, picture, attention_summary(picture))
    except Exception as exc:  # noqa: BLE001 -- the briefing is the contract; the cache is a speed-up
        print(f"[briefings] cache write failed for {encounter_id}: {type(exc).__name__}: {exc}", flush=True)
    return picture


def _generation(store) -> str | None:
    try:
        return store.corpus_generation()
    except Exception:  # noqa: BLE001 -- a store that cannot name its generation gets the "unknown" key
        return None


def read_fresh(store, encounter_id: str, today: date) -> Picture | None:
    """The cached picture for today, or None on a miss or a stale row."""
    key = BriefingKey.for_encounter(encounter_id, _generation(store))
    try:
        stored = store.briefing_cache.read(key)
    except Exception as exc:  # noqa: BLE001 -- a cache that cannot be read is a miss, not an outage
        print(f"[briefings] cache read failed for {encounter_id}: {type(exc).__name__}: {exc}", flush=True)
        return None
    return stored.picture() if is_fresh(stored, today) else None


def read_attention(store, encounter_id: str, today: date) -> dict[str, Any] | None:
    """The cached attention summary for today, or None. Reads the row's summary column, never the picture."""
    key = BriefingKey.for_encounter(encounter_id, _generation(store))
    try:
        stored = store.briefing_cache.read(key)
    except Exception:  # noqa: BLE001
        return None
    return dict(stored.attention) if is_fresh(stored, today) else None


async def warm(store, encounter_ids: list[str], *, build: Builder, today: date | None = None,
               concurrency: int = 2, log: Callable[[str], None] = print) -> dict[str, int]:
    """Build every listed encounter that has no fresh row, `concurrency` at a time. Returns the counts."""
    today = today or date.today()
    counts = {"requested": len(encounter_ids), "fresh": 0, "built": 0, "failed": 0}
    gate = asyncio.Semaphore(max(1, concurrency))
    seen: set[str] = set()

    async def one(encounter_id: str) -> None:
        if read_fresh(store, encounter_id, today) is not None:
            counts["fresh"] += 1
            return
        async with gate:
            started = time.monotonic()
            try:
                await build_and_cache(store, encounter_id, build=build, today=today)
                counts["built"] += 1
                log(f"warm: built {encounter_id} in {round(time.monotonic() - started, 1)}s")
            except Exception as exc:  # noqa: BLE001 -- one failed encounter must not stop the job
                counts["failed"] += 1
                log(f"warm: FAILED {encounter_id}: {type(exc).__name__}: {exc}")

    await asyncio.gather(*(one(encounter_id) for encounter_id in encounter_ids
                           if encounter_id and encounter_id not in seen and not seen.add(encounter_id)))
    return counts
