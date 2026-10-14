"""Owner of the narration outcome for one Picture.

`narrate` runs the bounded two-stage Foundry path -- generation, then the Decision-1
verification gate -- and returns one `NarrationOutcome`. `narration_fields` is the single,
total projection of that value onto the frozen flat `Picture.narration_*` fields
(ADR-0001); `pipeline` consumes the dict and never re-derives a status.

The internal vocabulary is closed and a little richer than the wire. Every member
maps onto the wire columns in `_WIRE_STATUS`, and the wire columns are unchanged.

The transport is resolved by attribute lookup at call time (`stages`, default the
`llm` module), so `monkeypatch.setattr(llm, ...)` stubs keep working. The stage
budgets live here; the preparation window crosses as one `Preparation` value so
the joint cap still anchors at request start (not at narration-call time).
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Literal, Sequence

from . import decision_model, llm
from .grounding import accept_supported_candidate, validate_fresh_candidate
from .picture import GroundingResult
from .policy import Scoped
# pi-lens-ignore: reportMissingImports
from .telemetry import record as telemetry_record  # type: ignore[reportMissingImports]

NARRATION_GENERATION_BUDGET_S = 70.0
NARRATION_SUPPORT_BUDGET_S = 8.0
NARRATION_JOINT_BUDGET_S = 80.0
NARRATION_PREPARATION_RESERVE_S = 5.0

NarrationName = Literal[
    "unavailable",
    "budget_exhausted",
    "generation_timeout",
    "generation_failed",
    "safety_blocked",
    "candidate_invalid",
    "support_timeout",
    "support_failed",
    "support_invalid",
    "unsupported",
    "unknown",
    "supported",
    "partial_support",
]

# The one map from the internal vocabulary to the frozen wire columns.
_WIRE_STATUS: dict[str, tuple[str, str, str]] = {
    "unavailable": ("unavailable", "not_run", "rejected"),
    "budget_exhausted": ("timed_out", "not_run", "rejected"),
    "generation_timeout": ("timed_out", "not_run", "rejected"),
    "generation_failed": ("failed", "not_run", "rejected"),
    "safety_blocked": ("failed", "not_run", "rejected"),
    "candidate_invalid": ("generated", "invalid", "rejected"),
    "support_timeout": ("generated", "timed_out", "rejected"),
    "support_failed": ("generated", "invalid", "rejected"),
    "support_invalid": ("generated", "invalid", "rejected"),
    "unsupported": ("generated", "unsupported", "rejected"),
    "unknown": ("generated", "unknown", "rejected"),
    "supported": ("generated", "supported", "accepted"),
    "partial_support": ("generated", "supported", "accepted"),
}

_DEFAULT_REASON: dict[str, str | None] = {
    "unavailable": "foundry_unavailable",
    "budget_exhausted": "budget_exhausted",
    "generation_timeout": "generation_timeout",
    "generation_failed": "generation_failed",
    "safety_blocked": "content_filtered",
    "candidate_invalid": "invalid_output",
    "support_timeout": "support_timeout",
    "support_failed": "support_failed",
    "support_invalid": "invalid_verdicts",
    "unsupported": "unsupported",
    "unknown": "unknown",
    "supported": None,
    "partial_support": "partial_support",
}

_ACCEPTANCE_NAME: dict[str, NarrationName] = {
    "supported": "supported",
    "partial": "partial_support",
    "unsupported": "unsupported",
    "unknown": "unknown",
    "invalid": "support_invalid",
}


@dataclass(frozen=True)
class Preparation:
    """One value carrying the request-start anchor and the preparation deadline.

    The joint narration budget is capped at `started_at + JOINT_BUDGET` (the old
    request-start anchor), guarded by `deadline - RESERVE`.
    """

    started_at: float
    deadline: float


@dataclass(frozen=True)
class NarrationOutcome:
    name: NarrationName
    selected_record_ids: tuple[str, ...]
    grounding: GroundingResult
    generation_path: Literal["agent_reference", "direct_fallback", "direct_chat_completions"] | None
    generation_model: str | None
    support_model: str | None
    generation_elapsed_ms: int
    generation_input_tokens: int | None
    generation_output_tokens: int | None
    support_elapsed_ms: int
    support_input_tokens: int | None
    support_output_tokens: int | None
    elapsed_ms: int
    reason: str | None


def _empty_grounding() -> GroundingResult:
    return GroundingResult(
        narrative=None, citations=[], grounded=False, dropped_citations=[], dropped_actions=[]
    )


def _outcome(
    name: NarrationName,
    *,
    selected_record_ids: tuple[str, ...] = (),
    grounding: GroundingResult | None = None,
    generation_path: Literal["agent_reference", "direct_fallback", "direct_chat_completions"] | None = None,
    generation_model: str | None = None,
    support_model: str | None = None,
    generation_elapsed_ms: int = 0,
    generation_input_tokens: int | None = None,
    generation_output_tokens: int | None = None,
    support_elapsed_ms: int = 0,
    support_input_tokens: int | None = None,
    support_output_tokens: int | None = None,
    elapsed_ms: int = 0,
    reason: str | None = None,
) -> NarrationOutcome:
    return NarrationOutcome(
        name=name,
        selected_record_ids=selected_record_ids,
        grounding=grounding if grounding is not None else _empty_grounding(),
        generation_path=generation_path,
        generation_model=generation_model,
        support_model=support_model,
        generation_elapsed_ms=generation_elapsed_ms,
        generation_input_tokens=generation_input_tokens,
        generation_output_tokens=generation_output_tokens,
        support_elapsed_ms=support_elapsed_ms,
        support_input_tokens=support_input_tokens,
        support_output_tokens=support_output_tokens,
        elapsed_ms=elapsed_ms,
        reason=_DEFAULT_REASON[name] if reason is None else reason,
    )


def _milliseconds(started: float, clock: Callable[[], float]) -> int:
    return max(0, round((clock() - started) * 1000))


async def narrate(
    permitted: Scoped,
    selected_record_ids: list[str],
    *,
    today: date,
    preparation: Preparation,
    proposal_ids: Sequence[str] | None = None,
    stages=None,
    clock: Callable[[], float] = time.monotonic,
    foundry_enabled: bool = True,
) -> NarrationOutcome:
    """Generate, membership-check and verify within the preparation window.

    `preparation` carries the request-start anchor and deadline as one value.
    `stages` is the transport (default the `llm` module), resolved by attribute
    lookup so test stubs apply. At most one rejected draft is regenerated; retry
    feedback contains only sentence IDs, rejection reasons and permitted citation IDs.
    """
    transport = llm if stages is None else stages
    allowed_proposals = [] if proposal_ids is None else list(proposal_ids)
    selected = tuple(selected_record_ids)
    started = clock()

    def result(name: NarrationName, **fields: Any) -> NarrationOutcome:
        return _outcome(name, selected_record_ids=selected, **fields)

    if not foundry_enabled or not transport.foundry_configured():
        return result("unavailable", reason="foundry_disabled" if not foundry_enabled else "foundry_unavailable")

    joint_deadline = min(
        preparation.started_at + NARRATION_JOINT_BUDGET_S,
        preparation.deadline - NARRATION_PREPARATION_RESERVE_S,
    )
    joint_remaining = joint_deadline - clock()
    verify_reserve = min(NARRATION_SUPPORT_BUDGET_S, max(0.0, joint_remaining))
    generation_timeout = min(
        NARRATION_GENERATION_BUDGET_S,
        max(0.0, joint_remaining - verify_reserve),
    )
    if generation_timeout <= 0:
        return result("budget_exhausted", elapsed_ms=_milliseconds(started, clock))

    generation_deadline = clock() + generation_timeout
    generation_results = []
    support_results = []
    generation_elapsed = 0
    support_elapsed = 0
    rejection_feedback = None
    attempt = 0

    def token_total(rows: list[Any], key: str) -> int | None:
        values: list[int] = []
        for row in rows:
            value = row.get(key) if isinstance(row, dict) else getattr(row, key)
            if type(value) is not int or value < 0:
                return None
            values.append(value)
        return sum(values) if values else None

    def generation_fields() -> dict[str, Any]:
        return {
            "generation_path": generation_results[-1].path if generation_results else None,
            "generation_model": generation_results[-1].model if generation_results else None,
            "generation_elapsed_ms": generation_elapsed,
            "generation_input_tokens": token_total(generation_results, "input_tokens"),
            "generation_output_tokens": token_total(generation_results, "output_tokens"),
        }

    def support_fields() -> dict[str, Any]:
        return {
            "support_model": next((row.get("model") for row in reversed(support_results) if row.get("model")), None),
            "support_elapsed_ms": support_elapsed,
            "support_input_tokens": token_total(support_results, "input_tokens"),
            "support_output_tokens": token_total(support_results, "output_tokens"),
        }

    def retry_timeout() -> float:
        return max(0.0, min(generation_deadline - clock(),
                            joint_deadline - clock() - NARRATION_SUPPORT_BUDGET_S))

    while True:
        attempt += 1
        timeout = generation_timeout if attempt == 1 else retry_timeout()
        if timeout <= 0:
            return result("budget_exhausted", **generation_fields(), **support_fields(),
                          elapsed_ms=_milliseconds(started, clock))
        generation_started = clock()
        generation_trace_started = time.monotonic()
        try:
            if rejection_feedback is None:
                generated = await transport.generate_fresh_narration(permitted, today, timeout)
            else:
                generated = await transport.generate_fresh_narration(
                    permitted, today, timeout, rejection_feedback=rejection_feedback
                )
            generation_elapsed += _milliseconds(generation_started, clock)
            generation_results.append(generated)
            telemetry_record("foundry.generation", generation_trace_started,
                             model=generated.model, path=generated.path,
                             input_tokens=generated.input_tokens, output_tokens=generated.output_tokens,
                             status="generated")
        except TimeoutError:
            generation_elapsed += _milliseconds(generation_started, clock)
            telemetry_record("foundry.generation", generation_trace_started,
                             model=llm.FOUNDRY_MODEL, status="timeout")
            return result("generation_timeout", **generation_fields(), **support_fields(),
                          elapsed_ms=_milliseconds(started, clock))
        except llm.ContentFiltered:
            generation_elapsed += _milliseconds(generation_started, clock)
            # A safety hit is terminal; retrying or falling back could defeat the content filter.
            telemetry_record("foundry.generation", generation_trace_started, model=llm.FOUNDRY_MODEL,
                             status="content_filtered")
            return result("safety_blocked", **generation_fields(), **support_fields(),
                          elapsed_ms=_milliseconds(started, clock))
        except Exception as exc:  # noqa: BLE001 -- structured picture remains available
            generation_elapsed += _milliseconds(generation_started, clock)
            telemetry_record("foundry.generation", generation_trace_started, model=llm.FOUNDRY_MODEL,
                             status="failed")
            print(f"[llm] narration generation failed: {type(exc).__name__}", file=sys.stderr)
            return result("generation_failed", **generation_fields(), **support_fields(),
                          elapsed_ms=_milliseconds(started, clock))

        validation = validate_fresh_candidate(permitted, generated.output, selected_record_ids, allowed_proposals)
        if not validation.valid or validation.candidate is None:
            if (attempt == 1 and validation.reason in {"invalid_membership", "missing_citations"}
                    and validation.retry_feedback and retry_timeout() > 0):
                rejection_feedback = [
                    {"sentence_id": sentence_id, "reason": validation.reason,
                     "permitted_citation_ids": list(citation_ids)}
                    for sentence_id, citation_ids in validation.retry_feedback
                ]
                continue
            return result("candidate_invalid", **generation_fields(), **support_fields(),
                          elapsed_ms=_milliseconds(started, clock),
                          reason=validation.reason or "invalid_output")

        support_timeout = min(NARRATION_SUPPORT_BUDGET_S, joint_deadline - clock())
        if support_timeout <= 0:
            return result("support_timeout", **generation_fields(), **support_fields(),
                          elapsed_ms=_milliseconds(started, clock))
        support_started = clock()
        support_trace_started = time.monotonic()
        try:
            reviewed = await decision_model.verify_narration(
                permitted, validation.candidate, today, support_timeout
            )
            support_elapsed += _milliseconds(support_started, clock)
            support_results.append(reviewed)
            telemetry_record("grounding.verification", support_trace_started, model=reviewed.get("model"),
                             input_tokens=reviewed.get("input_tokens"), output_tokens=reviewed.get("output_tokens"),
                             status=reviewed.get("status", "complete"))
        except TimeoutError:
            support_elapsed += _milliseconds(support_started, clock)
            telemetry_record("grounding.verification", support_trace_started, model=decision_model.MODEL,
                             status="timeout")
            return result("support_timeout", **generation_fields(), **support_fields(),
                          elapsed_ms=_milliseconds(started, clock))
        except Exception as exc:  # noqa: BLE001 -- invalid/failed verifier rejects the whole prose
            support_elapsed += _milliseconds(support_started, clock)
            telemetry_record("grounding.verification", support_trace_started, model=decision_model.MODEL,
                             status="failed")
            print(f"[llm] Decision-1 grounding verifier failed: {type(exc).__name__}", file=sys.stderr)
            return result("support_failed", **generation_fields(), **support_fields(),
                          elapsed_ms=_milliseconds(started, clock))

        review = {"verdicts": [{key: row[key] for key in ("sentence_id", "verdict", "supporting_record_ids")}
                               for row in reviewed["verdicts"]]}
        grounding, support_status, reason = accept_supported_candidate(validation, review)
        if attempt == 1 and support_status in {"unsupported", "unknown"} and grounding.rejected_sentences:
            sources_by_id = {sentence.sentence_id: sentence.source_ids
                             for sentence in validation.candidate.sentences}
            feedback = [
                {"sentence_id": rejected.sentence_id, "reason": rejected.reason,
                 "permitted_citation_ids": list(sources_by_id.get(rejected.sentence_id, ())) }
                for rejected in grounding.rejected_sentences
            ]
            if feedback and retry_timeout() > 0:
                rejection_feedback = feedback
                continue

        return result(
            _ACCEPTANCE_NAME[support_status],
            grounding=grounding,
            **generation_fields(),
            **support_fields(),
            elapsed_ms=_milliseconds(started, clock),
            reason=reason,
        )


def narration_fields(outcome: NarrationOutcome) -> dict[str, Any]:
    """Total projection of the outcome onto the frozen flat `Picture` fields."""
    generation_status, support_status, acceptance_status = _WIRE_STATUS[outcome.name]
    return {
        "narration_generation_status": generation_status,
        "narration_input_record_ids": list(outcome.selected_record_ids) if generation_status == "generated" else [],
        "narration_support_status": support_status,
        "narration_acceptance_status": acceptance_status,
        "narration_generation_deployment": llm.FOUNDRY_MODEL if generation_status == "generated" else None,
        "narration_generation_path": outcome.generation_path,
        "narration_generation_model": outcome.generation_model,
        "narration_support_deployment": None,
        "narration_support_model": outcome.support_model,
        "narration_generation_elapsed_ms": outcome.generation_elapsed_ms,
        "narration_generation_input_tokens": outcome.generation_input_tokens,
        "narration_generation_output_tokens": outcome.generation_output_tokens,
        "narration_support_elapsed_ms": outcome.support_elapsed_ms,
        "narration_support_input_tokens": outcome.support_input_tokens,
        "narration_support_output_tokens": outcome.support_output_tokens,
        "narration_elapsed_ms": outcome.elapsed_ms,
        "narration_rejection_reason": outcome.reason,
    }
