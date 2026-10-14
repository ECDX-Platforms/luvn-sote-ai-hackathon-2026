"""Land first, parse never: the landing zone, and the shape report that names the vendor's vocabulary."""

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import landing


RECEIVED = datetime(2026, 10, 13, 6, 0, tzinfo=timezone.utc)
# Finnish shape: semicolon delimiter, decimal comma, DD.MM.YYYY, and Latin-1 å/ä/ö that is NOT valid UTF-8.
FINNISH_CSV = (
    "Henkilötunniste;Kirjausaika;Paino;Salassapitoluokka;Aihe;Kirjaus\n"
    "H-1;13.10.2026;72,5;julkinen;Laakitys;Yrjö Ärje voi hyvin\n"
    "H-2;14.10.2026;81,0;salainen;Verenpaine;Ääni kuului hyvin\n"
)


def _land(tmp_path: Path, source: Path, name: str = "landing") -> Path:
    landing_dir = tmp_path / name
    landing.land_delivery(source, landing_dir, received_at=RECEIVED)
    return landing_dir


def test_landing_stores_bytes_verbatim_and_hashes_them(tmp_path: Path):
    source = tmp_path / "delivery"
    source.mkdir()
    raw = FINNISH_CSV.encode("latin-1")
    (source / "henkilot.csv").write_bytes(raw)

    manifest = landing.land_delivery(source, tmp_path / "landing", received_at=RECEIVED)

    assert manifest.files[0].sha256 == hashlib.sha256(raw).hexdigest()
    assert manifest.files[0].bytes == len(raw)
    stored = tmp_path / "landing" / manifest.files[0].path
    assert stored.read_bytes() == raw, "landing must not transcode"
    document = json.loads(manifest.manifest_path.read_text(encoding="utf-8"))
    assert document["kind"] == "delivery-run"
    assert document["received_at"] == RECEIVED.isoformat()
    assert document["manifest_hash"] == manifest.manifest_hash


def test_manifest_hash_is_stable_for_the_same_bytes(tmp_path: Path):
    source = tmp_path / "delivery"
    source.mkdir()
    (source / "a.csv").write_text(FINNISH_CSV, encoding="utf-8")
    (source / "nested").mkdir()
    (source / "nested" / "b.jsonl").write_text('{"a": 1}\n', encoding="utf-8")

    first = landing.land_delivery(source, tmp_path / "one", received_at=RECEIVED)
    second = landing.land_delivery(source, tmp_path / "two", received_at=RECEIVED)
    assert first.manifest_hash == second.manifest_hash
    assert read_source_paths(landing_dir=tmp_path / "two") == ["a.csv", "nested/b.jsonl"]


def read_source_paths(*, landing_dir: Path) -> list[str]:
    document = landing.read_manifest(landing_dir)
    return [entry["source_path"] for entry in document["files"]]


def test_landing_accepts_many_layouts_including_a_zip(tmp_path: Path):
    source = tmp_path / "delivery"
    (source / "per_person").mkdir(parents=True)
    (source / "one.json").write_text(json.dumps([{"id": "H-1"}, {"id": "H-2"}]), encoding="utf-8")
    (source / "per_person" / "p1.json").write_text(json.dumps({"id": "H-1"}), encoding="utf-8")
    (source / "per_person" / "deep").mkdir()
    (source / "per_person" / "deep" / "p2.json").write_text(json.dumps({"id": "H-2"}), encoding="utf-8")
    (source / "records.jsonl").write_text('{"id": "H-1"}\n{"id": "H-2"}\n', encoding="utf-8")
    with zipfile.ZipFile(source / "bundle.zip", "w") as archive:
        archive.writestr("inner.csv", FINNISH_CSV)

    manifest = landing.land_delivery(source, tmp_path / "landing", received_at=RECEIVED)
    assert len(manifest.files) == 5
    assert any(entry.content_type == "application/zip" for entry in manifest.files)
    document = landing.profile_delivery(tmp_path / "landing", tmp_path / "landing")
    names = [entry["source_path"] for entry in document["files"]]
    assert "bundle.zip!inner.csv" in names
    assert "per_person/deep/p2.json" in names


def test_finnish_csv_detection_semicolon_decimal_comma_dates_and_latin1(tmp_path: Path):
    source = tmp_path / "delivery"
    source.mkdir()
    (source / "henkilot.csv").write_bytes(FINNISH_CSV.encode("latin-1"))

    document = landing.profile_delivery(_land(tmp_path, source), tmp_path / "landing")
    entry = document["files"][0]

    assert entry["encoding"] == "latin-1"
    assert entry["delimiter"] == ";", "a Finnish export must not be read as comma-separated"
    assert entry["header_present"] is True
    columns = {column["name"]: column for column in entry["columns"]}
    assert columns["Paino"]["inferred_type"] == "float"
    assert columns["Paino"]["min"] == "72.5" and columns["Paino"]["max"] == "81.0"
    assert columns["Kirjausaika"]["inferred_type"] == "date"
    assert "Yrjö Ärje voi hyvin" in columns["Kirjaus"]["samples"], "Latin-1 å/ä/ö must decode, not mojibake"


