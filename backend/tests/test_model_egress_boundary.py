"""The negative and enumeration tests for work order L2B.

`test_model_projection.py` pins `source.basics_payload(record, "model")` itself. This file drives every
real model-calling path with a record shaped like the loaded corpus and carrying an identity value, and
asserts the identity value reaches no outbound request body -- the check the adversarial audit found
missing. It also enumerates the textual call sites that can send a body to a model, so a sixth path added
without the transport-boundary guard fails a test rather than waiting for another audit.
"""

from __future__ import annotations

import ast
import asyncio
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import cast

import httpx

from app import decision_model, llm, source
from app.picture import GroundingOutcome, Picture, Records, RecordView, Risk, Situation
from app.policy import Scoped
from app.upm import ConsentScope, Persona, Record

from test_model_projection import CORPUS_KEYS, INJECTED_IDENTITY, ROW_SHAPE  # noqa: E402

APP_DIR = Path(__file__).resolve().parents[1] / "app"
RECORDED = datetime(2024, 6, 23, 12, 29, tzinfo=timezone.utc)
TODAY = date(2026, 9, 30)


def _corpus_shaped_record(record_type: str) -> Record:
    """One record of `record_type`, shaped like the loaded 2024-2026 corpus and carrying every identity value."""
    topic, role, scope = ROW_SHAPE[record_type]
    payload = {**dict.fromkeys(CORPUS_KEYS[record_type], "substance-value"), **INJECTED_IDENTITY}
    return Record(record_id=f"corpus-{record_type}", source="delivery", record_type=record_type,
                  authored_by_role=role, recorded_at=RECORDED, topic=topic, sensitivity_tag="none",
                  consent_scope=cast(ConsentScope, scope), payload=payload)


CORPUS_RECORDS = [_corpus_shaped_record(record_type) for record_type in sorted(CORPUS_KEYS)]


def _picture(records: list[Record]) -> Picture:
    """A picture carrying exactly these records -- built directly, bypassing `policy.cut`'s topic filter, so
    every record type in the loaded corpus's own vocabulary is exercised regardless of purpose/topic rules
    that are not this test's concern."""
    views = [RecordView.from_record(record) for record in records]
    return Picture(role="sairaanhoitaja", purpose="medication_follow_up", encounter_id="corpus-enc",
                   situation=Situation(situation_type="", confidence=""),
                   risk=Risk(risk_level="", reason="", basis=None),
                   actions=[], conflicts=[], narrative=None, citations=[],
                   records=Records(priority=views, accessible_other=[]),
                   trace=[], grounded=False,
                   grounding=GroundingOutcome(dropped_citations=[], dropped_actions=[]))


def _scoped(records: list[Record]) -> Scoped:
    persona = Persona(persona_id="corpus-synthetic", label="Corpus-shaped", records=records)
    return Scoped(persona=persona, role="sairaanhoitaja", purpose="medication_follow_up")


def _assert_clean(body: str, *, where: str) -> None:
    for value in INJECTED_IDENTITY.values():
        assert value not in body, f"identity value {value!r} reached the model via {where}"
    for key in source.IDENTITY_KEYS:
        assert f'"{key}"' not in body, f"identity key {key!r} reached the model via {where}"


# --- path 2: narration generator + agent fallback ------------------------------------------------------

def test_narration_generator_sends_no_identity_value(monkeypatch):
    captured = []

    async def complete(schema, instructions, user, timeout_s):
        captured.append(user)
        sentence = llm.NarrationSentence(sentence_id="s1", text="Lausunto.",
                                         source_ids=[CORPUS_RECORDS[0].record_id])
        return llm.FoundryResult(output=llm.FreshNarration(sentences=[sentence]), model="stub-model")

    monkeypatch.setattr(llm, "_complete_bounded", complete)
    asyncio.run(llm.generate_fresh_narration(_scoped(CORPUS_RECORDS), TODAY, timeout_s=8.0))
    assert captured, "generate_fresh_narration made no model call -- this test proves nothing"
    _assert_clean(captured[0], where="llm.generate_fresh_narration (path 2)")


# --- paths 1 and 3: decision relevance and the per-sentence verifier, over the real transport ----------

def _decision_response(model=decision_model.MODEL) -> bytes:
    def choice(value, labels):
        return {"type": "choice", "choice": value, "confidence": 0.8,
                "probabilities": {label: (0.8 if label == value else 0.2 / (len(labels) - 1)) for label in labels}}
    return json.dumps({"model": model, "answers": {
        "relevance": choice("relevant", ("relevant", "irrelevant", "unclear")),
        "time_sensitivity": choice("current", ("current", "historical", "unclear")),
        "subject_binding": choice("person", ("person", "other_person", "unclear")),
        "urgency": {"type": "score", "score": 2.0, "confidence": 0.8,
                    "probabilities": {"0": 0.0, "1": 0.0, "2": 1.0, "3": 0.0, "4": 0.0},
                    "legend": {str(i): level for i, level in enumerate(decision_model.URGENCY_LEVELS)}},
    }}).encode()


