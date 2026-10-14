"""Runtime grounding check for fresh narration.

Schema validation checks SHAPE, not grounding. A well-formed but wrong output
looks authoritative, so this deterministic pair decides what prose renders:
  - `validate_fresh_candidate`: every cited record_id is in the role/session-
    permitted *and* selected set, and every proposal_id is allowed;
  - `accept_supported_candidate`: every sentence needs one unique `supported`
    verdict backed by ids drawn from that sentence's own sources, and a sentence
    whose text is empty after `.strip()` rejects the whole candidate (no vacuous
    pass: blank prose must not render under a green badge);
  - on any violation, the prose is dropped and the structured decision object
    renders directly (graceful degradation).

Actions are deliberately not checked here: they render from the decision object,
never from model text.

`validate_fresh_candidate` returns a typed `CandidateValidation`;
`accept_supported_candidate` returns a `(GroundingResult, status, reason)`
tuple describing what the check found. Assembling the external `Picture` is
`pipeline`'s job, not this module's (ADR-0002).
"""

from __future__ import annotations

from dataclasses import dataclass

from .llm import FreshNarration, SupportReview
from .picture import GroundingResult, RecordView, RejectedSentence
from .policy import Scoped


@dataclass(frozen=True)
class CandidateValidation:
    candidate: FreshNarration | None
    valid: bool
    reason: str | None
    records: dict[str, RecordView]
    # Internal retry metadata only; rejected sentence text never crosses into feedback.
    retry_feedback: tuple[tuple[str, tuple[str, ...]], ...] = ()


def validate_fresh_candidate(
    scoped: Scoped,
    candidate: object,
    selected_record_ids: list[str],
    allowed_proposal_ids: list[str] | None = None,
) -> CandidateValidation:
    """Validate output shape and selected-source/proposal membership before judging."""
    permitted = set(scoped.ids)
    selected = set(selected_record_ids)
    allowed_proposals = set(allowed_proposal_ids or [])
    records = {record.record_id: RecordView.from_record(record) for record in scoped.records}
    if not selected <= permitted:
        return CandidateValidation(None, False, "invalid_membership", records)
    try:
        parsed = candidate if isinstance(candidate, FreshNarration) else FreshNarration.model_validate(candidate)
    except Exception:  # malformed output is a whole-output rejection
        return CandidateValidation(None, False, "invalid_output", records)
    sentence_ids = [sentence.sentence_id for sentence in parsed.sentences]
    if len(sentence_ids) != len(set(sentence_ids)):
        return CandidateValidation(None, False, "invalid_output", records)
    missing_citations = tuple(
        (sentence.sentence_id, tuple(sorted(selected & permitted)))
        for sentence in parsed.sentences if not sentence.source_ids
    )
    if missing_citations:
        return CandidateValidation(None, False, "missing_citations", records,
                                   tuple((sentence_id, ids) for sentence_id, ids in missing_citations if ids))
    rejected: list[tuple[str, tuple[str, ...]]] = []
    # A sentence citing an id outside the scope with nothing to re-point it to (an empty selection, or a
    # placeholder such as "/") is a whole-output rejection here, so the verifier never sees an id it cannot
    # resolve: it raises on one, and that read as `support_failed` instead of an invalid candidate.
    unresolvable = False
    for sentence in parsed.sentences:
        if len(sentence.source_ids) != len(set(sentence.source_ids)) or len(sentence.proposal_ids) != len(set(sentence.proposal_ids)):
            return CandidateValidation(None, False, "invalid_output", records)
        if (not set(sentence.source_ids) <= selected or not set(sentence.source_ids) <= permitted
                or not set(sentence.proposal_ids) <= allowed_proposals):
            allowed_ids = set(sentence.source_ids) & selected & permitted
            if not allowed_ids:
                allowed_ids = selected & permitted
            if allowed_ids:
                rejected.append((sentence.sentence_id, tuple(sorted(allowed_ids))))
            else:
                unresolvable = True
    if rejected or unresolvable:
        return CandidateValidation(None, False, "invalid_membership", records, tuple(rejected))
    return CandidateValidation(parsed, True, None, records)


def accept_supported_candidate(
    validation: CandidateValidation, review: object
) -> tuple[GroundingResult, str, str | None]:
    """Require one unique, in-scope supported verdict for every sentence."""
    if not validation.valid or validation.candidate is None:
        return GroundingResult(narrative=None, citations=[], grounded=False, dropped_citations=[], dropped_actions=[]), "invalid", validation.reason or "invalid_output"
    try:
        parsed = review if isinstance(review, SupportReview) else SupportReview.model_validate(review)
    except Exception:
        return GroundingResult(narrative=None, citations=[], grounded=False, dropped_citations=[], dropped_actions=[]), "invalid", "invalid_verdicts"
    sentences = {sentence.sentence_id: sentence for sentence in validation.candidate.sentences}
    verdicts = {verdict.sentence_id: verdict for verdict in parsed.verdicts
                if verdict.sentence_id in sentences}
    if len(verdicts) != len(parsed.verdicts):
        return GroundingResult(narrative=None, citations=[], grounded=False, dropped_citations=[], dropped_actions=[]), "invalid", "unexpected_verdict"
    accepted: list[tuple] = []
    rejected: list[tuple] = []
    reasons: set[str] = set()

    def drop(sentence, reason: str) -> None:
        rejected.append((sentence, reason))
        reasons.add(reason)

    for sentence_id, sentence in sentences.items():
        verdict = verdicts.get(sentence_id)
        if verdict is None:
            drop(sentence, "missing_verdict")
        elif not sentence.text.strip():
            drop(sentence, "empty_narration")
        elif (len(verdict.supporting_record_ids) != len(set(verdict.supporting_record_ids))
              or not set(verdict.supporting_record_ids) <= set(sentence.source_ids)
              or (verdict.verdict == "supported" and not verdict.supporting_record_ids)):
            drop(sentence, "invalid_support_ids")
        elif verdict.verdict == "supported":
            accepted.append((sentence, verdict))
        else:
            drop(sentence, verdict.verdict)
    rejected_rows = [RejectedSentence(sentence_id=sentence.sentence_id, reason=reason)
                     for sentence, reason in rejected]
    if not accepted:
        status = ("invalid" if reasons & {"empty_narration", "invalid_support_ids"}
                  else "unsupported" if "unsupported" in reasons else "unknown")
        reason = "empty_narration" if "empty_narration" in reasons else status
        return GroundingResult(narrative=None, citations=[], grounded=False,
                               dropped_citations=[], dropped_actions=[],
                               rejected_sentences=rejected_rows), status, reason
    citations: list[RecordView] = []
    seen: set[str] = set()
    for _sentence, verdict in accepted:
        for record_id in verdict.supporting_record_ids:
            if record_id not in seen:
                seen.add(record_id)
                citations.append(validation.records[record_id])
    candidate_source_ids = [record_id for sentence in sentences.values() for record_id in sentence.source_ids]
    dropped_ids = [record_id for record_id in candidate_source_ids if record_id not in seen]
    narrative = " ".join(sentence.text.strip() for sentence, _verdict in accepted)
    partial = len(accepted) != len(sentences)
    return GroundingResult(narrative=narrative, citations=citations, grounded=True,
                           dropped_citations=list(dict.fromkeys(dropped_ids)), dropped_actions=[],
                           rejected_sentences=rejected_rows), \
        ("partial" if partial else "supported"), ("partial_support" if partial else None)