def test_utf16_and_utf8_are_decided_by_validity(tmp_path: Path):
    source = tmp_path / "delivery"
    source.mkdir()
    (source / "utf16.csv").write_bytes("a;b\n1;2\n".encode("utf-16"))
    (source / "utf8.csv").write_bytes("a;b\n1;2\n".encode("utf-8"))

    document = landing.profile_delivery(_land(tmp_path, source), tmp_path / "landing")
    encodings = {entry["source_path"]: entry["encoding"] for entry in document["files"]}
    assert encodings == {"utf16.csv": "utf-16", "utf8.csv": "utf-8"}


def test_shape_report_names_candidate_vocabularies_and_roles(tmp_path: Path):
    source = tmp_path / "delivery"
    source.mkdir()
    (source / "records.jsonl").write_text("".join(json.dumps({
        "person_id": f"H-{index}",
        "encounter_id": f"E-{index}",
        "recorded_at": "2026-10-13T08:00:00Z",
        "record_type": "Laakemaarays",
        "icd10": "E11",
        "note": "Pitkä vapaateksti jossa on paljon sisältöä ja kokonaisia lauseita.",
    }, ensure_ascii=False) + "\n" for index in range(6)), encoding="utf-8")

    document = landing.profile_delivery(_land(tmp_path, source), tmp_path / "landing")

    vocabularies = document["candidate_vocabularies"]["records.jsonl"]
    assert [item["value"] for item in vocabularies["record_type"]] == ["Laakemaarays"]
    roles = document["candidate_roles"]["records.jsonl"]
    assert "person_identifier" in roles["person_id"]
    assert "encounter_identifier" in roles["encounter_id"]
    assert "timestamp" in roles["recorded_at"]
    assert "code" in roles["icd10"]
    assert "record_type" in roles["record_type"]
    assert "free_text_note" in roles["note"]
    assert document["files"][0]["layout"] == "wide (one row per person)"


def test_shape_report_reports_long_layout_nested_json_and_shared_keys(tmp_path: Path):
    source = tmp_path / "delivery"
    source.mkdir()
    (source / "events.json").write_text(json.dumps({"events": [
        {"person_id": "H-1", "encounter_id": "E-1", "kind": "x"},
        {"person_id": "H-1", "encounter_id": "E-2", "kind": "y"},
        {"person_id": "H-2", "encounter_id": "E-3", "kind": "x"},
    ]}), encoding="utf-8")
    (source / "people.csv").write_text("person_id,encounter_id\nH-1,E-1\n", encoding="utf-8")

    document = landing.profile_delivery(_land(tmp_path, source), tmp_path / "landing")
    events = next(entry for entry in document["files"] if entry["source_path"] == "events.json")
    assert events["format"] == "json" and events["nested"] is True and events["container"] == "events"
    assert events["layout"] == "long (many rows per person)"
    assert document["shared_keys"], "files sharing person_id/encounter_id must be reported"


def test_read_rows_uses_the_profilers_own_detection(tmp_path: Path):
    source = tmp_path / "delivery"
    source.mkdir()
    (source / "henkilot.csv").write_bytes(FINNISH_CSV.encode("latin-1"))
    rows, meta = landing.read_rows(source / "henkilot.csv")
    assert meta["delimiter"] == ";" and meta["encoding"] == "latin-1"
    assert rows[0]["Henkilötunniste"] == "H-1" and rows[1]["Paino"] == "81,0"


def test_profile_without_a_manifest_refuses(tmp_path: Path):
    import pytest

    with pytest.raises(landing.LandingError, match="land step"):
        landing.profile_delivery(tmp_path / "nowhere", tmp_path)


# --- streaming: bounded memory, estimated cardinality, honest reporting ------------------------------


def _landing_for(tmp_path: Path, source: Path, name: str = "landing") -> Path:
    landing_dir = tmp_path / name
    landing.land_delivery(source, landing_dir, received_at=RECEIVED)
    return landing_dir


