"""The arrival-day import: a raw delivery of ANY accepted layout, mapped before the envelope is bound.

The gap this closes: `import_delivery` used to require `profiles.csv` + `records.jsonl` and Python-level
aliases, so an unknown vendor shape had to be turned into canonical rows by hand. These tests drive the
declarative mapping into the import path and assert BOTH halves of what that buys:

* the three layouts the task names (one wide CSV, one JSONL, one JSON array) plus one-file-per-person,
  nested directories and a zip, all imported with no Python change; and
* every failure staying loud and specific -- an unknown vendor column, a value-set miss, a missing rule for
  a required permission axis, a rule that matched nothing, and a layout the mapping cannot express.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from app import delivery, source
from app.delivery import DeliveryShapeError, DeliveryIndex, import_delivery
from app.mapping import MappingError

VENDOR_HEADER = ("TietueVersio", "Henkilötunniste", "Kirjaustunniste", "Kirjaustyyppi", "Kirjaaja",
                 "Kirjausaika", "Aihe", "Salassapitoluokka", "SuostumuksenLaajuus", "Sisältö")
RECORD_TYPE_SET = {"VapaaTeksti": "vapaateksti", "Laakemaarays": "lääkitys"}
TOPIC_SET = {"Yleiskirjaus": "hoito", "Laakitys": "lääkitys"}
SENSITIVITY_SET = {"julkinen": "none", "salainen": "sensitive"}
CONSENT_SET = {"hoito": "treatment"}


def _mapping_document(csv_payload: bool) -> dict:
    """The mapping a human writes from the shape report, for a long/narrow vendor CSV (or a wide one)."""
    rules = [
        {"id": "source", "target_field": "source", "transform": "const", "value": "delivery"},
        {"id": "person", "source_path": "Henkilötunniste", "target_field": "source_person_id"},
        {"id": "record-id", "source_path": "Kirjaustunniste", "target_field": "record_id"},
        {"id": "record-type", "source_path": "Kirjaustyyppi", "target_field": "record_type",
         "transform": "lookup_valueset", "valueset": "record_type"},
        {"id": "author", "source_path": "Kirjaaja", "target_field": "authored_by_role"},
        {"id": "recorded-at", "source_path": "Kirjausaika", "target_field": "recorded_at",
         "transform": "date"},
        {"id": "topic", "source_path": "Aihe", "target_field": "topic",
         "transform": "lookup_valueset", "valueset": "topic"},
        {"id": "sensitivity", "source_path": "Salassapitoluokka", "target_field": "sensitivity_tag",
         "transform": "lookup_valueset", "valueset": "sensitivity_tag"},
        {"id": "consent", "source_path": "SuostumuksenLaajuus", "target_field": "consent_scope",
         "transform": "lookup_valueset", "valueset": "consent_scope"},
    ]
    if csv_payload:
        rules.append({"id": "payload", "source_path": "Sisältö", "target_field": "payload"})
    return {
        "version": "test-mapping-v1",
        "ignore_source_paths": ["TietueVersio"],
        "valuesets": {"record_type": dict(RECORD_TYPE_SET), "topic": dict(TOPIC_SET),
                      "sensitivity_tag": dict(SENSITIVITY_SET), "consent_scope": dict(CONSENT_SET)},
        "rules": rules,
    }


def _write_mapping(tmp_path: Path, document: dict, name: str = "mapping.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _csv_bytes(rows: list[list[str]], delimiter: str = ";", encoding: str = "utf-8") -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\n")
    writer.writerows(rows)
    return buffer.getvalue().encode(encoding)


def _vendor_row(record_id: str, person: str, record_type: str, topic: str, payload: dict,
                when: str = "13.10.2026", sensitivity: str = "julkinen",
                consent: str = "hoito") -> list[str]:
    return ["4", person, record_id, record_type, "laakari", when, topic, sensitivity, consent,
            json.dumps(payload, ensure_ascii=False)]


def _long_csv(tmp_path: Path, name: str = "kirjaukset.csv", encoding: str = "latin-1") -> Path:
    source_dir = tmp_path / "delivery"
    source_dir.mkdir(parents=True, exist_ok=True)
    rows = [list(VENDOR_HEADER),
            _vendor_row("R-1", "H-1", "VapaaTeksti", "Yleiskirjaus", {"teksti": "Yrjö voi hyvin"}),
            _vendor_row("R-2", "H-1", "Laakemaarays", "Laakitys", {"lääke": "Sertraliini"},
                        sensitivity="salainen"),
            _vendor_row("R-3", "H-2", "VapaaTeksti", "Yleiskirjaus", {"teksti": "Ääni kuului hyvin"})]
    (source_dir / name).write_bytes(_csv_bytes(rows, encoding=encoding))
    return source_dir


def _loaded(out: Path) -> dict[str, dict]:
    index = DeliveryIndex(out)
    try:
        return {record.record_id: record
                for persona_id in index for record in index.persona(persona_id).records}
    finally:
        index.close()


# --- 1. layouts the mapping must express -------------------------------------------------------------


def test_a_semicolon_latin1_csv_with_finnish_dates_imports_through_a_mapping(tmp_path: Path):
    """The rehearsal shape in miniature: `;`, Latin-1 `ä`/`ö`, `DD.MM.YYYY`, vendor vocabulary."""
    source_dir = _long_csv(tmp_path)
    mapping = _write_mapping(tmp_path, _mapping_document(csv_payload=True))

    report = import_delivery(source_dir, tmp_path / "out", mapping=mapping)

    assert (report.personas, report.records, report.encounters) == (2, 3, 0)
    assert report.mapping_version == "test-mapping-v1" and report.mapping_path == mapping
    records = _loaded(tmp_path / "out")
    # The vendor's spelling was translated BEFORE binding: a canonical type and topic, not a refusal.
    assert records["R-2"].record_type == source.MEDICATION and records["R-2"].topic == "lääkitys"
    assert records["R-2"].sensitivity_tag == "sensitive" and records["R-2"].consent_scope == "treatment"
    assert records["R-1"].recorded_at.isoformat().startswith("2026-10-13")
    # Latin-1 `ä`/`ö` crossed the whole path without mojibake.
    assert records["R-1"].payload["teksti"] == "Yrjö voi hyvin"
    assert records["R-3"].payload["teksti"] == "Ääni kuului hyvin"


def test_a_single_json_array_imports_and_streams_element_by_element(tmp_path: Path):
    source_dir = tmp_path / "delivery"
    source_dir.mkdir()
    elements = [
        {"Henkilötunniste": "H-1", "Kirjaustunniste": "R-1", "Kirjaustyyppi": "VapaaTeksti",
         "Kirjaaja": "laakari", "Kirjausaika": "2026-10-13T08:30:00Z", "Aihe": "Yleiskirjaus",
         "Salassapitoluokka": "julkinen", "SuostumuksenLaajuus": "hoito",
         "Sisältö": {"teksti": "Yrjö voi hyvin"}, "TietueVersio": 4},
        {"Henkilötunniste": "H-2", "Kirjaustunniste": "R-2", "Kirjaustyyppi": "Laakemaarays",
         "Kirjaaja": "laakari", "Kirjausaika": "2026-10-14", "Aihe": "Laakitys",
         "Salassapitoluokka": "salainen", "SuostumuksenLaajuus": "hoito",
         "Sisältö": {"lääke": "Metformiini"}, "TietueVersio": 4},
    ]
    (source_dir / "kaikki.json").write_text(json.dumps(elements, ensure_ascii=False), encoding="utf-8")
    mapping = _write_mapping(tmp_path, _mapping_document(csv_payload=True))

    report = import_delivery(source_dir, tmp_path / "out", mapping=mapping)

    assert report.records == 2
    records = _loaded(tmp_path / "out")
    assert records["R-2"].record_type == source.MEDICATION
    assert records["R-1"].recorded_at.isoformat().startswith("2026-10-13")


def test_one_json_object_per_line_with_a_nested_payload_imports(tmp_path: Path):
    source_dir = tmp_path / "delivery"
    source_dir.mkdir()
    lines = [
        {"Henkilötunniste": "H-1", "Kirjaustunniste": "R-1", "Kirjaustyyppi": "VapaaTeksti",
         "Kirjaaja": "laakari", "Kirjausaika": "2026-10-13T08:30:00Z", "Aihe": "Yleiskirjaus",
         "Salassapitoluokka": "julkinen", "SuostumuksenLaajuus": "hoito",
         "Sisältö": {"teksti": "Yrjö voi hyvin"}},
        {"Henkilötunniste": "H-1", "Kirjaustunniste": "R-2", "Kirjaustyyppi": "Laakemaarays",
         "Kirjaaja": "laakari", "Kirjausaika": 1791900000000, "Aihe": "Laakitys",
         "Salassapitoluokka": "salainen", "SuostumuksenLaajuus": "hoito",
         "Sisältö": {"lääke": "Sertraliini"}},
    ]
    (source_dir / "records.jsonl").write_text(
        "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), encoding="utf-8")
    mapping = _write_mapping(tmp_path, _mapping_document(csv_payload=True))

    report = import_delivery(source_dir, tmp_path / "out", mapping=mapping)

    assert report.records == 2 and report.personas == 1
    records = _loaded(tmp_path / "out")
    # An epoch-millisecond timestamp and an ISO one both bind; one `date` rule, one parser.
    assert records["R-2"].recorded_at.isoformat().startswith("2026-10-13")
    assert source.DIAGNOSIS not in {record.record_type for record in records.values()}


def test_a_wide_csv_maps_payload_columns_through_nested_targets(tmp_path: Path):
    """A WIDE vendor row is one record: payload as columns, Finnish decimal commas included."""
    source_dir = tmp_path / "delivery"
    source_dir.mkdir()
    header = ["TietueVersio", "Henkilötunniste", "Kirjaustunniste", "Kirjaustyyppi", "Kirjaaja",
              "Kirjausaika", "Aihe", "Salassapitoluokka", "SuostumuksenLaajuus", "Paino", "Pituus",
              "Verenpaine"]
    rows = [header,
            ["4", "H-1", "R-1", "MittausTulos", "hoitaja", "13.10.2026", "Verenpaine", "julkinen",
             "hoito", "72,5", "168,0", "128/78"],
            ["4", "H-2", "R-2", "MittausTulos", "hoitaja", "13.10.2026", "Verenpaine", "julkinen",
             "hoito", "81,0", "175,5", "135/85"]]
    (source_dir / "mittaukset.csv").write_bytes(_csv_bytes(rows, encoding="latin-1"))
    document = _mapping_document(csv_payload=False)
    document["valuesets"]["record_type"]["MittausTulos"] = "mittaus"
    document["valuesets"]["topic"]["Verenpaine"] = "verenpaine"
    document["rules"].extend([
        {"id": "weight", "source_path": "Paino", "target_field": "payload.paino_kg"},
        {"id": "height", "source_path": "Pituus", "target_field": "payload.pituus_cm"},
        {"id": "pressure", "source_path": "Verenpaine", "target_field": "payload.verenpaine"},
    ])
    mapping = _write_mapping(tmp_path, document)

    report = import_delivery(source_dir, tmp_path / "out", mapping=mapping)

    assert report.records == 2
    records = _loaded(tmp_path / "out")
    assert records["R-1"].record_type == source.MEASUREMENT
    assert records["R-1"].payload == {"paino_kg": "72,5", "pituus_cm": "168,0", "verenpaine": "128/78"}
    # The decimal comma is the vendor's spelling of a number and is passed through unchanged: the mapping
    # has no numeric transform, and silently rewriting it would be a guess about the unit and precision.
    assert records["R-2"].payload["paino_kg"] == "81,0"


def test_one_file_per_person_in_nested_directories_imports(tmp_path: Path):
    source_dir = tmp_path / "delivery"
    (source_dir / "people" / "deep").mkdir(parents=True)
    for name, person, record_id in (("a.jsonl", "H-1", "R-1"), ("deep/b.jsonl", "H-2", "R-2")):
        row = {"Henkilötunniste": person, "Kirjaustunniste": record_id, "Kirjaustyyppi": "VapaaTeksti",
               "Kirjaaja": "laakari", "Kirjausaika": "2026-10-13", "Aihe": "Yleiskirjaus",
               "Salassapitoluokka": "julkinen", "SuostumuksenLaajuus": "hoito",
               "Sisältö": {"teksti": "Yrjö voi hyvin"}}
        (source_dir / "people" / name).write_text(json.dumps(row, ensure_ascii=False) + "\n",
                                                  encoding="utf-8")
    mapping = _write_mapping(tmp_path, _mapping_document(csv_payload=True))

    report = import_delivery(source_dir, tmp_path / "out", mapping=mapping)

    assert (report.personas, report.records) == (2, 2)
    assert set(_loaded(tmp_path / "out")) == {"R-1", "R-2"}


def test_a_zip_of_one_csv_imports_from_the_landing_zone(tmp_path: Path):
    """The whole arrival path: land the bytes, then import the LANDING ZONE through the mapping."""
    import zipfile

    from app.landing import land_delivery

    source_dir = _long_csv(tmp_path / "raw")
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.write(source_dir / "kirjaukset.csv", "kirjaukset.csv")
    landing = tmp_path / "landing"
    land_delivery(archive, landing)
    mapping = _write_mapping(tmp_path, _mapping_document(csv_payload=True))

    report = import_delivery(landing, tmp_path / "out", mapping=mapping)

    assert report.records == 3
    assert set(_loaded(tmp_path / "out")) == {"R-1", "R-2", "R-3"}


def test_the_canonical_path_is_untouched_when_no_mapping_is_given(tmp_path: Path):
    """Preserving the existing behaviour is part of the task: no mapping means the old contract exactly."""
    source_dir = _long_csv(tmp_path)
    with pytest.raises(DeliveryShapeError, match="delivery needs profiles.csv"):
        import_delivery(source_dir, tmp_path / "out")
    assert not (tmp_path / "out" / "index.sqlite").exists()


def test_a_file_the_mapping_ignores_is_skipped_and_a_file_that_yields_nothing_is_refused(tmp_path: Path):
    """A vendor profile file beside the records is declared, never silently dropped."""
    source_dir = _long_csv(tmp_path)
    (source_dir / "ihmiset.csv").write_text("Henkilötunniste;Nimi\nH-1;Yrjö\n", encoding="latin-1")
    document = _mapping_document(csv_payload=True)
    mapping = _write_mapping(tmp_path, document)

    # Not ignored: the profile file's columns are unknown to the mapping, so the import refuses by name.
    with pytest.raises(DeliveryShapeError, match="unknown source path"):
        import_delivery(source_dir, tmp_path / "strict", mapping=mapping)

    document["ignore_source_files"] = ["ihmiset.csv"]
    ignored = _write_mapping(tmp_path, document, name="mapping-ignored.json")
    report = import_delivery(source_dir, tmp_path / "ignored", mapping=ignored)
    assert report.records == 3

    # An EMPTY file is not skipped by omission: it names itself unless the mapping ignores it.
    (source_dir / "ihmiset.csv").unlink()
    document.pop("ignore_source_files")
    (source_dir / "tyhja.csv").write_text("", encoding="utf-8")
    empty_mapping = _write_mapping(tmp_path, document, name="mapping-empty.json")
    with pytest.raises(DeliveryShapeError, match="tyhja.csv") as error:
        import_delivery(source_dir, tmp_path / "empty", mapping=empty_mapping)
    assert "unsupported_layout" in error.value.code


# --- 2. loud, specific failures ---------------------------------------------------------------------


def test_a_mapping_without_a_rule_for_a_required_axis_is_refused_before_any_row(tmp_path: Path):
    source_dir = _long_csv(tmp_path)
    document = _mapping_document(csv_payload=True)
    document["rules"] = [rule for rule in document["rules"]
                         if rule["target_field"] != "sensitivity_tag"]
    mapping = _write_mapping(tmp_path, document)

    with pytest.raises(MappingError, match="sensitivity_tag") as error:
        import_delivery(source_dir, tmp_path / "out", mapping=mapping)
    assert "no rule for required envelope field" in str(error.value)
    # Nothing was read or written: the refusal is a property of the mapping FILE.
    assert not (tmp_path / "out" / "index.sqlite").exists()


def test_a_row_whose_permission_axis_cell_is_empty_is_refused_by_field_name(tmp_path: Path):
    """A rule that exists but supplies nothing for a row must not become a defaulted permission axis."""
    source_dir = _long_csv(tmp_path)
    document = _mapping_document(csv_payload=True)
    mapping = _write_mapping(tmp_path, document)

    with pytest.raises(DeliveryShapeError, match="required envelope field") as error:
        # Rewrite the sensitivity cell of the second row as empty.
        path = source_dir / "kirjaukset.csv"
        rows = list(csv.reader(io.StringIO(path.read_bytes().decode("latin-1")), delimiter=";"))
        rows[2][7] = ""
        path.write_bytes(_csv_bytes(rows, encoding="latin-1"))
        import_delivery(source_dir, tmp_path / "out", mapping=mapping)
    assert "sensitivity_tag" in str(error.value) and error.value.code == "missing_required_target"


def test_a_lookup_miss_names_the_offending_value_and_the_known_set(tmp_path: Path):
    source_dir = _long_csv(tmp_path)
    document = _mapping_document(csv_payload=True)
    document["valuesets"]["record_type"].pop("Laakemaarays")
    mapping = _write_mapping(tmp_path, document)

    with pytest.raises(DeliveryShapeError, match="not in valueset") as error:
        import_delivery(source_dir, tmp_path / "out", mapping=mapping)
    assert "Laakemaarays" in str(error.value)
    assert "VapaaTeksti" in str(error.value), "the known set is the mapping instruction"
    assert error.value.code == "mapping_refused"


def test_a_rule_pointing_at_a_source_path_that_exists_nowhere_is_refused(tmp_path: Path):
    source_dir = _long_csv(tmp_path)
    document = _mapping_document(csv_payload=True)
    document["rules"].append({"id": "note", "source_path": "MuistiinpanoPuuttuu",
                              "target_field": "heading"})
    mapping = _write_mapping(tmp_path, document)

    with pytest.raises(DeliveryShapeError, match="matched no row") as error:
        import_delivery(source_dir, tmp_path / "out", mapping=mapping)
    assert "MuistiinpanoPuuttuu" in str(error.value) and error.value.code == "rules_matched_nothing"


def test_a_rule_that_matched_nothing_because_a_sibling_matched_is_not_refused(tmp_path: Path):
    """The vendor spelling is provisional, so two rules may target one field; the loser is not a typo."""
    source_dir = _long_csv(tmp_path)
    document = _mapping_document(csv_payload=True)
    document["rules"].append({"id": "person-alt", "source_path": "HenkilötunnusVaihtoehtoinen",
                              "target_field": "source_person_id"})
    mapping = _write_mapping(tmp_path, document)

    report = import_delivery(source_dir, tmp_path / "out", mapping=mapping)

    assert report.records == 3


def test_a_layout_the_mapping_cannot_express_is_refused_naming_the_file_and_format(tmp_path: Path):
    source_dir = tmp_path / "delivery"
    source_dir.mkdir()
    (source_dir / "muistiinpanot.txt").write_text("Yrjö voi hyvin\nEi erottimia täällä\n",
                                                  encoding="utf-8")
    mapping = _write_mapping(tmp_path, _mapping_document(csv_payload=True))

    with pytest.raises(DeliveryShapeError) as error:
        import_delivery(source_dir, tmp_path / "out", mapping=mapping)
    assert "muistiinpanot.txt" in str(error.value) and "unparsed" in str(error.value)
    assert error.value.code == "unsupported_layout"

    (source_dir / "muistiinpanot.txt").unlink()
    (source_dir / "binaari.bin").write_bytes(bytes(range(256)) * 4)
    with pytest.raises(DeliveryShapeError) as binary_error:
        import_delivery(source_dir, tmp_path / "out2", mapping=mapping)
    assert "binaari.bin" in str(binary_error.value) and "binary" in str(binary_error.value)


# --- 3. quarantine and retry in mapping mode --------------------------------------------------------


def test_quarantine_keeps_the_good_rows_and_the_raw_vendor_row_for_a_retry(tmp_path: Path):
    """One bad vendor value must not cost the other rows, and the stored row must be re-attemptable."""
    source_dir = _long_csv(tmp_path)
    rows = list(csv.reader(io.StringIO((source_dir / "kirjaukset.csv").read_bytes().decode("latin-1")),
                           delimiter=";"))
    rows[1][6] = "TuntematonAihe"  # a topic spelling the value set does not declare
    (source_dir / "kirjaukset.csv").write_bytes(_csv_bytes(rows, encoding="latin-1"))
    mapping = _write_mapping(tmp_path, _mapping_document(csv_payload=True))

    report = import_delivery(source_dir, tmp_path / "out", on_error="quarantine", mapping=mapping)

    assert report.records == 2 and report.quarantined == 1
    assert report.mapping_version == "test-mapping-v1"
    entries = [json.loads(line) for line in report.quarantine_path.read_text(encoding="utf-8").splitlines()]
    assert entries[0]["reason_code"] == "mapping_refused"
    assert entries[0]["raw"]["Kirjaustunniste"] == "R-1", "the RAW vendor row must be kept"
    assert set(_loaded(tmp_path / "out")) == {"R-2", "R-3"}

    # A mapping revision that declares the missing spelling recovers the row without re-reading the delivery.
    revised = _mapping_document(csv_payload=True)
    revised["valuesets"]["topic"]["TuntematonAihe"] = "hoito"
    revised_path = _write_mapping(tmp_path, revised, name="mapping-revised.json")
    retry = delivery.retry_quarantine(report.quarantine_path, tmp_path / "out", mapping=revised_path)

    assert (retry.attempted, retry.recovered, retry.remaining) == (1, 1, 0)
    assert set(_loaded(tmp_path / "out")) == {"R-1", "R-2", "R-3"}

    # And a retry WITHOUT the mapping cannot use a raw vendor row: it says so instead of half-binding it.
    report2 = import_delivery(source_dir, tmp_path / "out2", on_error="quarantine", mapping=mapping)
    plain = delivery.retry_quarantine(report2.quarantine_path, tmp_path / "out2")
    assert plain.recovered == 0 and plain.remaining == 1


def test_the_import_cli_maps_a_raw_delivery_and_reports_the_mapping(tmp_path: Path, capsys):
    from scripts import reconcile_delivery

    source_dir = _long_csv(tmp_path)
    mapping = _write_mapping(tmp_path, _mapping_document(csv_payload=True))

    status = reconcile_delivery.main(["import", str(source_dir), "--out", str(tmp_path / "cli"),
                                      "--mapping", str(mapping)])

    assert status == 0
    output = capsys.readouterr().out
    assert "test-mapping-v1" in output and "personas:   2" in output
    assert set(_loaded(tmp_path / "cli")) == {"R-1", "R-2", "R-3"}


def test_the_import_cli_refuses_a_wrong_mapping_with_a_readable_message(tmp_path: Path, capsys):
    from scripts import reconcile_delivery

    source_dir = _long_csv(tmp_path)
    document = _mapping_document(csv_payload=True)
    document["rules"] = [rule for rule in document["rules"] if rule["target_field"] != "consent_scope"]
    mapping = _write_mapping(tmp_path, document)

    status = reconcile_delivery.main(["import", str(source_dir), "--out", str(tmp_path / "cli"),
                                      "--mapping", str(mapping), "--on-error=quarantine"])

    # Even quarantine cannot rescue a mapping with no rule for a permission axis.
    assert status == 1
    captured = capsys.readouterr().err
    assert "consent_scope" in captured and "no rule for required envelope field" in captured
