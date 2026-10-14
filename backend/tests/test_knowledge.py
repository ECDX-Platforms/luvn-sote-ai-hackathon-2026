"""Offline tests for the knowledge registry and the deterministic lookup.

No network: snapshots are injected from `tmp_path`, so the gate exercises provenance, hash checking and
the miss path without touching fimea.fi or the Code Service.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app import knowledge


def test_registry_sources_carry_the_fields_a_citation_needs():
    rows = knowledge.sources()
    assert rows, "the registry must carry at least one candidate source"
    for row in rows:
        for field in ("id", "title", "licence", "attribution", "redistribution"):
            value = row.get(field)
            assert isinstance(value, str) and value.strip(), f"{row.get('id')} is missing non-empty {field}"
        assert isinstance(row.get("machine_readable"), bool), row["id"]


def test_tero_licence_and_machine_readability():
    row = knowledge.source("finto-tero")
    assert row is not None
    assert row["licence"] == "CC BY 3.0"
    assert row["machine_readable"] is True
    assert row["attribution"] == "Finnish Terminology Centre TSK / Finto"


def test_a_machine_readable_source_names_its_interface_and_a_blocked_one_names_why():
    for row in knowledge.sources():
        if row["machine_readable"]:
            interface = row.get("interface") or {}
            assert interface.get("url", "").startswith("https://"), row["id"]
            assert interface.get("verified_http_status") == 200, row["id"]
        else:
            assert row.get("reason"), f"{row['id']} must say why it is not machine-readable"
            assert row.get("interface") is None


def test_the_code_service_row_records_the_reachability_constraint():
    """The interfaces are Finland-only, which decides where a fetch may run from."""
    row = knowledge.source("thl-code-service")
    assert row is not None
    assert any("only from Finland" in constraint for constraint in row["constraints"])
    assert row["licence_basis"].startswith("conditional permission")


def test_fimea_is_not_treated_as_a_usable_source():
    row = knowledge.source("fimea-open-data")
    assert row is not None and row["machine_readable"] is False
    assert "403" in row["reason"]


def _snapshot(tmp_path, source_id: str, labels: dict, version: str = "2026-10-09") -> dict:
    document = {"source_id": source_id, "version": version, "labels": labels}
    raw = json.dumps(document, sort_keys=True).encode()
    (tmp_path / f"{source_id}.json").write_bytes(raw)
    (tmp_path / f"{source_id}.sha256").write_text(hashlib.sha256(raw).hexdigest() + "  snapshot\n")
    return document


def test_lookup_resolves_nothing_without_a_snapshot():
    assert knowledge.lookup("icd10", "F32.1", snapshots={}) is None


def test_lookup_returns_a_citation_with_source_version_licence_and_attribution(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "SNAPSHOT_DIR", tmp_path)
    _snapshot(tmp_path, "thl-code-service", {"icd10": {"F32.1": "Keskivaikea masennusjakso"}}, version="2026-01")
    reference = knowledge.lookup("icd10", "F32.1")
    assert reference is not None
    assert reference.label == "Keskivaikea masennusjakso"
    assert reference.as_ref() == "ref:thl-code-service@2026-01#icd10:F32.1"
    assert reference.licence.startswith("SNOMED International Affiliate Member")
    assert reference.attribution == "SNOMED International and THL"


def test_a_source_with_no_machine_readable_interface_refuses_to_load(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "SNAPSHOT_DIR", tmp_path)
    _snapshot(tmp_path, "fimea-open-data", {"atc": {"C09AA05": "ramipriili"}})
    with pytest.raises(knowledge.SnapshotError, match="no machine-readable interface"):
        knowledge.load_snapshot("fimea-open-data")


def test_a_snapshot_whose_bytes_changed_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "SNAPSHOT_DIR", tmp_path)
    _snapshot(tmp_path, "thl-code-service", {"icd10": {"F32.1": "Keskivaikea masennusjakso"}})
    (tmp_path / "thl-code-service.json").write_text(
        json.dumps({"source_id": "thl-code-service", "version": "x", "labels": {"icd10": {"F32.1": "tampered"}}}),
        encoding="utf-8")
    with pytest.raises(knowledge.SnapshotError, match="does not match the recorded"):
        knowledge.load_snapshot("thl-code-service")


def test_coverage_names_the_codes_it_cannot_resolve(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "SNAPSHOT_DIR", tmp_path)
    _snapshot(tmp_path, "thl-code-service", {"icd10": {"F32.1": "Keskivaikea masennusjakso"}})
    report = knowledge.coverage([("icd10", "F32.1"), ("icd10", "Z99.9"), ("atc", "C09AA05")])
    assert (report["requested"], report["resolved"], report["unresolved"]) == (3, 1, 2)
    assert {miss["code"] for miss in report["misses"]} == {"Z99.9", "C09AA05"}
    assert report["references"][0]["ref"] == "ref:thl-code-service@2026-10-09#icd10:F32.1"


def test_an_unknown_source_is_an_error_not_a_silent_empty():
    assert knowledge.source("nope") is None
    with pytest.raises(knowledge.SnapshotError, match="unknown source"):
        knowledge.load_snapshot("nope")


# --- external evidence: the UpToDate capture seam ----------------------------------------------------

def _capture(tmp_path, capture_id: str = "uc-001", **fields) -> dict:
    document = {"capture_id": capture_id, "source_id": "uptodate-expert-ai",
                "question": "Mitä ammattilaisen tulisi huomioida ennen käyntiä?",
                "audience": "professional",
                "answer": "Tarkista lääkityksen toteutuminen ja huimauksen yhteys annokseen.",
                "captured_at": "2026-10-09T12:30:00Z", "surface": "UpToDate Expert AI (browser)",
                **fields}
    raw = json.dumps(document, sort_keys=True, ensure_ascii=False).encode()
    (tmp_path / f"{capture_id}.json").write_bytes(raw)
    (tmp_path / f"{capture_id}.sha256").write_text(hashlib.sha256(raw).hexdigest() + "  capture\n")
    return document


def test_a_capture_renders_with_source_audience_date_and_attribution(tmp_path):
    _capture(tmp_path)
    evidence = knowledge.load_capture("uc-001", tmp_path)
    assert evidence.as_ref() == "ref:uptodate-expert-ai@2026-10-09#uc-001"
    assert evidence.audience == "professional"
    assert evidence.attribution == "UpToDate, Wolters Kluwer"
    assert evidence.as_dict()["answer"].startswith("Tarkista lääkityksen")


def test_a_capture_without_a_hash_is_not_evidence(tmp_path):
    _capture(tmp_path)
    (tmp_path / "uc-001.sha256").unlink()
    with pytest.raises(knowledge.SnapshotError, match="no hash sidecar"):
        knowledge.load_capture("uc-001", tmp_path)


def test_a_tampered_capture_is_refused(tmp_path):
    _capture(tmp_path)
    document = json.loads((tmp_path / "uc-001.json").read_text(encoding="utf-8"))
    document["answer"] = "Suosittelen lopettamaan lääkityksen."
    (tmp_path / "uc-001.json").write_text(json.dumps(document, sort_keys=True, ensure_ascii=False),
                                          encoding="utf-8")
    with pytest.raises(knowledge.SnapshotError, match="does not match the recorded"):
        knowledge.load_capture("uc-001", tmp_path)


def test_the_audience_is_required_and_validated(tmp_path):
    _capture(tmp_path, audience="physician")
    with pytest.raises(knowledge.SnapshotError, match="audience"):
        knowledge.load_capture("uc-001", tmp_path)


def test_a_capture_naming_an_unknown_source_is_refused(tmp_path):
    _capture(tmp_path, source_id="someone-elses-database")
    with pytest.raises(knowledge.SnapshotError, match="unknown source"):
        knowledge.load_capture("uc-001", tmp_path)


def test_a_capture_needs_both_a_question_and_an_answer(tmp_path):
    _capture(tmp_path, answer="   ")
    with pytest.raises(knowledge.SnapshotError, match="question and an answer"):
        knowledge.load_capture("uc-001", tmp_path)


def test_capture_refs_names_every_refusal_instead_of_hiding_it(tmp_path):
    _capture(tmp_path, capture_id="uc-good")
    _capture(tmp_path, capture_id="uc-bad", audience="doctor")
    report = knowledge.capture_refs(tmp_path)
    assert (report["captures"], report["valid"], report["refused"]) == (2, 1, 1)
    assert report["references"][0]["capture_id"] == "uc-good"
    assert report["refusals"][0]["capture_id"] == "uc-bad"


def test_no_captures_is_an_empty_report_not_an_error(tmp_path):
    assert knowledge.capture_ids(tmp_path) == []
    assert knowledge.capture_refs(tmp_path) == {"captures": 0, "valid": 0, "refused": 0,
                                                "references": [], "refusals": []}


# --- alias mapping tests -----------------------------------------------------------------------------

def test_alias_map_resolves_iadl_rai_ldl_with_synthetic_snapshot(tmp_path, monkeypatch):
    """IADL, RAI, and LDL resolve with the right Finnish labels from an injected snapshot."""
    monkeypatch.setattr(knowledge, "SNAPSHOT_DIR", tmp_path)
    _snapshot(
        tmp_path,
        "kanta-code-service",
        {
            "instrument": {
                "1252": "Lawtonin IADL -asteikko",
                "1262": "RAI väline",
            },
            "measure": {
                "2099": "fS-kolesteroli, low density lipoproteiinit",
            },
        },
        version="2026-10-12",
    )
    ref_iadl = knowledge.lookup("instrument", "IADL")
    assert ref_iadl is not None
    assert ref_iadl.code == "1252"
    assert ref_iadl.alias == "IADL"
    assert ref_iadl.label == "Lawtonin IADL -asteikko"
    assert ref_iadl.as_ref() == "ref:kanta-code-service@2026-10-12#instrument:IADL→1252"
    assert ref_iadl.as_dict()["via_alias"] is True

    ref_rai = knowledge.lookup("instrument", "RAI")
    assert ref_rai is not None
    assert ref_rai.code == "1262"
    assert ref_rai.alias == "RAI"
    assert ref_rai.label == "RAI väline"
    assert ref_rai.as_ref() == "ref:kanta-code-service@2026-10-12#instrument:RAI→1262"
    assert ref_rai.as_dict()["via_alias"] is True

    ref_ldl = knowledge.lookup("measure", "LDL")
    assert ref_ldl is not None
    assert ref_ldl.code == "2099"
    assert ref_ldl.alias == "LDL"
    assert ref_ldl.label == "fS-kolesteroli, low density lipoproteiinit"
    assert ref_ldl.as_ref() == "ref:kanta-code-service@2026-10-12#measure:LDL→2099"
    assert ref_ldl.as_dict()["via_alias"] is True


def test_alias_map_resolves_against_real_snapshot_when_present():
    """When the real gitignored snapshot is present, assert that IADL, RAI, and LDL resolve live."""
    try:
        knowledge.load_snapshot("kanta-code-service")
    except knowledge.SnapshotError:
        pytest.skip("gitignored kanta-code-service snapshot is absent")

    ref_iadl = knowledge.lookup("instrument", "IADL")
    assert ref_iadl is not None and ref_iadl.label == "Lawtonin IADL -asteikko"
    ref_rai = knowledge.lookup("instrument", "RAI")
    assert ref_rai is not None and ref_rai.label == "RAI väline"
    ref_ldl = knowledge.lookup("measure", "LDL")
    assert ref_ldl is not None and ref_ldl.label == "fS-kolesteroli, low density lipoproteiinit"


def test_unmapped_value_is_a_miss():
    """An unmapped value is a miss: no fuzzy fallback, no similarity guess."""
    assert knowledge.lookup("instrument", "MMSE_UNKNOWN_ALIAS") is None
    assert knowledge.lookup("measure", "HDL") is None
    assert knowledge.lookup("instrument", "iadl") is None  # case-sensitive; exact match only


def test_every_alias_target_is_syntactically_valid():
    """Every alias target in committed knowledge_aliases.json is non-empty and well-formed."""
    aliases_data = knowledge.load_aliases()
    aliases = aliases_data.get("aliases", {})
    assert aliases, "committed aliases must not be empty"
    for system, mapping in aliases.items():
        assert isinstance(system, str) and system.strip()
        assert mapping, f"system {system} has empty alias mapping"
        for alias, target_code in mapping.items():
            assert isinstance(alias, str) and alias.strip(), f"empty alias key in {system}"
            assert isinstance(target_code, str) and target_code.strip(), f"empty target_code for {alias} in {system}"


def test_every_alias_target_exists_in_the_real_snapshot_when_present():
    """When the real snapshot is present, every committed alias target must exist in it."""
    try:
        snapshot = knowledge.load_snapshot("kanta-code-service")
    except knowledge.SnapshotError:
        pytest.skip("gitignored kanta-code-service snapshot is absent")

    aliases_data = knowledge.load_aliases()
    labels = snapshot.get("labels", {})
    for system, mapping in aliases_data.get("aliases", {}).items():
        assert system in labels, f"system {system} not in snapshot labels"
        for alias, target_code in mapping.items():
            assert target_code in labels[system], (
                f"alias {alias} maps to {target_code}, which is missing from {system} in snapshot"
            )


def test_phq9_is_not_in_alias_map_and_does_not_resolve():
    """PHQ-9 has no national label, must not be in the alias map, and resolves to None."""
    aliases_data = knowledge.load_aliases()
    for system, mapping in aliases_data.get("aliases", {}).items():
        assert "PHQ-9" not in mapping, f"PHQ-9 must not be in alias map for {system}"
    assert knowledge.lookup("instrument", "PHQ-9") is None


# --- Ticket 03 (THL Sote-sanastot) & Ticket 05 (fhir-fi) tests ---------------------------------------

def test_sote_sanastot_registry_and_snapshot_namespace():
    """Sote-sanastot row exists, snapshot loads with 'concept' namespace, and known concepts resolve."""
    row = knowledge.source("thl-sote-sanastot")
    assert row is not None
    assert row["licence"] == "CC BY 4.0"
    assert row["publisher"] == "THL"
    assert row["machine_readable"] is True
    assert row["interface"]["verified_http_status"] == 200

    try:
        snapshot = knowledge.load_snapshot("thl-sote-sanastot")
    except knowledge.SnapshotError:
        pytest.skip("gitignored thl-sote-sanastot snapshot is absent")

    labels = snapshot.get("labels", {})
    assert "concept" in labels, "labels must use 'concept' namespace"
    assert set(labels.keys()) == {"concept"}, "labels must not use clinical namespaces (atc/icd10/measure/instrument)"

    # Known concepts: asiakastieto (063ffde4-c2aa-45e3-8497-1b065fbc52c7), hoitosuhde (f94779e9-10f4-4f59-a3f5-14eec64c3f35)
    ref_asiakastieto = knowledge.lookup("concept", "063ffde4-c2aa-45e3-8497-1b065fbc52c7")
    assert ref_asiakastieto is not None
    assert ref_asiakastieto.label == "asiakastieto"
    assert ref_asiakastieto.source_id == "thl-sote-sanastot"
    assert ref_asiakastieto.licence == "CC BY 4.0"

    ref_hoitosuhde = knowledge.lookup("concept", "f94779e9-10f4-4f59-a3f5-14eec64c3f35")
    assert ref_hoitosuhde is not None
    assert ref_hoitosuhde.label == "hoitosuhde"
    assert ref_hoitosuhde.source_id == "thl-sote-sanastot"

    # Sibling details retained in the snapshot
    concept_meta = snapshot.get("concepts", {}).get("063ffde4-c2aa-45e3-8497-1b065fbc52c7")
    assert concept_meta is not None
    assert concept_meta["status"] == "recommendation"
    assert "sosiaali- ja terveydenhuollon asiakastieto" in concept_meta["synonyms_fi"]
    assert concept_meta["definition_fi"] is not None


def test_fhir_fi_security_and_release_labels():
    """TURVAKIELTO resolves to Turvakielto with CC0-1.0; code absent from snapshot is a miss."""
    row = knowledge.source("fhir-fi-base-profiles")
    assert row is not None
    assert row["licence"] == "CC0-1.0"
    assert row["publisher"] == "HL7 Finland / fhir-fi"
    assert row["machine_readable"] is True
    assert row["interface"]["verified_http_status"] == 200

    try:
        snapshot = knowledge.load_snapshot("fhir-fi-base-profiles")
    except knowledge.SnapshotError:
        pytest.skip("gitignored fhir-fi-base-profiles snapshot is absent")

    assert "security" in snapshot.get("labels", {})
    assert "release" in snapshot.get("labels", {})

    ref = knowledge.lookup("security", "TURVAKIELTO")
    assert ref is not None
    assert ref.label == "Turvakielto"
    assert ref.source_id == "fhir-fi-base-profiles"
    assert ref.licence == "CC0-1.0"
    assert ref.attribution == "HL7 Finland / fhir-fi"
    assert ref.as_ref() == f"ref:fhir-fi-base-profiles@{snapshot['version']}#security:TURVAKIELTO"

    # Release system
    ref_release = knowledge.lookup("release", "1")
    assert ref_release is not None
    assert ref_release.label == "Viivästetty"

    # Absent code is a miss (None), not an empty label
    assert knowledge.lookup("security", "NONEXISTENT_SECURITY_LABEL") is None
    assert knowledge.lookup("release", "999") is None


def test_yti_interoperability_platform_registry_row():
    """Ticket 10: YTI row is present, machine-readable, records per-item licence and separate API spec licence."""
    row = knowledge.source("yti-interoperability-platform")
    assert row is not None
    assert row["publisher"] == "Digi- ja väestötietovirasto (DVV)"
    assert row["machine_readable"] is True
    assert row["licence_per_item"] is True
    assert "CC0 or CC BY 4.0" in row["licence"]
    assert "EUPL-1.2" in row["api_specification_licence"]
    assert row["interface"]["kind"] == "rest-json"
    assert row["interface"]["url"] == "https://koodistot.suomi.fi/codelist-api/api/v1/coderegistries"
    assert row["interface"]["verified_http_status"] == 200
    assert any("sotute holds one legislative scheme" in c for c in row["constraints"])


def test_maarays_workaround_row_records_the_owner_decision_and_forbids_vendoring():
    """Ticket 07 §4: the annexes are used under a recorded owner work-around, not under a licence."""
    row = knowledge.source("thl-maarays-5-2024-annexes")
    assert row is not None
    assert row["machine_readable"] is True
    assert "Owner work-around" in row["licence_basis"]
    assert "NOT a legal finding" in row["licence_basis"], "the work-around must not read as a legal finding"
    assert "OPEN question of law" in row["licence"]
    assert "never vendor" in row["redistribution"]
    assert row["interface"]["kind"] == "document-files"
    assert row["interface"]["verified_http_status"] == 200
    assert any("demo window" in c for c in row["constraints"]), "the work-around is time-scoped, and says so"


def test_sosmeta_row_is_cite_only_because_no_licence_is_stated():
    """Ticket 07: Sosmeta states no licence, so it is recorded as a citation, not a source."""
    row = knowledge.source("sosmeta-data-components")
    assert row is not None
    assert row["machine_readable"] is False
    assert "no content licence is stated" in row["licence"]
    assert row.get("reason"), "a machine_readable: false row must carry the reason it is not machine-readable"
    assert any("sosmeta.fi" in c and "sosmeta.thl.fi" in c for c in row["constraints"]), "the host trap is recorded"
    assert "not permitted" in row["redistribution"]
