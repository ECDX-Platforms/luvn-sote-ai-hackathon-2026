"""Arrival-day intake: refusals that still report, and a quarantine that does not roll back 700 000 rows."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import delivery, policy, purpose
from app.upm import Persona, Record
from scripts import reconcile_delivery


def _write_source(source_dir: Path, profiles: list[tuple[str, str]], records: list[dict],
                  encounters: str | None = None) -> None:
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "profiles.csv").write_text(
        "source_person_id,persona_id,label\n"
        + "".join(f"{key},{persona},Synthetic\n" for key, persona in profiles), encoding="utf-8")
    with (source_dir / "records.jsonl").open("w", encoding="utf-8") as stream:
        for row in records:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    if encounters is not None:
        (source_dir / "encounters.csv").write_text(encounters, encoding="utf-8")


def _record(record_id: str, person_key: str, **overrides) -> dict:
    row = {"record_id": record_id, "source_person_id": person_key, "record_type": "vapaateksti",
           "authored_by_role": "laakari", "recorded_at": "2026-01-01T00:00:00+00:00", "topic": "hoito",
           "sensitivity_tag": "none", "consent_scope": "treatment", "payload": {"teksti": record_id}}
    row.update(overrides)
    return row


ENCOUNTERS = ("encounter_id,source_person_id,role,purpose,example_label,demo_name,consent_holders,life_stage\n"
              "e1,key-a,laakari,medication_follow_up,Example,Demo,self,adult\n")


# --- 1. a refusal must still produce the report -------------------------------------------------------


def test_refusal_writes_the_report_first_with_counts_and_examples(tmp_path: Path):
    source_dir = tmp_path / "src"
    _write_source(source_dir, [("key-a", "persona-a")], [
        _record("r-1", "key-a", consent_scope="everything"),
        _record("r-2", "key-a", topic="tuntematon_aihe"),
        _record("r-3", "key-a"),
    ])
    out = tmp_path / "out"

    with pytest.raises(delivery.DeliveryShapeError, match="refused") as error:
        delivery.dry_run_delivery(source_dir, out)
    # The message names each distinct reason, not just the first row.
    assert "unknown_topic" in str(error.value) and "invalid_envelope" in str(error.value)

    report = json.loads((out / "results" / "delivery-reconciliation-report.json").read_text(encoding="utf-8"))
    assert report["rows_examined"] == 3
    assert report["rows_refused"] == 2
    assert report["records_seen"] == 1
    assert report["status"] == "refused"
    reasons = {entry["code"]: entry for entry in report["refusals"]}
    assert reasons["invalid_envelope"]["count"] == 1
    assert reasons["unknown_topic"]["count"] == 1
    example = reasons["invalid_envelope"]["examples"][0]
    assert example["locator"] == "records.jsonl:1"
    assert example["record_id"] == "r-1"
    assert example["values"] == {"consent_scope": "everything"}
    topic_example = reasons["unknown_topic"]["examples"][0]
    assert topic_example["record_id"] == "r-2"
    assert topic_example["values"] == {"topic": "tuntematon_aihe"}


def test_refusal_report_examples_are_capped_but_counted_in_full(tmp_path: Path):
    source_dir = tmp_path / "src"
    _write_source(source_dir, [("key-a", "persona-a")],
                  [_record(f"r-{index}", "key-a", topic="tuntematon_aihe") for index in range(9)])
    with pytest.raises(delivery.DeliveryShapeError):
        delivery.dry_run_delivery(source_dir, tmp_path / "out")
    report = json.loads((tmp_path / "out" / "results" / "delivery-reconciliation-report.json")
                        .read_text(encoding="utf-8"))
    entry = report["refusals"][0]
    assert entry["count"] == 9
    assert len(entry["examples"]) == 5


def test_cli_refusal_prints_a_real_report_path_and_the_examples(tmp_path: Path, capsys):
    source_dir = tmp_path / "src"
    _write_source(source_dir, [("key-a", "persona-a")], [_record("r-1", "key-a", sensitivity_tag="x")])
    out = tmp_path / "out"
    status = reconcile_delivery.main(["dry-run", str(source_dir), "--out", str(out)])
    assert status == 1
    captured = capsys.readouterr().err
    assert "report:" in captured and "(not written)" not in captured
    printed = Path(captured.split("report:")[1].strip().splitlines()[0])
    assert printed.is_file()
    assert "r-1" in captured


def test_missing_profiles_file_is_reported_not_silently_unwritten(tmp_path: Path):
    """A file-level refusal is still a report: the operator needs to know what was examined (nothing)."""
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "records.jsonl").write_text(json.dumps(_record("r-1", "key-a")) + "\n", encoding="utf-8")
    with pytest.raises(delivery.DeliveryShapeError, match="delivery needs profiles.csv"):
        delivery.dry_run_delivery(source_dir, tmp_path / "out")
    report = json.loads((tmp_path / "out" / "results" / "delivery-reconciliation-report.json")
                        .read_text(encoding="utf-8"))
    assert report["status"] == "refused"
    assert report["refusals"][0]["code"] == "missing_profiles"


# --- 2. quarantine ------------------------------------------------------------------------------------


def test_quarantine_stages_exactly_the_bad_rows_and_loads_the_rest(tmp_path: Path):
    source_dir = tmp_path / "src"
    _write_source(source_dir, [("key-a", "persona-a"), ("key-b", "persona-b")], [
        _record("good-1", "key-a"),
        _record("bad-topic", "key-a", topic="tuntematon_aihe"),
        _record("good-2", "key-b"),
        _record("bad-scope", "key-b", consent_scope="everything"),
        _record("good-3", "key-b"),
    ], encounters=ENCOUNTERS)

    report = delivery.import_delivery(source_dir, tmp_path / "out", on_error="quarantine")

    assert report.records == 3
    assert report.quarantined == 2
    assert report.quarantine_path is not None and report.quarantine_path.is_file()
    quarantine = [json.loads(line) for line in report.quarantine_path.read_text(encoding="utf-8").splitlines()]
    assert {entry["raw"]["record_id"] for entry in quarantine} == {"bad-topic", "bad-scope"}
    assert {entry["reason_code"] for entry in quarantine} == {"unknown_topic", "invalid_envelope"}
    assert all(entry["locator"].startswith("records.jsonl:") for entry in quarantine)

    index = delivery.DeliveryIndex(tmp_path / "out")
    try:
        loaded = {record.record_id for persona_id in index for record in index.persona(persona_id).records}
    finally:
        index.close()
    assert loaded == {"good-1", "good-2", "good-3"}

    summary = json.loads(report.quarantine_summary.read_text(encoding="utf-8"))
    assert summary["loaded"] == 3 and summary["quarantined"] == 2
    assert {entry["code"]: entry["count"] for entry in summary["reasons"]} == {
        "unknown_topic": 1, "invalid_envelope": 1}


def test_quarantine_is_opt_in_and_the_default_stays_strict(tmp_path: Path):
    source_dir = tmp_path / "src"
    _write_source(source_dir, [("key-a", "persona-a")],
                  [_record("good", "key-a"), _record("bad", "key-a", topic="tuntematon_aihe")],
                  encounters=ENCOUNTERS)
    with pytest.raises(delivery.DeliveryShapeError, match="unknown topic"):
        delivery.import_delivery(source_dir, tmp_path / "strict")
    assert not (tmp_path / "strict" / "quarantine").exists()


def test_quarantine_cli_exit_codes_follow_the_caller(tmp_path: Path):
    source_dir = tmp_path / "src"
    _write_source(source_dir, [("key-a", "persona-a")],
                  [_record("good", "key-a"), _record("bad", "key-a", topic="tuntematon_aihe")],
                  encounters=ENCOUNTERS)

    # Default: a quarantine run succeeds even though something was quarantined.
    assert reconcile_delivery.main(["import", str(source_dir), "--out", str(tmp_path / "a"),
                                    "--on-error=quarantine"]) == 0
    # The caller can ask for non-zero.
    assert reconcile_delivery.main(["import", str(source_dir), "--out", str(tmp_path / "b"),
                                    "--on-error=quarantine", "--fail-on-quarantine"]) == 1


def test_quarantine_that_loads_nothing_is_an_error(tmp_path: Path):
    source_dir = tmp_path / "src"
    _write_source(source_dir, [("key-a", "persona-a")],
                  [_record("bad-1", "key-a", topic="tuntematon_aihe"),
                   _record("bad-2", "key-a", consent_scope="everything")],
                  encounters=ENCOUNTERS)
    assert reconcile_delivery.main(["import", str(source_dir), "--out", str(tmp_path / "out"),
                                    "--on-error=quarantine"]) == 1


def test_quarantine_recovers_rows_after_a_vocabulary_change(tmp_path: Path, monkeypatch):
    source_dir = tmp_path / "src"
    _write_source(source_dir, [("key-a", "persona-a")],
                  [_record("good", "key-a"), _record("bad", "key-a", topic="uusi_aihe")],
                  encounters=ENCOUNTERS)
    report = delivery.import_delivery(source_dir, tmp_path / "out", on_error="quarantine")
    assert report.quarantined == 1

    # The vocabulary revision: the topic is declared now, so the stored raw row binds. Both layers that
    # know the vocabulary must be widened: the importer's own alias AND the source of truth the `Record`
    # model validates against (D6 added that second check), or the model refuses the recovered row.
    widened = delivery.declared_topics() | {"uusi_aihe"}
    monkeypatch.setattr(delivery, "declared_topics", lambda: widened)
    monkeypatch.setattr(purpose, "topics", lambda: widened)
    retry = delivery.retry_quarantine(report.quarantine_path, tmp_path / "out")
    assert retry.attempted == 1 and retry.recovered == 1 and retry.remaining == 0

    index = delivery.DeliveryIndex(tmp_path / "out")
    try:
        loaded = {record.record_id for persona_id in index for record in index.persona(persona_id).records}
    finally:
        index.close()
    assert loaded == {"good", "bad"}


def test_retry_quarantine_refuses_to_overwrite_its_own_input(tmp_path: Path):
    source_dir = tmp_path / "src"
    _write_source(source_dir, [("key-a", "persona-a")], [_record("bad", "key-a", topic="x")],
                  encounters=ENCOUNTERS)
    report = delivery.import_delivery(source_dir, tmp_path / "out", on_error="quarantine")
    with pytest.raises(delivery.DeliveryShapeError, match="must differ"):
        delivery.retry_quarantine(report.quarantine_path, tmp_path / "out", output=report.quarantine_path)


# --- 3. fail closed on an unknown topic ---------------------------------------------------------------


def _persona_with_topic(topic: str) -> Persona:
    # `model_construct` deliberately skips validation: `Record` itself now refuses an undeclared topic
    # (D6), and this test pins the POLICY layer's separate guard for a record that reached it anyway (a
    # future path, a `model_construct` caller, or a row written before the model learned the rule).
    return Persona(persona_id="p", label="p", records=[Record.model_construct(
        record_id="r", source="synthetic", record_type="vapaateksti", authored_by_role="laakari",
        recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc), topic=topic, sensitivity_tag="none",
        consent_scope="treatment", payload={"teksti": "x"})])


def test_an_unknown_topic_cannot_yield_a_silently_empty_permitted_set():
    with pytest.raises(policy.UnknownVocabularyError) as error:
        policy.cut(_persona_with_topic("tuntematon_aihe"), "laakari", "mental_health_assessment")
    assert "tuntematon_aihe" in str(error.value)
    assert "mielenterveys" in str(error.value), "the known set must be named for the operator to map"
    assert error.value.axis == "topic" and error.value.unknown == ["tuntematon_aihe"]


def test_a_declared_but_irrelevant_topic_still_yields_an_empty_set():
    """The refusal is specific: a topic that is declared and simply not this purpose's is policy, not error."""
    scoped = policy.cut(_persona_with_topic("toimeentulo"), "laakari", "mental_health_assessment")
    assert scoped.records == []