def test_decision_relevance_sends_no_identity_value_over_the_wire(monkeypatch):
    seen_bodies = []

    def handler(request):
        seen_bodies.append(request.content.decode())
        return httpx.Response(200, content=_decision_response())

    real = httpx.AsyncClient
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    monkeypatch.setattr(decision_model.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    scoped = _scoped(CORPUS_RECORDS)
    asyncio.run(decision_model.classify_notes(scoped, set(), TODAY))
    assert seen_bodies, "classify_notes made no model call -- this test proves nothing"
    for body in seen_bodies:
        _assert_clean(body, where="decision_model.classify_notes (path 1)")


def test_decision_verifier_sends_no_identity_value_over_the_wire(monkeypatch):
    seen_bodies = []

    def handler(request):
        seen_bodies.append(request.content.decode())
        return httpx.Response(200, content=_decision_response())

    real = httpx.AsyncClient
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "fake")
    monkeypatch.setattr(decision_model.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    scoped = _scoped(CORPUS_RECORDS)
    candidate = type("Candidate", (), {"sentences": [
        type("Sentence", (), {"sentence_id": "s1", "text": "Lausunto.",
                             "source_ids": [record.record_id for record in CORPUS_RECORDS]})()
    ]})()
    asyncio.run(decision_model.verify_narration(scoped, candidate, TODAY, timeout_s=8.0))
    assert seen_bodies, "verify_narration made no model call -- this test proves nothing"
    for body in seen_bodies:
        _assert_clean(body, where="decision_model.verify_narration (path 3)")


# --- the enumeration test -------------------------------------------------------------------------------

# Functions in which a model-transport call is enforced by `source.assert_model_safe` (work order L2B,
# change 2). A new call site is safe only if it is added inside one of these, or a new function is added
# to this set *and* reviewed for the same guard.
ENFORCED_BOUNDARY_FUNCTIONS = {
    ("llm.py", "_complete_bounded"),
    ("llm.py", "_complete_prompt_agent"),
    # The assistant's tool loop: `source.assert_model_safe` runs on the serialized messages before EVERY round,
    # so a tool result that carried an identity key is refused at the wire on the round that would send it.
    ("llm.py", "_complete_tool_loop"),
    ("decision_model.py", "_post"),
    ("decision_providers.py", "call"),
}


def _is_model_transport_call(node: ast.Call) -> bool:
    """A `<...>.chat.completions.create(...)`, `<...>.responses.create(...)` or `<client>.stream("POST", ...)`
    call -- the three shapes this codebase's two real model clients (the OpenAI SDK and the Decision-1
    `httpx` transport) use to put a request on the wire."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr == "create" and isinstance(func.value, ast.Attribute) and func.value.attr in {"completions", "responses"}:
        return True
    return bool(func.attr == "stream" and node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "POST")


class _CallSiteVisitor(ast.NodeVisitor):
    def __init__(self, filename: str):
        self.filename = filename
        self._function_stack: list[str] = []
        self.sites: list[tuple[str, str | None]] = []

    def _visit_function(self, node):
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Call(self, node):
        if _is_model_transport_call(node):
            enclosing = self._function_stack[-1] if self._function_stack else None
            self.sites.append((self.filename, enclosing))
        self.generic_visit(node)


def _model_transport_call_sites() -> set[tuple[str, str | None]]:
    sites: set[tuple[str, str | None]] = set()
    for path in sorted(APP_DIR.glob("*.py")):
        visitor = _CallSiteVisitor(path.name)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        sites.update(visitor.sites)
    return sites


def test_every_model_transport_call_site_is_inside_an_enforced_boundary_function():
    """Change 3's enumeration test.

    What it proves: today, a source scan of every module in `app/` finds exactly the call shapes that put a
    request on the wire (`*.chat.completions.create(...)`, `*.responses.create(...)`, `client.stream("POST",
    ...)`), and every one of them sits inside a function that calls `source.assert_model_safe` before the
    body leaves the process (`ENFORCED_BOUNDARY_FUNCTIONS`).

    What it does NOT prove, stated plainly: this is a static text/AST scan of three known call shapes, not a
    runtime interception of every possible way to reach a network socket. It will catch a fifth or sixth
    path written with one of these three call shapes in a *different* function -- the site then does not
    match `ENFORCED_BOUNDARY_FUNCTIONS` and this test fails, naming the new (file, function). It will NOT
    catch a call to a model built through a transport this scan does not recognise (a bare `requests.post`,
    a raw socket, a different SDK method name), and it will not catch a call site copied verbatim *inside*
    one of the three already-enforced functions that then builds its own second, unguarded body alongside
    the guarded one. The guard itself (`source.assert_model_safe`) is a textual key scan over the outbound
    body, not a semantic check that no identity value travels under a different key -- see its own
    docstring and `source.basics_payload`'s invariant tests for that half.
    """
    sites = _model_transport_call_sites()
    assert sites, "the scan found no model-transport call sites at all -- it is not exercising anything"
    assert sites == ENFORCED_BOUNDARY_FUNCTIONS, (
        "a model-transport call site is not inside an enforced boundary function (or the whitelist is "
        f"stale): found {sites}, expected {ENFORCED_BOUNDARY_FUNCTIONS}")
