"""Same-origin API and built-SPA surface for the synthetic LUVN demo."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import access, actor as actor_module, assistant, briefings, chat, guidance, llm, provenance
from .decide import decide
from .encounters import Encounter, EncounterMetadata, resolve_encounter
from .pipeline import PREPARATION_WINDOW_S, build_picture_for_encounter
from .picture import Picture
from .policy import UnknownVocabularyError, cut_for_encounter, roles as policy_roles
from .proposals import proposals_for_encounter
from .selection import RULE_VERSION, baseline
from .sqldelivery import CorruptCorpusError
from .store import DecisionConflict, Store, get_store
from .upm import Persona

app = FastAPI(title="LUVN decision-guided situation picture")


@app.exception_handler(UnknownVocabularyError)
def unknown_vocabulary(request: Request, exc: UnknownVocabularyError) -> JSONResponse:
    """An undeclared vocabulary value in a corpus record is a 4xx, not a 500.

    `policy.cut` deliberately refuses an undeclared `topic` or `record_type` instead of serving an empty
    permitted set, which is the right failure. Until now it surfaced as an unhandled 500 with a traceback:
    an operator whose hand-edited corpus carried one misspelled topic got an opaque error and no way to see
    which value, or which set to map it into. The body names both, and nothing else — no path, no traceback.
    """
    return JSONResponse(status_code=422, content={
        "detail": str(exc),
        "axis": exc.axis,
        "unknown": exc.unknown,
        "known": exc.known,
    })


@app.exception_handler(access.AccessPolicyError)
def unusable_access_policy(request: Request, exc: access.AccessPolicyError) -> JSONResponse:
    """A configured access policy that cannot be used is a 5xx that names the defect, never open access.

    This is the whole point of parsing the policy at first use rather than trusting it: the dangerous
    failure is the silent one, where a typo in the variable makes the app fall back to serving every
    encounter to everybody. The body names the variable and the field that failed, which is what an
    operator watching a whole deployment answer 500 needs, and it carries no path, no traceback and none of
    the policy's own values — an encounter id or a UPN quoted back would be exactly what a caller probing a
    misconfigured instance is looking for, and the file path would hand them the mount layout. That
    withholding is deliberate and narrow, not a black box: `AccessPolicyError.public_message()` keeps the
    location (`rules[0].upns`, `LUVN_ACCESS_POLICY_FILE`) and drops the value, and the two classes that fire
    on a plausible value — whitespace in an id, an id that is not an id — say what is wrong and where
    without repeating it. The *log* line below is the full detail, so the operator loses nothing by that
    and does not have to reproduce the fault to see it. Deliberately not cached: an operator fixes the
    variable or the mounted file, restarts, and the next request re-reads it.
    """
    print(f"[access] unusable access policy: {exc}", file=sys.stderr)
    return JSONResponse(status_code=500, content={
        "detail": f"the deployment's access policy is unusable: {exc.public_message()}",
        "reason": "access_policy_unusable",
    })


# The browser origins allowed to call this API. The shipped app is same-origin — this process serves the
# built SPA — so no cross-origin browser request is legitimate and the hosted deployment allows none.
# Development is the one exception: Vite serves the SPA from a loopback port. That exception is gated on
# `LUVN_HOSTED`, because with `allow_credentials` a permissive loopback regex in the hosted app lets any
# page served from a local port read the API with the victim's Entra session cookie, and write as the
# victim. `LUVN_DEV_ORIGINS` pins exact origins for a development deployment that is not loopback.
LOOPBACK_ORIGIN_REGEX = r"http://(localhost|127\.0\.0\.1)(:\d+)?"


def _cors_configuration() -> tuple[list[str], str | None]:
    """`(exact_origins, origin_regex)` for this process; `([], None)` means install no CORS at all."""
    if os.environ.get("LUVN_HOSTED") == "1":
        return [], None
    configured = os.environ.get("LUVN_DEV_ORIGINS")
    if configured:
        return [origin.strip() for origin in configured.split(",") if origin.strip()], None
    return [], LOOPBACK_ORIGIN_REGEX


def install_cors(application: FastAPI) -> None:
    """Install CORS only when there is an origin to allow.

    `expose_headers` is not cosmetic: without it a browser consumer on an allowed origin receives the
    payload but cannot read the marking headers, because CORS hides every non-safelisted response header
    from `fetch()`. The marking would be on the wire and invisible to the reader it exists for.
    """
    origins, origin_regex = _cors_configuration()
    if not origins and origin_regex is None:
        return
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=origin_regex,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=list(provenance.EXPOSED_HEADERS),
    )


install_cors(app)

PURPOSE_LABELS = {
    "medication_follow_up": "Lääkityksen seuranta",
    "mental_health_assessment": "Mielialan arviointi",
    "functioning_service_needs": "Palvelutarpeen arviointi",
    "child_health_checkup": "Neuvolan terveystarkastus",
}

# The catalogue endpoint is paged, because a professional reads a worklist, not a dump. The default is a
# screen-sized page. Page size and corpus size are now different questions: the release carries 38,469
# encounters across two cohorts, so a caller pages through them rather than asking for the whole catalogue in
# one response, and this maximum only bounds how much a single call may return.
DEFAULT_ENCOUNTER_LIMIT = 50
MAX_ENCOUNTER_LIMIT = 500
MAX_ENCOUNTER_SEARCH_CHARS = 200
# `potilas` is a policy comparison key, never a professional lens: the concept boundary register records
# the citizen/self view as REJECTED for this product. The catalogue's roster and role count exclude it,
# exactly as the worklist does, so the two surfaces cannot disagree about who is on offer.
NON_PROFESSIONAL_ROLES = frozenset({"potilas"})


def _hosted() -> bool:
    return os.environ.get("LUVN_HOSTED") == "1"


def _fold(value: str) -> str:
    """A case- and diacritic-insensitive comparison key for a human search.

    Finnish characters are the point and not an edge case: a professional searching `MÄKINEN` must match
    `Mäkinen`, and one whose keyboard will not produce `ä` must still find the same row by typing
    `Makinen`. NFKD followed by combining-mark removal gives the second, `casefold()` the first. This is a
    filter over labels the endpoint already returns — it grants nothing, and it hides nothing that was on
    the page.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(character for character in decomposed
                   if not unicodedata.combining(character)).casefold()


def _encounter_summary(entries: list[EncounterMetadata]) -> dict:
    """The encounter/person/purpose counts over the entries it is handed.

    `people` is `COUNT(DISTINCT source_person_id)`, not distinct labels: two people can share a display name
    and one person can carry two, and the count of people must not depend on either.
    """
    return {
        "encounters": len(entries),
        "people": len({entry.source_person_id for entry in entries if entry.source_person_id is not None}),
        "purposes": len({entry.purpose for entry in entries}),
    }


def _role_label(entry: EncounterMetadata, roles: dict) -> str:
    return roles[entry.role]["label"]


def _professional_roster(entries: list[EncounterMetadata], roles: dict) -> list[dict]:
    """The professional roles the whole catalogue carries, in catalogue order, once each."""
    roster: dict[str, str] = {}
    for entry in entries:
        if not entry.role or entry.role in NON_PROFESSIONAL_ROLES or entry.role in roster:
            continue
        roster[entry.role] = _role_label(entry, roles)
    return [{"role": role, "label": label} for role, label in roster.items()]


def _worklist_row(entry: EncounterMetadata, roles: dict) -> dict:
    """The row shape the SPA navigates by. Built from metadata alone, so a search runs over the same labels."""
    return {
        "encounter_id": entry.encounter_id,
        "role": entry.role,
        "role_label": _role_label(entry, roles),
        "purpose": entry.purpose,
        "purpose_label": PURPOSE_LABELS[entry.purpose],
        # The store's own label, or none: the catalogue keeps no map of display names.
        "example_label": entry.example_label or "",
        "picker_label": f"{PURPOSE_LABELS[entry.purpose]} · {roles[entry.role]['label']}",
    }