def test_the_profiler_streams_a_large_delivery_in_bounded_memory(tmp_path: Path):
    """The ~1 GB peak this replaces was a row list plus a per-column value list.

    Asserted two ways, because "it streams" is otherwise a claim: the profiler's own instrumented counter
    must never hold more than one row at a time, and its measured peak allocation must be far smaller than
    the file it profiled. The file is deliberately larger than every bounded buffer the profiler uses
    (``ENCODING_SAMPLE_BYTES`` is 256 KiB, ``DISTINCT_CAP`` is 200 values, the reservoir is 64), so a
    regression to materialising rows would blow both bounds.
    """
    import tracemalloc

    source = tmp_path / "delivery"
    source.mkdir()
    rows = 120_000
    note = "Pitkä vapaateksti jossa on kokonaisia lauseita ja riittävästi merkkejä."
    with (source / "records.jsonl").open("w", encoding="utf-8") as stream:
        for index in range(rows):
            stream.write(json.dumps({"person_id": f"H-{index}", "encounter_id": f"E-{index}",
                                     "kind": "Mittaus", "note": note}, ensure_ascii=False) + "\n")
    size = (source / "records.jsonl").stat().st_size
    assert size > 8 * 1024 * 1024, f"the file must dwarf the bound, it is {size} bytes"
    landing_dir = _landing_for(tmp_path, source)

    landing.PROFILE_ROWS.reset()
    tracemalloc.start()
    document = landing.profile_delivery(landing_dir, None)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert document["sampling"]["rows_profiled_total"] == rows
    assert document["sampling"]["rows_held_at_once_high_water"] == 1, (
        "the profiler held more than one row: it is materialising again")
    assert peak < 4 * 1024 * 1024, f"peak allocation {peak} bytes for a {size}-byte file"
    entry = document["files"][0]
    assert entry["rows_profiled"] == rows and entry["rows_wrong_field_count"] == 0
    person = next(column for column in entry["columns"] if column["name"] == "person_id")
    # A per-person identifier column overflows the exact set, so its count is an ESTIMATE and says so --
    # and it still detects the wide layout, which a bare ">=200" could not.
    assert person["distinct_count_is_estimate"] is True
    assert person["distinct_lower_bound"] == landing.DISTINCT_CAP
    assert "hyperloglog" in person["distinct_method"]
    assert abs(person["distinct_count_value"] - rows) <= rows * 0.05
    assert entry["layout"] == "wide (one row per person)"
    assert "sampling:" in landing.render_shape_summary(document)


def test_distinct_is_exact_until_the_cap_and_flagged_as_an_estimate_after_it(tmp_path: Path):
    source = tmp_path / "delivery"
    source.mkdir()
    with (source / "records.jsonl").open("w", encoding="utf-8") as stream:
        for repeat in range(4):
            for index in range(5_000):
                stream.write(json.dumps({"koodi": f"V-{index:05d}", "laji": "Mittaus"},
                                        ensure_ascii=False) + "\n")
    document = landing.profile_delivery(_landing_for(tmp_path, source), None)

    columns = {column["name"]: column for column in document["files"][0]["columns"]}
    estimated = columns["koodi"]
    assert estimated["distinct_count_is_estimate"] is True
    assert abs(estimated["distinct_count_value"] - 5_000) <= 5_000 * 0.05
    assert estimated["distinct_count"] == f"~{estimated['distinct_count_value']}"
    assert estimated["samples_method"] == "deterministic-reservoir"
    assert "candidate_vocabulary" not in estimated, "a capped column's vocabulary is not a vocabulary"

    exact = columns["laji"]
    assert exact["distinct_count"] == 1 and exact["distinct_count_is_estimate"] is False
    assert exact["distinct_method"] == "exact" and exact["samples_method"] == "exact-most-frequent"
    assert [item["value"] for item in exact["candidate_vocabulary"]] == ["Mittaus"]
    assert document["sampling"]["distinct_exact_limit"] == landing.DISTINCT_CAP
    assert "hyperloglog" in document["sampling"]["distinct_beyond_limit"]
    # The same column is profiled the same way twice: the reservoir is hash-ordered, not random.
    again = landing.profile_delivery(_landing_for(tmp_path, source, name="landing2"), None)
    assert {column["name"]: column["samples"] for column in again["files"][0]["columns"]} == \
        {column["name"]: column["samples"] for column in document["files"][0]["columns"]}


def test_a_json_array_is_streamed_element_by_element_and_a_truncated_one_is_refused(tmp_path: Path):
    source = tmp_path / "delivery"
    source.mkdir()
    (source / "all.json").write_text(
        "[\n" + ",\n".join(json.dumps({"person_id": f"H-{index}", "kind": "x"}) for index in range(500))
        + "\n]\n", encoding="utf-8")
    produced = []
    for raw, rows in landing.iter_raw_files(source / "all.json"):
        assert raw.format == "json"
        produced = [row for _number, row, error in rows if error is None]
    assert len(produced) == 500 and produced[-1] == {"person_id": "H-499", "kind": "x"}

    (source / "truncated.json").write_text('[{"a": 1}, {"a": 2}', encoding="utf-8")
    for _raw, rows in landing.iter_raw_files(source / "truncated.json"):
        with pytest.raises(landing.LandingError, match="closing bracket"):
            list(rows)


