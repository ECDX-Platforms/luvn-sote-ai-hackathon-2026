"""The assistant (Avustaja): a tool-using agent over one cached, permitted picture, verified before display.

What replaced what. The chat surface routed questions by four- and five-character stem matching over record
payloads, and refused any question whose words it did not recognise as a "topic". This module gives the
generator the question and a set of TOOLS that reach exactly the picture the briefing shows -- its records,
its pre-filled esitiedot, the engine's own assessment, the release's forms -- and nothing else. The model
decides what to read; it cannot read anything the cut did not admit, because the tools are closed over the
built `Picture` and there is no wider set to hand them.

What did not change. Every factual sentence the model writes cites record ids and is verified by Decision-1
against exactly the records it cites, through the same per-sentence verifier narration and the pre-fill use;
an unverified sentence is dropped, never softened. A proposed step is a proposal (accept / edit / dismiss),
labelled as the model's, allowed only when every record it cites is in the picture and every figure in it is
traceable to those records. The refusal is one cause-neutral sentence, used only when the turn has nothing
verified and no engine step to show. Nothing is written back; nothing withheld is named.

Transport: `llm._complete_tool_loop`, the second enforced egress boundary (`source.assert_model_safe` on every
outbound body). Streaming: the route emits progress and tool events and the VERIFIED sentences as they are
confirmed -- never raw tokens, so unverified text is never on screen.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from . import briefings, chat, decision_model, grounding, llm, source
from .picture import Picture, RecordView

log = logging.getLogger(__name__)

ASSISTANT_BUDGET_S = float(__import__("os").environ.get("LUVN_ASSISTANT_BUDGET", "60"))
MAX_SENTENCES = 4
MAX_STEPS = 3
MAX_FOLLOWUPS = 3
MAX_HISTORY_TURNS = 6
MODEL_STEP_LABEL = "Tekoälyn ehdotus, tarkistettavaksi"
MODEL_STEP_BASIS = ("Tekoälyn ehdottama askel sallittujen kirjausten perusteella; ei hoitomääräys, "
                    "ammattilainen päättää.")

INSTRUCTIONS = """Olet LUVN-avustaja: autat sosiaali- ja terveydenhuollon ammattilaista valmistautumaan yhteen
asiakaskohtaamiseen. Sinulla on työkalut, jotka lukevat juuri tämän kohtaamisen sallitut kirjaukset, esitäytetyt
esitiedot, sääntöpohjaisen arvion ja aineiston omat lomakkeet. Mitään muuta tietoa ei ole: jos työkalut eivät
sano asiaa, sitä ei tiedetä.

Säännöt:
1. Vastaa suomeksi, lyhyesti, ammattilaiselle. Enintään neljä lausetta (sentences), kukin yksi asia.
2. Jokainen lause, joka väittää jotakin asiakkaasta, nimeää source_ids: täsmälleen ne kirjaukset, joista
   asia luettiin. Lause ilman kirjausta ei saa väittää mitään asiakkaasta. Älä keksi lukuja tai päivämääriä:
   ne tulevat kirjauksista sellaisenaan.
3. Älä tee diagnoosia, älä luokittele vakavuutta, älä anna hoitomääräystä. Voit ehdottaa askelia
   (steps): mitä kysyä, mitä tarkistaa, mitä kirjata. Jokainen askel nimeää source_ids, jos se perustuu
   kirjaukseen; askel on ehdotus, jonka ammattilainen päättää.
4. Jos kysymykseen ei löydy vastausta työkaluista, palauta sentences tyhjänä äläkä arvaa.
5. Ehdota lopuksi enintään kolme jatkokysymystä (followups), joihin tämä aineisto voi vastata.
6. Älä koskaan toista henkilön nimeä tai tunnistetta.