def _search_text(row: dict) -> str:
    """The displayed labels a search matches against, in the order they have always been folded."""
    return " ".join((row["example_label"], row["purpose_label"], row["picker_label"], row["role_label"]))


def _worklist_entries(store: Store, scope: access.EncounterScope | None = None) -> list[EncounterMetadata]:
    """The caller's encounters as metadata, in id order: the one set every worklist number is taken over.

    `scope` is `None` when the deployment configured no access policy: the open demo behaviour, where
    every authenticated presenter may read every encounter. A configured policy resolves to a scope (an
    unnamed principal resolves to an *empty* one). The scope is applied here, before any page, count, roster
    or search, so a withheld encounter never reaches a total, an aggregate or a row.

    This reads no person and no record. `store.encounter_metadata()` is the encounter table joined to the
    people table, and the picture route is the only path that resolves a person's records.
    """
    # The order is the handler's contract, not the store's: offset paging is stable only over a stable set.
    return sorted((entry for entry in store.encounter_metadata()
                   if scope is None or scope.contains(entry.encounter_id)),
                  key=lambda entry: entry.encounter_id)


def _resolve(encounter_id: str, store: Store) -> tuple[Encounter, Persona]:
    encounters = store.encounter_index()
    if encounter_id not in encounters:
        raise HTTPException(status_code=404, detail="unknown encounter")
    try:
        encounter, persona = resolve_encounter(encounter_id, store.persona_index(), encounters)
        if encounter.source_person_id is None:
            raise ValueError("unbound encounter is local regression only")
        return encounter, persona
    except CorruptCorpusError as exc:
        # A `ValidationError` is a `ValueError`, so a corrupt stored record used to be reported as
        # `404 unknown encounter` -- the caller is told the person does not exist when the truth is that
        # the corpus is broken. The two are distinct: this one is a 5xx that names the defect.
        raise HTTPException(status_code=503, detail=f"the stored corpus is corrupt: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="unknown encounter") from exc


def _encounter_proposals(encounter_id: str, store: Store, today: date | None = None):
    encounter, persona = _resolve(encounter_id, store)
    today = today or date.today()
    permitted = cut_for_encounter(persona, encounter)
    baseline_records, _ = baseline(permitted, today)
    decision = decide(permitted)
    return encounter, persona, proposals_for_encounter(
        encounter, permitted, baseline_records, decision["conflicts"], today
    )


# ---------------------------------------------------------------------------------------------------
# The single enforcement point for per-actor authorization.
#
# Why a dependency and not six copies of a check: every scoped route already took an `encounter_id` path
# parameter, so a dependency that resolves the caller's scope and tests that one id can be attached to
# each route in one line, and — crucially — it runs *before* the handler body, which means before
# `_resolve` decides whether the encounter exists. Putting the check inside each handler would have let a
# route resolve first and refuse second, and the two orders are not equivalent: see `require_encounter`
# for the oracle that ordering decision closes. There is no middleware, because `main.app` also serves the
# built SPA and the fallback routes, and a path-prefix guard in middleware would be a second, less visible
# copy of "which routes are encounter-scoped" that could silently disagree with the route table.
#
# The rule these two dependencies implement, in one sentence: unset policy is today's open behaviour;
# a configured policy is fail closed, where the caller's scope is the only thing the routes consult.
# ---------------------------------------------------------------------------------------------------
ENCOUNTER_FORBIDDEN_DETAIL = "this encounter is outside the scope configured for this caller"


def require_encounter(encounter_id: str, request: Request) -> access.EncounterScope | None:
    """The caller's scope, or the route refuses before anything resolves the encounter.

    With **no** policy configured (`LUVN_ACCESS_POLICY` and `LUVN_ACCESS_POLICY_FILE` unset or empty) this
    is the open demo behaviour, exactly: the ingress authenticated the caller and every authenticated
    presenter may read any encounter, so this returns `None` and the handler proceeds unchanged. With a
    policy configured it is fail closed: a principal the policy does not name resolves to an empty scope,
    and an encounter outside the scope is refused here.

    The refusal is `403` and deliberately the *same* body whether or not the encounter exists. A caller
    must not be able to tell "exists but not yours" from "does not exist": if the forbidden case answered
    403 and an unknown id answered 404, that pair would be an existence oracle over encounter ids, which
    is exactly the class of oracle this codebase closed on the chat-review route (`TURN_NOT_AVAILABLE`)
    and in the feedback route's proposal probes. The price is that a *legitimate* caller who mistypes an
    id is told "not in your scope" rather than "not found", which is a worse error message for them and
    the right trade for this product: the demo's catalogue is the picker, so a human types an id only when
    they are already outside the paths the UI offers, whereas an id-guessing caller is the adversary.
    With no policy configured the pair is unreachable anyway, so nothing about the open demo changes.

    The one-body promise is about *ids*, on the canonical paths this route serves, and it is worth being
    exact because the obvious probe is coarse: this dependency is only reached when the request matches the
    route at all. `/api/encounters/{id}/picture/` and `/api/encounters//picture` match no route, so they
    answer the application's ordinary unknown-API `404` — the same body for an in-scope, an out-of-scope and
    a nonexistent id, since no id in a non-canonical path is ever looked at. That is routing, not an
    existence signal, and it is why no aliases are registered to make a trailing slash look tidy: a second
    spelling of the path would be a second place for "which routes are encounter-scoped" to disagree with
    the route table. So: for an **out-of-scope or absent encounter id on this route's own path**, one body;
    anything that is not this route's shape is the unknown-API `404`, uniformly.

    A request with **no** principal at all is not this dependency's case: the routes that need an actor
    (`/picture`, `/chat`, `/chat/prompts`, `/feedback`, `/decisions`) also declare `require_actor`, which answers the
    caller's own `401` before this dependency runs, so a 401/403 pair says nothing about an encounter
    either. `/picture` declares `require_actor` too, so an anonymous caller there gets the same 401 and
    never reaches the scope check.
    """
    actor = actor_module.actor_from_headers(request.headers)
    scope = access.scope_for_actor(actor)
    if scope is None:
        # No policy configured: authentication is the boundary, per the decision stated in the runbook.
        return None
    if not scope.contains(encounter_id):
        raise HTTPException(status_code=403, detail=ENCOUNTER_FORBIDDEN_DETAIL)
    return scope


def require_actor(request: Request) -> actor_module.Actor:
    """The platform principal, or `401` — the same actor semantics the write routes always had.

    Declared *after* `require_encounter` in a signature on purpose: FastAPI resolves dependencies in
    declaration order, so an anonymous caller gets its `401` before scope is consulted at all. That keeps
    the per-route contract ("an authenticated professional is required") identical to what these routes
    answered before the policy existed, so the auth half of a refusal did not move when authorization
    arrived. Extracting it into a dependency rather than leaving the check in the handler body is what
    lets the ordering be the signature's, not something the reader has to reconstruct from two places.
    """
    actor = actor_module.actor_from_headers(request.headers)
    if actor is None:
        raise HTTPException(status_code=401, detail="an authenticated professional is required")
    return actor


def require_catalogue(request: Request) -> access.EncounterScope | None:
    """The caller's catalogue scope: `None` = no policy configured = the whole catalogue.

    The catalogue is a *read* of a set, so it has no 403: a principal the policy does not name gets the
    empty page (with its counts computed over that empty set), not a refusal. Refusing would be the worse
    answer for the one legitimate case — a presenter whose policy grants nothing yet should be told by an
    empty worklist that there is nothing here for them, not by an error.
    """
    return access.scope_for_actor(actor_module.actor_from_headers(request.headers))


@app.get("/health/live")
def live() -> dict:
    return {"status": "live", "revision": os.environ.get("LUVN_BUILD_REVISION", "unknown")}


@app.get("/health/ready")
def ready(store: Store = Depends(get_store)):
    revision = os.environ.get("LUVN_BUILD_REVISION", "unknown")
    static_dir = os.environ.get("LUVN_STATIC_DIR")
    if static_dir and not (Path(static_dir) / "index.html").is_file():
        return JSONResponse(status_code=503, content={"status": "not_ready", "revision": revision})
    # Readiness includes the data layer. A replica that cannot reach its corpus must not receive traffic,
    # because every briefing it accepts would fail; before this, readiness checked only the SPA and so
    # reported `ready` with the database unreachable. `None` means the runtime reads a local delivery
    # directory or fixtures and there is nothing to probe, which is not a failure.
    if store.data_layer_ready() is False:
        return JSONResponse(status_code=503, content={"status": "not_ready", "revision": revision})
    # Foundry/Decision-1 are optional stages, not application readiness dependencies.
    return {"status": "ready", "revision": revision}


@app.get("/api/diagnostics")
async def diagnostics() -> dict:
    static_dir = os.environ.get("LUVN_STATIC_DIR")
    agent_configured = llm.prompt_agent_configured()
    # The actual branch belongs to each Picture; a config/token probe cannot infer it.
    token_status = await llm.probe_prompt_agent_token() if agent_configured else "not_configured"
    return {
        "revision": os.environ.get("LUVN_BUILD_REVISION", "unknown"),
        "hosted": _hosted(),
        "spa_enabled": bool(static_dir and (Path(static_dir) / "index.html").is_file()),
        "foundry_agent": {"configured": agent_configured, "token_status": token_status},
    }


# Audience decision, stated rather than implied. The owner has since made per-actor authorization an
# opt-in deployment configuration and that is what `require_catalogue` applies: with no policy configured
# (`LUVN_ACCESS_POLICY`/`LUVN_ACCESS_POLICY_FILE` unset or empty) this is what it always was — the ingress
# authenticated the caller, and any authenticated presenter may read any encounter's catalogue. With a
# policy configured, the rows are the caller's scope and nothing else. The fixture roles remain what they
# were: presentation lenses, not authorization. See `app/access.py` for the contract and `app/main.py`
# `require_encounter` for the scoped counterpart.
@app.get("/api/encounters")
def encounters(
    limit: int = Query(DEFAULT_ENCOUNTER_LIMIT, ge=1, le=MAX_ENCOUNTER_LIMIT),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, max_length=MAX_ENCOUNTER_SEARCH_CHARS),
    role: str | None = Query(None, max_length=64),
    cursor: str | None = Query(None),
    store: Store = Depends(get_store),
    scope: access.EncounterScope | None = Depends(require_catalogue),
) -> dict:
    """One page of the operator catalogue, plus the whole-catalogue counts.

    The response is an envelope rather than a bare list, for two reasons that are the same reason. A list
    cannot say how many rows there are, so a client that receives a page cannot know whether it has the
    catalogue or the first slice of one; and a client that reduces over the whole list to get a count is
    doing the server's work on data it had to download first. The envelope carries both: `encounters` is
    the page, `total` is how many rows matched, and `aggregate` is the whole-catalogue count the dashboard
    reads without the catalogue ever crossing the wire.

    "Whole catalogue" means the caller's whole catalogue when a policy is configured: `aggregate` is
    computed over the rows the caller may read, and is unaffected by `limit`, `offset`, `q` or `role`. A
    dashboard that changed its totals when a worklist search narrowed would be reporting the search, not
    the corpus; a dashboard that counted encounters the caller cannot open would be reporting the
    operator's corpus to someone who is not allowed to see it. `aggregate.matching` is the same counts for
    the current filter, which is what a role-scoped worklist reads.

    Invalid paging is rejected, never silently clamped: `limit` outside `1..MAX_ENCOUNTER_LIMIT`, a
    negative `offset`, or an over-long `q` is a `422` from the query schema. Clamping would answer a
    different question than the one asked, and the caller would never learn it.
    """
    # `cursor` is accepted only to refuse it. Ignoring it would answer page one to a client that believes it
    # is paging, so the refusal comes before any read. Keyset paging is a separate piece (ADR-0025 §3).
    if cursor is not None:
        raise HTTPException(status_code=422, detail=CURSOR_REFUSED_DETAIL)
    entries = _worklist_entries(store, scope)
    roles = policy_roles()
    roster = _professional_roster(entries, roles)
    needle = _fold(q.strip()) if q and q.strip() else ""
    matching = [
        entry for entry in entries
        if (role is None or entry.role == role)
        and (not needle or needle in _fold(_search_text(_worklist_row(entry, roles))))
    ]
    page = [_worklist_row(entry, roles) for entry in matching[offset:offset + limit]]
    return {
        "encounters": page,
        "total": len(matching),
        "count": len(page),
        "limit": limit,
        "offset": offset,
        "query": q or "",
        "role": role,
        "aggregate": {
            "catalogue": {**_encounter_summary(entries), "roles": len(roster)},
            "roster": roster,
            "matching": _encounter_summary(matching),
        },
    }