def test_unknown_topic_is_refused_at_ingestion_not_dropped(tmp_path: Path):
    source_dir = tmp_path / "src"
    _write_source(source_dir, [("key-a", "persona-a")], [_record("r-1", "key-a", topic="tuntematon_aihe")])
    with pytest.raises(delivery.DeliveryShapeError, match="unknown topic"):
        delivery.import_delivery(source_dir, tmp_path / "out")


def test_an_ambiguous_second_demografia_basics_is_refused_or_quarantined(tmp_path: Path):
    """`source.perustieto_for` binds one identity and raises on two; catch it at load, not at request time."""
    source_dir = tmp_path / "src"
    basics = {"record_type": "perustieto", "topic": "demografia", "payload": {"nimi": "A"}}
    _write_source(source_dir, [("key-a", "persona-a")], [
        _record("basics-1", "key-a", **basics),
        _record("basics-2", "key-a", **basics),
    ], encounters=ENCOUNTERS)
    with pytest.raises(delivery.DeliveryShapeError, match="more than one demografia"):
        delivery.import_delivery(source_dir, tmp_path / "strict")

    report = delivery.import_delivery(source_dir, tmp_path / "quarantine", on_error="quarantine")
    assert report.records == 1 and report.quarantined == 1
    index = delivery.DeliveryIndex(tmp_path / "quarantine")
    try:
        records = index.persona("persona-a").records
    finally:
        index.close()
    assert [record.record_id for record in records] == ["basics-1"]


