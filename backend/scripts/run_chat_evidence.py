#!/usr/bin/env python
"""Measure the conversational surface's deterministic properties so a review can judge them, not take them on trust.

The oracle's fourth-loop review marked three claims about the chat "unsupported", and it was right for a reason
worth fixing: the properties were asserted in the test suite, and the evidence pack carried none of them. A
reviewer reading the pack could not tell a seatbelt from a claim.

This writes an artifact of the properties themselves, measured by exercising the composition directly. It is
**offline by construction**: no model is configured, so every turn here is `assistant.compose_turn` over an empty
`AssistantResult` -- the engine arm (`chat.engine_parts`) alone, which is the point of the first measurement: a
surface that needs no model to answer or refuse. The model arm (the tool loop, the per-sentence verifier) needs a
model and is covered by `tests/test_assistant.py`, not here.

    .venv/bin/python scripts/run_chat_evidence.py
    .venv/bin/python scripts/run_chat_evidence.py --check
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

# Scripts are evidence producers too, so this goes through the encounter seam rather than calling `cut`
# directly: ADR-0012's equivalence test refuses a raw cut outside `policy.py`, and it is right to.
from app import assistant, chat, fixtures, guidance  # noqa: E402
from app.decide import decide  # noqa: E402
from app.encounters import resolve_encounter  # noqa: E402
from tests.fixtures.bound_encounters import ENCOUNTERS  # noqa: E402
from app.pipeline import PREPARATION_WINDOW_S, build_picture_for_encounter  # noqa: E402
from app.policy import cut_for_encounter  # noqa: E402

RESULTS = ROOT / ".scratch" / "retrieval-and-knowledge" / "results"
TARGET = RESULTS / "chat-surface-properties-v1.json"

ENCOUNTER = "bound-encounter-002-laakitys-sairaanhoitaja"
OTHER_ENCOUNTER = "bound-encounter-003-mielenterveys-sairaanhoitaja"
# Questions the engine arm answers from its own output (the risk finding and the steps), and ones it cannot: with no
# model and no step to offer, the second set is what the refusal must look like.
ANSWERABLE = ("mikä on riski", "mitkä ovat seuraavat toimenpiteet", "mitä lääkityksestä tiedetään")
UNANSWERABLE = ("kerro säästä", "kuka voitti jalkapallon", "onko hänellä syöpä", "mitä kuuluu")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _scoped(encounter_id: str = ENCOUNTER):
    """The permitted set as the application builds it: resolved from an encounter, then cut."""
    personas = {persona.persona_id: persona for persona in fixtures.load_personas()}
    # `None` selects the bound encounter catalog; a bound encounter is what the app resolves.
    encounter, persona = resolve_encounter(encounter_id, personas, ENCOUNTERS)
    return cut_for_encounter(persona, encounter), persona


def _picture(encounter_id: str = ENCOUNTER):
    """The **built picture** the assistant and guidance surfaces consume (`CONTRACT` rule 7).

    This builds the same picture the API's `/picture` route builds; `foundry_enabled=False` skips the briefing's
    narration, which this harness does not consume (its generator is deliberately unconfigured), while the record
    selection is the one the surfaces read.
    """
    started_at = time.monotonic()
    return asyncio.run(build_picture_for_encounter(
        encounter_id,
        encounters=ENCOUNTERS,
        started_at=started_at,
        preparation_deadline=started_at + PREPARATION_WINDOW_S,
        foundry_enabled=False,
    ))


def _turn(question: str, picture, decision, digest, *, seq: int, turn_id: str) -> chat.Turn:
    """One turn with no model: `compose_turn` over an empty result is the engine arm and nothing else."""
    return assistant.compose_turn(question, picture, decision, assistant.AssistantResult(), digest=digest, seq=seq,
                                  turn_id=turn_id)


def run() -> dict:
    scoped, persona = _scoped()
    picture = _picture()
    # The picture's own records, in the picture's order: what the engine arm and the model may cite.
    selected = chat.picture_records(picture)
    decision = decide(scoped)
    digest = chat.seal_digest(picture, policy_version="chat-evidence-v1", persona_id=persona.persona_id)
    # The refusal is measured on a picture with nothing to offer: the engine's steps are published whatever the
    # question asks, so a refusal is possible only when the picture carries none.
    stepless = picture.model_copy(update={"actions": []})

    # 1. The engine arm: answers and refuses with no model configured, so no model call is made.
    answers, refusals = [], []
    for index, question in enumerate(ANSWERABLE):
        turn = _turn(question, picture, decision, digest, seq=index + 1, turn_id=f"a{index}")
        classes = sorted({item.statement_class for item in turn.suggestions})
        citations = sorted({rid for item in turn.suggestions for rid in item.source_record_ids})
        numbers = [{"text": item.text[:80],
                    "traceable": chat._digits_traceable(  # noqa: SLF001 - the guard is the property
                        item.text, [r for r in selected if r.record_id in item.source_record_ids])}
                   for item in turn.suggestions if re.search(r"\d", item.text)]
        answers.append({"question": question, "outcome": turn.outcome, "generator": turn.generator,
                        "generator_state": turn.generator_state,
                        "statement_classes": classes, "suggestion_count": len(turn.suggestions),
                        "cited_record_ids": citations,
                        "all_citations_within_scope": set(citations) <= set(scoped.ids),
                        "every_statement_has_a_class": all(item.statement_class for item in turn.suggestions),
                        "every_step_carries_the_tables_status": all(
                            item.basis_note and item.basis_note.endswith(chat.ACTIONS_BASIS_NOTE)
                            for item in turn.suggestions if chat._is_step(item)),  # noqa: SLF001
                        "numbers": numbers,
                        "every_number_traceable": all(item["traceable"] for item in numbers),
                        "uncited_statements": [{"statement_class": item.statement_class, "text": item.text}
                                               for item in turn.suggestions
                                               if not item.source_record_ids]})
    for index, question in enumerate(UNANSWERABLE):
        turn = _turn(question, stepless, decision, digest, seq=len(ANSWERABLE) + index + 1, turn_id=f"r{index}")
        refusals.append({"question": question, "outcome": turn.outcome, "abstention": turn.abstention,
                         "suggestion_count": len(turn.suggestions)})

    # 2. The refusal is one string. A refusal that varied with what the hidden set holds would be a
    #    differential oracle over it, so identity is the property, not similarity.
    distinct = sorted({item["abstention"] for item in refusals})
    # 3. The digest binds the thread to a subject. A digest that no longer matches must not be silently
    #    accepted, so `digest_is_current` is measured against a changed record set.
    other_picture = _picture(OTHER_ENCOUNTER)
    uncited = [{"question": turn["question"], "statement_class": statement["statement_class"],
                "text": statement["text"]}
               for turn in answers for statement in turn.get("uncited_statements", [])]
    # A role or purpose mutation must also break the digest, not only a different record set: the digest
    # binds the professional and the reason for the encounter, not just the records.
    mutated = []
    for other_encounter in (OTHER_ENCOUNTER,):
        other_scoped, _person = _scoped(other_encounter)
        mutated.append({"encounter_id": other_encounter,
                        "permitted_set_differs": set(other_scoped.ids) != set(scoped.ids),
                        "digest_accepted": chat.digest_is_current(
                            digest, other_picture, policy_version="chat-evidence-v1",
                            persona_id=persona.persona_id)})
    # 4. Offered questions and guideline links, each justified by a row the picture renders.
    prompts = assistant.opening_prompts(picture)
    links = guidance.links_for(picture)
    rendered = {record.record_id for record in selected}
    # 4b. The guideline statement, measured where it can appear: beside a verified sentence that cites the visible
    # record a recorded guideline is attached to. No model is configured here, so the sentence is a stand-in
    # `VerifiedSentence` citing that record (its text is a label, not a claim); what is measured is the citation's
    # own shape -- recorded metadata, the deep link, no clinical content -- and that the engine arm alone cites none.
    guidance_turns = []
    for index, link in enumerate(links):
        supporting = [record_id for record_id in link["evidence_record_ids"] if record_id in rendered]
        if not supporting:
            continue
        said = assistant.AssistantResult(
            sentences=[assistant.VerifiedSentence(text="(stand-in for a verified sentence)", source_ids=(supporting[0],))],
            generator_called=True, status="answered")
        turn = assistant.compose_turn("mitä lääkityksestä tiedetään", picture, decision, said, digest=digest,
                                      seq=100 + index, turn_id=f"g{index}")
        rows = [item for item in turn.suggestions if item.statement_class == chat.STATEMENT_EXTERNAL_EVIDENCE]
        for row in rows:
            guidance_turns.append({
                "beside_record_id": supporting[0], "text": row.text, "url": row.url,
                "label": row.label, "source_record_ids": row.source_record_ids,
                "cites_only_permitted_records": set(row.source_record_ids) <= set(scoped.ids),
                "figures_traceable": chat._digits_traceable(  # noqa: SLF001 - the guard is the property
                    row.text, [r for r in selected if r.record_id in row.source_record_ids]),
                "is_a_recorded_guideline": any(item.url == row.url for item in guidance.GUIDELINES),
                "states_no_clinical_content": not any(
                    phrase in row.text for phrase in ("suositellaan", "tulee aloittaa", "hoitotavoite")),
            })
    engine_only_cites_no_guideline = all(
        item.statement_class != chat.STATEMENT_EXTERNAL_EVIDENCE
        for index, question in enumerate(ANSWERABLE)
        for item in _turn(question, picture, decision, digest, seq=200 + index, turn_id=f"e{index}").suggestions)
    return {
        "kind": "chat-surface-properties", "generated_by": "backend/scripts/run_chat_evidence.py",
        "generated_at": _now(),
        "scope": {"encounter_id": ENCOUNTER, "subject": persona.label,
                  "permitted_record_ids": sorted(scoped.ids),
                  # The record set the surfaces actually read (`CONTRACT` rule 7). It is recorded here so a
                  # divergence between the permitted and the selected set is visible in the artifact.
                  "selected_record_ids": sorted(record.record_id for record in selected),
                  "assessed_cap": decision.get("assessed_cap")},
        "deterministic_arm_needs_no_model": {
            "generator": None,
            "every_turn_produced_an_outcome": all(item["outcome"] for item in answers + refusals),
            "outcomes": {"answers": [item["outcome"] for item in answers],
                         "refusals": [item["outcome"] for item in refusals]},
            "note": ("Every turn is `assistant.compose_turn` over an empty `AssistantResult`, which is what an "
                     "unconfigured model supplies, so these turns are the engine arm with no model call: the "
                     "framing lead, the risk and situation findings, and the actions table's steps."),
        },
        "statements_carry_class_and_evidence": answers,
        "guideline_statements": {
            "count": len(guidance_turns),
            "statements": guidance_turns,
            "engine_arm_alone_cites_no_guideline": engine_only_cites_no_guideline,
            "note": ("Käypä hoito is cited in a turn only beside a verified sentence that cites the visible "
                     "record a recorded guideline is attached to, and only then. The statement carries the "
                     "guideline's recorded title, publication date and deep link, and no clinical content. No "
                     "model is configured here, so the supporting sentence is a stand-in `VerifiedSentence`; the "
                     "licence boundary (`docs/references.md` §6) is pinned by "
                     "`tests/test_chat.py::test_no_guideline_metadata_reaches_the_model_payload` rather than "
                     "measured here, because measuring it would require the model path."),
        },
        "statements_that_cite_nothing": {
            "count": len(uncited),
            "statements": uncited,
            # By class, because the two shapes are different claims: a `rule_output` is the engine's own
            # output and cannot name a record, while an `answer_lead` is the framing sentence and must not
            # pretend to be one. A class that is not in `chat.EVIDENCE_CLASSES` is where an uncited statement
            # is allowed to be, and only there.
            "by_class": {klass: sum(1 for item in uncited if item["statement_class"] == klass)
                         for klass in sorted({item["statement_class"] for item in uncited})},
            "classes_allowed_to_cite_nothing": [chat.STATEMENT_ANSWER_LEAD, chat.STATEMENT_RULE_OUTPUT],
            "note": ("Known gap, recorded rather than papered over, and reported by class. A `rule_output` "
                     "is the deterministic engine's own output and the engine does not expose which visible "
                     "records triggered a given rule, so these statements carry no record ids; the statement "
                     "class and its label are what tell the reader it is the engine speaking rather than a "
                     "source fact. An `answer_lead` is the framing sentence, which cites nothing **by class** "
                     "and is deliberately not an evidence class: it names the question and states what "
                     "follows, and presenting framing as evidence is the failure that boundary exists to "
                     "prevent. The fix for the rule_output gap belongs in the engine -- it should surface the "
                     "record ids that fired each rule -- not in the surface, because attaching a plausible "
                     "record here would be a fabricated citation."),
        },
        "digest_rejects_a_role_or_purpose_mutation": mutated,
        "topics_with_no_guideline": guidance.topics_without_guidance(),
        "refusal_is_one_string": {
            "distinct_abstentions": distinct, "distinct_count": len(distinct),
            "questions_tested": len(refusals),
            "byte_identical": len(distinct) == 1,
            "no_refusal_offered_a_statement": all(item["suggestion_count"] == 0 for item in refusals),
        },
        "digest_binds_the_thread": {
            "sealed_digest_id": digest.digest_id,
            "members": len(digest.members),
            "current_for_the_same_subject": chat.digest_is_current(
                digest, picture, policy_version="chat-evidence-v1", persona_id=persona.persona_id),
            "current_for_a_different_permitted_set": chat.digest_is_current(
                digest, other_picture, policy_version="chat-evidence-v1", persona_id=persona.persona_id),
            "current_under_a_different_policy_version": chat.digest_is_current(
                digest, picture, policy_version="chat-evidence-v2", persona_id=persona.persona_id),
        },
        "opening_prompts": {
            "count": len(prompts),
            "limit": assistant.MAX_OPENING_PROMPTS,
            "prompts": [{"text": item["text"], "why": item["why"],
                         "evidence_record_ids": item["evidence_record_ids"]} for item in prompts],
            "every_prompt_cites_only_rendered_records": all(
                set(item["evidence_record_ids"]) <= rendered for item in prompts),
            "no_prompt_repeats": len({item["text"] for item in prompts}) == len(prompts),
            "note": ("`assistant.opening_prompts` reads the attention summary the day view already shows, which "
                     "is computed over the picture's rendered rows only, so a prompt implied by a withheld or "
                     "permitted-but-unselected record cannot appear."),
        },
        "guideline_links_are_visible_state_only": {
            "count": len(links),
            "links": [{"code": item["code"], "title": item["title"], "citation": item["citation"],
                       "evidence_record_ids": item["evidence_record_ids"]} for item in links],
            "every_link_cites_only_permitted_records": all(
                set(item["evidence_record_ids"]) <= set(scoped.ids) for item in links),
            "no_link_reproduces_content": all("sisältöä ei ole kopioitu" in item["note"] for item in links),
        },
        "limits": [
            "This measures the engine arm. The assistant's tool loop and per-sentence verifier need a model, so "
            "their acceptance is covered by `tests/test_assistant.py` rather than here.",
            "The HTTP layer (401 without a principal, 409 on a stale digest) is covered by the API tests and is "
            "not exercised by this script.",
        ],
    }


def check(document: dict) -> int:
    problems: list[str] = []
    if not document["deterministic_arm_needs_no_model"]["every_turn_produced_an_outcome"]:
        problems.append("a turn produced no outcome with the generator absent")
    for answer in document["statements_carry_class_and_evidence"]:
        for statement in answer.get("uncited_statements", []):
            # The class boundary, checked rather than trusted: only a class that is *not* an evidence class
            # may cite nothing. An `answer_lead` is the framing sentence and a `rule_output` is the engine's
            # own output; a `source_fact`, an `external_evidence` or a `model_summary` without a citation is
            # a statement presented as evidence with nothing behind it, which is the failure this whole
            # document exists to measure.
            if statement["statement_class"] not in chat.EVIDENCE_CLASSES:
                continue
            problems.append(f"{answer['question']}: a {statement['statement_class']} cites no record, "
                            f"which a source fact, an external source or a model summary must never do")
        if not answer["every_statement_has_a_class"]:
            problems.append(f"{answer['question']}: a statement carried no class")
        if not answer["all_citations_within_scope"]:
            problems.append(f"{answer['question']}: a citation fell outside the permitted set")
        if not answer["every_number_traceable"]:
            problems.append(f"{answer['question']}: a statement carried a number its evidence lacks")
        if not answer["every_step_carries_the_tables_status"]:
            problems.append(f"{answer['question']}: a step was published without the actions table's status")
        if answer["generator_state"] != chat.GENERATOR_NOT_CONFIGURED:
            problems.append(f"{answer['question']}: a model-free turn reported generator_state "
                            f"{answer['generator_state']!r}")
    if not any(item["outcome"] == chat.OUTCOME_ANSWER for item in document["statements_carry_class_and_evidence"]):
        problems.append("no answerable question was answered by the engine arm, so it is not demonstrated")
    refusal = document["refusal_is_one_string"]
    if not refusal["byte_identical"]:
        problems.append(f"the refusal is not one string: {refusal['distinct_abstentions']}")
    if not refusal["no_refusal_offered_a_statement"]:
        problems.append("a refusal offered a statement")
    digest = document["digest_binds_the_thread"]
    if not digest["current_for_the_same_subject"]:
        problems.append("the digest does not match the subject it was sealed from")
    if digest["current_for_a_different_permitted_set"]:
        problems.append("the digest is accepted for a different permitted set")
    if digest["current_under_a_different_policy_version"]:
        problems.append("the digest is accepted under a different policy version")
    if not document.get("topics_with_no_guideline"):
        problems.append("no topics are recorded as having no guideline, so the gap is not stated")
    guidance_block = document.get("guideline_statements", {})
    guidance_rows = guidance_block.get("statements", [])
    if not guidance_rows:
        problems.append("no turn cited a recorded guideline, so the guideline citation is not demonstrated")
    if not guidance_block.get("engine_arm_alone_cites_no_guideline"):
        problems.append("the engine arm cited a guideline with no verified sentence beside it")
    for row in guidance_rows:
        if not row["cites_only_permitted_records"]:
            problems.append(f"{row['beside_record_id']}: a guideline statement cited a record outside the permitted set")
        if not row["is_a_recorded_guideline"]:
            problems.append(f"{row['beside_record_id']}: a guideline statement carried a URL that is not recorded")
        if not row["figures_traceable"]:
            problems.append(f"{row['beside_record_id']}: a guideline statement carried a figure its evidence lacks")
        if not row["states_no_clinical_content"]:
            problems.append(f"{row['beside_record_id']}: a guideline statement reproduced guideline content")
    for item in document.get("digest_rejects_a_role_or_purpose_mutation", []):
        if item.get("digest_accepted"):
            problems.append(f"{item['encounter_id']}: the digest was accepted for a different encounter")
    prompts = document["opening_prompts"]
    if not prompts["every_prompt_cites_only_rendered_records"]:
        problems.append("opening_prompts: a prompt was derived from outside the rendered picture")
    if not prompts["no_prompt_repeats"] or prompts["count"] > prompts["limit"]:
        problems.append("opening_prompts: a prompt repeats or the cap was exceeded")
    if not document["guideline_links_are_visible_state_only"]["every_link_cites_only_permitted_records"]:
        problems.append("guideline_links_are_visible_state_only: a link was derived from outside the permitted set")
    answers = document["statements_carry_class_and_evidence"]
    print(f"check: {len(answers)} answerable question(s) answered and {refusal['questions_tested']} "
          f"unanswerable one(s) refused, all with {refusal['distinct_count']} distinct refusal string")
    print(f"check: outcome is {answers[0]['outcome'] if answers else '-'} with generator=None; "
          f"digest matches its subject only; "
          f"{document['statements_that_cite_nothing']['count']} statement(s) cite no record "
          f"{document['statements_that_cite_nothing'].get('by_class', {})} "
          f"(non-evidence classes only), "
          f"{len(guidance_rows)} guideline statement(s) cite a recorded suositus, "
          f"{prompts['count']} opening prompt(s), "
          f"{len(document['topics_with_no_guideline'])} topic(s) have no guideline")
    for problem in problems:
        print(f"check: {problem}", file=sys.stderr)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure the conversational surface's properties")
    parser.add_argument("--check", action="store_true", help="verify the committed artifact")
    args = parser.parse_args(argv)
    if args.check:
        if not TARGET.exists():
            print(f"check: no artifact at {TARGET}", file=sys.stderr)
            return 1
        try:
            document = json.loads(TARGET.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"check: {TARGET.name} unreadable: {type(exc).__name__}", file=sys.stderr)
            return 1
        return check(document)
    document = run()
    RESULTS.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return check(document)


if __name__ == "__main__":
    raise SystemExit(main())