CURSOR_REFUSED_DETAIL = ("cursor paging is not implemented: keyset paging is not available yet; "
                         "page with offset and limit")


# ADR-0025 §4: one encounter's worklist row, resolved by id and scope. Authorization is the catalogue's
# (`require_catalogue`), not the picture's, so with no policy any caller reads it and a configured policy
# filters it. The row comes from `_worklist_row`, the builder `/api/encounters` uses, so the two cannot drift.
# Reads `encounter_metadata()` only: no `records` row and no picture is resolved.
@app.get("/api/encounters/{encounter_id}")
def encounter_metadata_row(
    encounter_id: str,
    store: Store = Depends(get_store),
    scope: access.EncounterScope | None = Depends(require_catalogue),
) -> dict:
    # A refusal is the same answer an unknown id gets under a policy (`require_encounter` on `/picture`):
    # 403 with one body, decided before anything is read. With no policy the only refusal is the 404 below.
    if scope is not None and not scope.contains(encounter_id):
        raise HTTPException(status_code=403, detail=ENCOUNTER_FORBIDDEN_DETAIL)
    # ponytail: linear scan over the metadata set; add a by-id store lookup if this route becomes hot.
    entry = next((entry for entry in store.encounter_metadata() if entry.encounter_id == encounter_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown encounter")
    return _worklist_row(entry, policy_roles())


WORKLIST_DAY_SPAN = 7  # the date strip: today minus and plus this many days


def _worklist_day_rows(store: Store, day: date, role: str | None, scope: access.EncounterScope | None,
                       roles: dict) -> list[dict]:
    rows = []
    for appointment in store.appointments_for_day(day):
        if appointment.encounter_id is None or (role is not None and appointment.role != role):
            continue
        if scope is not None and not scope.contains(appointment.encounter_id):
            continue
        # A briefing is built for the date it is read on (`today`), whichever day the professional is planning;
        # readiness for tomorrow's row is therefore whether a briefing built today exists, not one built "tomorrow".
        attention = briefings.read_attention(store, appointment.encounter_id, date.today())
        rows.append({
            "appointment_id": appointment.appointment_id,
            "encounter_id": appointment.encounter_id,
            "scheduled_on": appointment.scheduled_on.isoformat(),
            "domain": appointment.domain,
            "status_text": appointment.status_text,
            "urgency_text": appointment.urgency_text,
            "contact_record_id": appointment.contact_record_id,
            "role": appointment.role,
            "role_label": roles[appointment.role]["label"] if appointment.role in roles else appointment.role,
            "purpose": appointment.purpose,
            "purpose_label": PURPOSE_LABELS.get(appointment.purpose or "", appointment.purpose),
            "example_label": appointment.example_label or "",
            "readiness": "ready" if attention is not None else "pending",
            "attention": attention,
        })
    # Urgent contacts first (the source's own wording), then a ready briefing's risk, then the id: a stable order
    # the professional can predict. Nothing here is a care-urgency claim.
    def order(row: dict) -> tuple:
        urgent = 0 if (row["urgency_text"] or "").lower().startswith("hoito akuutti") else 1
        risk = {"korkea": 0, "kohonnut": 1}.get(((row["attention"] or {}).get("risk_level") or ""), 2)
        return (urgent, risk, row["appointment_id"])
    rows.sort(key=order)
    return rows


# The day view's read (Työpäivä): one day's appointments for a role, each with the readiness and attention
# summary of its cached briefing. It reads the appointments table and the briefings cache only: no record and
# no pipeline run. The catalogue's authorization applies (`require_catalogue`): with a policy, rows outside the
# caller's scope are not returned; with none, any presenter reads any day.
@app.get("/api/worklist")
def worklist(
    day: str | None = Query(None, max_length=10),
    role: str | None = Query(None, max_length=64),
    store: Store = Depends(get_store),
    scope: access.EncounterScope | None = Depends(require_catalogue),
) -> dict:
    try:
        chosen = date.fromisoformat(day) if day else date.today()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="day must be an ISO date (YYYY-MM-DD)") from exc
    roles = policy_roles()
    if role is not None and role not in roles:
        raise HTTPException(status_code=422, detail="unknown role")
    rows = _worklist_day_rows(store, chosen, role, scope, roles)
    span_start, span_end = chosen - timedelta(days=WORKLIST_DAY_SPAN), chosen + timedelta(days=WORKLIST_DAY_SPAN)
    days = store.appointment_days(span_start, span_end, role)
    # When the briefings cache was last written, over the whole cache and not the caller's scope: a timestamp
    # is not a count, and it names no encounter, so it discloses nothing about rows outside the scope. `None`
    # means nothing has been prepared yet (or the backend has no cache), which the day view says in words.
    last_built = store.latest_briefing_built_at()
    return {
        "day": chosen.isoformat(),
        "role": role,
        "rows": rows,
        "count": len(rows),
        "ready": sum(1 for row in rows if row["readiness"] == "ready"),
        "warmup": {"last_built_at": last_built.isoformat() if last_built is not None else None},
        "days": [{"day": (span_start + timedelta(days=offset)).isoformat(),
                  "appointments": days.get((span_start + timedelta(days=offset)).isoformat(), 0)}
                 for offset in range(2 * WORKLIST_DAY_SPAN + 1)],
    }


def _audit_briefing_read(store: Store, actor: actor_module.Actor, encounter_id: str) -> None:
    """Append the one read-audit row for a briefing about to be released. Metadata only, never clinical text.

    The row carries the actor as the platform resolved it, the encounter the actor already reached, the
    corpus generation, and `permitted`. It carries no persona id, record id, evidence id, label, narrative or
    proposal, so it cannot disclose anything the caller did not already receive from the same response.
    If the row cannot be written the briefing is withheld (503): an unaudited release is not an outcome.

    `corpus_generation` is `store.corpus_generation()` — the generation the loader actually stamped on the
    data this read came from (ADR-0025 §5), not the application image's build revision: the two can differ,
    because the corpus is published under a mutable `corpus/current` pointer independently of a deploy. Only
    a SQL-backed index can name its generation (it is `load_state.generation`, read back from the same
    database); the delivery-directory and compiled-fixture backends carry no independent generation to report,
    so this is the literal `"unknown"` for them — an honest gap, not the build revision standing in for it.

    **The generation is enrichment; the row write is the contract.** They are resolved separately on purpose.
    Reading the generation can only fail where it does real work — a SQL-backed index, never a fixture — so
    when it was evaluated inside the same `try` as the write, a failed lookup took the release down with it and
    every briefing answered 503. A store that cannot name its generation now reports `"unknown"`; only a store
    that cannot *write the row* withholds the briefing.
    """
    try:
        generation = store.corpus_generation() or "unknown"
    except Exception as exc:  # noqa: BLE001 -- enrichment, never a reason to refuse the release
        print(f"[audit] corpus generation not readable: {type(exc).__name__}", file=sys.stderr)
        generation = "unknown"
    try:
        store.add_read_audit({
            **actor.as_record(),
            "encounter_id": encounter_id,
            "corpus_generation": generation,
            "permitted": True,
        }, uuid4().hex)
    except Exception as exc:  # noqa: BLE001 -- an audit that cannot be written is an explicit refusal
        print(f"[audit] briefing read not recorded: {type(exc).__name__}", file=sys.stderr)
        raise HTTPException(status_code=503, detail="the briefing was not released: the read could not be recorded") from exc


# The same audience decision as the catalogue above. This route is encounter-scoped, so per-actor
# authorization is applied by `require_encounter` before the handler body runs — before `_resolve` gets to
# answer whether the encounter exists. It also declares `require_actor` ahead of that dependency, so an
# anonymous caller gets the 401 rather than a scope answer, and each released read writes one audit row. With no policy configured the dependency returns `None` and every
# authenticated presenter may read any encounter's picture, exactly as before. The fixture role is which
# lens is computed, not who is allowed to look.
@app.get("/api/encounters/{encounter_id}/picture", response_model=Picture)
async def encounter_picture(
    encounter_id: str,
    response: Response,
    mode: Literal["normal", "decision-off", "foundry-off"] = "normal",
    store: Store = Depends(get_store),
    actor: actor_module.Actor = Depends(require_actor),
    scope: access.EncounterScope | None = Depends(require_encounter),
) -> Picture:
    # Anchor the shared preparation budget at handler entry, before any work.
    started_at = time.monotonic()
    deadline = started_at + PREPARATION_WINDOW_S
    _resolve(encounter_id, store)
    today = date.today()
    # The normal picture is served from the briefings cache when a fresh row exists (built by the warm-up job
    # or by an earlier miss); the compare arms are always rebuilt, because they are the question "what would
    # this look like without a model" asked now. Authorization and the audit row are per read either way.
    picture = briefings.read_fresh(store, encounter_id, today) if mode == "normal" else None
    served_from_cache = picture is not None
    if picture is None and mode == "normal":
        picture = await briefings.build_and_cache(
            store, encounter_id, build=build_picture_for_encounter, today=today,
            started_at=started_at, preparation_deadline=deadline)
    elif picture is None:
        picture = await build_picture_for_encounter(
            encounter_id,
            personas=store.persona_index(),
            encounters=store.encounter_index(),
            preparation_deadline=deadline,
            started_at=started_at,
            decision_enabled=mode != "decision-off",
            foundry_enabled=mode != "foundry-off",
        )
    _audit_briefing_read(store, actor, encounter_id)
    response.headers["X-LUVN-Briefing-Source"] = "cache" if served_from_cache else "built"
    # The machine-readable marking for model-drafted text (Art. 50(2), appraisal in
    # `docs/regulatory-position.md` §5). The condition is the whole semantic one, stated explicitly:
    # prose was generated, and the pipeline accepted it. `generated` alone is not enough —
    # `narration.py` maps `candidate_invalid`, `support_failed`, `support_invalid`, `unsupported` and
    # `unknown` to `generated` as well, each with no narrative, and none of those may be marked. A
    # fail-closed view emits nothing, because everything in it is deterministic.
    emitted = (
        picture.narration_generation_status == "generated"
        and picture.narration_acceptance_status == "accepted"
        and bool(picture.narrative)
    )
    for name, value in provenance.marking_headers(
        generated=emitted,
        model=picture.narration_generation_model,
        deployment=picture.narration_generation_deployment,
    ).items():
        response.headers[name] = value
    return picture


def _decided_at(entry: dict) -> str | None:
    """A decision's server timestamp as an ISO string, whatever the storage seam returned.

    The backends disagree on the in-memory type (`jsonl` parses the line to a `datetime`, a Blob read does
    the same, and a hand-built memory entry may carry either), so the wire conversion belongs here rather
    than in an assumption about one of them.
    """
    value = entry.get("recorded_at")
    return value.isoformat() if hasattr(value, "isoformat") else value


# The decisions read is deliberately NOT folded into the Picture. `Picture` is a pure function of
# (encounter, mode): it is compared between the normal and `-off` arms, and a capture of it is a
# reproducible artifact. A per-caller field would make every one of those depend on who asked, so the
# caller's own decision state gets its own route keyed on the encounter.
#
# What this route may say is narrower than what storage holds: it returns the calling principal's own
# decisions and nothing at all about any other principal. When another professional has decided a proposal
# the caller has not, that proposal is simply absent here — no count, no boolean, no "handled" flag,
# because the caller's answer must not disclose that a colleague acted. The per-actor split that lets two
# professionals decide the same proposal independently is what keeps this honest: the other decision is not
# a conflict for this caller, so there is nothing to report.
@app.get("/api/encounters/{encounter_id}/decisions")
def encounter_decisions(
    encounter_id: str,
    store: Store = Depends(get_store),
    actor: actor_module.Actor = Depends(require_actor),
    scope: access.EncounterScope | None = Depends(require_encounter),
) -> dict:
    """What **this** professional has already decided on this encounter.

    Reads the platform principal exactly as the write routes do, through `require_actor`: without one there
    is no "own" decision to return, so the answer is `401` rather than an anonymous subset.

    The body contains only what a decision is: the proposal or suggestion id, the action, and when it was
    decided. It carries no actor id (the caller is the actor), no note, no proposal text and no evidence
    ids — those live in the audit record and in the briefing, not in this read. A chat decision carries
    `turn_id` because suggestion ids restart at `s0` on every turn, so the turn is part of the subject's
    identity, not extra disclosure.
    """
    encounter, persona = _resolve(encounter_id, store)
    proposals = store.proposal_decisions(persona.persona_id, encounter.encounter_id, actor.actor_id)
    suggestions = store.chat_decisions(persona.persona_id, encounter.encounter_id, actor.actor_id)
    return {
        "encounter_id": encounter.encounter_id,
        # Ordered by decision time then id so a caller's own page is stable; the caller sees only rows
        # already filtered to that caller, so the ordering reveals nothing about anyone else.
        "proposals": sorted(
            ({"proposal_id": entry["proposal_id"], "action": entry["action"],
              "decided_at": _decided_at(entry)} for entry in proposals),
            key=lambda row: (row["decided_at"] or "", row["proposal_id"]),
        ),
        "chat_suggestions": sorted(
            ({"turn_id": entry["turn_id"], "suggestion_id": entry["suggestion_id"], "action": entry["action"],
              "decided_at": _decided_at(entry)} for entry in suggestions),
            key=lambda row: (row["decided_at"] or "", row["turn_id"], row["suggestion_id"]),
        ),
    }


class PrefillReviewRequest(BaseModel):
    """One verdict on one pre-filled form answer. `edited_text` carries the professional's wording for
    `muokattu` and nothing for the other two verdicts, so a verdict never carries text it did not need."""

    model_config = ConfigDict(extra="forbid", strict=True)

    # The id becomes a storage-key component (`prefill-review-{review_id}`); the blob guard caps the whole key
    # at 128 characters, so the limit accounts for the prefix (see `ChatReviewRequest`).
    review_id: str = Field(min_length=1, max_length=128 - len("prefill-review-"), pattern=r"^[A-Za-z0-9_-]+$")
    form_id: str = Field(min_length=1, max_length=80)
    question_id: str = Field(min_length=1, max_length=80)
    verdict: Literal["vahvistettu", "muokattu", "hylätty"]
    edited_text: str | None = Field(default=None, max_length=2000)


# The accept / edit / reject rail on the pre-filled form. A verdict is a professional's statement about one
# prefilled answer and is stored as that, append-only, under the actor who made it. Nothing is written back
# to the form, the record the answer was taken from, or the cached briefing: the pre-fill stays a proposal
# and the verdict stays beside it. Reads are per actor, as `/decisions` is: the body names what THIS caller
# decided and never that a colleague decided anything.
@app.post("/api/encounters/{encounter_id}/prefill/reviews")
def prefill_review(
    encounter_id: str,
    review: PrefillReviewRequest,
    store: Store = Depends(get_store),
    actor: actor_module.Actor = Depends(require_actor),
    scope: access.EncounterScope | None = Depends(require_encounter),
) -> dict:
    # Request-shape validation first, before any lookup or write, for the same reason as on `/feedback`:
    # this 400 must not become reachable only for an encounter that exists.
    if review.verdict == "muokattu":
        if not review.edited_text or not review.edited_text.strip():
            raise HTTPException(status_code=400, detail="edited text is required for muokattu")
    elif review.edited_text is not None:
        raise HTTPException(status_code=400, detail="edited text is only accepted with muokattu")
    encounter, persona = _resolve(encounter_id, store)
    try:
        generation = store.corpus_generation() or "unknown"
    except Exception as exc:  # noqa: BLE001 -- enrichment, never a reason to refuse the verdict
        print(f"[audit] corpus generation not readable: {type(exc).__name__}", file=sys.stderr)
        generation = "unknown"
    # The one audit row, in the shape `_audit_briefing_read` writes: the actor as the platform resolved it,
    # the encounter the actor already reached, the corpus generation, and the verdict -- no record id, no
    # answer text the form was filled from, nothing the caller did not already have.
    entry = {
        **actor.as_record(),
        "persona_id": persona.persona_id,
        "encounter_id": encounter.encounter_id,
        "corpus_generation": generation,
        "form_id": review.form_id,
        "question_id": review.question_id,
        "verdict": review.verdict,
        "edited_text": review.edited_text.strip() if review.edited_text else None,
    }
    try:
        store.add_prefill_review(entry, review.review_id)
        saved = store.prefill_review(review.review_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="review_id conflicts with an earlier request") from exc
    except Exception as exc:  # noqa: BLE001 -- storage failure is an explicit non-acknowledgement
        print(f"[prefill] review write failed: {type(exc).__name__}", file=sys.stderr)
        raise HTTPException(status_code=503, detail="the review was not saved") from exc
    if saved is None:
        raise HTTPException(status_code=503, detail="the review was not saved")
    return {"ok": True, "review_id": review.review_id, "form_id": entry["form_id"],
            "question_id": entry["question_id"], "verdict": entry["verdict"],
            "edited_text": entry["edited_text"], "recorded_at": _decided_at(saved),
            "label": "Tallennettu palveluun"}


@app.get("/api/encounters/{encounter_id}/prefill/reviews")
def prefill_reviews(
    encounter_id: str,
    store: Store = Depends(get_store),
    actor: actor_module.Actor = Depends(require_actor),
    scope: access.EncounterScope | None = Depends(require_encounter),
) -> dict:
    """What **this** professional has decided on this encounter's pre-filled answers, in decision order."""
    encounter, persona = _resolve(encounter_id, store)
    reviews = store.prefill_reviews(persona.persona_id, encounter.encounter_id, actor.actor_id)
    return {
        "encounter_id": encounter.encounter_id,
        "reviews": sorted(
            ({"review_id": entry["review_id"], "form_id": entry.get("form_id"),
              "question_id": entry.get("question_id"), "verdict": entry.get("verdict"),
              "edited_text": entry.get("edited_text"), "decided_at": _decided_at(entry)}
             for entry in reviews),
            key=lambda row: (row["decided_at"] or "", row["review_id"]),
        ),
    }


class ProposalFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    proposal_id: str = Field(min_length=1, max_length=80)
    rule_version: str = Field(min_length=1, max_length=80)
    action: Literal["accept", "edit", "dismiss"]
    note: str | None = Field(default=None, max_length=2000)


@app.post("/api/encounters/{encounter_id}/feedback")
def encounter_feedback(
    encounter_id: str,
    feedback: ProposalFeedback,
    store: Store = Depends(get_store),
    # The primary human decision point records the platform principal exactly as the chat review route
    # does: without an actor there is no audit trail, so an anonymous decision is refused rather than
    # stored. `require_actor` answers that 401 before `require_encounter` consults scope.
    actor: actor_module.Actor = Depends(require_actor),
    scope: access.EncounterScope | None = Depends(require_encounter),
) -> dict:
    # Request-shape validation is stateless and therefore runs before any proposal lookup or storage write.
    # The oracle this closes: while it ran after the lookup, this 400 was reachable only for a proposal id
    # that exists, so `409` vs `400` answered "is this id real?". It must also run before the decision claim
    # (in `store.add_feedback`), because a claim taken for a mistyped request is never released and would
    # permanently close this actor's one slot on that proposal.
    if feedback.action == "edit":
        if not feedback.note or not feedback.note.strip():
            raise HTTPException(status_code=400, detail="edited proposal text is required")
    elif feedback.note is not None:
        raise HTTPException(status_code=400, detail="note is only accepted for edit")

    encounter, persona, proposals = _encounter_proposals(encounter_id, store)
    proposal = next((item for item in proposals if item.proposal_id == feedback.proposal_id), None)
    if proposal is None:
        # One body for an id this encounter does not have, and deliberately not a 404: it must not read as
        # "this id exists but not for you". Another professional's decision is not a conflict at all — the
        # claim in `store.add_feedback` is actor-scoped — so this is the only state that is "not available"
        # to everyone. A decision *this* actor already recorded may be reported as such (they know it), and
        # is the only other 409 below.
        raise HTTPException(status_code=409, detail="proposal is no longer available")

    # Enough to reconstruct what was decided without re-deriving it: the proposal's own text, status and
    # evidence ids, alongside the rule version and the actor who decided.
    entry = {
        "encounter_id": encounter.encounter_id,
        "persona_id": persona.persona_id,
        "role": encounter.role,
        "proposal_id": proposal.proposal_id,
        "rule_version": proposal.rule_version,
        "proposal_text": proposal.text,
        "proposal_status": proposal.status,
        "proposal_evidence_record_ids": list(proposal.evidence_record_ids),
        "action": feedback.action,
        "note": feedback.note,
        **actor.as_record(),
    }
    try:
        store.add_feedback(entry, event_id=feedback.event_id)
        # Acknowledge with the caller's OWN record, read by its exact owner-scoped key and verified to be
        # this actor's decision on this encounter. An unscoped by-id read must not be used here: `event_id`
        # is caller-supplied and can equal another actor's decision-claim key, and the old acknowledgement
        # returned that claim's `recorded_at` as if it were this caller's.
        stored = store.feedback_acknowledgement(
            feedback.event_id, persona.persona_id, actor.actor_id, encounter.encounter_id)
    except DecisionConflict as exc:
        # The one 409 that means "the caller already decided this", distinguished in the body from the other
        # 409s this route can return (`proposal is no longer available`, a conflicting `event_id`). The
        # client renders exactly this as "you already decided this" rather than as a generic rejection, so
        # the distinction has to be machine-readable: matching on prose would break the first time the
        # sentence is reworded. It is only ever emitted for the caller's *own* decision — another
        # professional's earlier decision does not reach this branch, because the claim that raises it is
        # actor-scoped.
        return JSONResponse(status_code=409, content={"detail": str(exc), "reason": "already_decided"})
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="feedback event conflicts with an earlier request") from exc
    except Exception as exc:  # noqa: BLE001 -- storage failure is an explicit non-acknowledgement
        print(f"[feedback] durable write failed: {type(exc).__name__}", file=sys.stderr)
        raise HTTPException(status_code=503, detail="feedback was not saved") from exc
    if stored is None:
        raise HTTPException(status_code=503, detail="feedback was not saved")
    return {"ok": True, "event_id": feedback.event_id, "recorded_at": stored["recorded_at"].isoformat(),
            "actor_id": actor.actor_id, "actor_name": actor.name, "actor_source": actor.source}


