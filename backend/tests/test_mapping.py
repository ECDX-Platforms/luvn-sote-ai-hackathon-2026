"""The declarative mapping: four transforms, one date parser, and a loud failure on an unknown path."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.mapping import (  # noqa: E402
    TRANSFORMS, MappingError, MappingSpec, parse_date, parse_decimal_fi, parse_int_fi,
)
from scripts import reconcile_delivery

MAPPING_PATH = Path(__file__).resolve().parents[1] / "mappings" / "delivery-mapping-v1.json"


def _spec(tmp_path: Path, document: dict) -> MappingSpec:
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return MappingSpec.load(path)


def test_the_one_date_parser_accepts_finnish_iso_and_epoch():
    assert parse_date("13.10.2026") == datetime(2026, 10, 13, tzinfo=timezone.utc)
    assert parse_date("13.10.2026 08:30") == datetime(2026, 10, 13, 8, 30, tzinfo=timezone.utc)
    assert parse_date("2026-10-13T08:30:00Z") == datetime(2026, 10, 13, 8, 30, tzinfo=timezone.utc)
    assert parse_date("2026-10-13") == datetime(2026, 10, 13, tzinfo=timezone.utc)
    seconds = datetime(2026, 10, 13, 8, 30, tzinfo=timezone.utc)
    assert parse_date(int(seconds.timestamp())) == seconds
    assert parse_date(int(seconds.timestamp() * 1000)) == seconds, "epoch milliseconds must not read as year 58000"


def test_the_date_parser_refuses_rather_than_returning_none():
    with pytest.raises(MappingError, match="unrecognised date"):
        parse_date("13/10/2026")
    with pytest.raises(MappingError, match="empty value"):
        parse_date("")


def test_identity_const_and_date_transforms_apply(tmp_path: Path):
    spec = _spec(tmp_path, {
        "version": "test-v1",
        "rules": [
            {"id": "source", "target_field": "source", "transform": "const", "value": "delivery"},
            {"id": "person", "source_path": "HETU", "target_field": "source_person_id"},
            {"id": "when", "source_path": "Aika", "target_field": "recorded_at", "transform": "date"},
        ],
    })
    target, matched = spec.apply_with_matches({"HETU": "H-1", "Aika": "13.10.2026"})
    assert target == {"source": "delivery", "source_person_id": "H-1",
                      "recorded_at": "2026-10-13T00:00:00+00:00"}
    assert matched == {"source", "person", "when"}


def test_lookup_valueset_maps_vendor_spelling_and_refuses_a_miss(tmp_path: Path):
    spec = _spec(tmp_path, {
        "version": "test-v1",
        "valuesets": {"record_type": {"Laakemaarays": "lääkitys"}},
        "rules": [{"id": "type", "source_path": "Tyyppi", "target_field": "record_type",
                   "transform": "lookup_valueset", "valueset": "record_type"}],
    })
    assert spec.apply({"Tyyppi": "Laakemaarays"}) == {"record_type": "lääkitys"}
    with pytest.raises(MappingError, match="not in valueset") as error:
        spec.apply({"Tyyppi": "Tuntematon"})
    assert "Laakemaarays" in str(error.value), "the known set is the mapping instruction"


def test_an_unknown_source_path_is_a_hard_failure(tmp_path: Path):
    spec = _spec(tmp_path, {
        "version": "test-v1",
        "rules": [{"id": "person", "source_path": "HETU", "target_field": "source_person_id"}],
    })
    with pytest.raises(MappingError, match="unknown source path"):
        spec.apply({"HETU": "H-1", "Ylimääräinen": "x"})
    # An explicitly ignored path is not unknown; silent dropping is what the failure prevents.
    allowed = _spec(tmp_path, {
        "version": "test-v2",
        "ignore_source_paths": ["Ylimääräinen"],
        "rules": [{"id": "person", "source_path": "HETU", "target_field": "source_person_id"}],
    })
    assert allowed.apply({"HETU": "H-1", "Ylimääräinen": "x"}) == {"source_person_id": "H-1"}


def test_dry_run_names_the_rules_that_matched_nothing(tmp_path: Path):
    spec = _spec(tmp_path, {
        "version": "test-v1",
        "rules": [
            {"id": "matched", "source_path": "HETU", "target_field": "source_person_id"},
            {"id": "never", "source_path": "Puuttuva", "target_field": "record_id"},
        ],
    })
    document = spec.dry_run([{"HETU": "H-1"}, {"HETU": "H-2"}])
    assert document["rows_examined"] == 2
    assert document["rules_matched_nothing"] == ["never"]
    counts = {rule["id"]: rule["matched_rows"] for rule in document["rules"]}
    assert counts == {"matched": 2, "never": 0}


def test_a_mapping_revision_is_rejected_when_it_is_not_honest(tmp_path: Path):
    with pytest.raises(MappingError, match="unknown transform"):
        _spec(tmp_path, {"version": "v", "rules": [
            {"id": "r", "source_path": "a", "target_field": "b", "transform": "guess"}]})
    with pytest.raises(MappingError, match="does not declare"):
        _spec(tmp_path, {"version": "v", "rules": [
            {"id": "r", "source_path": "a", "target_field": "b",
             "transform": "lookup_valueset", "valueset": "missing"}]})
    with pytest.raises(MappingError, match="no rules"):
        _spec(tmp_path, {"version": "v", "rules": []})
    with pytest.raises(MappingError, match="string 'version'"):
        _spec(tmp_path, {"rules": [{"id": "r", "source_path": "a", "target_field": "b"}]})


def test_the_seeded_mapping_is_provisional_and_loads():
    spec = MappingSpec.load(MAPPING_PATH)
    assert spec.version == "delivery-mapping-v1"
    assert spec.provisional is True, "an unconfirmed spelling must be marked provisional"
    assert spec.note and "PROVISIONAL" in spec.note
    transforms = {rule.transform for rule in spec.rules}
    assert transforms <= set(TRANSFORMS), "only declared transforms may appear in a mapping file"
    # The seeded measurement columns carry the Finnish number transform AND are declared optional, because
    # the delivery may spell them differently or not carry them at all.
    numeric = {rule.id: rule for rule in spec.rules if rule.transform in ("decimal_fi", "int_fi")}
    assert numeric, "the seed must show where a measurement transform belongs"
    assert all(rule.optional for rule in numeric.values()), (
        "an unconfirmed measurement column must be optional, or a delivery without it would refuse")


def test_the_seeded_mapping_maps_a_plausible_vendor_row_end_to_end():
    spec = MappingSpec.load(MAPPING_PATH)
    target = spec.apply({
        "Lähdehenkilötunniste": "H-1",
        "Kirjaustunniste": "R-1",
        "Kirjaustyyppi": "Laakemaarays",
        "Kirjaaja": "laakari",
        "Kirjausaika": "13.10.2026",
        "Aihe": "Laakitys",
        "Salassapitoluokka": "salainen",
        "SuostumuksenLaajuus": "hoito",
        "Sisältö": {"lääke": "Amlodipin"},
        "TietueVersio": "3",
    })
    assert target["source_person_id"] == "H-1"
    assert target["record_type"] == "lääkitys"
    assert target["topic"] == "lääkitys"
    assert target["recorded_at"] == "2026-10-13T00:00:00+00:00"
    assert target["sensitivity_tag"] == "sensitive"
    assert target["consent_scope"] == "treatment"
    assert target["source"] == "delivery"


def test_the_map_dry_run_cli_prints_per_rule_match_counts_and_refuses_an_unknown_path(tmp_path: Path,
                                                                                     capsys):
    records = tmp_path / "records.jsonl"
    records.write_text('{"Lähdehenkilötunniste": "H-1", "Kirjaustunniste": "R-1", "Tuntematon": "x"}\n',
                       encoding="utf-8")
    status = reconcile_delivery.main(["map", str(records), "--mapping", str(MAPPING_PATH), "--dry-run"])
    out = capsys.readouterr().out
    assert "rules matched nothing" in out
    assert "source-person" in out and "record-id" in out
    assert status == 1, "unknown source paths are a hard failure even in a dry run"


# --- required targets, ignored files, and nested payload targets -------------------------------------------------


def _required_mapping() -> dict:
    """A mapping that states every required target, so `require_targets` has nothing to refuse."""
    return {
        "version": "required-v1",
        "rules": [
            {"id": "source", "target_field": "source", "transform": "const", "value": "delivery"},
            {"id": "person", "source_path": "HETU", "target_field": "source_person_id"},
            {"id": "record", "source_path": "TUNNISTE", "target_field": "record_id"},
            {"id": "type", "source_path": "TYYPPI", "target_field": "record_type"},
            {"id": "author", "source_path": "KIRJAAJA", "target_field": "authored_by_role"},
            {"id": "when", "source_path": "AIKA", "target_field": "recorded_at"},
            {"id": "topic", "source_path": "AIHE", "target_field": "topic"},
            {"id": "sensitive", "source_path": "SALAISUUS", "target_field": "sensitivity_tag"},
            {"id": "consent", "source_path": "SUOSTUMUS", "target_field": "consent_scope"},
        ],
    }


def test_a_mapping_must_state_every_required_target_and_names_the_missing_one(tmp_path: Path):
    from app.delivery import REQUIRED_ENVELOPE_TARGETS

    complete = _spec(tmp_path, _required_mapping())
    assert complete.missing_required_targets(REQUIRED_ENVELOPE_TARGETS) == []
    complete.require_targets(REQUIRED_ENVELOPE_TARGETS)  # does not raise

    document = _required_mapping()
    document["rules"] = [rule for rule in document["rules"]
                         if rule["target_field"] not in ("sensitivity_tag", "consent_scope")]
    partial = _spec(tmp_path, document)
    assert partial.missing_required_targets(REQUIRED_ENVELOPE_TARGETS) == ["sensitivity_tag", "consent_scope"]
    with pytest.raises(MappingError, match="sensitivity_tag") as error:
        partial.require_targets(REQUIRED_ENVELOPE_TARGETS)
    assert "no rule for required envelope field" in str(error.value)
    # Anti-vacuity for the check itself: a rule for a DIFFERENT target field cannot silently count.
    assert "record_id" not in partial.missing_required_targets(REQUIRED_ENVELOPE_TARGETS)


def test_the_required_target_set_is_exactly_what_the_record_model_requires_less_source():
    """The list is hand-written, so it is pinned to the model it claims to mirror.

    `source` is excluded on purpose: the importer states provenance itself (`delivery`) and would override
    any mapping rule, so requiring a rule for it would be a rule that does nothing. Every other field the
    envelope cannot be built without must be on the list.
    """
    from app.delivery import REQUIRED_ENVELOPE_TARGETS
    from app.upm import Record

    model_required = {name for name, field in Record.model_fields.items() if field.is_required()}
    # Every field the model cannot be built without is required, except `source` (see above).
    assert model_required - {"source"} <= set(REQUIRED_ENVELOPE_TARGETS)
    # `source_person_id` is optional in the model but NOT in the importer: `_bind_record` refuses a record
    # whose key is not a bound person, so a mapping that omitted it would produce rows that cannot bind.
    assert set(REQUIRED_ENVELOPE_TARGETS) - model_required == {"source_person_id"}
    # And the seed mapping (the one a delivery-day operator copies) satisfies it, so the documented
    # starting point is not itself an example that the importer refuses.
    assert MappingSpec.load(MAPPING_PATH).missing_required_targets(REQUIRED_ENVELOPE_TARGETS) == []


def test_ignored_files_are_matched_by_pattern_and_never_silently(tmp_path: Path):
    spec = _spec(tmp_path, {"version": "v", "ignore_source_files": ["profiles.csv", "nested/*.bak"],
                            "rules": [{"id": "p", "source_path": "a", "target_field": "source_person_id"}]})
    assert spec.ignores_file("profiles.csv") is True
    assert spec.ignores_file("nested/old.bak") is True
    assert spec.ignores_file("records.jsonl") is False
    with pytest.raises(MappingError, match="list of file name patterns"):
        _spec(tmp_path, {"version": "v", "ignore_source_files": "profiles.csv",
                         "rules": [{"id": "p", "source_path": "a", "target_field": "b"}]})


def test_a_nested_payload_target_builds_the_payload_object(tmp_path: Path):
    """A WIDE vendor CSV carries the payload as columns; `payload.<key>` keeps that expressible."""
    spec = _spec(tmp_path, {"version": "v", "rules": [
        {"id": "person", "source_path": "HETU", "target_field": "source_person_id"},
        {"id": "weight", "source_path": "Paino", "target_field": "payload.paino_kg"},
        {"id": "height", "source_path": "Pituus", "target_field": "payload.pituus_cm"},
    ]})
    target, matched = spec.apply_with_matches({"HETU": "H-1", "Paino": "72,5", "Pituus": "168,0"})
    assert target == {"source_person_id": "H-1", "payload": {"paino_kg": "72,5", "pituus_cm": "168,0"}}
    assert matched == {"person", "weight", "height"}


def test_two_nested_rules_for_one_payload_key_conflict_rather_than_overwrite(tmp_path: Path):
    spec = _spec(tmp_path, {"version": "v", "rules": [
        {"id": "a", "source_path": "A", "target_field": "payload.arvo"},
        {"id": "b", "source_path": "B", "target_field": "payload.arvo"},
    ]})
    with pytest.raises(MappingError, match="conflicting rules for target 'payload.arvo'"):
        spec.apply({"A": "1", "B": "2"})
    # Identical values are not a conflict: the same vendor fact named twice is not a contradiction.
    assert spec.apply({"A": "1", "B": "1"}) == {"payload": {"arvo": "1"}}


def test_a_nested_target_must_be_one_level_and_well_formed(tmp_path: Path):
    with pytest.raises(MappingError, match="nested target"):
        _spec(tmp_path, {"version": "v", "rules": [
            {"id": "deep", "source_path": "a", "target_field": "payload.a.b"}]})
    with pytest.raises(MappingError, match="nested target"):
        _spec(tmp_path, {"version": "v", "rules": [
            {"id": "empty", "source_path": "a", "target_field": "payload."}]})


# --- the Finnish number transform -------------------------------------------------------------------


def test_decimal_fi_reads_the_finnish_spellings_and_a_plain_float():
    """The value the arrival runbook used to record as a hole: `72,5` passed through as a string."""
    assert parse_decimal_fi("72,5") == 72.5
    assert parse_decimal_fi("1 234,5") == 1234.5
    assert parse_decimal_fi("1\u00a0234,5") == 1234.5, "a non-breaking space is a thousands separator"
    assert parse_decimal_fi("1\u202f234,5") == 1234.5, "the narrow no-break space too"
    assert parse_decimal_fi("72.5") == 72.5, "an anglo decimal point is read as itself"
    assert parse_decimal_fi("1.234,5") == 1234.5, "dot-grouped thousands beside a comma decimal is unambiguous"
    assert parse_decimal_fi("12345,6") == 12345.6
    assert parse_decimal_fi("-1 234,5") == -1234.5
    assert parse_decimal_fi(72) == 72.0 and isinstance(parse_decimal_fi(72), float)
    assert parse_decimal_fi(72.5) == 72.5


def test_decimal_fi_refuses_a_malformed_value_by_name_rather_than_passing_it_through():
    for bad in ("", "   ", "abc", "72,", ",5", "1.2.3", "72 5", "1 23,5", "nan", "inf", "1_0", None, True):
        with pytest.raises(MappingError) as error:
            parse_decimal_fi(bad)
        message = str(error.value)
        assert "decimal_fi" in message, "the refusal names the transform"
        assert repr(bad) in message, "the refusal names the offending value"
    assert "boolean" in str(pytest.raises(MappingError, parse_decimal_fi, True).value)


def test_int_fi_reads_thousands_separated_integers_and_refuses_a_decimal_comma():
    assert parse_int_fi("1 234") == 1234
    assert parse_int_fi("1\u00a0234") == 1234
    assert parse_int_fi("1234") == 1234
    assert parse_int_fi(12) == 12
    for bad in ("72,5", "1.234", "12,0", ""):
        with pytest.raises(MappingError, match="int_fi"):
            parse_int_fi(bad)
    # One string, two transforms, one answer each: `1.234` is the float 1.234 and is refused as an integer,
    # so the two can never silently disagree by a factor of 1000.
    assert parse_decimal_fi("1.234") == 1.234


def test_the_number_transforms_act_through_a_mapping_rule(tmp_path: Path):
    spec = _spec(tmp_path, {"version": "v", "rules": [
        {"id": "weight", "source_path": "Paino", "target_field": "payload.paino_kg",
         "transform": "decimal_fi"},
        {"id": "pulse", "source_path": "Syke", "target_field": "payload.syke", "transform": "int_fi"},
    ]})
    target, matched = spec.apply_with_matches({"Paino": "1\u00a0234,5", "Syke": "72"})
    assert target == {"payload": {"paino_kg": 1234.5, "syke": 72}}
    assert matched == {"weight", "pulse"}
    with pytest.raises(MappingError, match="decimal_fi"):
        spec.apply({"Paino": "seitsemänkymmentä", "Syke": "72"})


def test_an_unknown_transform_is_still_refused(tmp_path: Path):
    with pytest.raises(MappingError, match="unknown transform"):
        _spec(tmp_path, {"version": "v", "rules": [
            {"id": "r", "source_path": "a", "target_field": "b", "transform": "decimal"}]})


# --- optional rules: a plausible column the delivery may not carry ----------------------------------


def test_a_rule_may_be_declared_optional_and_is_then_reported_not_refused(tmp_path: Path):
    """A seeded mapping for an unknown delivery needs to name a plausible column without becoming a trap."""
    spec = _spec(tmp_path, {"version": "v", "rules": [
        {"id": "person", "source_path": "HETU", "target_field": "source_person_id"},
        {"id": "weight", "source_path": "Paino", "target_field": "payload.paino_kg",
         "transform": "decimal_fi", "optional": True},
        {"id": "typo", "source_path": "Puuttuva", "target_field": "payload.x"},
    ]})
    counts = {"person": 2, "weight": 0, "typo": 0}
    assert spec.rules_matched_nothing(counts) == ["'typo' (source_path 'Puuttuva' -> target 'payload.x')"]
    assert spec.rules_matched_nothing(counts, optional=True) == [
        "'weight' (source_path 'Paino' -> target 'payload.paino_kg')"]
    document = spec.dry_run([{"HETU": "H-1"}, {"HETU": "H-2"}])
    assert document["rules_matched_nothing"] == ["typo"]
    assert document["optional_rules_matched_nothing"] == ["weight"]
    assert document["rules"][1]["id"] == "weight" and document["rules"][1]["optional"] is True
    assert document["rules"][2]["optional"] is False


def test_an_optional_sibling_is_exempt_when_the_other_spelling_matched(tmp_path: Path):
    spec = _spec(tmp_path, {"version": "v", "rules": [
        {"id": "weight", "source_path": "Paino", "target_field": "payload.paino_kg", "optional": True},
        {"id": "weight-alt", "source_path": "PainoKg", "target_field": "payload.paino_kg",
         "optional": True},
    ]})
    document = spec.dry_run([{"Paino": "72,5"}])
    assert document["optional_rules_matched_nothing"] == ["weight-alt"]
