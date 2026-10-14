"""The preview is the human defence against a wrong-but-matching mapping, and the checks are its net.

What these tests pin, in order of how much they matter:

* a mapping that pairs the wrong columns and matches EVERY rule still imports cleanly -- that is the
  author's stated biggest risk, and it is asserted here rather than described;
* the preview renders that pairing so a person can see it (`Paino -> payload.pituus_cm`), which is the only
  detection that exists for it;
* the mechanical checks refuse the cases that ARE mechanically impossible (one distinct person per record,
  a record id on every row, a required field empty everywhere, a numeric field still carrying `72,5`) and
  every finding names what it saw and says it is a heuristic;
* `preview` imports NOTHING -- no index, no people, no SQL -- and its report says so;
* a partial scan says it is partial instead of pretending to a total.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.delivery import import_delivery
from app.preview import preview_delivery, write_preview
from app.sanity import HEURISTIC_NOTE, SanityAccumulator
from scripts import reconcile_delivery

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
PEOPLE = 40
ROWS = 120

COLUMNS = ["TietueVersio", "Henkilötunniste", "Kirjaustunniste", "Kirjaustyyppi", "Kirjaaja", "Kirjausaika",
           "Aihe", "Salassapitoluokka", "SuostumuksenLaajuus", "Paino", "Pituus"]


def _delivery(root: Path, rows: int = ROWS) -> Path:
    """A semicolon CSV with Finnish spellings, as a vendor might really send it."""
    raw = root / "delivery"
    raw.mkdir(parents=True, exist_ok=True)
    lines = [COLUMNS]
    for index in range(1, rows + 1):
        lines.append(["4", f"H-{1000 + (index % PEOPLE):04d}", f"R-{index}", "MittausTulos", "hoitaja",
                      f"{1 + index % 28:02d}.03.2026", "VerenpaineSeuranta", "julkinen", "hoito",
                      f"7{index % 9},5", f"1 {600 + index % 50},0"])
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=";", lineterminator="\n").writerows(lines)
    (raw / "mittaukset.csv").write_bytes(buffer.getvalue().encode("latin-1"))
    return raw


def _mapping(root: Path, *, name: str = "mapping.json", weight_target: str = "payload.paino_kg",
             height_target: str = "payload.pituus_cm", swap_ids: bool = False,
             numeric: bool = True) -> Path:
    person = "Kirjaustunniste" if swap_ids else "Henkilötunniste"
    record = "Henkilötunniste" if swap_ids else "Kirjaustunniste"
    weight_rule = {"id": "weight", "source_path": "Paino", "target_field": weight_target}
    height_rule = {"id": "height", "source_path": "Pituus", "target_field": height_target}
    if numeric:
        weight_rule["transform"] = "decimal_fi"
        height_rule["transform"] = "decimal_fi"
    document = {
        "version": "preview-test-v1",
        "ignore_source_paths": ["TietueVersio"],
        "valuesets": {
            "record_type": {"MittausTulos": "mittaus"},
            "topic": {"VerenpaineSeuranta": "verenpaine"},
            "sensitivity_tag": {"julkinen": "none"},
            "consent_scope": {"hoito": "treatment"},
        },
        "rules": [
            {"id": "source", "target_field": "source", "transform": "const", "value": "delivery"},
            {"id": "person", "source_path": person, "target_field": "source_person_id"},
            {"id": "record", "source_path": record, "target_field": "record_id"},
            {"id": "type", "source_path": "Kirjaustyyppi", "target_field": "record_type",
             "transform": "lookup_valueset", "valueset": "record_type"},
            {"id": "author", "source_path": "Kirjaaja", "target_field": "authored_by_role"},
            {"id": "when", "source_path": "Kirjausaika", "target_field": "recorded_at",
             "transform": "date"},
            {"id": "topic", "source_path": "Aihe", "target_field": "topic",
             "transform": "lookup_valueset", "valueset": "topic"},
            {"id": "sensitivity", "source_path": "Salassapitoluokka", "target_field": "sensitivity_tag",
             "transform": "lookup_valueset", "valueset": "sensitivity_tag"},
            {"id": "consent", "source_path": "SuostumuksenLaajuus", "target_field": "consent_scope",
             "transform": "lookup_valueset", "valueset": "consent_scope"},
            weight_rule, height_rule,
        ],
    }
    path = root / name
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def _line(text: str, needle: str) -> str:
    return next(line for line in text.splitlines() if needle in line)


# --- the risk itself --------------------------------------------------------------------------------

def test_a_wrong_but_matching_mapping_imports_cleanly_and_no_check_can_see_it(tmp_path: Path):
    """The author's biggest stated risk, asserted instead of described: every rule matches, the import
    succeeds, and the only thing that can notice the swap is a human reading the preview."""
    raw = _delivery(tmp_path)
    wrong = _mapping(tmp_path, name="wrong.json", weight_target="payload.pituus_cm",
                     height_target="payload.paino_kg")
    report = import_delivery(raw, tmp_path / "import", mapping=wrong)
    assert report.records == ROWS, "a plausible-but-wrong mapping matches every rule and imports cleanly"

    preview = preview_delivery(raw, wrong, sample=3, now=NOW)
    assert preview.refuses() is False, "no mechanical check can see a weight/height swap; that is the point"
    # ... and the preview is what a human reads: the source COLUMN name sits beside the target FIELD name.
    weight_line = _line(preview.text, "-> payload.pituus_cm")
    assert "Paino" in weight_line, "the swapped pairing is visible with both names on one line"
    assert ",5" in weight_line and "=> " in weight_line and "[decimal_fi]" in weight_line, (
        "the Finnish comma value and the real number it became are beside each other")


def test_the_preview_refuses_a_person_id_column_that_is_unique_per_record(tmp_path: Path):
    """The mechanical half: a real column in the wrong meaning that IS detectable, refused by name."""
    raw = _delivery(tmp_path)
    swapped = _mapping(tmp_path, name="swapped.json", swap_ids=True)
    preview = preview_delivery(raw, swapped, sample=2, now=NOW, expected_profiles=PEOPLE)

    assert preview.refuses() is True
    cardinality = [finding for finding in preview.findings
                   if finding["check"] == "source_person_id_cardinality"]
    assert cardinality, "one distinct person per record must be found"
    assert any(finding["severity"] == "refusal" for finding in cardinality)
    assert "one distinct value per RECORD" in cardinality[0]["summary"]
    assert str(ROWS) in cardinality[0]["summary"], "the finding names the numbers it saw"
    assert f"more than the {PEOPLE} profiles" in " ".join(f["summary"] for f in cardinality)


def test_the_preview_imports_nothing_and_says_so(tmp_path: Path):
    raw = _delivery(tmp_path)
    report = preview_delivery(raw, _mapping(tmp_path), sample=2, now=NOW)

    assert report.document["imports_records"] is False
    assert report.document["rows_mapped"] == ROWS
    assert not (tmp_path / "import").exists()
    assert not (tmp_path / "index.sqlite").exists()
    assert not (tmp_path / "people").exists()


# --- what a human sees ------------------------------------------------------------------------------

def test_every_rule_is_shown_with_its_source_value_and_the_value_it_produced(tmp_path: Path):
    raw = _delivery(tmp_path)
    report = preview_delivery(raw, _mapping(tmp_path), sample=2, now=NOW)
    record = report.document["records"][0]
    pairs = {(entry["source_path"], entry["target_field"]): entry for entry in record["fields"]}

    translated = pairs[("Kirjaustyyppi", "record_type")]
    assert translated["source_value"] == "MittausTulos" and translated["mapped_value"] == "mittaus"
    assert translated["changed"] is True and translated["transform"] == "lookup_valueset"
    dated = pairs[("Kirjausaika", "recorded_at")]
    assert dated["changed"] is True and dated["mapped_value"].startswith("2026-03-")
    number = pairs[("Paino", "payload.paino_kg")]
    assert isinstance(number["mapped_value"], float) and number["changed"] is True
    assert isinstance(number["source_value"], str) and "," in number["source_value"]
    assert pairs[("Henkilötunniste", "source_person_id")]["changed"] is False
    assert pairs[(None, "source")]["source_value"] == "delivery"
    # The rendered checklist carries the same pairing, with the transform named, for a non-programmer.
    assert "=> mittaus   [lookup_valueset]" in report.text
    assert "READ THIS FIRST" in report.text
    assert HEURISTIC_NOTE in report.text
    assert "This step imported nothing" in report.text


def test_the_sample_is_spread_over_the_scan_rather_than_the_first_rows(tmp_path: Path):
    raw = _delivery(tmp_path)
    report = preview_delivery(raw, _mapping(tmp_path), sample=4, now=NOW)
    numbers = sorted(int(record["locator"].split(":")[1]) for record in report.document["records"])
    assert len(set(numbers)) == 4
    assert max(numbers) > 10, "a reservoir sample must not be the head of the file"


def test_a_partial_scan_says_it_is_partial(tmp_path: Path):
    raw = _delivery(tmp_path)
    report = preview_delivery(raw, _mapping(tmp_path), sample=2, scan_limit=60, now=NOW)
    assert report.document["complete_scan"] is False
    assert report.document["rows_scanned"] == 60
    assert "PARTIAL SCAN" in report.text


def test_the_preview_writes_its_json_and_text_checklist(tmp_path: Path):
    raw = _delivery(tmp_path)
    report = preview_delivery(raw, _mapping(tmp_path), sample=2, now=NOW)
    json_path, text_path = write_preview(report, tmp_path / "out")

    document = json.loads(json_path.read_text(encoding="utf-8"))
    assert document["kind"] == "delivery-mapping-preview"
    assert document["sanity"]["kind"] == "delivery-sanity-checks"
    assert len(document["records"]) == 2
    assert text_path.read_text(encoding="utf-8") == report.text
    assert json_path == tmp_path / "out" / "results" / "delivery-mapping-preview.json"


def test_the_preview_cli_refuses_a_wrong_mapping_and_passes_a_clean_one(tmp_path: Path, capsys):
    raw = _delivery(tmp_path)
    good = _mapping(tmp_path)
    status = reconcile_delivery.main(["preview", str(raw), "--mapping", str(good),
                                      "--sample", "2", "--to", str(tmp_path / "out")])
    output = capsys.readouterr().out
    assert status == 0, "a clean preview exits 0 and still says the checks are not a proof"
    assert "not a proof the mapping is right" in output
    assert (tmp_path / "out" / "results" / "delivery-mapping-preview.txt").is_file()

    swapped = _mapping(tmp_path, name="swapped.json", swap_ids=True)
    status = reconcile_delivery.main(["preview", str(raw), "--mapping", str(swapped), "--sample", "2",
                                      "--profiles", str(PEOPLE)])
    captured = capsys.readouterr()
    assert status == 1
    assert "REFUSE this mapping's output" in captured.err
    assert "one distinct value per RECORD" in captured.out


# --- the mechanical checks, each naming what it saw --------------------------------------------------

def _canonical(index: int, **overrides) -> dict:
    row = {"record_id": f"R-{index}", "source_person_id": f"H-{index % 10}", "record_type": "mittaus",
           "authored_by_role": "hoitaja", "recorded_at": f"2026-03-{1 + index % 28:02d}T08:00:00+00:00",
           "topic": "verenpaine", "sensitivity_tag": "none", "consent_scope": "treatment",
           "payload": {"paino_kg": 72.5, "syke": 70}}
    row.update(overrides)
    return row


def test_the_timestamp_window_check_names_the_values_and_its_own_weakness():
    accumulator = SanityAccumulator(now=NOW)
    for index in range(60):
        accumulator.observe(_canonical(index, recorded_at=f"1889-01-{1 + index % 28:02d}T00:00:00+00:00"),
                            f"rows.csv:{index}")
    finding = next(f for f in accumulator.findings() if f.check == "timestamp_window")
    assert finding.severity == "refusal"
    assert "1889" in finding.summary and "60" in finding.summary
    assert "mis-mapped column" in finding.heuristic, "the check says it cannot tell a bad row from a bad column"
    assert finding.detail["out_of_window"] == 60


def test_the_empty_required_field_check_reads_the_refusals_by_field_name():
    accumulator = SanityAccumulator(now=NOW)
    for index in range(60):
        accumulator.observe(_canonical(index), f"rows.csv:{index}")
        accumulator.refuse("missing_required_target",
                           "row supplies no value for required envelope field(s) ['consent_scope']",
                           f"rows.csv:{index}", {"missing": ["consent_scope"]})
    findings = accumulator.findings()
    coverage = next(f for f in findings if f.check == "required_field_coverage")
    assert coverage.severity == "refusal"
    assert "consent_scope" in coverage.summary and "60 of 60" in coverage.summary
    refusals = next(f for f in findings if f.check == "mapping_refusals")
    assert refusals.severity == "refusal" and "consent_scope" in refusals.summary


def test_the_dominance_check_refuses_one_record_id_on_every_row():
    accumulator = SanityAccumulator(now=NOW)
    for index in range(60):
        accumulator.observe(_canonical(index, record_id="SAME"), f"rows.csv:{index}")
    finding = next(f for f in accumulator.findings() if f.check == "single_value_dominance")
    assert finding.severity == "refusal" and "'SAME'" in finding.summary
    assert "cannot identify a record" in finding.summary
    assert "many distinct values cannot be dominated" in finding.heuristic


def test_the_code_or_prose_check_warns_in_both_directions():
    accumulator = SanityAccumulator(now=NOW)
    for index in range(60):
        accumulator.observe(_canonical(index, topic="potilas kertoi voivansa aiempaa paremmin tällä viikolla",
                                       payload={"teksti": f"F3{index % 9}.1"}), f"rows.csv:{index}")
    findings = {finding.check + ":" + finding.detail.get("field", "") for finding in accumulator.findings()}
    assert "code_or_prose:topic" in findings, "a vocabulary field carrying prose is a swapped pair"
    assert "code_or_prose:payload.teksti" in findings, "a free-text field carrying codes is a swapped pair"
    for finding in accumulator.findings():
        assert finding.heuristic, "every finding explains why it is only a heuristic"


def test_the_numeric_passthrough_check_points_at_the_transform_that_fixes_it():
    accumulator = SanityAccumulator(now=NOW)
    for index in range(60):
        accumulator.observe(_canonical(index, payload={"paino_kg": f"7{index % 9},5"}), f"rows.csv:{index}")
    finding = next(f for f in accumulator.findings() if f.check == "numeric_passthrough")
    assert finding.severity == "warning"
    assert "'payload.paino_kg'" in finding.summary
    assert "'7" in finding.summary, "the finding quotes the string it saw"
    assert finding.detail["fix"] == {"transform": "decimal_fi"}
    assert "judgement a human must make" in finding.heuristic


def test_the_cardinality_check_compares_against_the_declared_profile_count():
    accumulator = SanityAccumulator(expected_profiles=5, now=NOW)
    for index in range(60):
        accumulator.observe(_canonical(index, source_person_id=f"P-{index}"), f"rows.csv:{index}")
    findings = [finding for finding in accumulator.findings()
                if finding.check == "source_person_id_cardinality"]
    assert len(findings) == 2, "one impossible shape, one disagreement with the declared count"
    assert all(finding.severity == "refusal" for finding in findings)


def test_a_clean_corpus_produces_no_findings_and_the_report_still_disclaims_itself():
    accumulator = SanityAccumulator(now=NOW)
    for index in range(200):
        accumulator.observe(_canonical(index), f"rows.csv:{index}")
    document = accumulator.as_dict()
    assert document["findings"] == []
    assert document["note"] == HEURISTIC_NOTE
    assert "not a proof" in document["note"]


def test_the_import_summary_carries_the_checks(tmp_path: Path):
    """A run is self-describing: the import writes and prints what its own output looked like."""
    raw = _delivery(tmp_path)
    report = import_delivery(raw, tmp_path / "import", mapping=_mapping(tmp_path, numeric=False))
    document = json.loads(report.sanity_report.read_text(encoding="utf-8"))
    checks = {finding["check"] for finding in document["findings"]}
    assert "numeric_passthrough" in checks, "the unmapped decimal comma is reported by the run itself"
    assert document["rows_examined"] == ROWS
    assert document["note"] == HEURISTIC_NOTE


def test_the_seeded_mapping_reports_optional_rules_it_did_not_match(tmp_path: Path):
    """An optional rule that matched nothing is reported by name, never silently dropped."""
    from app.mapping import MappingSpec

    seeded = MappingSpec.load(Path(__file__).resolve().parents[1] / "mappings" / "delivery-mapping-v1.json")
    document = seeded.dry_run([{"Lähdehenkilötunniste": "H-1", "Kirjaustunniste": "R-1",
                                "Kirjaustyyppi": "Laakemaarays", "Kirjaaja": "laakari",
                                "Kirjausaika": "13.10.2026", "Aihe": "Laakitys",
                                "Salassapitoluokka": "salainen", "SuostumuksenLaajuus": "hoito",
                                "Sisältö": {"lääke": "Amlodipin"}, "TietueVersio": "3"}])
    assert "weight" in document["optional_rules_matched_nothing"]
    # No optional measurement rule is ever listed as a typo, whatever the delivery spelled its columns.
    assert not [rule for rule in document["rules_matched_nothing"]
                if rule in {"weight", "weight-alt", "height", "height-alt", "pulse", "pulse-alt"}]


def test_a_preview_needs_a_sample(tmp_path: Path):
    with pytest.raises(ValueError, match="at least 1"):
        preview_delivery(_delivery(tmp_path), _mapping(tmp_path), sample=0)
