"""Purpose-permitted baseline and additive Decision-1 selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from . import assess, source
from .policy import Scoped
from .purpose import palvelukonteksti
from .upm import Record

RULE_VERSION = "challenge-1.1-decision-selection-v8"
# The deterministic baseline's own version, folded into the briefing cache key beside RULE_VERSION: a change
# to what the baseline selects (not to the Decision-1 contract) must rebuild every cached briefing, or a
# deploy serves the previous rule's pictures until the warm-up happens to reach them (seen 2026-10-14).
BASELINE_VERSION = "baseline-2026-10-14-today-cut"

@dataclass(frozen=True)
class Selection:
    records: tuple[Record, ...]
    baseline_record_ids: tuple[str, ...]
    additional_record_ids: tuple[str, ...]
    emphasized_record_ids: tuple[str, ...]
    reasons: dict[str, tuple[str, ...]]
    decision_status: str = "not_run"
    decision_notes: tuple[dict, ...] = ()
    narration_records: tuple[Record, ...] = ()
    demoted_record_ids: tuple[str, ...] = ()
    # Validated P(relevant) per assessed candidate, refused ones included. Read only by relevance_order.
    relevance_scores: dict[str, float] = field(default_factory=dict)

    def relevance_order(self, record_ids: list[str]) -> list[str]:
        """Order already-eligible sources: validated relevance descending, then urgency descending, then the incoming
        order. It only orders. Eligibility is decided before this is called, so nothing is added, removed or demoted.

        A source without a validated relevance (not assessed) sorts after every assessed one, and a source without an
        urgency score sorts after every scored one within the same relevance. Deterministic: same inputs, same order.
        """
        urgency = {item.get("record_id"): item["urgency"]["score"] for item in self.decision_notes
                   if isinstance(item.get("urgency"), dict)
                   and isinstance(item["urgency"].get("score"), (int, float))
                   and not isinstance(item["urgency"].get("score"), bool)}
        def key(pair):
            index, record_id = pair
            relevance = self.relevance_scores.get(record_id)
            score = urgency.get(record_id)
            return (relevance is None, -(relevance or 0.0), score is None, -(score or 0.0), index)
        return [record_id for _, record_id in sorted(enumerate(record_ids), key=key)]

    def urgency_order(self, record_ids: list[str]) -> list[str]:
        """Order already-eligible sources by validated Decision-1 urgency; urgency never selects them.

        Sources with a returned Score sort first by score descending; unscored and stalled sources
        keep their incoming order after them. The sort is stable, so the order is deterministic.
        """
        scores = {item.get("record_id"): item["urgency"]["score"] for item in self.decision_notes
                  if isinstance(item.get("urgency"), dict)
                  and isinstance(item["urgency"].get("score"), (int, float))
                  and not isinstance(item["urgency"].get("score"), bool)}
        return [record_id for _, record_id in sorted(
            enumerate(record_ids),
            key=lambda pair: (pair[1] not in scores, -scores.get(pair[1], 0.0), pair[0]),
        )]

    def priority_ids(self, cited_ids: list[str]) -> list[str]:
        """Emphasized sources lead, then already-eligible sources by Decision-1 urgency."""
        emphasized = list(dict.fromkeys(self.emphasized_record_ids))
        priority = emphasized + [record_id for record_id in cited_ids
                                 if record_id not in emphasized and record_id not in self.demoted_record_ids]
        return self.urgency_order(priority)


def baseline(scoped: Scoped, today: date) -> tuple[list[Record], dict[str, list[str]]]:
    """Select only explicit purpose-relevant facts after the policy cut."""
    if scoped.purpose is None:
        raise ValueError("purpose is required for selection")
    terms = palvelukonteksti(scoped.purpose).baseline_terms
    reasons: dict[str, list[str]] = {}
    assessment_rows = assess.select_for_picture(scoped.records, today)
    assessment_ids = {record_id for row in assessment_rows for record_id in row.lahteet}

    for record in scoped.records:
        why: list[str] = []
        if record.record_type == source.BASIC_INFO:
            why.append("context:person-basics")
        elif source.is_active_medication(record):
            why.append("context:active-medication")
        elif record.record_type == source.DIAGNOSIS and record.payload.get("tila") == source.MEDICATION_ACTIVE:
            why.append("context:active-diagnosis")
        elif (record.record_type in {source.LAB_RESULT, source.MEASUREMENT}
              and source.temporal_status(record, today) == "current"):
            # A result from the last year is context the way an active medication is: the professional reads
            # it whatever the question (owner's ruling 2026-10-14, "lab results are critical"); older ones
            # still need a keyword or a Decision-1 read.
            why.append("context:current-result")
        elif source.is_assessment(record):
            if record.record_id in assessment_ids:
                why.append("assessment:latest-relevant")
        elif source.temporal_status(record, today) in {"current", "history"}:
            text = " ".join([record.topic, source.note_text(record), *source.string_values(record)]).casefold()
            matching = [term for term in terms if term in text]
            if matching:
                why.append("keyword:" + ",".join(matching))
        if why:
            reasons[record.record_id] = why

    if scoped.purpose == HOME_CARE_FOLD_PURPOSE:
        _fold_same_day_home_care(scoped.records, reasons)
    chosen = [record for record in scoped.records if record.record_id in reasons]
    return chosen, reasons


# The social lens folds same-day home-care visits (owner decision 2026-10-14). A person under regular home care has
# several `kotihoidon palvelu` contacts a day, and the baseline admitted every one as a selected record, so the
# briefing listed ten near-identical rows. The fold keeps one visit per day in the baseline -- the newest, the same
# ordering `prefill._homecare_items` uses -- and names the folded visits in the kept record's reasons, so nothing
# leaves the selection silently. It changes the baseline only: Decision-1 still sees every permitted record, and a
# folded visit it judges relevant is added back by `apply_decision` like any other permitted record.
HOME_CARE_FOLD_PURPOSE = "functioning_service_needs"
HOME_CARE_TOPIC = "kotihoidon palvelu"
HOME_CARE_FOLD_REASON = "fold:same-day-home-care"


def _event_time(record: Record):
    return record.käyntiaika or record.recorded_at


def _fold_same_day_home_care(records, reasons: dict[str, list[str]]) -> None:
    by_day: dict[date, list[Record]] = {}
    for record in records:
        if (record.record_id in reasons and record.record_type == source.APPOINTMENT
                and record.topic == HOME_CARE_TOPIC):
            by_day.setdefault(_event_time(record).date(), []).append(record)
    for visits in by_day.values():
        if len(visits) < 2:
            continue
        # Newest first; a tie on time keeps the one the source listed first, so the fold is deterministic.
        ordered = sorted(enumerate(visits), key=lambda pair: (-_event_time(pair[1]).timestamp(), pair[0]))
        kept, folded = ordered[0][1], [visit for _, visit in ordered[1:]]
        for visit in folded:
            del reasons[visit.record_id]
        reasons[kept.record_id].append(HOME_CARE_FOLD_REASON + ":" + ",".join(visit.record_id for visit in folded))


def apply_decision(
    scoped: Scoped, baseline_records: list[Record], reasons: dict[str, list[str]],
    note_results: list[dict], today: date,
) -> Selection:
    """Add or emphasise any permitted record the model judged relevant, current or historical. The model proposes;
    grounding decides. A verdict never removes or demotes a baseline record, and the reason names the temporal status
    so a historical addition is never presented as a current fact.

    `no_input` means no assessable permitted record existed: classify_notes returns nothing only for an empty pool,
    and every candidate otherwise yields a row, so the empty list is the one case that reports it.
    """
    if scoped.purpose is None:
        raise ValueError("purpose is required for selection")
    baseline_ids = [record.record_id for record in baseline_records]
    baseline_set = set(baseline_ids)
    by_id = {record.record_id: record for record in scoped.records}
    additions: list[str] = []
    emphasized: list[str] = []
    merged_reasons = {key: list(value) for key, value in reasons.items()}
    for result in note_results:
        record_id = result.get("record_id")
        record = by_id.get(record_id) if isinstance(record_id, str) else None
        if result.get("status") != "relevant" or not isinstance(record_id, str) or record is None:
            continue
        reason = (f"decision:{scoped.purpose}:relevant:{record.record_type}"
                  f":{source.temporal_status(record, today)}")
        if record_id not in emphasized:
            emphasized.append(record_id)
        if record_id in baseline_set:
            merged_reasons.setdefault(record_id, []).append(reason)
        elif record_id not in additions:
            additions.append(record_id)
            merged_reasons[record_id] = [reason]
    # A completed ("attempted") explicit irrelevant read suppresses current
    # baseline free text from generation only; the professional keeps the source.
    irrelevant = {item.get("record_id") for item in note_results
                  if item.get("status") == "irrelevant" and bool(item.get("attempted"))}
    omitted = {record_id for record_id in irrelevant & baseline_set
               if isinstance(record_id, str)
               and (record := by_id.get(record_id)) is not None
               and record.record_type == source.FREE_TEXT
               and source.temporal_status(record, today) == "current"}
    demoted = {item.get("record_id") for item in note_results
               if isinstance(item.get("record_id"), str) and item.get("record_id") in baseline_set
               and item.get("status") != "relevant"
               and (record := by_id.get(item["record_id"])) is not None
               and record.record_type == source.FREE_TEXT}
    selected = baseline_set | set(additions)
    statuses = {item.get("status") for item in note_results}
    status = ("no_input" if not note_results else "complete" if statuses <= {"relevant", "irrelevant", "unclear"}
              else "partial" if statuses & {"relevant", "irrelevant", "unclear"} else "unavailable")
    return Selection(
        records=tuple(record for record in scoped.records if record.record_id in selected),
        narration_records=tuple(record for record in scoped.records if record.record_id in selected - omitted),
        demoted_record_ids=tuple(record_id for record_id in baseline_ids if record_id in demoted),
        baseline_record_ids=tuple(baseline_ids), additional_record_ids=tuple(additions),
        emphasized_record_ids=tuple(emphasized), reasons={key: tuple(value) for key, value in merged_reasons.items()},
        decision_status=status, decision_notes=tuple(note_results),
        relevance_scores=_relevance_scores(note_results),
    )


def _relevance_scores(note_results: list[dict]) -> dict[str, float]:
    """P(relevant) for every row whose relevance answer was validated (the parser sets it only after validation)."""
    scores: dict[str, float] = {}
    for item in note_results:
        relevance = item.get("relevance")
        probability = (relevance.get("probabilities") or {}).get("relevant") if isinstance(relevance, dict) else None
        if isinstance(item.get("record_id"), str) and isinstance(probability, (int, float)) \
                and not isinstance(probability, bool):
            scores[item["record_id"]] = probability
    return scores