# --- 4. the shipped default corpus must reconcile clean -----------------------------------------------


def test_the_shipped_default_delivery_dir_reconciles_ready(tmp_path: Path):
    """G4 (2026-10-11): a dev host serves `DEFAULT_DELIVERY_DIR` with no configuration, so whatever it
    names must pass the repository's own arrival check. It used to name `delivery-sample`, which warns on
    five types; the default now names the demo corpus, and this pins the property rather than the path."""
    assert delivery.DEFAULT_DELIVERY_DIR.is_dir(), delivery.DEFAULT_DELIVERY_DIR
    report = delivery.dry_run_delivery(delivery.DEFAULT_DELIVERY_DIR, tmp_path)
    assert report.status == "ready", report.report_path.read_text(encoding="utf-8")
    assert report.rows_refused == 0
    document = json.loads(report.report_path.read_text(encoding="utf-8"))
    for name, item in document["types"].items():
        assert item["missing_render_keys"] == {} and item["unmapped_payload_keys"] == {}, name


def test_the_loaders_default_corpus_resolves_on_a_dev_host():
    """G4 (2026-10-11): the container path is not a directory on a dev host, and the old fallback was a
    cwd-relative path that only resolved from the repository root. The default must resolve wherever the
    loader is imported from."""
    from app import load_sql

    assert load_sql.DEFAULT_CORPUS.is_dir(), load_sql.DEFAULT_CORPUS
    assert (load_sql.DEFAULT_CORPUS / "records.jsonl").is_file()


