"""Retrieval over the committed form-knowledge snapshot, with the citation each item needs.

The snapshot is derived by `scripts/import_form_knowledge.py` from the challenge's own form definitions and
carries the form's Finnish wording unedited. This module reads it and ranks questions by overlap with a query.

A snapshot that does not load cleanly resolves nothing: `load_snapshot` raises `SnapshotError` on any
malformed document rather than returning a partial one. A miss is a miss: `retrieve` returns `[]` rather
than a nearest-neighbour guess.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .text import overlap as _overlap, tokens as _tokens
from .knowledge import SnapshotError

SNAPSHOT_PATH = Path(__file__).with_name("knowledge_forms_v1.json")
SNAPSHOT_VERSION = "luvn-form-knowledge-v1"


@dataclass(frozen=True)
class FormItem:
    form_id: str
    revision_id: str
    form_name: str
    form_version: str
    category_id: str | None
    category_name: str | None
    question_id: str
    question_text: str
    source: str

    def as_ref(self) -> str:
        return f"form:{self.form_id}#{self.question_id}"

    def as_dict(self) -> dict:
        return {"ref": self.as_ref(), "form_name": self.form_name, "form_version": self.form_version,
                "category_name": self.category_name, "question_text": self.question_text,
                "source": self.source}


def _fail(message: str) -> SnapshotError:
    return SnapshotError(f"{SNAPSHOT_PATH.name}: {message}")


def _object(value: object, what: str) -> dict:
    if not isinstance(value, dict):
        raise _fail(f"{what} is not an object")
    return value


def _list(value: object, field: str, where: str) -> list:
    if not isinstance(value, list):
        raise _fail(f"{where} has no {field} list")
    return value


def _text(value: object, field: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(f"{where} has no non-empty {field}")
    return value


def _str(value: object, field: str, where: str) -> str:
    """A string that may be empty (a form's description, which the source may leave blank)."""
    if not isinstance(value, str):
        raise _fail(f"{where} {field} is not a string")
    return value


def _flag(value: object, field: str, where: str) -> bool:
    if not isinstance(value, bool):
        raise _fail(f"{where} {field} is not true or false")
    return value


def _validate(document: object) -> dict:
    document = _object(document, "the snapshot")
    if document.get("snapshot_version") != SNAPSHOT_VERSION:
        raise _fail(f"snapshot_version {document.get('snapshot_version')!r} is not {SNAPSHOT_VERSION!r}")

    source = _object(document.get("source"), "the source block")
    _text(source.get("title"), "title", "the source block")
    _text(source.get("archive"), "archive", "the source block")
    _text(source.get("retrieved"), "retrieved", "the source block")
    _str(source.get("note"), "note", "the source block")
    files = _list(source.get("files"), "files", "the source block")
    if not files:
        raise _fail("the source block names no files")
    rows: dict[str, dict] = {}
    for row in files:
        row = _object(row, "a source file")
        name = _text(row.get("name"), "name", "a source file")
        if name in rows:
            raise _fail(f"the source block repeats file {name}")
        digest = _text(row.get("sha256"), "sha256", f"source file {name}")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise _fail(f"source file {name} sha256 is not 64 lowercase hex characters")
        if "duplicate_of" in row:
            _text(row["duplicate_of"], "duplicate_of", f"source file {name}")
        rows[name] = row
    dropped_names: set[str] = set()
    for dropped in _list(source.get("duplicates_dropped"), "duplicates_dropped", "the source block"):
        dropped = _object(dropped, "a dropped duplicate")
        name = _text(dropped.get("name"), "name", "a dropped duplicate")
        target = _text(dropped.get("duplicate_of"), "duplicate_of", "a dropped duplicate")
        if name not in rows or target not in rows or "duplicate_of" in rows[target]:
            raise _fail(f"dropped duplicate {name} does not name a kept file in the source block")
        if rows[name].get("duplicate_of") != target or rows[name]["sha256"] != rows[target]["sha256"]:
            raise _fail(f"dropped duplicate {name} is not a byte-identical copy of {target}")
        dropped_names.add(name)
    for name, row in rows.items():
        if "duplicate_of" in row and name not in dropped_names:
            raise _fail(f"source file {name} is marked a duplicate but is not listed as dropped")
    kept = len(rows) - len(dropped_names)

    forms = _list(document.get("forms"), "forms", "the snapshot")
    if not forms:
        raise _fail("the snapshot has no forms")
    form_ids: set[str] = set()
    for form in forms:
        form = _object(form, "a form")
        form_id = _text(form.get("form_id"), "form_id", "a form")
        where = f"form {form_id}"
        if form_id in form_ids:
            raise _fail(f"form {form_id} appears more than once")
        form_ids.add(form_id)
        _text(form.get("revision_id"), "revision_id", where)
        _text(form.get("name_fi"), "name_fi", where)
        _text(form.get("schema_version"), "schema_version", where)
        _str(form.get("description_fi"), "description_fi", where)

        category_ids: set[str] = set()
        for category in _list(form.get("categories"), "categories", where):
            category = _object(category, f"a category of {where}")
            category_id = _text(category.get("id"), "category id", where)
            if category_id in category_ids:
                raise _fail(f"{where} repeats category id {category_id}")
            category_ids.add(category_id)
            _text(category.get("name_fi"), f"name_fi of category {category_id}", where)

        questions = _list(form.get("questions"), "questions", where)
        if not questions:
            raise _fail(f"{where} has no questions")
        seen: set[str] = set()
        for question in questions:
            question = _object(question, f"a question of {where}")
            question_id = _text(question.get("id"), "question id", where)
            if question_id in seen:
                raise _fail(f"{where} repeats question id {question_id}")
            seen.add(question_id)
            _text(question.get("text_fi"), f"text_fi of {question_id}", where)
            if "category_id" not in question:
                raise _fail(f"question {question_id} of {where} has no category_id (null when it has none)")
            category_id = question["category_id"]
            # The type check comes first: an unhashable value must be a SnapshotError, not a TypeError.
            if category_id is not None and (not isinstance(category_id, str) or category_id not in category_ids):
                raise _fail(f"question {question_id} names category {category_id!r} not in {where}")
            _str(question.get("type"), "type", f"question {question_id} of {where}")
            _flag(question.get("is_optional"), f"is_optional of question {question_id}", where)
            _flag(question.get("scoring_enabled"), f"scoring_enabled of question {question_id}", where)
            answer_ids: set[str] = set()
            for answer in _list(question.get("answers"), "answers", f"question {question_id} of {where}"):
                answer = _object(answer, f"an answer of question {question_id} of {where}")
                answer_id = _text(answer.get("id"), "answer id", f"question {question_id} of {where}")
                if answer_id in answer_ids:
                    raise _fail(f"question {question_id} of {where} repeats answer id {answer_id}")
                answer_ids.add(answer_id)
                _str(answer.get("text_fi"), "text_fi", f"answer {answer_id} of {where}")
                points = answer.get("points")
                if points is not None and (isinstance(points, bool) or not isinstance(points, int)):
                    raise _fail(f"answer {answer_id} of {where} has non-integer points")
        for calc in _list(form.get("score_calculations"), "score_calculations", where):
            calc = _object(calc, f"a score calculation of {where}")
            _str(calc.get("function"), "function", f"a score calculation of {where}")
            for bound in ("min", "max"):
                value = calc.get(bound)
                if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                    raise _fail(f"a score calculation of {where} has a non-integer {bound}")
            _str(calc.get("title_fi"), "title_fi", f"a score calculation of {where}")
            for summary in _list(calc.get("summaries"), "summaries", f"a score calculation of {where}"):
                summary = _object(summary, f"a score summary of {where}")
                _str(summary.get("text_fi"), "text_fi", f"a score summary of {where}")
    if len(forms) != kept:
        raise _fail(f"{len(forms)} forms but {kept} kept source files")
    return document


def _read(path: Path) -> dict:
    if not path.exists():
        raise _fail("missing")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise _fail(f"unreadable ({error})") from error
    return _validate(document)


@lru_cache(maxsize=1)
def load_snapshot() -> dict:
    """The validated committed snapshot. Raises `SnapshotError` if it is malformed; never partial."""
    return _read(SNAPSHOT_PATH)


def items() -> list[FormItem]:
    """Every question in the snapshot, in the snapshot's own order."""
    snapshot = load_snapshot()
    title = snapshot["source"]["title"]
    out: list[FormItem] = []
    for form in snapshot["forms"]:
        categories = {c["id"]: c["name_fi"] for c in form["categories"]}
        for question in form["questions"]:
            category_id = question["category_id"]
            out.append(FormItem(
                form_id=form["form_id"], revision_id=form["revision_id"], form_name=form["name_fi"],
                form_version=form["schema_version"],
                category_id=category_id,
                category_name=categories.get(category_id) if category_id is not None else None,
                question_id=question["id"], question_text=question["text_fi"], source=title,
            ))
    return out


@dataclass(frozen=True)
class FormAnswer:
    id: str
    text_fi: str
    points: int | None


@dataclass(frozen=True)
class FormQuestion:
    id: str
    category_id: str | None
    category_name: str | None
    text_fi: str
    type: str
    is_optional: bool
    scoring_enabled: bool
    answers: tuple[FormAnswer, ...]


@dataclass(frozen=True)
class ScoreCalculation:
    function: str
    min: int | None
    max: int | None
    title_fi: str
    summaries: tuple[dict, ...]


@dataclass(frozen=True)
class FormDefinition:
    """One whole form from the snapshot: what the pre-fill renders and the calculator sums."""

    form_id: str
    revision_id: str
    name_fi: str
    schema_version: str
    source: str
    source_file: str | None
    source_sha256: str | None
    categories: tuple[tuple[str, str], ...]
    questions: tuple[FormQuestion, ...]
    score_calculations: tuple[ScoreCalculation, ...]

    @property
    def scored_questions(self) -> tuple[FormQuestion, ...]:
        return tuple(q for q in self.questions if q.scoring_enabled)

    @property
    def is_scored(self) -> bool:
        return bool(self.scored_questions) and bool(self.score_calculations)

    def question(self, question_id: str) -> FormQuestion | None:
        return next((q for q in self.questions if q.id == question_id), None)

    def as_ref(self) -> str:
        return f"form:{self.form_id}"


@lru_cache(maxsize=1)
def forms() -> tuple[FormDefinition, ...]:
    """Every form in the snapshot, whole, in the snapshot's own order."""
    snapshot = load_snapshot()
    title = snapshot["source"]["title"]
    # The source file is named after the form's REVISION GUID, so each form can cite the file and its hash.
    files = [(row["name"], row["sha256"]) for row in snapshot["source"]["files"] if "duplicate_of" not in row]
    out: list[FormDefinition] = []
    for form in snapshot["forms"]:
        categories = {c["id"]: c["name_fi"] for c in form["categories"]}
        match = next(((name, digest) for name, digest in files
                      if form["revision_id"] in name or form["form_id"] in name), (None, None))
        out.append(FormDefinition(
            form_id=form["form_id"], revision_id=form["revision_id"], name_fi=form["name_fi"],
            schema_version=form["schema_version"], source=title, source_file=match[0], source_sha256=match[1],
            categories=tuple((c["id"], c["name_fi"]) for c in form["categories"]),
            questions=tuple(FormQuestion(
                id=q["id"], category_id=q["category_id"],
                category_name=categories.get(q["category_id"]) if q["category_id"] is not None else None,
                text_fi=q["text_fi"], type=q["type"], is_optional=q["is_optional"],
                scoring_enabled=q["scoring_enabled"],
                answers=tuple(FormAnswer(id=a["id"], text_fi=a["text_fi"], points=a.get("points"))
                              for a in q.get("answers", [])),
            ) for q in form["questions"]),
            score_calculations=tuple(ScoreCalculation(
                function=c["function"], min=c.get("min"), max=c.get("max"), title_fi=c["title_fi"],
                summaries=tuple(c.get("summaries", []))) for c in form.get("score_calculations", [])),
        ))
    return tuple(out)


def form_by_id(form_id: str) -> FormDefinition | None:
    return next((form for form in forms() if form.form_id == form_id), None)


def find_form(name_fragment: str) -> FormDefinition | None:
    """The first form whose Finnish name contains the fragment, case-insensitively; None when none does."""
    needle = name_fragment.casefold()
    return next((form for form in forms() if needle in form.name_fi.casefold()), None)


def _terms(text: str) -> set[str]:
    """The query's terms. One-character tokens (a bare "9") carry no meaning and are dropped."""
    return {t for t in _tokens(text) if len(t) >= 2}


# Where a query term matches decides how much it counts. A form's own name says what the form is for, so it outranks the
# category, and a question's wording is the weakest signal: a long form has many question-text hits, which is how an
# 88-question school form outranked a 12-question depression instrument when every hit counted the same.
FORM_NAME_WEIGHT = 3
CATEGORY_WEIGHT = 2
QUESTION_WEIGHT = 1
# An instrument the encounter's own assessments carry: its form gets this bonus, the same as a form-name hit.
INSTRUMENT_BONUS = FORM_NAME_WEIGHT
# One form may contribute at most this many items, so one long form cannot fill a result list. Measured: the PHQ-9 and
# BDI-21 forms each have 12 and 21 items, and three is enough to show that a form is an instrument, not a question list.
MAX_ITEMS_PER_FORM = 3


def _matches(term: str, haystack: set[str]) -> bool:
    """A long term matches by stem (`_overlap`); a short one must be present exactly, as `_score` always required."""
    return bool(_overlap({term}, haystack)) if len(term) >= 4 else term in haystack


def _instrument_named(form_name: str, instruments: Sequence[str]) -> bool:
    """The form's name carries an instrument's name, ignoring case and punctuation: "PHQ-9" in "(PHQ-9)" matches."""
    squeezed = re.sub(r"[^0-9a-zäöå]", "", form_name.casefold())
    for instrument in instruments:
        key = re.sub(r"[^0-9a-zäöå]", "", instrument.casefold())
        if len(key) >= 2 and key in squeezed:
            return True
    return False


def _score(query: set[str], item: FormItem, instruments: Sequence[str]) -> tuple[int, bool]:
    """(score, covered). A hit must match more than half of the query's terms.

    Each term scores the weight of the strongest field it matches: form name, then category, then question text. A
    form whose name carries one of the encounter's instruments gets `INSTRUMENT_BONUS` on top. Coverage counts every
    term, so one shared word is not enough: "moottorin öljynvaihto" shares "moottorin" with an ADHD question
    ("kuin moottorin ajamana") and is a miss.
    """
    form = _tokens(item.form_name)
    category = _tokens(item.category_name or "")
    question = _tokens(item.question_text)
    weights = []
    for term in query:
        weight = 0
        for field_weight, haystack in ((FORM_NAME_WEIGHT, form), (CATEGORY_WEIGHT, category),
                                       (QUESTION_WEIGHT, question)):
            if _matches(term, haystack):
                weight = field_weight
                break
        weights.append(weight)
    matched = sum(1 for weight in weights if weight)
    bonus = INSTRUMENT_BONUS if _instrument_named(item.form_name, instruments) else 0
    return sum(weights) + bonus, matched * 2 > len(query)


def retrieve(query: str, *, limit: int = 3, instruments: Sequence[str] = ()) -> list[FormItem]:
    """Top-`limit` items covering more than half of the query's terms. `[]` when none do.

    Ranked by `_score`, then at most `MAX_ITEMS_PER_FORM` items per form, then the snapshot's own order for ties, so the
    same query always returns the same list. `instruments` are the encounter's own instrument names (see `chat`).
    """
    query_tokens = _terms(query)
    if not query_tokens:
        return []
    scored = []
    for index, item in enumerate(items()):
        score, covered = _score(query_tokens, item, instruments)
        if covered:
            scored.append((-score, index, item))
    scored.sort(key=lambda row: (row[0], row[1]))
    per_form: Counter[str] = Counter()
    chosen = []
    for _, _, item in scored:
        if per_form[item.form_id] < MAX_ITEMS_PER_FORM:
            per_form[item.form_id] += 1
            chosen.append(item)
        if len(chosen) == limit:
            break
    return chosen


# Retrieval compares whole tokens, so an inflected question term ("masennusoireilua") never equals the form's own word
# ("masennuskysely"). Retrieval also counts every term of the question against its coverage rule, so filler words can
# leave a question covering nothing even when one of its long words reaches the right form: "Miten masennusoireilua
# tulisi hoitaa?" returns nothing as written. So the question is retrieved as it stands, and once more per long token,
# by that token's stem. The stem is the first `STEM_LENGTH` characters, so an inflection of a question word matches.
STEM_MIN_TOKEN = 8
STEM_LENGTH = 8


def _stem_queries(question: str) -> list[str]:
    """The stems of the question's long tokens: longest token first, each stem once.

    Longest first is measured, not assumed: shortest first surfaces AUDIT and GAD-7 ahead of the depression instruments
    for "huomioida" (work order U).
    """
    tokens = sorted(_tokens(question), key=lambda token: (-len(token), token))
    stems: list[str] = []
    for token in tokens:
        if len(token) >= STEM_MIN_TOKEN and token[:STEM_LENGTH] not in stems:
            stems.append(token[:STEM_LENGTH])
    return stems


def candidates(question: str, *, instruments: Sequence[str] = (), limit: int = 6) -> list[dict]:
    """The knowledge items a question reaches, as `{ref, form, version, source, text, category}`.

    The question is retrieved as it stands, then once per long-token stem (`_stem_queries`). The results are merged in
    that order and deduplicated by ref, so the retriever's own list for the question leads and the stems can only add
    to it. `instruments` are the encounter's own instrument names (the assessments the picture renders), which is the
    one thing the old chat surface read from the picture here. Real refs only: every one resolves through this module.
    Empty when nothing is retrieved. This is the offline fallback for a knowledge lookup with no model configured.
    """
    named = [name for name in instruments if name]
    found: list[FormItem] = []
    for query in [question, *_stem_queries(question)]:
        found.extend(retrieve(query, limit=limit, instruments=named))
    first_seen: dict[str, FormItem] = {}
    for item in found:
        first_seen.setdefault(item.as_ref(), item)
    return [{"ref": ref, "form": item.form_name, "version": item.form_version, "source": item.source,
             "text": item.question_text, "category": item.category_name}
            for ref, item in list(_substantive_first(list(first_seen.values())).items())[:limit]]


# "Substantive" (work order X2) is a heuristic over the snapshot's own metadata, not a clinical judgement. A question is
# substantive when the snapshot marks it `scoring_enabled`: it is an item of a scored instrument (PHQ-9, BDI-21, AUDIT).
# The `Ohje` category is the form's instructions and carries no clinical content, so it is excluded whenever a question
# outside it was retrieved. Unscored questions in other categories are kept, ranked after the scored ones: the snapshot
# does not mark demographic questions as such, so they are not separately excluded.
OHJE_CATEGORY = "Ohje"


@lru_cache(maxsize=1)
def _scored_refs() -> frozenset[str]:
    """The refs of the snapshot's scored instrument items, read from the snapshot."""
    return frozenset(f"form:{form['form_id']}#{question['id']}"
                     for form in load_snapshot()["forms"] for question in form["questions"]
                     if question.get("scoring_enabled"))


def _substantive_first(found: list[FormItem]) -> dict[str, FormItem]:
    """The retrieved items keyed by ref, re-ranked: scored instrument items first, then the rest, and `Ohje` only when
    nothing else was retrieved. Each group keeps the retriever's own order (a stable sort), so no order is invented."""
    kept = [item for item in found if (item.category_name or "").strip() != OHJE_CATEGORY] or found
    scored = _scored_refs()
    ranked = sorted(kept, key=lambda item: item.as_ref() not in scored)
    return {item.as_ref(): item for item in ranked}
