"""Evaluate the pre-filled esitiedot (the flagship) over the bound fixture encounters, and publish the result.

The pre-fill has two levels (`app/prefill.py`): the county's own esitieto structure, filled deterministically
from the notes' `## ` sections, and the official forms from the release, answered by the generator and
confirmed item by item by the verifier. The application shows both, with provenance, but nothing in the
repository ever measured them on frozen inputs. This script does, per bound fixture encounter that carries a
note, and records for every form:

* `generation_status` and `verification_status` as the picture reports them;
* how many questions were answered and how many are still open, and how many the verifier declined
  (`proposed` minus `verified`);
* that every answered item cites only records inside the permitted set of its own encounter (the permission
  rule, `policy.cut`), and whether those records are rows the picture itself renders;
* that every figure in an answer is traceable to a cited record (`chat._digits_traceable`, the same crude
  deterministic rule the assistant's statements pass);
* whether a score is present (the deterministic instruments, PHQ-9 and IADL, carry one; a free-text form never).

Plus totals, a scope digest derived from the corpus, and per-encounter latency.

**Three ways to run it, and only one is offline.**

    .venv/bin/python scripts/run_prefill_eval.py --offline       # no model: fixture corpus, foundry disabled
    .venv/bin/python scripts/run_prefill_eval.py                 # live: build each picture with the generator
    .venv/bin/python scripts/run_prefill_eval.py --from-cache    # live: read the deployed `briefings` cache
    .venv/bin/python scripts/run_prefill_eval.py --check         # verify the committed artifact, offline

`--offline` builds every picture with `foundry_enabled=False` and `decision_enabled=False`, so every form
reports `generation_status == "unavailable"` with zero answers and the Level-1 esitiedot are the only filled
part. That is the honest baseline the committed artifact carries until the owner runs it live: it measures the
deterministic level and the permission rule, and it says nothing about the generator. The artifact records
`model_enabled: false` so `--check` can tell the two apart and refuses an offline artifact that claims a
generated form.

The live run differs in two ways. Without `--from-cache` it builds each picture through the shipped
`build_picture_for_encounter(..., foundry_enabled=True)`, which calls the generator, the verifier and
Decision-1, so it needs the deployment's environment (`LUNA_FOUNDRY_*`). With `--from-cache` and `LUVN_SQL_*`
set, it reads today's row from the deployed `briefings` table instead, so the measured forms are the ones the
day view actually served; an encounter without a fresh row is built live as above. A live artifact must contain
at least one generated form, or every rate in it is vacuous and `--check` says so.

`--check` is in `scripts/check.sh`'s spike loop. It is offline: it re-derives the cases from the fixture corpus
and refuses an artifact whose scope digest no longer matches, whose totals disagree with its rows, or whose
rows are missing a field. It re-verifies the permission rule and the digits rule from the row's own answer
text against the corpus, rather than trusting a boolean the run wrote, and refuses any answered item that
cites a record outside its encounter's permitted set.

**This is not clinical validation.** The fixtures are synthetic and hand-authored, and the per-item verifier
is the component the application uses, so agreement between this script and the app is not independent
evidence. It is a regression baseline for the pre-fill.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import chat, fixtures, source  # noqa: E402
from app.encounters import resolve_encounter  # noqa: E402
from app.pipeline import build_picture_for_encounter  # noqa: E402
from app.picture import Picture  # noqa: E402
from app.policy import cut_for_encounter  # noqa: E402
from app.prefill import PREFILL_VERSION  # noqa: E402

from tests.fixtures.bound_encounters import ENCOUNTERS  # noqa: E402

ARTIFACT = ROOT / ".scratch" / "hackathon-readiness" / "results" / "prefill-eval-v1.json"
KIND = "prefill-eval"


def _has_note(records) -> bool:
    """A free-text record. The fixture notes carry `teksti` only; `osiot` sections exist in the release corpus."""
    return any(record.record_type == source.FREE_TEXT for record in records)


def _personas() -> dict:
    return {persona.persona_id: persona for persona in fixtures.load_personas()}


def _cases(personas: dict | None = None) -> list[dict]:
    """The frozen cases: every bound fixture encounter whose permitted set carries a note.

    Derived from the corpus rather than stored beside it, so `--check` can re-derive them and refuse an artifact
    about a corpus that no longer exists. The record ids are the permitted set after `policy.cut`.
    """
    personas = _personas() if personas is None else personas
    cases: list[dict] = []
    for encounter_id in sorted(ENCOUNTERS):
        encounter, persona = resolve_encounter(encounter_id, personas, ENCOUNTERS)
        scoped = cut_for_encounter(persona, encounter)
        if not _has_note(scoped.records):
            continue
        cases.append({
            "case_id": encounter_id,
            "role": scoped.role,
            "purpose": scoped.purpose,
            "record_ids": list(scoped.ids),
        })
    return cases


def _scope_digest(cases: list[dict]) -> str:
    material = json.dumps(
        [{"case_id": case["case_id"], "record_ids": case["record_ids"]} for case in cases],
        ensure_ascii=False, sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _permitted_records(case_id: str, personas: dict) -> dict:
    """The permitted records of one case, by id, re-cut from the corpus. Never a withheld record."""
    encounter, persona = resolve_encounter(case_id, personas, ENCOUNTERS)
    return {record.record_id: record for record in cut_for_encounter(persona, encounter).records}


def _figures_traceable(text: str | None, cited_ids: list[str], permitted: dict) -> bool:
    cited = [permitted[record_id] for record_id in cited_ids if record_id in permitted]
    return chat._digits_traceable(text or "", cited)


def _rendered_ids(picture: Picture) -> set[str]:
    return {row.record_id for row in picture.records.priority + picture.records.accessible_other}


def _form_rows(case: dict, picture: Picture, permitted: dict, rendered: set[str]) -> list[dict]:
    rows: list[dict] = []
    for form in picture.prefilled_forms:
        answered_items = [item for item in form.items if item.status == "answered"]
        cited = {record_id for item in answered_items for record_id in item.source_record_ids}
        rows.append({
            "case_id": case["case_id"],
            "form_id": form.form_id,
            "name": form.name,
            "form_role": form.form_role,
            "generation_status": form.generation_status,
            "generation_model": form.generation_model,
            "verification_status": form.verification_status,
            "total_questions": form.total_questions,
            "proposed": form.proposed,
            "verified": form.verified,
            "answered": len(answered_items),
            "open": sum(1 for item in form.items if item.status == "unknown"),
            "answered_items": [{"question_id": item.question_id, "answer_text": item.answer_text,
                                "source_record_ids": list(item.source_record_ids)} for item in answered_items],
            "every_answer_cites_a_record": all(item.source_record_ids for item in answered_items),
            "citations_in_permitted_scope": cited <= set(permitted),
            "citations_in_rendered_picture": cited <= rendered,
            "figures_traceable": all(_figures_traceable(item.answer_text, item.source_record_ids, permitted)
                                     for item in answered_items),
            "score_present": form.score is not None,
            "score_total": form.score.total if form.score is not None else None,
        })
    return rows


def _esitiedot_summary(picture: Picture, permitted: dict) -> dict:
    sections = picture.esitiedot
    items = [item for section in sections for item in section.items]
    cited = {record_id for item in items for record_id in item.source_record_ids}
    return {
        "sections": len(sections),
        "answered": sum(1 for section in sections if section.status == "answered"),
        "open": sum(1 for section in sections if section.status == "unknown"),
        "items": len(items),
        "citations_in_permitted_scope": cited <= set(permitted),
        "figures_traceable": all(_figures_traceable(item.text, item.source_record_ids, permitted) for item in items),
    }


async def _picture_for(case: dict, personas: dict, today: date, *, model_enabled: bool, from_cache: bool,
                       store) -> tuple[Picture, str]:
    if from_cache and store is not None:
        from app import briefings

        cached = briefings.read_fresh(store, case["case_id"], today)
        if cached is not None:
            return cached, "briefings-cache"
    picture = await build_picture_for_encounter(
        case["case_id"], personas, today, decision_enabled=model_enabled, foundry_enabled=model_enabled,
        encounters=ENCOUNTERS,
    )
    return picture, "built-live" if model_enabled else "built-offline"


async def _run(*, model_enabled: bool, from_cache: bool) -> dict:
    personas = _personas()
    cases = _cases(personas)
    today = date.today()
    store = None
    if from_cache and os.environ.get("LUVN_SQL_SERVER"):
        from app.store import Store

        store = Store()  # the configured SQL index, whose `briefings` table the day view reads
    encounters: list[dict] = []
    forms: list[dict] = []
    for case in cases:
        permitted = _permitted_records(case["case_id"], personas)
        started = time.monotonic()
        picture, served_from = await _picture_for(case, personas, today, model_enabled=model_enabled,
                                                  from_cache=from_cache, store=store)
        rendered = _rendered_ids(picture)
        rows = _form_rows(case, picture, permitted, rendered)
        forms.extend(rows)
        encounters.append({
            "case_id": case["case_id"],
            "served_from": served_from,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "permitted_records": len(permitted),
            "rendered_records": len(rendered),
            "forms": len(rows),
            "esitiedot": _esitiedot_summary(picture, permitted),
        })
    return {
        "kind": KIND,
        "evaluation_mode": (
            "offline: fixture corpus, foundry and Decision-1 disabled; measures the deterministic level and the "
            "permission rule only" if not model_enabled else
            "live: shipped pipeline with the generator and the verifier" + (
                ", read from the deployed briefings cache where a fresh row existed" if from_cache else "")),
        "model_enabled": model_enabled,
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "today": today.isoformat(),
        "prefill_version": PREFILL_VERSION,
        "corpus": "bound fixtures via app.fixtures.load_personas (tracked, synthetic)",
        "case_scope_digest": _scope_digest(cases),
        "cases": cases,
        "totals": _totals(encounters, forms),
        "limits": [
            "synthetic, hand-authored, unadjudicated fixtures; expectations are structural, not clinical",
            "the per-item verifier is the component the application uses, so agreement is not independent evidence",
            "the digits rule is crude by design: it cannot judge meaning, only that a figure appears in a cited record",
            "an offline artifact (model_enabled false) says nothing about the generator: every form is unavailable",
        ],
        "encounters": encounters,
        "results": forms,
    }


def _totals(encounters: list[dict], forms: list[dict]) -> dict:
    def count(field: str) -> int:
        return sum(1 for row in forms if row.get(field) is True)

    def status(field: str, value: str) -> int:
        return sum(1 for row in forms if row.get(field) == value)

    latencies = [row["latency_ms"] for row in encounters if row.get("latency_ms") is not None]
    return {
        "encounters": len(encounters),
        "forms": len(forms),
        "forms_generated": status("generation_status", "generated"),
        "forms_unavailable": status("generation_status", "unavailable"),
        "forms_failed": status("generation_status", "failed"),
        "forms_timed_out": status("generation_status", "timed_out"),
        "verification_complete": status("verification_status", "complete"),
        "verification_partial": status("verification_status", "partial"),
        "questions": sum(row.get("total_questions") or 0 for row in forms),
        "answered": sum(row.get("answered") or 0 for row in forms),
        "open": sum(row.get("open") or 0 for row in forms),
        "proposed": sum(row.get("proposed") or 0 for row in forms),
        "verified": sum(row.get("verified") or 0 for row in forms),
        "citations_in_permitted_scope": count("citations_in_permitted_scope"),
        "citations_in_rendered_picture": count("citations_in_rendered_picture"),
        "figures_traceable": count("figures_traceable"),
        "score_present": count("score_present"),
        "esitieto_sections_answered": sum(row["esitiedot"]["answered"] for row in encounters),
        "esitieto_sections_open": sum(row["esitiedot"]["open"] for row in encounters),
        "latency_p50_ms": int(statistics.median(latencies)) if latencies else None,
    }


def problems(document: dict, personas: dict | None = None) -> list[str]:
    """Everything wrong with an artifact, offline. Empty means it passes."""
    found: list[str] = []
    if document.get("kind") != KIND:
        found.append(f"the artifact does not declare kind {KIND!r}")
    rows = document.get("results") or []
    encounters = document.get("encounters") or []
    totals = document.get("totals") or {}
    if not encounters:
        found.append("the artifact has no encounters")
    if "model_enabled" not in document:
        found.append("the artifact does not say whether a model was enabled")

    personas = _personas() if personas is None else personas
    try:
        cases = _cases(personas)
    except Exception as exc:  # noqa: BLE001 - a corpus that cannot be derived is a failed check
        found.append(f"the cases could not be re-derived from the corpus: {type(exc).__name__}: {exc}")
        return found
    digest = _scope_digest(cases)
    if document.get("case_scope_digest") != digest:
        found.append(f"the artifact's case scope digest is {document.get('case_scope_digest')!r}, but the "
                     f"corpus now derives {digest!r}")
    current = {case["case_id"] for case in cases}
    recorded = {row.get("case_id") for row in encounters}
    if recorded != current:
        found.append(f"the artifact's encounters are {sorted(recorded)}, but the corpus derives {sorted(current)}")

    expected = _totals(encounters, rows) if encounters else {}
    for field, value in expected.items():
        if field == "latency_p50_ms":
            continue
        if totals.get(field) != value:
            found.append(f"totals.{field} is {totals.get(field)!r}, but the rows give {value}")

    permitted_by_case = {case_id: _permitted_records(case_id, personas) for case_id in current}
    for row in rows:
        case_id = row.get("case_id")
        label = f"{case_id}/{row.get('form_id')}"
        for field in ("form_id", "generation_status", "verification_status", "answered", "open",
                      "answered_items", "citations_in_permitted_scope", "figures_traceable", "score_present"):
            if field not in row:
                found.append(f"row {label!r} is missing {field!r}")
        if case_id not in permitted_by_case:
            found.append(f"row {label!r} belongs to no derived case")
            continue
        permitted = permitted_by_case[case_id]
        items = row.get("answered_items") or []
        if row.get("answered") != len(items):
            found.append(f"row {label!r} says {row.get('answered')} answered, but lists {len(items)} item(s)")
        if document.get("model_enabled") is False and row.get("generation_status") == "generated":
            found.append(f"row {label!r} claims a generated form in an artifact produced without a model")
        for item in items:
            cited = item.get("source_record_ids") or []
            if not cited:
                found.append(f"row {label!r} answered {item.get('question_id')!r} without citing a record")
            outside = sorted(set(cited) - set(permitted))
            if outside:
                # The permission rule: an answer may cite only what its own encounter was permitted to read.
                found.append(f"row {label!r} answered {item.get('question_id')!r} citing {outside}, which are "
                             f"outside the encounter's permitted set")
            elif not _figures_traceable(item.get("answer_text"), cited, permitted):
                found.append(f"row {label!r} answered {item.get('question_id')!r} with a figure its cited "
                             f"records do not carry")
    for encounter in encounters:
        esitiedot = encounter.get("esitiedot") or {}
        if not esitiedot.get("citations_in_permitted_scope"):
            found.append(f"{encounter.get('case_id')}: an esitieto item cited outside the permitted set")
        if not esitiedot.get("figures_traceable"):
            found.append(f"{encounter.get('case_id')}: an esitieto item carried a figure its record lacks")
    if document.get("model_enabled") is True and rows and totals.get("forms_generated", 0) == 0:
        found.append("a live artifact generated no form, so every rate in it is vacuous")
    return found


def _check() -> int:
    if not ARTIFACT.exists():
        print(f"check: {ARTIFACT} is missing", file=sys.stderr)
        return 1
    try:
        document = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"check: {ARTIFACT} is not readable JSON: {exc}", file=sys.stderr)
        return 1
    found = problems(document)
    if found:
        for problem in found:
            print(f"check: {problem}", file=sys.stderr)
        return 1
    totals = document["totals"]
    mode = "live" if document["model_enabled"] else "offline, no model"
    print(f"check: pre-fill evaluation ({mode}) over {totals['encounters']} encounter(s), "
          f"{totals['forms']} form(s), scope {document['case_scope_digest'][:19]}…")
    print(f"check: forms generated {totals['forms_generated']}/{totals['forms']}, unavailable "
          f"{totals['forms_unavailable']}, failed {totals['forms_failed']}, timed out {totals['forms_timed_out']}")
    print(f"check: answered {totals['answered']}, open {totals['open']}, verified {totals['verified']} of "
          f"{totals['proposed']} proposed; esitieto sections answered {totals['esitieto_sections_answered']}, "
          f"open {totals['esitieto_sections_open']}")
    print(f"check: citations in permitted scope {totals['citations_in_permitted_scope']}/{totals['forms']}, "
          f"in rendered picture {totals['citations_in_rendered_picture']}/{totals['forms']}, figures traceable "
          f"{totals['figures_traceable']}/{totals['forms']}, score present {totals['score_present']}, "
          f"p50 {totals['latency_p50_ms']}ms")
    for row in document["results"]:
        print(f"check:   {row['case_id']:48s} {row['form_id']:24s} status={row['generation_status']} "
              f"answered={row['answered']} open={row['open']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="verify the committed artifact, offline")
    parser.add_argument("--offline", action="store_true",
                        help="build without a model (foundry and Decision-1 disabled); the committed baseline")
    parser.add_argument("--from-cache", action="store_true",
                        help="read today's pictures from the deployed briefings table when LUVN_SQL_* is set")
    args = parser.parse_args(argv)
    if args.check:
        return _check()
    if args.offline and args.from_cache:
        parser.error("--offline builds from the fixture corpus; it cannot read the cache")
    document = asyncio.run(_run(model_enabled=not args.offline, from_cache=args.from_cache))
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"artifact": str(ARTIFACT), "totals": document["totals"]}, ensure_ascii=False, indent=2))
    return _check()


if __name__ == "__main__":
    raise SystemExit(main())