class ChatAsk(BaseModel):
    """One question about one encounter. The client supplies an id, never history."""

    model_config = ConfigDict(extra="forbid", strict=True)

    turn_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    question: str = Field(min_length=1, max_length=chat.MAX_QUESTION_CHARS)
    digest_id: str | None = Field(default=None, max_length=64)


class AssistantAsk(ChatAsk):
    """The assistant's ask: the question plus the client's own transcript of this conversation.

    `history` is what the client displayed for earlier turns (question and the verified sentences), bounded,
    and it is model context only: the audit keeps no statement text, so it is never stored and never evidence.
    """

    history: list[dict] | None = Field(default=None, max_length=assistant.MAX_HISTORY_TURNS)


# One cause-neutral answer for an unknown turn and a turn owned by another professional, so the review route
# cannot be used to probe which turn ids exist.
TURN_NOT_AVAILABLE = "no such turn for this professional"


class ChatReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # Both ids become storage-key components (`review-{review_id}`, `turn-{turn_id}`) and the blob guard caps
    # the whole key at 128 characters, so the length limits here account for the prefix: an over-long id is a
    # 422 at the schema boundary on every backend instead of a 409 or a 500 on one of them.
    review_id: str = Field(min_length=1, max_length=128 - len("review-"), pattern=r"^[A-Za-z0-9_-]+$")
    turn_id: str = Field(min_length=1, max_length=128 - len("turn-"), pattern=r"^[A-Za-z0-9_-]+$")
    suggestion_id: str = Field(min_length=1, max_length=80)
    action: Literal["accept", "edit", "dismiss"]
    edited_text: str | None = Field(default=None, max_length=2000)
    digest_id: str | None = Field(default=None, max_length=64)


