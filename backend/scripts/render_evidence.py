#!/usr/bin/env python
"""Render `docs/decision-evidence-summary.md` from the measurement artifacts. Nothing is retyped.

The failure this removes: a model reading numbers out of a JSON file and typing them into Markdown,
twice, at different times. Once that happens the document is no longer a record of the measurement —
it is a claim about it, and the two drift silently.

Everything numeric below is read from an artifact or computed from code constants:

  .scratch/hackathon-readiness/results/evidence-manifest.json          (kind -> artifact path index)
  frontend/src/App.jsx                                                 (browser deadline)
  app.pipeline / app.narration / app.llm                               (window constants)

`--check` re-renders and exits non-zero if the committed file differs, so the document cannot be
stale. It also refuses to accept a trial artifact produced under a different narration window than
the code currently declares: change the window and you must re-run the trial.

    .venv/bin/python scripts/render_evidence.py           # write
    .venv/bin/python scripts/render_evidence.py --check   # verify (CI / pre-commit)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import decision_model, narration, pipeline  # noqa: E402

RESULTS = ROOT / ".scratch" / "hackathon-readiness" / "results"
MANIFEST_PATH = RESULTS / "evidence-manifest.json"
APP_JSX = ROOT / "frontend" / "src" / "App.jsx"
TARGET = ROOT / "docs" / "decision-evidence-summary.md"

OPENING_MODE = "decision-on-foundry-on"


class RenderError(RuntimeError):
    """A source is missing or has drifted from the code; the document must not be guessed."""


def _sentence_order_metrics(trial: dict) -> dict:
    """Keep the primary-priority match distinct from any-priority-leading-in-sentence-1."""
    rows = []
    for encounter_id, modes in trial["per_encounter"].items():
        mode = modes.get(OPENING_MODE) or {}
        if mode.get("generation_path") != "agent_reference":
            continue
        priority = mode.get("priority_record_ids") or []
        citations = mode.get("sentence_citations")
        first = next((row for row in citations or [] if row.get("sentence_id") == "s1"), None)
        if not priority or first is None:
            continue
        first_sources = set(first.get("source_ids") or [])
        wanted = set(priority)
        position = next((index + 1 for index, sentence in enumerate(citations or [])
                         if wanted & set(sentence.get("source_ids") or [])), None)
        rows.append({
            "encounter_id": encounter_id,
            "primary_id_in_sentence_1": priority[0] in first_sources,
            "any_priority_in_sentence_1": position == 1,
            "position": position,
            "primary_is_note": priority[0].startswith("bound-note"),
        })
    return {
        "rows": rows,
        "eligible_encounters": len(rows),
        "primary_id_matches": sum(row["primary_id_in_sentence_1"] for row in rows),
        "any_priority_leads": sum(row["any_priority_in_sentence_1"] for row in rows),
        "within_two": sum(row["position"] in (1, 2) for row in rows),
        "cited": sum(row["position"] is not None for row in rows),
        "note_led_total": sum(row["primary_is_note"] for row in rows),
        "note_led_first": sum(row["primary_is_note"] and row["any_priority_in_sentence_1"] for row in rows),
    }


def artifacts() -> dict[str, Path]:
    """Resolve every cited artifact from the manifest, so no caller knows a filename."""
    document = _load(MANIFEST_PATH)
    if document.get("kind") != "evidence-manifest":
        raise RenderError(f"{MANIFEST_PATH.name} is not an evidence manifest")
    entries = document.get("artifacts")
    if not isinstance(entries, list) or not entries:
        raise RenderError(f"{MANIFEST_PATH.name} lists no artifacts")
    paths: dict[str, Path] = {}
    for entry in entries:
        kind, relative = entry.get("kind"), entry.get("path")
        if not kind or not relative:
            raise RenderError(f"{MANIFEST_PATH.name} has an entry without a kind or path")
        if kind in paths:
            raise RenderError(f"{MANIFEST_PATH.name} lists {kind!r} twice")
        paths[kind] = ROOT / relative
    return paths


def _load(path: Path) -> dict:
    if not path.is_file():
        raise RenderError(f"missing artifact {path.relative_to(ROOT)}; run the evidence script first")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RenderError(f"cannot read {path.relative_to(ROOT)}: {type(error).__name__}") from error


def _agent_cap() -> float:
    """Read the hard agent cap out of llm.py. A refactor should fail loudly, not silently re-render."""
    source = (ROOT / "backend" / "app" / "llm.py").read_text(encoding="utf-8")
    match = re.search(r"agent_budget = min\(([0-9.]+), timeout_s \* ([0-9.]+)\)", source)
    if not match:
        raise RenderError("cannot find the agent budget cap in backend/app/llm.py — update this renderer")
    return float(match.group(1))


def _browser_deadline_ms() -> int:
    match = re.search(r"REQUEST_DEADLINE_MS = ([0-9_]+)",
                      APP_JSX.read_text(encoding="utf-8"))
    if not match:
        raise RenderError("cannot find REQUEST_DEADLINE_MS in frontend/src/App.jsx")
    return int(match.group(1).replace("_", ""))


def _paired_sensitivity(ablation: dict, ablation_path: Path) -> tuple[str, list[str]]:
    """The floor/margin ablation as a paired comparison, with the prediction's verdict.

    Every number is read from the artifact, and the verdict sentences are selected by the artifact's
    own pre-registered expectation booleans, so a re-run that flips a result re-renders the wording
    instead of leaving a stale claim in the document.
    """
    if ablation.get("kind") != "decision-floor-ablation":
        raise RenderError(f"{ablation_path.name} is not a floor ablation artifact")
    result = ablation["result"]
    totals = result["totals"]
    expectations = result["pre_registered_expectations"]
    variants = ablation["variants"]
    production, zero = variants["production"], variants["zero_floor"]
    additions = totals["additions"]
    omissions = totals["selection_omitted_baseline_record_ids"]
    refused = totals["gate_refused_count"]
    assessed = totals["assessed_count"]
    wrong = totals["wrong_omissions"]
    rows = [
        f"| Additions | {len(additions['production'])} | {len(additions['zero_floor'])} |",
        f"| Omitted baseline records | {len(omissions['production'])} | {len(omissions['zero_floor'])} |",
        f"| Unexpected omissions (authored keys) | {wrong['production']} | {wrong['zero_floor']} |",
        f"| Assessed candidates | {assessed} | {assessed} |",
        f"| Gate-refused | {refused['production']} | {refused['zero_floor']} |",
    ]
    lines = [
        "## Threshold sensitivity: the gates replayed at zero",
        "",
        f"Paired ablation over one recorded candidate set (`{ablation_path.name}`, source "
        f"`{_load(ablation_path).get('source_artifact', 'unstated source')}` — a different run from the "
        f"cap-80 headline above, which is why its addition count differs): both variants replay the "
        "same recorded model answers through the same classifier, so only the three thresholds differ — "
        f"`addition_floor={production['addition_floor']}`, `omission_floor={production['omission_floor']}`, "
        f"`decision_margin={production['decision_margin']}` against all three at "
        f"`{zero['addition_floor']}`. The model's explicit choice, the currentness and person-binding "
        "requirements, the permission cut and the fail-closed handling stay in force in both variants.",
        "",
        "| Quantity | Production floors | Floors at zero |",
        "| --- | --- | --- |",
        *rows,
        "",
        "- Pre-registered **additions unchanged** "
        + ("held." if expectations["additions_unchanged"] else "was **falsified**."),
        "- Pre-registered **omissions unchanged** "
        + ("held." if expectations["omissions_unchanged"] else
           f"was **FALSIFIED**: floors at zero add `{'`, `'.join(sorted(set(omissions['zero_floor']) - set(omissions['production']))) or 'no'}` as omitted baseline records."),
    ]
    if (expectations["additions_unchanged"] and not expectations["omissions_unchanged"]
            and wrong["zero_floor"] > wrong["production"]):
        lines.append(
            f"- Reading: at this size zeroing the gates buys **no** additional source"
            f" ({len(additions['zero_floor'])} additions either way) and costs "
            f"**{wrong['zero_floor'] - wrong['production']} unexpected omissions**, so the omission floor "
            "is the load-bearing gate where the candidate population is large. Those notes were answered "
            "`irrelevant` but gate-refused on floor/margin; they stay visible in the briefing, so this is a "
            "generation-eligibility change. The direction strengthens the product: the gates prevent "
            "unsupported omissions without suppressing a single authored addition.")
    lines += [
        "- Gate-refused counts are a subset of assessed, never a disjoint bucket.",
        "- This is a **sensitivity** result on one recorded answer set, not accuracy, calibration or "
        "validation, and not a measurement of how a fresh selector call would behave under zero floors.",
        "",
    ]
    return "\n".join(lines), list(ablation.get("limits") or [])


def _structured_agreement(disagreement: dict, disagreement_path: Path) -> tuple[str, list[str]]:
    """The structured-fact disagreement counts, published as agreement and never as accuracy."""
    if disagreement.get("kind") != "decision-structured-fact-disagreement":
        raise RenderError(f"{disagreement_path.name} is not a structured-fact disagreement artifact")
    counts = disagreement["counts"]
    classes = disagreement["classes"]
    rendered = ", ".join(f"{name} {counts[name]}" for name in classes)
    lines = [
        "## Structured-fact disagreement (author agreement, not accuracy)",
        "",
        f"One recorded candidate set (`{disagreement.get('source_artifact', 'unstated source')}`) — "
        f"**not** the cap-80 headline run above, whose produced set differs — contributed "
        f"{len(disagreement['population']['addition_or_emphasis_ids'])} addition/emphasis sources, classified "
        f"over {counts['pairs']} authored claim pairs — "
        f"{rendered} — so published disagreement is **{counts['disagreement']}/{counts['disagreement_denominator']}**. "
        "Pairing is authored per claim and never inferred from text similarity; a produced source with no "
        "authored pair is reported `not-comparable` rather than dropped, so the denominator always covers "
        "the whole produced population. The pairing table and the comparison rules are pre-registered in "
        "`issues/05-phase-4-experiments.md`.",
        "",
        "This is a disagreement rate between authored artefacts, not an error rate for the note, the model "
        "or the structured record, and not independent validation.",
        "",
    ]
    return "\n".join(lines), list(disagreement.get("limits") or [])


def _delivery_scale_dry_run(dry_run: dict, dry_run_path: Path) -> tuple[str, list[str]]:
    """The batch importer at the assumed delivery shape: feasibility, never the delivery's own result.

    Every number comes from the artifact. The integrity block must reconcile with the totals before
    anything renders: a truncated import that lost records must fail the renderer rather than emit a
    clean-looking line.
    """
    if dry_run.get("kind") != "delivery-scale-dry-run":
        raise RenderError(f"{dry_run_path.name} is not a delivery-scale dry-run artifact")
    integrity = dry_run["integrity"]
    if (integrity["persona_files"] != dry_run["profiles"]
            or integrity["records_on_disk"] != dry_run["records"]
            or integrity["indexed_records"] != dry_run["records"]):
        raise RenderError(f"{dry_run_path.name}'s integrity counts disagree with its own totals "
                          f"({integrity} vs profiles={dry_run['profiles']}, records={dry_run['records']})")
    if integrity["duplicate_record_ids"]:
        raise RenderError(f"{dry_run_path.name} reports {integrity['duplicate_record_ids']} duplicate "
                          "record IDs; the import did not reconcile and must be re-run")
    low, high = dry_run["records_per_profile"]
    lines = [
        "## Delivery-shape dry run (synthetic, assumed schema)",
        "",
        f"Measured once by `backend/scripts/generate_delivery_corpus.py` (`{dry_run_path.name}`, "
        f"{dry_run['date']}; seed `{dry_run['seed']}`, {low}–{high} records per profile): "
        f"**{dry_run['profiles']:,} synthetic profiles → {dry_run['records']:,} records** were generated "
        f"in {dry_run['generate_wall_s']} s, then imported and indexed in **{dry_run['import_wall_s']} s** "
        f"(whole pipeline to a resolved encounter {dry_run['pipeline_wall_s']} s), producing a "
        f"**{dry_run['index_mb']} MB** index from {dry_run['source_mb']} MB of source at **"
        f"{dry_run['peak_rss_mb']} MB** peak RSS; one {dry_run['resolved_records']}-record encounter "
        f"resolved through that index in **{dry_run['resolve_one_encounter_ms']} ms**. Reconciliation after "
        f"the import: **{integrity['records_on_disk']:,} records on disk and {integrity['indexed_records']:,} "
        f"indexed, {integrity['persona_files']:,} persona files, {integrity['duplicate_record_ids']} duplicate "
        f"record IDs** (the separate re-read took {dry_run['integrity_wall_s']} s and is not part of the "
        "timings above).",
        "",
        "This is a **synthetic** deterministic corpus over the **assumed** delivery shape, on **one machine** — "
        "it is **not** the real 50,000-record delivery, whose schema is still unavailable, and it is **not** a "
        "load, concurrency or live-service throughput measurement. It establishes that the delivery importer, "
        "the per-person index and their reconciliation hold at delivery scale; it does **not** establish "
        "clinical validity, production readiness, or that the real schema imports unchanged.",
        "",
    ]
    return "\n".join(lines), list(dry_run.get("limits") or [])


def _check_window(trial: dict) -> tuple[int, int, int, int]:
    """Refuse a trial artifact produced under a window the code no longer declares."""
    prep = pipeline.PREPARATION_WINDOW_S
    joint = min(narration.NARRATION_JOINT_BUDGET_S, prep - narration.NARRATION_PREPARATION_RESERVE_S)
    generation = min(narration.NARRATION_GENERATION_BUDGET_S, joint - narration.NARRATION_SUPPORT_BUDGET_S)
    agent = min(_agent_cap(), generation * 0.8)
    if abs(trial["preparation_window_s"] - prep) > 0.5:
        raise RenderError(
            f"trial ran with a {trial['preparation_window_s']} s preparation window but the code declares "
            f"{prep} s — re-run backend/scripts/run_live_encounter_trial.py before rendering")
    declared = trial.get("effective_budgets_s") or {}
    for name, expected in (("joint", joint), ("generation", generation), ("agent", agent)):
        actual = declared.get(name)
        if actual is None or abs(actual - expected) > 1.0:
            raise RenderError(
                f"trial budget {name}={actual} does not match the code's {expected:.0f} s — re-run the trial")
    return int(prep), int(joint), int(generation), int(agent)


def _scale_case(eval_doc: dict) -> tuple[str, dict]:
    """The encounter with the most candidates is the scale case; pick it by data, not by name."""
    ranked = [(r["decision_on"]["coverage"].get("decision_candidate_count") or 0, enc, r)
              for enc, r in eval_doc["results"].items()]
    count, name, result = max(ranked)
    if not count:
        raise RenderError("no encounter reports a candidate count; cannot describe the scale case")
    return name, result["decision_on"]["coverage"]


def render() -> str:
    paths = artifacts()
    eval_path = paths["decision-value-eval"]
    trial_path = paths["scrubbed-live-encounter-trial-summary"]
    instability_path = paths["decision-value-eval-instability"]
    ablation_path = paths["decision-floor-ablation"]
    disagreement_path = paths["decision-structured-fact-disagreement"]
    probe_path = paths["content-safety-probe"]
    dry_run_path = paths["delivery-scale-dry-run"]
    eval_doc = _load(eval_path)
    trial = _load(trial_path)
    prep, joint, generation, agent = _check_window(trial)
    browser_ms = _browser_deadline_ms()
    totals = eval_doc["totals"]
    denominators = totals["f2_denominators"]
    verifier = trial["verifier"]["totals"]

    def pair(field: str) -> str:
        numerator, denominator = denominators[field]
        return f"{numerator}/{denominator}"

    # Which authored candidates actually moved, straight off the per-encounter metrics.
    moved: list[str] = []
    for encounter, result in sorted(eval_doc["results"].items()):
        metrics = result["metrics"]
        for label, key in (("added", "actual_additions"), ("omitted", "actual_omissions"),
                           ("abstained", "actual_abstentions")):
            for record_id in metrics.get(key) or []:
                moved.append(f"`{record_id}` {label} ({encounter})")

    scale_name, coverage = _scale_case(eval_doc)
    scale_encounter = eval_doc["results"][scale_name]
    scale_result = scale_encounter["decision_on"]
    scale_statuses: dict[str, int] = {}
    for note in scale_encounter.get("ablation_candidates") or []:
        status = note.get("status", "unknown")
        scale_statuses[status] = scale_statuses.get(status, 0) + 1
    scale_answered = sum(scale_statuses.get(status, 0) for status in ("relevant", "irrelevant", "unclear"))
    scale_failures = sum(scale_statuses.get(status, 0) for status in ("unavailable", "timeout", "invalid_response"))
    scale_failure_reading = ("observed no unavailable/timeouts or invalid responses (no throttling signal)"
                             if scale_failures == 0 else
                             f"observed {scale_failures} unavailable/invalid row(s); inspect the recorded statuses for timeout or throttling signals")
    aggregate_coverage = {
        field: sum(result["decision_on"]["coverage"][field] for result in eval_doc["results"].values())
        for field in ("decision_candidate_count", "decision_assessed_count", "decision_budget_exhausted_count", "decision_gate_refused_count")
    }
    ordered = sum(1 for r in eval_doc["results"].values()
                  if r["decision_on"].get("priority_record_ids"))
    ordered_ids = sum(len(r["decision_on"].get("priority_record_ids") or [])
                      for r in eval_doc["results"].values())

    accepted = sum(1 for modes in trial["per_encounter"].values()
                   if (modes.get(OPENING_MODE) or {}).get("narration_accepted"))
    encounters = trial["encounters"]
    sentence1_order = trial.get("sentence1_priority_source")
    if not isinstance(sentence1_order, dict):
        raise RenderError("trial summary lacks the per-sentence priority-source comparison")
    sentence1_matches = sentence1_order["matches"]
    sentence1_eligible = sentence1_order["eligible_encounters"]
    sentence1_total = sentence1_order["total_encounters"]
    sentence1_not_comparable = sentence1_order["not_comparable"]

    # These are different measures: the artifact's `matches` asks whether the FIRST listed priority ID
    # appears in sentence 1; `any_priority_leads` asks whether ANY priority ID appears by its earliest
    # cited sentence. A structured record can lead while the first-listed (often note) priority does not.
    ordering = _sentence_order_metrics(trial)
    if ordering["eligible_encounters"] != sentence1_eligible:
        raise RenderError(f"the trial summary reports {sentence1_eligible} comparable encounters but "
                          f"the per-encounter primary-ID rows contain {ordering['eligible_encounters']}")
    if ordering["primary_id_matches"] != sentence1_matches:
        raise RenderError(f"the trial summary's primary-ID sentence-1 count ({sentence1_matches}) disagrees "
                          f"with per-encounter primary-ID citations ({ordering['primary_id_matches']})")
    led = ordering["any_priority_leads"]
    within_two = ordering["within_two"]
    cited = ordering["cited"]
    note_led_total = ordering["note_led_total"]
    note_led_first = ordering["note_led_first"]

    # The borderline case is measured by repetition, so it is read from the repeat artifacts rather
    # than asserted in prose. A published stability claim without its artifact is not reproducible.
    instability = _load(instability_path)
    aggregate = instability["aggregate"]
    margins = [run["bound_note_017"]["subject_binding_margin"] for run in instability["per_run"]]
    if len(margins) != instability["runs"]:
        raise RenderError("instability summary disagrees with its own per-run count")
    margin_min, margin_max = min(margins), max(margins)
    if (abs(margin_min - aggregate["subject_binding_margin_min"]) > 0.005
            or abs(margin_max - aggregate["subject_binding_margin_max"]) > 0.005):
        raise RenderError("instability summary's aggregate margins disagree with its per-run rows")
    reference = aggregate.get("single_point_failure_reference") or {}
    # An earlier observation, not a measurement: it has no retained artifact and its recorded source is an
    # earlier revision of this same document. Say so rather than asserting a refusal status nothing recorded.
    history = (f"One earlier observation of this case recorded a subject-binding margin of "
               f"**{reference['margin']}**; it has no retained measurement artifact "
               f"(recorded source: {reference.get('source', 'unstated')}) and no repeat reproduced it. "
               if reference.get("margin") is not None
               else "No earlier single-point observation is on record for this case. ")

    standing = ["no real source integration, no hosted 50,000-profile service throughput result, and "
                "no clinical accuracy measurement exists; thresholds are inherited, not calibrated on "
                "Finnish data"]
    if probe_path.is_file():
        probe = _load(probe_path)
        if probe.get("acceptance_status") == "accepted":
            standing.insert(0, "the content-filter signal has not been observed firing: the synthetic probe "
                               f"(`{probe.get('kind')}`, trace `{probe.get('trace_id')}`) completed and was accepted")
    ablation_doc = _load(ablation_path)
    disagreement_doc = _load(disagreement_path)
    dry_run_doc = _load(dry_run_path)
    sensitivity_section, ablation_limits = _paired_sensitivity(ablation_doc, ablation_path)
    agreement_section, disagreement_limits = _structured_agreement(disagreement_doc, disagreement_path)
    dry_run_section, dry_run_limits = _delivery_scale_dry_run(dry_run_doc, dry_run_path)
    limit_groups = (
        ("From the selection scorecard", list(eval_doc.get("limits") or [])),
        ("From the narration trial", list(trial.get("limits") or [])),
        ("From the paired floor ablation", ablation_limits),
        ("From the structured-fact disagreement", disagreement_limits),
        ("From the delivery-scale dry run", dry_run_limits),
        ("Standing", standing),
    )

    lines = [
        "# Decision-1 evidence — Challenge 1.1",
        "",
        "<!-- Generated by backend/scripts/render_evidence.py. Do not edit by hand: numbers are read from",
        "     the artifacts below, and the pre-commit gate fails when this file and its sources disagree.",
        "     Regenerate: cd backend && .venv/bin/python scripts/render_evidence.py -->",
        "",
        f"**Evidence dates:** selection {eval_doc['today']}; narration {trial['today']}; "
        f"stability repeats {instability['date']}; delivery-shape dry run {dry_run_doc['date']}. Decision-1 reads "
        "every permitted record in the encounter's scope -- free text and structured, current and historical"
        " -- after",
        "the deterministic permission cut. It can add a missed source, emphasize a selected source, omit a",
        "baseline free-text note from generation on a confident `irrelevant` result, rank eligible sources",
        "by gate-checked urgency, or abstain. The source-wide negation question was dropped rather than paid",
        "for, because it cannot safely drive prose; the grounding verifier owns sentence-level contradiction",
        "risk. Thresholds remain "
        f"{decision_model.ADDITION_FLOOR} for additions and "
        f"{decision_model.OMISSION_FLOOR} for omissions, with a "
        f"{decision_model.DECISION_MARGIN} decision margin, under rule version "
        f"`{sorted({r['decision_on'].get('rule_version') for r in eval_doc['results'].values() if r['decision_on'].get('rule_version')})[0]}`.",
        "",
        "## Measured selection results",
        "",
        f"Live Decision-1 service, **{totals['encounters']} synthetic encounters**, author-authored and",
        f"unadjudicated expectations. Mode: {eval_doc['mode']}. Current limits are `SHORTLIST_LIMIT={decision_model.SHORTLIST_LIMIT}`, `MAX_NOTES={decision_model.MAX_NOTES}`, and `TOTAL_BUDGET_S={decision_model.TOTAL_BUDGET_S}`. Retained pre-cap scorecards are historical; their coverage describes the previous cap, not current coverage.",
        "",
        f"- **Additions:** {pair('addition_precision')} correct, precision "
        f"**{totals['addition_precision']}**; wrong additions {pair('wrong_additions')}; missed expected "
        f"additions {pair('missed_expected_additions')}.",
        f"- **Omission:** {pair('correct_omissions')} expected omissions correct; unexpected omissions "
        f"**{denominators['wrong_omissions'][0]}/{totals['baseline_attempted_denominator']}** attempted baseline candidates; "
        f"the wider selection-layer baseline universe is **{totals['baseline_candidate_denominator']} candidates**.",
        f"- **Abstention:** {pair('abstentions')} expected abstentions.",
        f"- **Emphasis:** {totals['emphasized_sources']} sources emphasised across "
        f"{totals['encounters_with_emphasis']}/{totals['encounters']} encounters; matched the authored "
        f"priority source {pair('emphasis_matches')}; wrong among emphasis that has an authored "
        f"expectation {pair('wrong_emphasis')}. {totals['unauthored_emphasis']} emphasised source(s) sit "
        "in encounters with no authored priority and are counted neither way.",
        f"- **Urgency order:** {ordered_ids} sources ordered across {ordered} encounters; urgency never "
        "changes eligibility, only the order of already-eligible sources.",
    ]
    if moved:
        lines.append("- **Candidates that moved:** " + "; ".join(moved) + ".")
    lines += [
        "",
        "## Stability of the borderline case",
        "",
        f"- `bound-note-017` was added in **{aggregate['added_bound_note_017']}/{aggregate['runs']}** live "
        f"repeats of the identical selection evaluation (additions per run {aggregate['additions_each']}), "
        f"with subject-binding margins **{margin_min:.2f}–{margin_max:.2f}** against the unchanged "
        f"{aggregate['threshold']} threshold — the tightest pass only "
        f"{margin_min - aggregate['threshold']:.2f} above it.",
        f"- {history}The case is therefore boundary-limited rather than stable, because the "
        "thresholds are inherited from the vendor's routing example and not calibrated on Finnish data. "
        "Per-run artifacts are listed under Sources.",
        "",
        "## Scale and narration checks",
        "",
        f"- Scale case `{scale_name}`: **{coverage['decision_candidate_count']} candidates** → "
        f"{coverage['decision_shortlisted_count']} shortlisted, {coverage['decision_assessed_count']} assessed, "
        f"{coverage['decision_too_long_count']} too long, {coverage['decision_budget_exhausted_count']} budget-exhausted, "
        f"{coverage['decision_not_shortlisted_count']} not shortlisted, {coverage['decision_unavailable_count']} unavailable "
        f"({coverage['decision_gate_refused_count']} gate-refused). Counts are asserted end-to-end in tests; this is a "
        "bounded synthetic Decision-1 selection scale check, not the delivery-scale import/index dry run below.",
        f"- Live scale-call result: Decision-1 stage **{scale_result['decision_elapsed_ms']} ms**; "
        f"{coverage['decision_assessed_count']}/{coverage['decision_candidate_count']} candidates were attempted, "
        f"{scale_answered} returned usable answers, statuses {', '.join(f'{name} {count}' for name, count in sorted(scale_statuses.items()))}. "
        f"This single {coverage['decision_assessed_count']}-request concurrent fan-out {scale_failure_reading}; it does not establish a vendor concurrency guarantee. An unanswered row is retained as `unavailable`.",
        f"- Aggregate coverage across {totals['encounters']} Decision-1-on encounters: "
        f"**{aggregate_coverage['decision_candidate_count']} candidates / {aggregate_coverage['decision_assessed_count']} assessed / "
        f"{aggregate_coverage['decision_budget_exhausted_count']} budget-exhausted / "
        f"{aggregate_coverage['decision_gate_refused_count']} gate-refused**; gate-refused is included among assessed, "
        "not a disjoint bucket.",
        f"- Unified window: **{prep} s** server preparation → joint {joint} s, generation {generation} s, "
        f"agent {agent} s; browser abort **{browser_ms // 1000} s**. The renderer refuses to emit this document "
        "if the trial artifact was produced under a different window.",
        f"- Narration trial: {encounters} cases ({trial['product_encounters']} product + "
        f"{trial['evaluator_only_encounters']} evaluator-only controls), agent path "
        f"**{trial['agent_reference_count']}/{trial['generation_enabled_modes']}**, direct fallbacks "
        f"{trial['direct_fallback_count']}/{trial['generation_enabled_modes']}, accepted narration "
        f"**{accepted}/{encounters}**.",
        f"- Trial scope: {trial['scope']}",
        f"- Sentence-1 primary-ID match: **{sentence1_matches}/{sentence1_eligible}**; any listed priority "
        f"ID appears first in sentence 1 in **{led}/{sentence1_eligible}**. These are different measures: "
        "the former tests the first listed ID, the latter any priority ID.\n"
        f"- Ordering: a priority source is cited in **{cited}/{sentence1_eligible}** comparable "
        f"encounters, leads in {led}/{sentence1_eligible}, and appears within the first two sentences in "
        f"{within_two}/{sentence1_eligible} ({sentence1_total} encounters total; "
        f"{sentence1_not_comparable} had no priority source). Split by what leads the list: "
        f"**{note_led_first}/{note_led_total}** when it is a free-text note, "
        f"{led - note_led_first}/{sentence1_eligible - note_led_total} when it is a structured record. The "
        "instruction is note-centric, so the bare sentence-1 rate understates this and the split is the "
        "honest reading: the source is never omitted, but a diagnosis or decision is context rather than a "
        "narratable time-critical fact. This is an observed rate on synthetic fixtures, not a guarantee.",
        f"- Verifier: **{verifier['sentences_proposed']} sentences proposed end-to-end, "
        f"{verifier['sentences_accepted']} rendered, {verifier['sentences_dropped']} withheld** "
        f"({', '.join(f'{n} {reason}' for reason, n in sorted(verifier['drop_reasons'].items()))}). "
        f"Decision-1 itself judged {verifier['decision_sentences_checked']} and supported "
        f"**{verifier['decision_supported']}/{verifier['decision_sentences_checked']}**; the deterministic membership "
        "check and Decision-1's verifier are separate gates and separate rates. This is a decision rate, not a "
        "gold-standard grounding measurement.",
        "",
        *dry_run_section.splitlines(),
        "",
        *sensitivity_section.splitlines(),
        "",
        *agreement_section.splitlines(),
        "",
        "## Limits / not established",
        "",
    ]
    for title, items in limit_groups:
        if not items:
            continue
        lines.append(f"*{title}*")
        lines.append("")
        lines += [f"- {item.rstrip('.')}." for item in items]
        lines.append("")
    lines += [
        "## Sources",
        "",
        f"- `{MANIFEST_PATH.relative_to(ROOT)}` (kind → artifact index)",
        f"- `{eval_path.relative_to(ROOT)}`",
        f"- `{trial_path.relative_to(ROOT)}`",
        f"- `{instability_path.relative_to(ROOT)}` (per-run rows beside it: "
        f"`run1.json`–`run{instability['runs']}.json`)",
        f"- `{ablation_path.relative_to(ROOT)}` (paired floor/margin ablation)",
        f"- `{disagreement_path.relative_to(ROOT)}` (structured-fact disagreement)",
        f"- `{dry_run_path.relative_to(ROOT)}` (synthetic delivery-shape import/index dry run)",
        f"- `{probe_path.relative_to(ROOT)}`" if probe_path.is_file() else "- content-safety probe: not present",
        "- Raw narration-bearing trial output stays gitignored; only the scrubbed summary above is tracked.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if the committed document differs from a fresh render")
    args = parser.parse_args()
    try:
        rendered = render()
    except RenderError as error:
        print(f"render_evidence: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.is_file() else ""
        if current != rendered:
            print(f"{TARGET.relative_to(ROOT)} is stale: re-run "
                  f"`cd backend && .venv/bin/python scripts/render_evidence.py`", file=sys.stderr)
            raise SystemExit(1)
        print(f"{TARGET.relative_to(ROOT)} matches its sources")
        return
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(rendered, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)} ({len(rendered.splitlines())} lines)")


if __name__ == "__main__":
    main()
