# ADR-0027 — The tool-using assistant supersedes the deterministic arm

Status: **accepted by the owner, 2026-10-13.** Supersedes [ADR-0019](0019-the-conversational-surface-explains.md)'s
first decision (*the deterministic arm is the surface, and it makes no model call*) and
[ADR-0022](0022-no-new-foundry-capability-before-the-event.md)'s refusal of function calling on the runtime
path. Every other invariant those two records state survives, and each is named below with the test that pins
it. The surface still explains; it does not author clinical judgement (ADR-0019, Decision 2 and 3, unchanged).

## Context

ADR-0019 shipped the conversational surface as a keyword-routed **deterministic arm**: a question was matched
by four- and five-character stems against record payloads, and a question whose words it did not recognise as
a "topic" was refused. The generator was an addition to that arm, restating what the arm had already selected.
In use, the arm refused the questions a professional actually asks — including the one whose whole purpose is
the engine's next steps — because the answer to "what should I do" cites nothing by design, and the refusal
could not tell an unknown word from an absent fact.

ADR-0022 rejected function calling because *"a tool that can choose what to fetch is a permission decision by
another name."* That is true of a tool over an open store. It is not true of a tool closed over a set the
permission cut has already produced: such a tool cannot choose anything the cut did not admit, because there
is no wider set to hand it. The objection was to model latitude over permission, and that latitude does not
exist when the tools are built from the cached `Picture`.

## Decision

**1. The assistant (`backend/app/assistant.py`) is the conversational surface, and it is a tool-using agent
over one cached, permitted picture.** `build_tools` closes six tools — `list_records`, `get_record`,
`get_esitiedot`, `get_prefilled_forms`, `get_engine_assessment`, `search_knowledge` — over the built `Picture`
and the engine's decision. The model decides what to read; `ask` runs the bounded tool loop, then sends every
sentence it wrote through the per-sentence verifier narration and the pre-fill already use; `compose_turn` assembles
the turn from the verified sentences, the engine's own findings and steps, and the model's proposed steps. The
browser posts to `POST /api/encounters/{id}/chat/stream`. The keyword routing (`chat.answer`, the JSON
`POST …/chat` route) is deleted in the same change as this record; what survives of `chat.py` is the engine's
own lead, findings and steps (`chat.engine_parts`), the pinned record-sentence renderers (`get_record` returns
`chat._model_payload`, not these), and the verified-replay helpers. `search_knowledge` reads through one retrieval seam, `knowledge_search.search`: the Azure SQL `VECTOR(1536)` index (`knowledge_chunks`, loaded by `load_knowledge` as the loader identity) when an embedding deployment is configured, else the committed snapshots' token overlap (`knowledge_forms.candidates`, widened by the Käypä hoito topics the query names); either way the model receives refs resolved against the committed snapshots — a form question, a guideline citation (code, title, URL; never content), a pathway step — and never a stored row's text.