def _wire_digest(digest: chat.Digest) -> dict:
    """The digest as the client needs it: the sealed-subject identity, without the internal persona key.

    `digest_id` is all the SPA reads. `persona_id` is the fixture key that joins every audit record and the
    client never needs it to describe or check the sealed subject, so it stays out of the response. The
    stored digest keeps it — the audit record is written from the full `digest.model_dump(mode="json")`,
    because the audit is exactly where the join key belongs.
    """
    return digest.model_dump(mode="json", exclude={"persona_id"})


async def _chat_picture(store: Store, encounter_id: str) -> Picture:
    """The encounter's built picture, for the assistant, the opening prompts and the guidance surface.

    `CONTRACT` rule 7: those surfaces receive the built picture and nothing upstream of it, so they cite
    the records the briefing selected rather than the whole permitted set. An assistant turn is its own request,
    so the picture is read from the briefing cache, or rebuilt here through the same pipeline the `/picture` route
    uses; the briefing's narration is not something these routes consume — the assistant's model arm is its own
    tool loop (`assistant.ask`) — while the record selection, which is what the surfaces read, is unchanged.
    """
    today = date.today()
    cached = briefings.read_fresh(store, encounter_id, today)
    if cached is not None:
        return cached
    started_at = time.monotonic()
    # A miss builds the same picture the `/picture` route would, once, and stores it: the next turn, and the
    # briefing read, both find it. Decision-1 is on (the assistant may cite only the records it selected for this
    # role and purpose). Narration -- which this route does not consume; the assistant's model arm is its own tool
    # loop -- runs only when the cache persists the result for the briefing read to reuse; a backend with no cache
    # would pay for prose nobody reads, so it does not.
    return await briefings.build_and_cache(
        store, encounter_id, build=build_picture_for_encounter, today=today,
        started_at=started_at, preparation_deadline=started_at + PREPARATION_WINDOW_S,
        foundry_enabled=bool(getattr(store.briefing_cache, "persists", False)))


