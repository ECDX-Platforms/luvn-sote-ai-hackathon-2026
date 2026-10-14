"""UpToDate-shaped pathway assembly over an authored stand-in corpus.

The vendor describes UpToDate Expert AI's mechanism as retrieval from an expert-written recommendation
database, with a language model used to *find and present* rather than to generate, and no internet
search. This module replicates that **mechanism**, not the content: a curated corpus, a retrieval step,
and — the part that matters — a per-step gate that drops any step the person's own records contradict.

Why the gate is the whole point: a pathway rendered next to a person's record is a clinical assertion
about *that person*, so a generic step that their record contradicts must not render. `s4` in the
authored corpus ("continue amlodipine unchanged") exists to be refused on the fixture where amlodipine
was stopped for oedema and dizziness; if it renders, the mechanism is unsafe and the demo should fail.

.. warning::
   **What the gate does and does not do.** It drops a step whose contradiction probability is **at or above
   0.7**, and it renders a step whose relevance is **at or above 0.6**. Those two numbers are borrowed from
   the runtime relevance gate, where a third condition — a 0.25 decision margin between the top two
   probabilities — also applies. A single Noul question has no runner-up to margin against, so **this gate
   applies no decision margin at all**: a contradiction of 0.69 renders. The thresholds are inherited and
   not calibrated on Finnish data, and Decision-1's own repeated-call drift on this inventory was measured at up to
   0.04 with one verdict flip in ten. Treat this as a unit-tested filter, not a safety control.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import decision_model

CORPUS_PATH = Path(__file__).with_name("pathway_corpus_v1.json")
REF_SOURCE = "pathway-corpus"
RELEVANT_FLOOR = decision_model.ADDITION_FLOOR
CONTRADICTION_CEILING = decision_model.OMISSION_FLOOR
_WORD = re.compile(r"[a-zä-öå0-9]+")


@dataclass(frozen=True)
class PathwayStep:
    """One candidate step, with the citation it will carry if it renders."""

    entry_id: str
    step_id: str
    text: str
    version: str
    illustrative: bool
    provenance: str

    def as_ref(self) -> str:
        return f"ref:{REF_SOURCE}@{self.version}#{self.entry_id}.{self.step_id}"

    def as_dict(self) -> dict:
        return {"ref": self.as_ref(), "entry_id": self.entry_id, "step_id": self.step_id,
                "text": self.text, "illustrative": self.illustrative, "provenance": self.provenance}


@lru_cache(maxsize=1)
def load_corpus() -> dict:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def steps_of(entry: Mapping[str, object], version: str, illustrative: bool, provenance: str) -> list[PathwayStep]:
    return [PathwayStep(entry_id=str(entry["entry_id"]), step_id=str(step["step_id"]),
                        text=str(step["text"]), version=version, illustrative=illustrative,
                        provenance=provenance)
            for step in entry.get("steps", [])]


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.casefold()))


def retrieve(query: str, *, limit: int = 1) -> list[dict]:
    """Rank corpus entries against a query. Deterministic; the search service is the production retriever."""
    terms = _tokens(query)
    scored: list[tuple[int, int, dict]] = []
    for entry in load_corpus()["entries"]:
        text = " ".join([str(entry.get("title", "")), str(entry.get("topic", "")),
                         *(str(step["text"]) for step in entry.get("steps", []))])
        scored.append((len(terms & _tokens(text)), len(entry.get("steps", [])), entry))
    ranked = sorted(scored, key=lambda row: (-row[0], -row[1]))
    return [entry for score, _count, entry in ranked[:limit] if score > 0]


def gate_questions() -> dict:
    """Two questions per step. `relevant` decides whether it belongs; `contradicts` decides whether it is safe."""
    return {
        "relevant": {"type": "noul", "instructions":
            "Onko tämä vaihe olennainen tämän ammattilaisroolin ja palvelukontekstin käynnin "
            "valmistelussa? Pelkkä aihepiirin osuminen ei riitä."},
        "contradicts_records": {"type": "noul", "instructions":
            "Onko henkilön kirjauksissa jotain, mikä on ristiriidassa tämän vaiheen kanssa? "
            "Jos vaihe kehottaa jatkamaan tai aloittamaan jotain, mitä kirjaukset osoittavat "
            "lopetetun tai haitallisen, vastaa true."},
    }


def gate_state(role: str, purpose: str, step: PathwayStep, records: Sequence[Mapping[str, object]]) -> dict:
    return {"encounter_role": role, "encounter_purpose": purpose, "pathway_step": step.text,
            "person_records": list(records)}


def assemble(entry: Mapping[str, object], answers: Mapping[str, Mapping[str, object]], *,
             relevant_floor: float = RELEVANT_FLOOR,
             contradiction_ceiling: float = CONTRADICTION_CEILING) -> dict:
    """Combine per-step answers into a pathway. A missing or malformed answer drops the step."""

    def probability(answer: object, name: str) -> float | None:
        try:
            return decision_model.noul_answer(answer, name)
        except ValueError:
            return None

    corpus = load_corpus()
    rendered: list[dict] = []
    dropped: list[dict] = []
    for step in steps_of(entry, str(corpus["corpus_version"]), bool(corpus["illustrative"]),
                         str(corpus["provenance"])):
        answer = answers.get(step.step_id)
        if not isinstance(answer, Mapping):
            dropped.append({**step.as_dict(), "reason": "no_answer"})
            continue
        relevant = probability(answer.get("relevant"), f"relevant:{step.step_id}")
        contradicts = probability(answer.get("contradicts_records"), f"contradicts:{step.step_id}")
        if relevant is None or contradicts is None:
            dropped.append({**step.as_dict(), "reason": "invalid_answer"})
        elif contradicts >= contradiction_ceiling:
            dropped.append({**step.as_dict(), "reason": "contradicted_by_record",
                            "relevance_probability": relevant,
                            "contradiction_probability": contradicts})
        elif relevant < relevant_floor:
            dropped.append({**step.as_dict(), "reason": "not_relevant",
                            "relevance_probability": relevant})
        else:
            rendered.append({**step.as_dict(), "relevance_probability": relevant,
                             "contradiction_probability": contradicts})
    return {"entry_id": entry.get("entry_id"), "title": entry.get("title"),
            "entry_topic": entry.get("topic"),
            "steps": rendered, "dropped": dropped, "rendered": len(rendered),
            "candidate": len(rendered) + len(dropped),
            "abstained": not rendered}
