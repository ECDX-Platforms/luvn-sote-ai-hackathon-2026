"""Tests for the committed form-knowledge snapshot, its retrieval, and the builder that derives it.

Offline: the committed snapshot is read from the repository; malformed snapshots and builder inputs are written
to `tmp_path`. The builder is exercised on a small synthetic source. The one check against the extracted release
runs only where that directory exists, and says so when it is skipped.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from app import knowledge_forms as kf
from app.knowledge import SnapshotError

BUILDER = Path(__file__).resolve().parents[1] / "scripts" / "import_form_knowledge.py"
SOURCE_TITLE = "SOTE AI Hackathon data — esitietolomakkeet ja kyselyt"


@pytest.fixture(autouse=True)
def _fresh_cache():
    kf.load_snapshot.cache_clear()
    yield
    kf.load_snapshot.cache_clear()


def _form_by_name_fragment(fragment: str) -> dict:
    matches = [f for f in kf.load_snapshot()["forms"] if fragment in f["name_fi"]]
    assert len(matches) == 1, fragment
    return matches[0]


def _builder():
    spec = importlib.util.spec_from_file_location("import_form_knowledge", BUILDER)
    assert spec is not None and spec.loader is not None, BUILDER
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_snapshot_loads_with_the_expected_version_and_hashed_sources():
    document = kf.load_snapshot()
    assert document["snapshot_version"] == kf.SNAPSHOT_VERSION
    files = document["source"]["files"]
    assert files, "the source block must name the files it was derived from"
    for row in files:
        assert len(row["sha256"]) == 64, row["name"]
        assert all(c in "0123456789abcdef" for c in row["sha256"]), row["name"]


def test_recorded_hashes_match_the_extracted_release_where_it_is_present():
    # Format alone does not authenticate a digest. This compares each one with the file it names, but only where
    # the extracted release exists; the gate runs without it, so there the skip is the honest answer.
    source = _builder().DEFAULT_SOURCE
    if not source.is_dir():
        pytest.skip(f"extracted release not present at {source}; hashes were verified when it was")
    for row in kf.load_snapshot()["source"]["files"]:
        assert hashlib.sha256((source / row["name"]).read_bytes()).hexdigest() == row["sha256"], row["name"]


def test_the_committed_snapshot_is_exactly_what_the_builder_derives_from_the_source(tmp_path):
    # Catches wording drift that the source-hash check cannot: a snapshot edited by hand, or a builder change that
    # alters the derived text. Only meaningful where the extracted release is present; skipped (with reason) where not.
    source = _builder().DEFAULT_SOURCE
    if not source.is_dir():
        pytest.skip(f"extracted release not present at {source}; the rebuild comparison runs where it is")
    out = tmp_path / "rebuilt.json"
    assert _builder().main(["--source", str(source), "--out", str(out)]) == 0
    assert out.read_bytes() == kf.SNAPSHOT_PATH.read_bytes()


def test_every_form_and_question_carries_the_fields_a_citation_needs():
    forms = kf.load_snapshot()["forms"]
    assert forms
    for form in forms:
        assert form["form_id"].strip(), form["name_fi"]
        assert form["name_fi"].strip()
        assert form["revision_id"].strip(), form["name_fi"]
        assert form["schema_version"].strip(), form["name_fi"]
        assert form["questions"], f"{form['name_fi']} has no questions"
        category_ids = {c["id"] for c in form["categories"]}
        question_ids = [q["id"] for q in form["questions"]]
        assert len(question_ids) == len(set(question_ids)), f"repeated question id in {form['name_fi']}"
        for question in form["questions"]:
            assert question["id"].strip()
            assert question["text_fi"].strip(), question["id"]
            # Strict: no question of the committed snapshot lacks a category, so none may skip the check.
            assert question["category_id"] in category_ids, question["id"]


def test_the_duplicate_source_is_kept_once_and_the_drop_is_recorded():
    document = kf.load_snapshot()
    names = [f["name_fi"] for f in document["forms"]]
    duplicated = [n for n in names if "Esitietolomake toisen asteen" in n]
    assert len(duplicated) == 1
    dropped = document["source"]["duplicates_dropped"]
    assert len(dropped) == 1
    assert "toisen-asteen" in dropped[0]["name"] and dropped[0]["duplicate_of"]
    assert dropped[0]["name"] != dropped[0]["duplicate_of"]
    # The drop is only honest if the two files are byte-identical: their recorded digests must agree.
    by_name = {row["name"]: row["sha256"] for row in document["source"]["files"]}
    assert by_name[dropped[0]["name"]] == by_name[dropped[0]["duplicate_of"]]


def test_the_source_block_accounts_for_every_file_and_the_measured_counts():
    # Measured on the extracted release and recorded in knowledge-layer-plan.md (Round Q): 15 files, 14 distinct
    # forms after one byte-identical duplicate, 486 questions. A change to the snapshot must change the record too.
    document = kf.load_snapshot()
    assert len(document["source"]["files"]) == len(document["forms"]) + len(document["source"]["duplicates_dropped"])
    assert len(document["forms"]) == 14
    assert sum(len(f["questions"]) for f in document["forms"]) == 486
    assert len(kf.items()) == 486


def test_the_source_title_is_the_work_order_literal():
    assert kf.load_snapshot()["source"]["title"] == SOURCE_TITLE


def test_phq9_query_returns_phq9_items_with_a_form_citation():
    hits = kf.retrieve("masennusoireilu phq-9")
    assert hits, "the PHQ-9 query must find the PHQ-9 form"
    phq9 = _form_by_name_fragment("PHQ-9")
    question_ids = {q["id"] for q in phq9["questions"]}
    for item in hits:
        assert item.form_id == phq9["form_id"]
        assert item.as_ref().startswith("form:")
        assert item.question_id in question_ids
        assert item.as_ref() == f"form:{phq9['form_id']}#{item.question_id}"


def test_a_bare_form_code_finds_its_form():
    # "PHQ-9" overlaps the PHQ-9 form name, so it is not a miss; short codes must count toward coverage.
    phq9 = _form_by_name_fragment("PHQ-9")
    hits = kf.retrieve("PHQ-9")
    assert hits, "a bare form code must find its form"
    assert all(item.form_id == phq9["form_id"] for item in hits)


def test_tied_hits_keep_the_snapshot_question_order():
    # Every PHQ-9 question ties on a bare form code, so the order must be the snapshot's own question order. Changed
    # deliberately (T2): one form contributes at most MAX_ITEMS_PER_FORM items, so the expected list is the first
    # MAX_ITEMS_PER_FORM questions in snapshot order, not all twelve.
    phq9 = _form_by_name_fragment("PHQ-9")
    expected = [f"form:{phq9['form_id']}#{q['id']}" for q in phq9["questions"][:kf.MAX_ITEMS_PER_FORM]]
    assert [i.as_ref() for i in kf.retrieve("PHQ-9", limit=len(phq9["questions"]))] == expected


def test_a_form_name_or_category_match_outranks_a_question_text_match():
    # "masennus" is in the name of the BDI-21 form and in the question text of long school and physio forms.
    hits = kf.retrieve("masennus", limit=3)
    assert hits and hits[0].form_name.startswith("Masennuskysely")


def test_retrieve_masennus_returns_a_depression_instrument_first():
    # Measured defect: the 88-question school-health form outranked both depression instruments on question-text hits.
    first = [i.form_name for i in kf.retrieve("masennus", limit=3)]
    assert any("PHQ-9" in name or "BDI-21" in name for name in first[:2]), first


def test_one_form_contributes_at_most_the_cap():
    hits = kf.retrieve("kysely", limit=20)
    counts = {}
    for item in hits:
        counts[item.form_id] = counts.get(item.form_id, 0) + 1
    assert hits and max(counts.values()) <= kf.MAX_ITEMS_PER_FORM


def test_an_instrument_the_encounter_carries_is_preferred():
    # Without the instrument, a bare "kysely" ranks by weight and snapshot order; naming BDI-21 must lift that form.
    bdi = _form_by_name_fragment("BDI-21")
    hits = kf.retrieve("masennus", limit=3, instruments=["BDI-21"])
    assert hits[0].form_id == bdi["form_id"]


def test_neuvola_query_returns_neuvola_items():
    hits = kf.retrieve("neuvola")
    assert hits
    assert all("euvola" in item.form_name for item in hits)


def test_unrelated_query_is_a_miss_not_a_guess():
    assert kf.retrieve("moottorin öljynvaihto") == []
    assert kf.retrieve("masennusoireilu phq-9"), "positive control: the miss must not be an always-empty retriever"


def test_a_bare_digit_is_a_miss():
    # One-character tokens carry no meaning; "9" alone must not match every item that mentions a 9.
    assert kf.retrieve("9") == []
    assert kf.retrieve("PHQ-9"), "positive control: the bare digit must miss for the right reason"


def test_same_query_twice_returns_the_same_order():
    # Changed deliberately (T2): the query matches one form, so the per-form cap leaves MAX_ITEMS_PER_FORM items, not 10.
    first = [i.as_ref() for i in kf.retrieve("masennusoireilu phq-9", limit=10)]
    second = [i.as_ref() for i in kf.retrieve("masennusoireilu phq-9", limit=10)]
    assert first == second and len(first) == kf.MAX_ITEMS_PER_FORM


def test_citation_dict_names_form_version_and_source_title():
    item = kf.retrieve("masennusoireilu phq-9")[0]
    cited = item.as_dict()
    assert cited["form_name"] == item.form_name and "PHQ-9" in cited["form_name"]
    assert cited["form_version"] == "1.2.0"
    assert cited["source"] == SOURCE_TITLE, "the citation must name the work-order title, not just echo the snapshot"
    assert cited["ref"] == item.as_ref()


def test_a_malformed_snapshot_raises_rather_than_loading_partially(tmp_path, monkeypatch):
    document = json.loads(kf.SNAPSHOT_PATH.read_text(encoding="utf-8"))
    document["forms"][0]["questions"][0]["category_id"] = "no-such-category"
    path = tmp_path / "knowledge_forms_v1.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(kf, "SNAPSHOT_PATH", path)
    kf.load_snapshot.cache_clear()
    with pytest.raises(SnapshotError, match="no-such-category"):
        kf.load_snapshot()
    with pytest.raises(SnapshotError):
        kf.retrieve("masennusoireilu phq-9")


def _mutated(tmp_path, monkeypatch, mutate) -> None:
    document = json.loads(kf.SNAPSHOT_PATH.read_text(encoding="utf-8"))
    mutate(document)
    path = tmp_path / "knowledge_forms_v1.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(kf, "SNAPSHOT_PATH", path)
    kf.load_snapshot.cache_clear()


@pytest.mark.parametrize("mutate, reason", [
    (lambda d: d.pop("source"), "the source block is not an object"),
    (lambda d: d["source"].pop("title"), "title"),
    (lambda d: d["source"].pop("archive"), "archive"),
    (lambda d: d["source"].update(retrieved=["2026-10-13"]), "has no non-empty retrieved"),
    (lambda d: d["source"]["files"][0].update(sha256="abc"), "64 lowercase hex"),
    (lambda d: d["source"].update(duplicates_dropped=[{"name": "x.json"}]), "duplicate_of"),
    (lambda d: d["forms"].append(dict(d["forms"][0])), "appears more than once"),
    (lambda d: d["forms"][0].pop("schema_version"), "schema_version"),
    (lambda d: d["forms"][0].pop("description_fi"), "description_fi is not a string"),
    (lambda d: d["forms"][0]["categories"][0].update(name_fi=""), "name_fi of category"),
    (lambda d: d["forms"][0]["categories"].append(dict(d["forms"][0]["categories"][0])), "repeats category id"),
    (lambda d: d["forms"][0].update(questions=[]), "has no questions"),
    (lambda d: d["forms"][0]["questions"][0].pop("type"), "type is not a string"),
    (lambda d: d["forms"][0]["questions"][0].update(is_optional="no"), "is_optional of question .* is not true or false"),
    (lambda d: d["forms"][0]["questions"][0].update(category_id=[]), "names category"),
    (lambda d: d["forms"][0]["questions"][0].update(category_id=7), "names category"),
    (lambda d: d["forms"][0]["questions"][0].pop("category_id"), "has no category_id"),
    (lambda d: d["source"].update(archive=""), "archive"),
    (lambda d: d["source"].update(retrieved=""), "retrieved"),
    (lambda d: d.update(forms=[]), "has no forms"),
    (lambda d: d["source"]["duplicates_dropped"][0].update(duplicate_of="not-listed.json"), "does not name a kept file"),
    (lambda d: next(r for r in d["source"]["files"] if r["name"] == d["source"]["duplicates_dropped"][0]["name"]).update(sha256="0" * 64), "is not a byte-identical copy"),
    (lambda d: d["source"]["duplicates_dropped"].clear(), "not listed as dropped"),
    (lambda d: d["forms"].pop(), "forms but 14 kept source files"),
], ids=[
    "no-source", "no-title", "no-archive", "retrieved-list", "bad-sha", "dropped-without-duplicate-of",
    "dup-form", "no-version", "no-description", "empty-category-name", "dup-category", "no-questions",
    "no-type", "flag-not-bool", "category-list", "category-int", "no-category-key", "empty-archive",
    "empty-retrieved", "no-forms", "dup-points-at-unlisted", "dup-digest-mismatch", "dup-not-dropped",
    "one-form-missing",
])
def test_each_malformed_shape_refuses_the_whole_snapshot(tmp_path, monkeypatch, mutate, reason):
    _mutated(tmp_path, monkeypatch, mutate)
    with pytest.raises(SnapshotError, match=reason):
        kf.load_snapshot()
    with pytest.raises(SnapshotError):
        kf.retrieve("masennusoireilu phq-9")


# --- the builder: scripts/import_form_knowledge.py, on a synthetic source -----------------------------

def _question(question_id: str, fi: str, sv: str = "ruotsi", category: str = "c1") -> dict:
    return {"questionId": question_id, "categoryId": category,
            "questionText": {"fi": fi, "sv": sv, "en": "english"},
            "type": "text", "isOptional": False, "scoringEnabled": False}


def _form(guid: str, name: str, questions: list[dict]) -> bytes:
    document = {"surveyGuid": guid, "revisionGuid": f"rev-{guid}", "schemaVersion": "1.2.0",
                "name": {"fi": name, "sv": "sv", "en": "en"},
                "description": {"fi": "", "sv": "", "en": ""},
                "categories": [{"categoryId": "c1", "name": {"fi": "Oireet", "sv": "sv", "en": "en"}}],
                "questions": questions}
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


def _source(directory: Path) -> Path:
    directory.mkdir()
    form_a = _form("g-a", "Lomake A", [
        _question("q1", "Mieliala on ollut matala?"),
        _question("q2", "", sv="Svenska"),         # empty Finnish: omitted, never filled from Swedish
        _question("q3", "   "),                     # whitespace-only is empty too
    ])
    (directory / "a-form.json").write_bytes(form_a)
    (directory / "b-form.json").write_bytes(_form("g-b", "Lomake B", [_question("q1", "Unen laatu?")]))
    (directory / "c-copy.json").write_bytes(form_a)  # byte-identical to a-form.json: the duplicate
    return directory


def test_builder_dedupes_counts_omissions_and_keeps_the_finnish_text_unedited(tmp_path):
    snapshot, summary = _builder().build(_source(tmp_path / "src"))
    assert summary == {"source_files": 3, "forms": 2, "questions": 2, "questions_in_source": 4,
                       "duplicates": 1, "omissions": 2}
    assert [d["name"] for d in snapshot["source"]["duplicates_dropped"]] == ["c-copy.json"]
    assert [f["form_id"] for f in snapshot["forms"]] == ["g-a", "g-b"]
    texts = [q["text_fi"] for f in snapshot["forms"] for q in f["questions"]]
    assert texts == ["Mieliala on ollut matala?", "Unen laatu?"]


def test_builder_main_prints_its_summary_and_writes_the_same_bytes_twice(tmp_path, capsys):
    builder = _builder()
    source = _source(tmp_path / "src")
    out = tmp_path / "out.json"
    assert builder.main(["--source", str(source), "--out", str(out)]) == 0
    line = capsys.readouterr().out
    assert "forms=2 questions=2 (in source 4) duplicates=1 omissions=2 source_files=3" in line
    assert f"bytes_written={out.stat().st_size}" in line
    first = out.read_bytes()
    builder.main(["--source", str(source), "--out", str(out)])
    assert out.read_bytes() == first


def test_builder_refuses_a_question_that_names_an_unknown_category(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "a-form.json").write_bytes(_form("g-a", "Lomake A", [_question("q1", "Kysymys?", category="nope")]))
    with pytest.raises(SystemExit, match="unknown category nope"):
        _builder().build(source)