def _store_turn(store: Store, actor: actor_module.Actor, persona: Persona, encounter: Encounter, ask: "ChatAsk",
                digest: chat.Digest, turn: chat.Turn, picture: Picture, knowledge_available: int = 0,
                tool_calls: list | None = None) -> tuple[chat.Turn, chat.Digest]:
    """Record one turn (ids, classes, labels, evidence -- never sentence text) and return the turn to reply with.

    The audit row and the idempotent-retry path of the assistant's stream route. On a retry the stored turn is
    replied, rebuilt from its record and re-verified where it carries a proposed step (`chat.replay_advice`); a
    browser must never render a turn nobody stored.
    """
    statement_rows = [{"suggestion_id": item.suggestion_id, "ordinal": item.ordinal,
                       "statement_class": item.statement_class, "label": item.label, "basis": item.basis,
                       "heading": item.heading,
                       "basis_note": item.basis_note,
                       "refs": list(item.refs),
                       "source_record_ids": sorted(item.source_record_ids),
                       # The text of a proposed step, and only that. It is kept so an idempotent retry can replay it,
                       # and the replay re-verifies it against the picture (`chat.replay_advice`: record membership
                       # and figure traceability). Every other statement's text is still never stored.
                       "text": item.text if item.statement_class == chat.STATEMENT_KNOWLEDGE_ADVICE else None,
                       "category": item.category,
                       # The guideline's own recorded URL, kept so an idempotent retry returns the stored
                       # turn *with* its link. It is citation metadata rather than statement text: the audit
                       # has always kept labels and evidence ids, and the URL is the evidence's locator.
                       "url": item.url}
                      for item in turn.suggestions]
    try:
        created = store.add_chat_turn(
            # The audit keeps ids, classes, labels, the digest, the actor, the outcome and counts — never the
            # statement text and never a dump of the whole turn.
            {"persona_id": persona.persona_id, "encounter_id": encounter.encounter_id,
             "digest_id": digest.digest_id, "digest": digest.model_dump(mode="json"),
             "seq": turn.seq, "asked_at": turn.asked_at.isoformat(),
             "question": ask.question, "outcome": turn.outcome, "generator": turn.generator,
             # Which of the three generator states the turn was in, so a stored turn rebuilt on the
             # idempotent-retry path reports the same state the professional saw.
             "generator_state": turn.generator_state,
             "statement_classes": [item.statement_class for item in turn.suggestions],
             "suggestions": statement_rows, "suggestion_count": len(turn.suggestions),
             "knowledge_available": knowledge_available,
             "evidence_ids": sorted({record_id for item in turn.suggestions
                                     for record_id in item.source_record_ids}),
             "tool_calls": [name for name, _arguments in (tool_calls or [])],
             **actor.as_record()},
            ask.turn_id,
            # The retry-equality key is the stable part of the request only (turn, question, digest, actor):
            # it deliberately excludes the generated prose, so a retry whose model output differs is still
            # idempotent rather than a spurious 409.
            content={"turn_id": ask.turn_id, "question": ask.question, "digest_id": digest.digest_id,
                     "actor_id": actor.actor_id})
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="turn_id conflicts with an earlier request") from exc
    except Exception as exc:  # noqa: BLE001 - storage failure is an explicit non-acknowledgement
        print(f"[chat] durable write failed: {type(exc).__name__}", file=sys.stderr)
        raise HTTPException(status_code=503, detail="the turn was not saved") from exc
    stored = store.chat_turn(ask.turn_id)
    if stored is None:
        raise HTTPException(status_code=503, detail="the turn was not saved")
    if not created:
        # The idempotent retry path. The store kept the *first* turn for this `turn_id`, and the reply
        # returned here must be that turn, not the one just recomputed: the generator's prose can differ
        # between the two calls, and a browser that rendered the second call while the audit kept the first
        # would be deciding on a turn nobody stored. The stored record keeps the turn's identity (ids,
        # classes, labels, evidence) and deliberately no prose, which is why the recovered turn's statements
        # carry their evidence and labels rather than any model text.
        if not stored.get("digest"):
            raise HTTPException(status_code=409, detail="the turn predates digest tracking")
        turn = chat.turn_from_record(stored, advice=chat.replay_advice(stored, picture))
        digest = chat.Digest.model_validate(stored["digest"])
    return turn, digest


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/api/encounters/{encounter_id}/chat/stream")
async def encounter_chat_stream(
    encounter_id: str,
    ask: AssistantAsk,
    store: Store = Depends(get_store),
    actor: actor_module.Actor = Depends(require_actor),
    scope: access.EncounterScope | None = Depends(require_encounter),
):
    """The assistant (Avustaja): one question, answered by a tool-using agent over the cached picture, streamed.

    The stream carries `progress` and `tool` events while the agent reads, each VERIFIED sentence as a
    `sentence` event the moment Decision-1 confirmed it, and one final `turn` event carrying the stored turn
    (`turn`, `digest`, `guideline_links`, `assistant`, `answer_notice`). Raw model text is never streamed:
    nothing unverified reaches the screen. The seal check runs before the stream opens, so a stale digest is a
    409 rather than a re-pointed thread; a retry under a stored turn id replays the stored turn without a model
    call.
    """
    encounter, persona = _resolve(encounter_id, store)
    scoped = cut_for_encounter(persona, encounter)
    picture = await _chat_picture(store, encounter_id)
    digest = chat.seal_digest(picture, policy_version=RULE_VERSION, persona_id=persona.persona_id)
    if ask.digest_id is not None and ask.digest_id != digest.digest_id:
        return JSONResponse(status_code=409, content={"detail": "the encounter changed since this conversation started",
                                                      "reason": "encounter_changed"})
    decision = decide(scoped)
    stored_already = store.chat_turn(ask.turn_id) is not None

    async def events():
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def on_tool(name: str, arguments: dict) -> None:
            await queue.put(_sse("tool", {"name": name}))

        async def on_sentence(sentence: assistant.VerifiedSentence) -> None:
            await queue.put(_sse("sentence", {"text": sentence.text, "source_ids": list(sentence.source_ids)}))

        yield _sse("progress", {"state": "replaying" if stored_already else "reading",
                                "generator_configured": llm.foundry_configured()})
        if stored_already:
            result = assistant.AssistantResult()
        else:
            task = asyncio.create_task(assistant.ask(ask.question, picture, decision, history=ask.history,
                                                     on_tool=on_tool, on_sentence=on_sentence))

            async def finish():
                try:
                    return await task
                finally:
                    await queue.put(None)

            waiter = asyncio.create_task(finish())
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
            result = await waiter
        seq = store.next_chat_seq(persona.persona_id)
        turn = assistant.compose_turn(ask.question, picture, decision, result, digest=digest, seq=seq,
                                      turn_id=ask.turn_id)
        try:
            stored_turn, stored_digest = _store_turn(store, actor, persona, encounter, ask, digest, turn, picture,
                                                     tool_calls=result.tool_calls)
        except HTTPException as exc:
            yield _sse("error", {"status": exc.status_code, "detail": exc.detail})
            return
        yield _sse("turn", {"turn": stored_turn.model_dump(mode="json"), "digest": _wire_digest(stored_digest),
                            "guideline_links": guidance.links_for(picture),
                            "assistant": {"status": result.status, "model": result.model,
                                          "tool_calls": [name for name, _a in result.tool_calls],
                                          "proposed_sentences": result.proposed_sentences,
                                          "verified_sentences": len(result.sentences)},
                            "answer_notice": "Olet yhteydessä tekoälyyn. Vastaus on tarkistettava."})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/encounters/{encounter_id}/chat/prompts")