def test_a_decimal_comma_and_a_date_column_survive_mixed_spellings(tmp_path: Path):
    """A column that mixes epoch numbers and ISO strings used to raise `TypeError` on min/max."""
    source = tmp_path / "delivery"
    source.mkdir()
    (source / "records.jsonl").write_text(
        json.dumps({"kirjausaika": 1791900000000, "paino": "72,5"}) + "\n"
        + json.dumps({"kirjausaika": "2026-10-14T09:00:00Z", "paino": "81,0"}) + "\n",
        encoding="utf-8")
    document = landing.profile_delivery(_landing_for(tmp_path, source), None)
    columns = {column["name"]: column for column in document["files"][0]["columns"]}
    assert columns["kirjausaika"]["inferred_type"] == "date"
    assert columns["kirjausaika"]["min"] is not None and columns["kirjausaika"]["max"] is not None
    assert columns["paino"]["inferred_type"] == "float"
    assert columns["paino"]["min"] == "72.5" and columns["paino"]["max"] == "81.0"


def test_landing_never_reads_a_file_whole(tmp_path, monkeypatch):
    """Landing streams, proved by making the non-streaming call impossible rather than by asserting it.

    The delivery is expected to be ~258 MB and `read_bytes` on a file that size is a peak the landing step
    has no reason to pay. A test that merely lands a large file would pass either way, so this removes the
    whole-file read entirely: if any part of landing still calls it, the test fails.
    """
    def refuse(self):  # noqa: ANN001 - signature must match Path.read_bytes
        raise AssertionError(f"landing read {self} whole; it must stream in chunks")

    monkeypatch.setattr(Path, "read_bytes", refuse)
    source = tmp_path / "delivery"
    source.mkdir()
    body = ("henkilotunnus;kirjaustyyppi\nH-1;MittausTulos\n" * 4000).encode("utf-8")
    (source / "mittaukset.csv").write_bytes(body)

    manifest = landing.land_delivery(source, tmp_path / "landing", received_at=RECEIVED)

    assert len(manifest.files) == 1
    landed = manifest.files[0]
    assert landed.bytes == len(body)
    assert landed.sha256 == hashlib.sha256(body).hexdigest()
    assert landed.content_type == "text/csv"
    # Read it back through a plain stream: `read_bytes` is monkeypatched to raise for the whole test.
    stored = tmp_path / "landing" / landed.path
    with stored.open("rb") as stream:
        assert stream.read() == body, "the landed bytes are byte-identical to the source"


def test_a_whole_json_document_above_the_bound_is_refused_by_name(tmp_path, monkeypatch):
    """L2: the non-array branch held the whole document in RAM (decoded, ~3x the text).

    The bound is lowered for the test rather than writing a 64 MiB fixture; the refusal names the file,
    the size and the limit, and names the two streamable shapes as the alternative.
    """
    monkeypatch.setattr(landing, "MAX_WHOLE_DOCUMENT_BYTES", 16)
    source = tmp_path / "delivery"
    source.mkdir()
    (source / "big.json").write_text(
        json.dumps({"events": [{"a": 1}, {"a": 2}]}), encoding="utf-8")
    for _raw, rows in landing.iter_raw_files(source / "big.json"):
        with pytest.raises(landing.LandingError, match="not an array") as error:
            list(rows)
    assert "32 bytes" in str(error.value) and "JSONL" in str(error.value)


def test_a_deeply_nested_json_array_element_is_refused_by_name_not_by_recursion_error(tmp_path):
    """A nested ELEMENT is not an incomplete element: looping for more text would re-raise forever."""
    source = tmp_path / "delivery"
    source.mkdir()
    (source / "deep.json").write_text(
        "[" + "[" * 20000 + "]" * 20000 + "]", encoding="utf-8")
    for _raw, rows in landing.iter_raw_files(source / "deep.json"):
        with pytest.raises(landing.LandingError, match="deeply nested"):
            list(rows)


def test_a_deeply_nested_jsonl_line_is_refused_with_a_named_reason(tmp_path):
    """JSONL is the arrival-day shape, so one nested line must be a refused row, never a traceback."""
    source = tmp_path / "delivery"
    source.mkdir()
    (source / "records.jsonl").write_text(
        json.dumps({"a": 1}) + "\n"
        + '{"deep": ' + "[" * 20000 + "]" * 20000 + "}\n", encoding="utf-8")
    for _raw, rows in landing.iter_raw_files(source / "records.jsonl"):
        collected = list(rows)
    errors = [error for _number, _row, error in collected if error]
    rows_ok = [row for _number, row, error in collected if error is None]
    assert len(rows_ok) == 1
    assert len(errors) == 1 and "not JSON" in errors[0] and "RecursionError" in errors[0]