**2. The transport is the Responses API.** `llm._complete_tool_loop` calls `client.responses.create` because the
configured deployment, `gpt-6.1-sol`, refuses function tools on chat completions (*"Function tools with
reasoning_effort are not supported ... use /v1/responses"*). Tool definitions are kept in the chat-completions
shape and translated at the transport, so the assistant's definition stays one. The loop is the second enforced
egress boundary: `source.assert_model_safe` runs on every outbound body, the first and every appended tool result.

**3. The invariants that survive, each with its test:**

| Invariant | Where it holds | Pinned by |
| --- | --- | --- |
| Permission before any model. The route takes `Scoped` from `cut_for_encounter`, the one seam over `policy.cut`; the assistant never builds a scope. | `main.encounter_chat_stream`, `policy.cut` | `test_challenge_guardrails.py::test_encounter_policy_cut_is_owned_by_one_seam`, `::test_every_narration_path_is_downstream_of_policy_cut` (names `assistant.py` as a verifier caller held to the same rule) |
| No tool reaches outside the cached picture. Every handler is closed over `chat.picture_records(picture)`; an id outside it answers `no such record in this briefing`; every tool result is model-safe. | `assistant.build_tools`, `chat.picture_records` | `test_assistant.py::test_the_tools_reach_exactly_the_picture_and_project_for_the_model` |
| Verification before display. A sentence that cites nothing, or cites outside the picture, is dropped before the verifier; the rest pass `grounding.validate_fresh_candidate` and `decision_model.verify_narration`, and only a `supported` verdict with traceable figures is published. A verifier failure publishes nothing. | `assistant.ask` | `test_assistant.py::test_only_verified_sentences_and_traceable_steps_are_published`; `test_model_egress_boundary.py::test_decision_verifier_sends_no_identity_value_over_the_wire` |
| One cause-neutral refusal. When nothing verified and no step exists, the turn is `chat.ABSTENTION`, one byte-identical sentence. | `assistant.compose_turn` | `test_assistant.py::test_without_a_model_the_turn_is_the_engines_own_or_the_one_refusal`; `test_chat.py::test_the_abstention_is_one_byte_identical_cause_neutral_string` |
| The stream never carries raw model text. Events are `progress`, `tool` (the tool's name only), `sentence` (a verified sentence, emitted as the verifier confirms it), and one final `turn`; `error` replaces `turn` when the store refuses. | `main.encounter_chat_stream` | `test_assistant.py::test_the_stream_route_emits_tools_then_verified_sentences_then_one_stored_turn` |
| Every model call sits inside an enforced egress boundary. | `llm._complete_tool_loop` | `test_model_egress_boundary.py::test_every_model_transport_call_site_is_inside_an_enforced_boundary_function` |
| The thread is bound to the sealed digest; a stale digest is `409 encounter_changed` before the stream opens; an anonymous request is `401`. | `main.encounter_chat_stream` | `test_assistant.py::test_the_stream_route_emits_tools_then_verified_sentences_then_one_stored_turn`; `test_chat.py::test_a_stale_digest_seals_the_thread_rather_than_re_pointing_it` |

**4. The turn is stored, and a stored turn replays without a model call.** `_store_turn` is shared with the
JSON chat route: the audit row keeps ids, classes, labels, evidence ids, the tool names called, the generator
state and the digest — never the text of a `model_summary` sentence. A retry under a stored turn id replays the
stored turn (`progress: replaying`), rebuilt from its record and re-verified where it carries advice
(`chat.replay_advice`), and the model is not called. The **conversation history is client-held**: `AssistantAsk.history`
is what the client displayed for earlier turns, bounded to `MAX_HISTORY_TURNS`, used as model context only,
never stored and never treated as evidence.

**5. A model step is a proposal, not a verified sentence.** `steps` are kept when every cited record is in the
picture and every figure is traceable to those records (a figure with no record behind it is dropped); they
are labelled `Tekoälyn ehdotus, tarkistettavaksi` with the basis note that the professional decides. They are
not sent to Decision-1. The engine's own steps (`rule_output`) remain the engine's, as ADR-0019 Decision 3 says.

## Consequences

- ADR-0019's Decision 1 is superseded: the surface now makes a model call when one is configured. With none
  configured, the turn is still the engine's own findings and steps, or the one refusal — that fallback stands.
- ADR-0022's row *Function calling / tools* is superseded for this surface only. Its reasoning holds for tools
  over an open store; the tools here are closed over the cut's output. The other rejections in that table stand,
  and Decision-1 stays a typed call ([ADR-0021](0021-decision-1-stays-a-typed-call.md)).
- The JSON route `POST /api/encounters/{id}/chat`, `chat.answer` and the keyword fact lookup are gone; the opening
  prompts (`GET …/chat/prompts`) are derived from the briefing's attention summary (`assistant.opening_prompts`),
  which reads rendered rows only, so nothing withheld can leak into a suggested question.
- The transparency notice (`answer_notice`) is on every `turn` event, so Art. 50(1) is honoured as ADR-0019 said.
- The model reads tool results and writes sentences; latent inference across records survives per-sentence
  verification exactly as ADR-0019 recorded. This ADR does not claim otherwise.