async def encounter_chat_prompts(
    encounter_id: str,
    store: Store = Depends(get_store),
    actor: actor_module.Actor = Depends(require_actor),
    scope: access.EncounterScope | None = Depends(require_encounter),
) -> dict:
    """The questions this encounter invites, so the surface offers them before the first one is asked.

    Derived from the built picture's rendered rows and nothing else (`assistant.opening_prompts`), and returned
    with the visible fact that justified each one, so the professional can see why it is being offered.

    No `persona_id` in the response: it is the internal key that joins every audit record, and the UI keys
    its decisions by `turn_id`/`suggestion_id`, so the client never needs it. Returning it would put the
    fixture identity key on the wire for no reader.

    `actor` is the caller's **own** principal name, and nothing else about anyone. The panel greets the
    professional by it ("Hei Juhannes,"), and it is not a disclosure: it is the name the platform asserted
    about the caller in `X-MS-CLIENT-PRINCIPAL-NAME`, returned to that same caller, on a route that already
    requires a principal. It is `None` when the platform asserted an id without a name — the local
    development case — and the UI degrades to a name-less greeting rather than rendering the string "None".
    """
    _resolve(encounter_id, store)
    picture = await _chat_picture(store, encounter_id)
    return {"prompts": assistant.opening_prompts(picture),
            "guideline_links": guidance.links_for(picture),
            "actor": {"name": actor.name}}