def test_a_retry_that_dies_mid_pass_loses_no_refused_row(tmp_path: Path, monkeypatch):
    """L1: the retry used to truncate its output, then rewrite the input at the very end.

    A death in between left only the rows quarantined so far. The pass now stages into a sibling and
    swaps with `os.replace`, so the bytes refused before the crash are on disk and the previous output is
    intact -- and a re-run folds the staged file in rather than starting over.
    """
    source_dir = tmp_path / "src"
    _write_source(source_dir, [("key-a", "persona-a")],
                  [_record("good", "key-a"), _record("bad-1", "key-a", topic="uusi_aihe"),
                   _record("bad-2", "key-a", topic="uusi_aihe")],
                  encounters=ENCOUNTERS)
    report = delivery.import_delivery(source_dir, tmp_path / "out", on_error="quarantine")
    assert report.quarantined == 2

    # The vocabulary is NOT widened, so both rows are refused again -- and the second refusal kills the
    # pass, mid-way through the staged file.
    output = tmp_path / "out" / "quarantine-retry.jsonl"
    staged = output.with_name(output.name + ".tmp")
    real_record = delivery.RefusalLog.record
    calls = {"n": 0}

    def explode(self, refusal):
        calls["n"] += 1
        real_record(self, refusal)
        if calls["n"] == 2:
            raise KeyboardInterrupt("the operator pressed Ctrl-C between two refused rows")

    monkeypatch.setattr(delivery.RefusalLog, "record", explode)
    with pytest.raises(KeyboardInterrupt):
        delivery.retry_quarantine(report.quarantine_path, tmp_path / "out", output=output)
    monkeypatch.setattr(delivery.RefusalLog, "record", real_record)

    staged_rows = [line for line in staged.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(staged_rows) == 2, "both refused rows reached disk before the crash"

    # The re-run completes and every refused row is in the final output exactly once.
    retry = delivery.retry_quarantine(report.quarantine_path, tmp_path / "out", output=output)
    assert retry.attempted == 2 and retry.remaining == 2
    final = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert {entry["record_id"] for entry in final} == {"bad-1", "bad-2"}, final
    assert not staged.exists(), "the staged file is swapped in, not left behind"
