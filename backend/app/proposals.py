"""Bounded source-review proposals; never reinterpret clinical action rules."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import date

from . import assess
from .encounters import Encounter
from .picture import Assessment, ProposalEvidence, ReviewProposal
from .policy import Scoped
from .upm import Record

ASSESSMENT_REVIEW_RULE = "challenge-1.1-assessment-review-v1"
CONFLICT_REVIEW_RULE = "challenge-1.1-source-conflict-review-v1"


def _proposal(
    encounter_id: str,
    rule_version: str,
    text: str,
    status: str,
    evidence: list[ProposalEvidence],
) -> ReviewProposal:
    evidence = sorted(evidence, key=lambda item: item.record_id)
    ids = sorted({item.record_id for item in evidence})
    identity = "\0".join((encounter_id, rule_version, *ids))
    proposal_id = "review-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return ReviewProposal(
        proposal_id=proposal_id,
        rule_version=rule_version,
        text=text,
        status=status,
        evidence_record_ids=ids,
        evidence=evidence,
    )


def review_proposals(
    encounter_id: str,
    assessments: list[Assessment],
    conflicts: list[dict],
    selected_record_ids: Iterable[str],
    records: list[Record],
) -> list[ReviewProposal]:
    """Create review-only proposals from exact selected assessment/conflict sources.

    The module guarantees "evidence ⊆ permitted" itself: every candidate id is intersected with the permitted
    record ids, so a caller that passes a `selected` set wider than `records` cannot make an out-of-permitted
    id appear in a proposal, and the `recorded_at` lookup cannot raise for one.
    """
    permitted = {record.record_id for record in records}
    selected = set(selected_record_ids) & permitted
    rows: dict[tuple, dict[str, ProposalEvidence]] = {}
    for result in assessments:
        if result.temporal_status not in {"incomplete", "ambiguous"}:
            continue
        if result.temporal_status == "incomplete" and not any(item.pisteet is None for item in result.osiot):
            continue
        evidence_ids = sorted(set(result.lahteet) & selected & permitted)
        if not evidence_ids:
            continue
        version = result.instrument_version
        key = (result.instrumentti, version, result.temporal_status, result.recorded_at)
        evidence = rows.setdefault(key, {})
        for record_id in evidence_ids:
            evidence[record_id] = ProposalEvidence(
                record_id=record_id,
                recorded_at=result.recorded_at,
                instrument=result.instrumentti,
                instrument_version=version,
            )

    proposals: list[ReviewProposal] = []
    for (instrument, _version, status, _recorded_at), evidence_by_id in rows.items():
        evidence = list(evidence_by_id.values())
        if status == "incomplete":
            text = f"Tarkista arvioinnin puuttuvat tai ristiriitaiset vastaukset ({instrument})."
        else:
            text = f"Tarkista ristiriitaiset vastaukset ({instrument})."
        proposals.append(_proposal(encounter_id, ASSESSMENT_REVIEW_RULE, text, status, evidence))

    record_by_id = {record.record_id: record for record in records}
    for conflict in conflicts:
        ids = [
            record_id for record_id in (conflict.get("medication_record_id"), conflict.get("note_record_id"))
            if isinstance(record_id, str)
        ]
        if len(ids) != 2 or not set(ids) <= selected:
            continue
        proposals.append(_proposal(
            encounter_id,
            CONFLICT_REVIEW_RULE,
            "Tarkista lääkitys- ja kirjauslähteiden välinen ristiriita.",
            "source_conflict",
            [ProposalEvidence(record_id=record_id, recorded_at=record_by_id[record_id].recorded_at) for record_id in ids],
        ))
    return proposals


def proposals_for_encounter(
    encounter: Encounter,
    permitted: Scoped,
    baseline_records: list[Record],
    conflicts: list[dict],
    today: date,
) -> list[ReviewProposal]:
    """Derive the deterministic proposal set shared by Picture and feedback.

    Proposals are bound to baseline records; Decision-1 additions do not change the set.
    """
    selected_ids = (record.record_id for record in baseline_records)
    assessments = assess.select_for_picture(baseline_records, today)
    return review_proposals(encounter.encounter_id, assessments, conflicts, selected_ids, permitted.records)