# `/api/chat/reviews` is not encounter-scoped in its path: the turn id is the subject, and the stored turn
# carries its `encounter_id`. It is therefore not covered by `require_encounter`, and its actor-bound check
# (`stored["actor_id"] != actor.actor_id` -> one 403 for "no such turn" and "someone else's turn") used to be
# the whole gate, on the assumption that scope at turn-creation time (`POST /{id}/chat/stream` is scoped) implies
# scope now. It does not: a policy can change between the turn being created and the review being posted, so
# the caller's CURRENT scope for the stored turn's encounter is re-checked here, before anything else reads or
# writes, with `access.scope_for_actor` — the same function `require_encounter` wraps — rather than a second
# implementation of the contract. The refusal is the same `TURN_NOT_AVAILABLE` body the ownership check above
# already answers, so this is not a second oracle: "no such turn", "someone else's turn" and "your turn, but
# you have since been scoped out of its encounter" are one answer. With no policy configured
# `scope_for_actor` returns `None` and this check is a no-op, so the unset-policy behaviour is unchanged.
@app.post("/api/chat/reviews")
async def chat_review(
    review: ChatReviewRequest,
    store: Store = Depends(get_store),
    actor: actor_module.Actor = Depends(require_actor),
) -> dict:
    """Record accept / edit / dismiss against the turn's own actor. Append-only, one decision per suggestion."""
    stored = store.chat_turn(review.turn_id)
    # One answer for "no such turn" and "someone else's turn": the lookup must not become an oracle for which
    # turn ids exist. A turn stored before the actor field existed has no owner, so it fails closed here too.
    if stored is None or stored.get("actor_id") != actor.actor_id:
        raise HTTPException(status_code=403, detail=TURN_NOT_AVAILABLE)
    encounter_id = stored.get("encounter_id")
    # The caller's scope is re-checked NOW, for THIS encounter, regardless of what it was when the turn was
    # created. A revoked principal gets the same refusal as a stranger to the turn — see the comment above.
    scope = access.scope_for_actor(actor)
    if scope is not None and (not encounter_id or not scope.contains(encounter_id)):
        raise HTTPException(status_code=403, detail=TURN_NOT_AVAILABLE)
    # The freshness check is unconditional, and it does NOT depend on the caller supplying `digest_id`.
    # This is the decision path — the one that writes the audit record — so the invariant "the decision is
    # bound to a frozen subject" must be enforced here whether or not the client opts in. The stored turn
    # already carries the digest it was sealed with, so the current digest is re-derived from the caller's
    # own built picture and compared with the stored one. A caller-supplied `digest_id` is only an
    # additional equality check against that stored digest; the server no longer depends on the client
    # sending it, and a caller that omits it gets the same refusal as one that sends a stale value.
    sealed = stored.get("digest")
    if not encounter_id or not sealed:
        raise HTTPException(status_code=409, detail="the turn predates digest tracking")
    _encounter, persona = _resolve(encounter_id, store)
    picture = await _chat_picture(store, encounter_id)
    # The digest the decision is being made against must be the current one: the picture's records, role,
    # purpose or rule change since the turn was sealed refuses the decision rather than binding it to a
    # subject that is no longer the one on screen.
    digest = chat.Digest.model_validate(sealed)
    if review.digest_id is not None and review.digest_id != digest.digest_id:
        raise HTTPException(status_code=409,
                            detail="the encounter changed since this conversation started")
    if not chat.digest_is_current(digest, picture, policy_version=RULE_VERSION,
                                  persona_id=persona.persona_id):
        raise HTTPException(status_code=409,
                            detail="the encounter changed since this conversation started")
    turn = chat.turn_from_record(stored, advice=chat.replay_advice(stored, picture))
    try:
        entry = chat.review_of(turn, review.suggestion_id, review.action, actor,
                               edited_text=review.edited_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    entry["persona_id"] = stored["persona_id"]
    # The digest the decision was actually made against, so the stored audit is self-describing.
    entry["digest_id"] = stored["digest_id"]
    try:
        store.add_chat_review(entry, review.review_id)
        saved = store.chat_review(review.review_id)
    except DecisionConflict as exc:
        # The same machine-readable discriminator as the proposal write: this 409 means the caller already
        # decided this suggestion, while the other 409s on this route (a stale digest, a conflicting
        # `review_id`) stay plain failures the rail must not report as an earlier decision.
        return JSONResponse(status_code=409, content={"detail": str(exc), "reason": "already_decided"})
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="review_id conflicts with an earlier request") from exc
    except Exception as exc:  # noqa: BLE001 - storage failure is an explicit non-acknowledgement
        print(f"[chat] review write failed: {type(exc).__name__}", file=sys.stderr)
        raise HTTPException(status_code=503, detail="the review was not saved") from exc
    if saved is None:
        raise HTTPException(status_code=503, detail="the review was not saved")
    return {"ok": True, "review_id": review.review_id, "action": entry["action"],
            "suggestion_id": entry["suggestion_id"], "actor_id": entry["actor_id"],
            "digest_id": entry["digest_id"],
            "recorded_at": saved["recorded_at"].isoformat(),
            "label": "Tallennettu palveluun"}


@app.api_route("/api", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def unknown_api(path: str = ""):
    return JSONResponse(status_code=404, content={"detail": "Not Found"})


_IMMUTABLE_ASSET_PREFIX = "assets/"


def _asset_cache_headers(path: str) -> dict[str, str]:
    """Hashed build assets are content-addressed, so they are safe to cache forever.

    Anything unhashed (favicon, icons, fonts served from public/) gets a plain revalidating cache:
    it is not content-addressed, so treating it as immutable would pin a stale copy the same way an
    unhashed shell would.
    """
    if path.startswith(_IMMUTABLE_ASSET_PREFIX):
        return {"Cache-Control": "public, max-age=31536000, immutable"}
    return {"Cache-Control": "no-cache"}


@app.get("/{path:path}")
def spa(path: str):
    static_dir = os.environ.get("LUVN_STATIC_DIR")
    if not static_dir:
        raise HTTPException(status_code=404, detail="Not Found")
    root = Path(static_dir).resolve()
    requested = (root / path).resolve()
    if not requested.is_relative_to(root):
        raise HTTPException(status_code=404, detail="Not Found")
    if requested.is_file():
        return FileResponse(requested, headers=_asset_cache_headers(path))
    # Missing assets are not valid client routes. Extensionless paths are SPA deep links.
    if path == "assets" or path.startswith("assets/") or Path(path).suffix:
        raise HTTPException(status_code=404, detail="Not Found")
    index = root / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="Not Found")
    # The shell must revalidate. Vite hashes asset filenames, so a shell cached without revalidation
    # pins a browser to a previous build's bundle indefinitely: the page keeps rendering the old app
    # while the revision, the health check and every gate report the new one. That is not hypothetical -
    # it cost two deploys, the second one diagnosed as a wrong image when the image was
    # already correct. `no-cache` still allows a 304 revalidation; it forbids reuse without asking.
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