Käytä työkaluja: lue ensin get_engine_assessment ja list_records, sitten get_record niille kirjauksille,
joita kysymys koskee; get_prefilled_forms ja get_esitiedot, kun kysymys koskee esitietoja tai lomakkeita;
search_knowledge, kun kysymys koskee lomakkeen sisältöä tai hoitosuositusta. Kun kysymys koskee
hoitosuositusta, vastaa ensin sillä, mitä asiakkaan kirjaukset sanovat suosituksen aiheesta (diagnoosi,
lääkitys, mittaus) ja nimeä ne kirjaukset source_ids-kenttään; hakutuloksen oma viite (guideline:-alkuinen tunnus)
voi olla saman lauseen source_ids-kentässä kirjauksen rinnalla, mutta ei koskaan yksin."""


class AssistantSentence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(min_length=1, max_length=chat.MAX_RESTATEMENT_CHARS)
    source_ids: list[str] = Field(max_length=8)


class AssistantStep(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(min_length=1, max_length=chat.MAX_RESTATEMENT_CHARS)
    source_ids: list[str] = Field(max_length=8)


class AssistantAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sentences: list[AssistantSentence] = Field(max_length=6)
    steps: list[AssistantStep] = Field(max_length=4)
    followups: list[str] = Field(max_length=4)


@dataclass(frozen=True)
class VerifiedSentence:
    """A sentence the verifier supported against `source_ids`; `knowledge_refs` are the resolved knowledge refs
    (`guideline:`, `form:`, `pathway:`) the model cited beside those records. They are carried, not verified: a
    ref names a committed snapshot item, never a record, so verification is of the record part alone."""
    text: str
    source_ids: tuple[str, ...]
    knowledge_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerifiedStep:
    """A proposed step whose records are in the picture and whose figures trace to them; refs as above."""
    text: str
    source_ids: tuple[str, ...]
    knowledge_refs: tuple[str, ...] = ()


@dataclass
class AssistantResult:
    sentences: list[VerifiedSentence] = field(default_factory=list)
    steps: list[VerifiedStep] = field(default_factory=list)
    followups: list[str] = field(default_factory=list)
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    model: str | None = None
    generator_called: bool = False
    proposed_sentences: int = 0
    status: str = "not_configured"  # not_configured | answered | empty | failed | timed_out


# --- tools -----------------------------------------------------------------------------------------------


def _title(view: RecordView) -> str:
    payload = view.payload or {}
    for key in ("otsikko", "lääke", "nimi", "tutkimus", "päätös", "aihe", "instrumentti"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:80]
    for key, label in (("verenpaine", "Verenpaine"), ("lämpö_c", "Lämpö"), ("syke", "Syke"), ("paino_kg", "Paino")):
        if payload.get(key) is not None:
            return f"{label} {payload[key]}"
    return view.record_type


def build_tools(picture: Picture, decision: Mapping[str, Any] | None) -> tuple[list[dict], dict[str, Callable]]:
    """The tool definitions and their handlers, closed over the built picture. Nothing reaches past it."""
    records = chat.picture_records(picture)
    by_id = {view.record_id: view for view in records}

    def list_records(arguments: dict) -> dict:
        wanted = arguments.get("record_type")
        rows = [view for view in records if not wanted or view.record_type == wanted]
        return {"records": [{"record_id": v.record_id, "record_type": v.record_type,
                             "recorded_at": v.recorded_at.date().isoformat(), "temporal_status": v.temporal_status,
                             "title": _title(v)} for v in rows],
                "record_types": sorted({v.record_type for v in records})}

    def get_record(arguments: dict) -> dict:
        record_id = str(arguments.get("record_id") or "")
        view = by_id.get(record_id)
        if view is None:
            return {"error": "no such record in this briefing"}
        return {"record_id": view.record_id, "record_type": view.record_type, "topic": view.topic,
                "recorded_at": view.recorded_at.isoformat(), "temporal_status": view.temporal_status,
                "payload": chat._model_payload(view)}  # noqa: SLF001 -- the one model projection

    def get_esitiedot(arguments: dict) -> dict:
        return {"sections": [{"heading": s.heading, "status": s.status,
                              "items": [{"text": i.text, "source_ids": i.source_record_ids,
                                         "temporal_status": i.temporal_status} for i in s.items]}
                             for s in picture.esitiedot]}

    def get_prefilled_forms(arguments: dict) -> dict:
        out = []
        for form in picture.prefilled_forms:
            answered = [{"question": i.question_text, "answer": i.answer_text, "source_ids": i.source_record_ids,
                         "historical": i.historical} for i in form.items if i.status == "answered"]
            open_questions = [i.question_text for i in form.items if i.status != "answered"]
            out.append({"form_id": form.form_id, "name": form.name, "role": form.form_role,
                        "note": form.relation_note, "generation_status": form.generation_status,
                        "answered": answered, "open_questions": open_questions[:12],
                        "open_count": len(open_questions),
                        "score": form.score.model_dump() if form.score else None})
        return {"forms": out}

    def get_engine_assessment(arguments: dict) -> dict:
        return {"situation": picture.situation.model_dump(), "risk": picture.risk.model_dump(),
                "actions": list(picture.actions),
                "conflicts": [c.model_dump() for c in picture.conflicts],
                "basis": chat.ACTIONS_BASIS_NOTE,
                "assessments": [{"instrument": a.instrumentti, "complete": a.valmis, "total": a.total,
                                 "max_total": a.max_total, "source_ids": a.lahteet} for a in picture.assessments]}

    def search_knowledge(arguments: dict) -> dict:
        # One seam (`knowledge_search.search`, ADR-0027): the SQL VECTOR index when an embedding deployment is
        # configured, else the committed snapshots' token overlap. Either way the model receives refs resolved
        # against the committed snapshots -- a form question, a Käypä hoito citation (code, title, URL; never
        # content), a pathway step -- and never a stored row's text. The encounter's own instruments are the one
        # thing read from the picture here; the rail's `guideline_links` stay on the wire, not in this result.
        from . import knowledge_search

        query = str(arguments.get("query") or "").strip()
        named = [a.instrumentti for a in picture.assessments if a.instrumentti]
        hits = knowledge_search.search(query, limit=6, instruments=named) if query else []
        return {"items": [hit.as_dict() for hit in hits]}

    tools = [
        {"type": "function", "function": {"name": "list_records", "description":
            "Listaa tämän kohtaamisen sallitut kirjaukset (tunniste, tyyppi, päivä, otsikko). Valinnainen record_type-suodatin.",
            "parameters": {"type": "object", "properties": {"record_type": {"type": "string"}}, "required": [],
                           "additionalProperties": False}}},
        {"type": "function", "function": {"name": "get_record", "description":
            "Lue yksi kirjaus kokonaan sen tunnisteella.",
            "parameters": {"type": "object", "properties": {"record_id": {"type": "string"}}, "required": ["record_id"],
                           "additionalProperties": False}}},
        {"type": "function", "function": {"name": "get_esitiedot", "description":
            "Esitäytetyt esitiedot otsikoittain (tulosyy, riskitiedot, lääkehoito, toimintakyky ...), kukin kirjauksiin viitaten.",
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}},
        {"type": "function", "function": {"name": "get_prefilled_forms", "description":
            "Tähän käyntiin kuuluvat viralliset lomakkeet: esitäytetyt (tarkistetut) vastaukset, avoimet kysymykset ja pistelasku.",
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}},
        {"type": "function", "function": {"name": "get_engine_assessment", "description":
            "Sääntöpohjainen arvio: tilanne, riskitaso perusteineen, ehdotetut askeleet, ristiriidat ja pisteytetyt mittarit. Havainnollistava, ei kliinisesti kalibroitu.",
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}},
        {"type": "function", "function": {"name": "search_knowledge", "description":
            "Hae tietämyksestä hakusanalla: aineiston lomakkeiden kysymyksiä, Käypä hoito -suositusten viitteitä (otsikko ja linkki, ei sisältöä) ja hoitopolun askelia. Jokainen osuma kantaa viitteen (ref).",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
                           "additionalProperties": False}}},
    ]
    handlers = {"list_records": list_records, "get_record": get_record, "get_esitiedot": get_esitiedot,
                "get_prefilled_forms": get_prefilled_forms, "get_engine_assessment": get_engine_assessment,
                "search_knowledge": search_knowledge}
    return tools, handlers


def _split_citations(source_ids: Sequence[str], permitted: set[str]) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """`source_ids` as (picture record ids, resolved knowledge refs), or `None` when any id is neither.

    The model's one citation list carries both kinds: the record ids its reading tools returned and the refs its
    `search_knowledge` hits carried (`knowledge_search.KINDS`, `guideline:hoi04010` and the like). Testing the whole
    list against record ids alone dropped every sentence that cited a guideline it had just been handed -- the
    N6 defect: a Käypä hoito question answered by the engine steps and the withheld caption. A ref is kept only
    when the committed snapshots resolve it (`knowledge_search.resolve`), so an invented code is as foreign as a
    record outside the picture; order is preserved and duplicates are dropped.
    """
    from . import knowledge_search

    record_ids: list[str] = []
    refs: list[str] = []
    for source_id in dict.fromkeys(source_ids):
        if source_id in permitted:
            record_ids.append(source_id)
        elif source_id.startswith(knowledge_search.PREFIXES) and knowledge_search.resolve(source_id) is not None:
            refs.append(source_id)
        else:
            return None
    return tuple(record_ids), tuple(refs)


# --- the ask --------------------------------------------------------------------------------------------------


def _history_payload(history: Sequence[Mapping[str, Any]] | None) -> list[dict]:
    """The client's own transcript of this conversation: the questions asked and the sentences it displayed.

    The audit keeps no statement text, so the server cannot replay earlier answers; the client sends what it
    showed, bounded, and it is model context only -- never stored, never trusted as evidence.
    """
    rows = []
    for entry in list(history or [])[-MAX_HISTORY_TURNS:]:
        question = str(entry.get("question") or "")[:chat.MAX_QUESTION_CHARS]
        answers = [str(t)[:chat.MAX_RESTATEMENT_CHARS] for t in (entry.get("answer") or [])][:MAX_SENTENCES]
        if question:
            rows.append({"question": question, "answer": answers})
    return rows


async def ask(question: str, picture: Picture, decision: Mapping[str, Any] | None, *,
              history: Sequence[Mapping[str, Any]] | None = None, timeout_s: float = ASSISTANT_BUDGET_S,
              on_tool: Callable[[str, dict], Awaitable[None]] | None = None,
              on_sentence: Callable[[VerifiedSentence], Awaitable[None]] | None = None) -> AssistantResult:
    """One question against one picture: the tool loop, then verification of every sentence it wrote."""
    result = AssistantResult()
    if not llm.foundry_configured():
        return result
    result.generator_called = True
    tools, handlers = build_tools(picture, decision)
    user = json.dumps({"question": question, "encounter_role": picture.role, "encounter_purpose": picture.purpose,
                       "conversation": _history_payload(history)}, ensure_ascii=False)
    started = time.monotonic()
    try:
        loop = await llm._complete_tool_loop(AssistantAnswer, INSTRUCTIONS, user, tools, handlers,  # noqa: SLF001
                                             timeout_s, on_tool=on_tool)
    except TimeoutError:
        result.status = "timed_out"
        return result
    except Exception as exc:  # noqa: BLE001 -- a failed generator is a turn without it
        log.warning("assistant: generator failed: %s", type(exc).__name__)
        result.status = "failed"
        return result
    result.model = loop.model
    result.tool_calls = list(loop.tool_calls)
    answer = loop.output
    result.proposed_sentences = len(answer.sentences)
    result.followups = [f.strip()[:200] for f in answer.followups if f.strip()][:MAX_FOLLOWUPS]
    evidence = chat._PictureEvidence(chat.picture_records(picture))  # noqa: SLF001
    permitted = set(evidence.ids)
    # Steps: proposals, kept when every cited record is in the picture and every figure is traceable to them.
    by_id = {view.record_id: view for view in evidence.records}
    for step in answer.steps[:MAX_STEPS]:
        split = _split_citations(step.source_ids, permitted)
        if split is None:
            continue
        record_ids, refs = split
        cited = [by_id[r] for r in record_ids]
        if cited and not chat._digits_traceable(step.text, cited):  # noqa: SLF001
            continue
        if not cited and re.search(r"\d", step.text):
            continue  # a figure with no record behind it is invented
        result.steps.append(VerifiedStep(text=step.text, source_ids=record_ids, knowledge_refs=refs))
    # Sentences: a claim about the person cites records and is verified against exactly those. A knowledge ref
    # beside them (the `search_knowledge` hit the model cited) rides along; a sentence citing a ref alone has no
    # record to verify against and is dropped, and so is one citing anything the picture or the snapshots do not
    # hold.
    candidates: list[tuple[AssistantSentence, tuple[str, ...], tuple[str, ...]]] = []
    for sentence in answer.sentences:
        split = _split_citations(sentence.source_ids, permitted)
        if split is not None and split[0]:
            candidates.append((sentence, *split))
    candidates = candidates[:MAX_SENTENCES]
    if not candidates:
        result.status = "empty"
        return result
    refs_by_sentence = {f"s{index}": refs for index, (_s, _ids, refs) in enumerate(candidates, start=1)}
    candidate = llm.FreshNarration(sentences=[
        llm.NarrationSentence(sentence_id=f"s{index}", text=s.text[:chat.MAX_RESTATEMENT_CHARS],
                              source_ids=list(record_ids)[:8])
        for index, (s, record_ids, _refs) in enumerate(candidates, start=1)])
    validation = grounding.validate_fresh_candidate(evidence, candidate, list(evidence.ids))
    if not validation.valid:
        result.status = "empty"
        return result
    remaining = max(1.0, timeout_s - (time.monotonic() - started))
    try:
        reviewed = await decision_model.verify_narration(evidence, candidate, datetime.now(timezone.utc).date(),
                                                         timeout_s=min(remaining, 20.0))
    except Exception as exc:  # noqa: BLE001 -- unverifiable is unpublished
        log.warning("assistant: verifier failed: %s", type(exc).__name__)
        result.status = "empty"
        return result
    verdicts = {row.get("sentence_id"): row.get("verdict") for row in reviewed.get("verdicts", [])}
    for sentence in candidate.sentences:
        if verdicts.get(sentence.sentence_id) != "supported":
            continue
        cited = [by_id[r] for r in sentence.source_ids]
        if not chat._digits_traceable(sentence.text, cited):  # noqa: SLF001
            continue
        verified = VerifiedSentence(text=sentence.text, source_ids=tuple(sentence.source_ids),
                                    knowledge_refs=refs_by_sentence.get(sentence.sentence_id, ()))
        result.sentences.append(verified)
        if on_sentence is not None:
            await on_sentence(verified)
    result.status = "answered" if result.sentences else "empty"
    return result


# --- the turn -----------------------------------------------------------------------------------------------


def compose_turn(question: str, picture: Picture, decision: Mapping[str, Any], result: AssistantResult, *,
                 digest: chat.Digest, seq: int, turn_id: str) -> chat.Turn:
    """One turn: the framing lead, the verified sentences, the engine's own findings and steps, the model's steps.

    The engine's steps and findings are the briefing's (`chat.engine_parts`, the rule outputs). A Käypä hoito
    citation is attached beside a verified sentence (`chat._external_evidence_suggestions`): one that cites the
    record supporting it, one that cites the guideline's own ref, or any one when the question names the
    guideline's topic. The citation is composed from the picture's own records, never from a withheld one.
    """
    lead, findings, steps = chat.engine_parts(question, picture, decision, turn_id=turn_id)
    summaries = [chat.Suggestion(suggestion_id="", turn_id=turn_id, ordinal=0,
                                 statement_class=chat.STATEMENT_MODEL_SUMMARY, text=s.text,
                                 label="Tekoälyn muotoilema, lähteisiin sidottu",
                                 source_record_ids=list(s.source_ids), refs=list(s.knowledge_refs))
                 for s in result.sentences]
    model_steps = [chat.Suggestion(suggestion_id="", turn_id=turn_id, ordinal=0,
                                   statement_class=chat.STATEMENT_KNOWLEDGE_ADVICE, text=s.text,
                                   label=MODEL_STEP_LABEL, basis_note=MODEL_STEP_BASIS,
                                   source_record_ids=list(s.source_ids), refs=list(s.knowledge_refs))
                   for s in result.steps]
    external = chat._external_evidence_suggestions(question, picture, [*summaries, *model_steps],  # noqa: SLF001
                                                   turn_id=turn_id)
    suggestions = [*lead, *summaries, *external, *findings, *steps, *model_steps]
    # Nothing verified and no engine step: the one cause-neutral refusal.
    substantive = [s for s in suggestions if s.statement_class != chat.STATEMENT_ANSWER_LEAD]
    if not substantive or (not result.sentences and not steps and not model_steps):
        suggestions = []
    suggestions = chat._renumber(suggestions)  # noqa: SLF001
    published = any(s.statement_class in (chat.STATEMENT_MODEL_SUMMARY, chat.STATEMENT_KNOWLEDGE_ADVICE)
                    for s in suggestions)
    state = (chat.GENERATOR_PUBLISHED if published else chat.GENERATOR_WITHHELD if result.generator_called
             else chat.GENERATOR_NOT_CONFIGURED)
    count = chat._source_count(len(digest.members), suggestions)  # noqa: SLF001
    now = datetime.now(timezone.utc)
    if not suggestions:
        return chat.Turn(turn_id=turn_id, digest_id=digest.digest_id, seq=seq, asked_at=now,
                         outcome=chat.OUTCOME_REFUSAL, abstention=chat.ABSTENTION,
                         generator="foundry" if published else None, generator_state=state, source_count=count,
                         followups=list(result.followups))
    return chat.Turn(turn_id=turn_id, digest_id=digest.digest_id, seq=seq, asked_at=now, outcome=chat.OUTCOME_ANSWER,
                     suggestions=suggestions, generator="foundry" if published else None, generator_state=state,
                     source_count=count, followups=list(result.followups))


# --- the opening prompts ----------------------------------------------------------------------------------


MAX_OPENING_PROMPTS = 4


def opening_prompts(picture: Picture) -> list[dict]:
    """Questions worth asking about *this* encounter before the first one is typed, from the rendered picture only.

    Each prompt is `{text, why, evidence_record_ids}`, the shape the panel has always rendered, and each is justified
    by a row the same picture renders: the attention summary (`briefings.attention_summary`) is computed over the
    rendered rows and nothing else, so a prompt implied by a withheld -- or permitted-but-unselected -- record cannot
    appear here. The conflict prompt cites the conflict's record ids only where the picture renders them. The prompt
    is offered, not answered: it is the professional's own question, and the assistant still has to answer it from
    the picture and cite its evidence.
    """
    summary = briefings.attention_summary(picture)
    rendered = {view.record_id for view in chat.picture_records(picture)}
    prompts: list[dict] = []

    def offer(text: str, why: str, record_ids: Sequence[str] = ()) -> None:
        if len(prompts) < MAX_OPENING_PROMPTS and all(item["text"] != text for item in prompts):
            prompts.append({"text": text, "why": why, "evidence_record_ids": sorted(set(record_ids) & rendered)})

    if summary["risk_reason"]:
        offer("Mihin riskiarvio perustuu?", "riskiarvio on annettu")
    if summary["conflicts"] > 0:
        conflict_ids = [record_id for conflict in picture.conflicts
                        for record_id in (conflict.medication_record_id, conflict.note_record_id)]
        offer("Mitkä lääkitykset ovat ristiriidassa keskenään?", "ristiriita kirjattu", conflict_ids)
    if summary["actions"] > 0:
        offer("Mitkä ovat seuraavat toimenpiteet?", "toimenpide-ehdotukset ovat olemassa")
    lead = summary["lead"]
    if lead and lead["record_id"] in rendered:
        offer(f"Mitä kirjauksesta «{lead['title']}» on kirjattu?", "kirjaus, johon huomio kohdistuu",
              [lead["record_id"]])
    if any(item.status != "answered" for form in picture.prefilled_forms for item in form.items):
        offer("Mitkä esitietolomakkeen kysymykset ovat vielä avoinna?", "esitietolomakkeella on avoimia kysymyksiä")
    return prompts
