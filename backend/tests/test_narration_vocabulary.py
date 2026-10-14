"""Offline tests for narration vocabulary control against THL Sote-sanastot term classes.

Clean-clone safe:
- Injects synthetic snapshots via tmp_path.
- Asserts deprecated terms are flagged with preferred alternatives.
- Asserts inflected occurrences of a discouraged term are caught.
- Asserts gradation variants (e.g. kk->k, t->d) are caught.
- Asserts clean sentences produce no hits.
- Asserts snapshot-absent behaviour exits cleanly with exit code 0 and documented status.
- Asserts arithmetic across checked terms, covered concepts, sentences, and hit rates.
- Asserts vocabulary coverage arithmetic over narration content words.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from scripts.check_narration_vocabulary import (
    check_sentences,
    extract_snapshot_words,
    generate_surface_patterns,
    load_term_rules_from_snapshot,
    measure_vocabulary_coverage,
    run_check,
)


def _make_snapshot(tmp_path: Path, concepts: dict) -> Path:
    doc = {
        "source_id": "thl-sote-sanastot",
        "version": "2026-10-12",
        "concepts": concepts,
        "labels": {"concept": {cid: c.get("prefLabel_fi", "") for cid, c in concepts.items()}},
    }
    path = tmp_path / "thl-sote-sanastot.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _make_adjudication(tmp_path: Path, sentences: list[dict]) -> Path:
    doc = {
        "encounters": [
            {
                "encounter_id": "test-encounter-001",
                "sentences": sentences,
            }
        ]
    }
    path = tmp_path / "verifier-adjudication-v1.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def test_sentence_using_deprecated_or_avoidable_term_is_flagged(tmp_path: Path):
    snapshot_path = _make_snapshot(
        tmp_path,
        {
            "c1": {
                "prefLabel_fi": "annosaika",
                "avoidable_fi": ["annostusaika"],
                "deprecated_fi": [],
                "vocabulary_title": "Lääkityksen sanasto",
            },
            "c2": {
                "prefLabel_fi": "sosiaalihuollon yleispalvelut (pl)",
                "avoidable_fi": [],
                "deprecated_fi": ["peruspalvelut (pl)"],
                "vocabulary_title": "Sosiaalihuollon sanasto",
            },
        },
    )

    sentences = [
        {"id": "e1:s1", "encounter_id": "e1", "sentence_id": "s1", "text": "Potilaan annostusaika on klo 8.00."},
        {"id": "e1:s2", "encounter_id": "e1", "sentence_id": "s2", "text": "Alueen peruspalvelut (pl) turvataan."},
        {"id": "e1:s3", "encounter_id": "e1", "sentence_id": "s3", "text": "Potilaan vointi on vakaa ja lääkitys kunnossa."},
    ]

    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    rule_info = load_term_rules_from_snapshot(data)
    hits, stats = check_sentences(sentences, rule_info["rules"])

    assert len(hits) == 2
    assert stats["sentences_in_scope"] == 3
    assert stats["sentences_with_hit"] == 2
    assert stats["sentences_not_covered"] == 1
    assert stats["hits_per_sentence"] == round(2 / 3, 4)

    hit_terms = {h["term"]: h["preferred_alternative"] for h in hits}
    assert hit_terms["annostusaika"] == "annosaika"
    assert hit_terms["peruspalvelut (pl)"] == "sosiaalihuollon yleispalvelut (pl)"


def test_inflected_occurrence_of_discouraged_term_is_caught(tmp_path: Path):
    """An inflected occurrence of a discouraged compound term is caught on its last component."""
    snapshot_path = _make_snapshot(
        tmp_path,
        {
            "c1": {
                "prefLabel_fi": "sosiaali- ja terveyskeskus",
                "avoidable_fi": ["sote-keskus"],
                "deprecated_fi": [],
                "vocabulary_title": "Sote-uudistuksen keskeiset käsitteet",
            },
        },
    )

    sentences = [
        {
            "id": "e1:s1",
            "encounter_id": "e1",
            "sentence_id": "s1",
            "text": "Asiakas ohjattiin alueelliseen sote-keskusta vastaavaan yksikköön.",
        },
    ]

    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    rule_info = load_term_rules_from_snapshot(data)
    hits, stats = check_sentences(sentences, rule_info["rules"])

    assert len(hits) == 1
    assert stats["sentences_with_hit"] == 1
    assert hits[0]["term"] == "sote-keskus"
    assert hits[0]["matched_surface"].lower() == "sote-keskusta"
    assert hits[0]["preferred_alternative"] == "sosiaali- ja terveyskeskus"


def test_consonant_gradation_variant_is_caught(tmp_path: Path):
    """Consonant gradation variants (e.g. kk->k, t->d) in discouraged terms are caught."""
    snapshot_path = _make_snapshot(
        tmp_path,
        {
            "c1": {
                "prefLabel_fi": "annosaika",
                "avoidable_fi": ["annostusaika"],
                "deprecated_fi": [],
            },
            "c2": {
                "prefLabel_fi": "digitaalinen palvelupiste",
                "avoidable_fi": ["etäpalvelupiste"],
                "deprecated_fi": [],
            },
        },
    )

    sentences = [
        # annostusaika -> gradation t->d yields annostusajalla / annostusajan
        {"id": "e1:s1", "encounter_id": "e1", "sentence_id": "s1", "text": "Lääkkeen annostusajalla on selkeä vaikutus hoitovasteeseen."},
        # etäpalvelupiste -> gradation st->s or final e/te inflection: etäpalvelupisteessä
        {"id": "e1:s2", "encounter_id": "e1", "sentence_id": "s2", "text": "Palvelu toteutetaan etäpalvelupisteessä."},
    ]

    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    rule_info = load_term_rules_from_snapshot(data)
    hits, stats = check_sentences(sentences, rule_info["rules"])

    assert len(hits) == 2
    assert stats["sentences_with_hit"] == 2
    assert stats["sentences_not_covered"] == 0
    assert hits[0]["term"] == "annostusaika"
    assert hits[0]["matched_surface"].lower() == "annostusajalla"
    assert hits[1]["term"] == "etäpalvelupiste"
    assert hits[1]["matched_surface"].lower() == "etäpalvelupisteessä"


def test_clean_sentence_produces_no_hits(tmp_path: Path):
    snapshot_path = _make_snapshot(
        tmp_path,
        {
            "c1": {
                "prefLabel_fi": "annosaika",
                "avoidable_fi": ["annostusaika"],
                "deprecated_fi": [],
            }
        },
    )

    sentences = [
        {"id": "e1:s1", "encounter_id": "e1", "sentence_id": "s1", "text": "Potilaan annosaika on klo 8.00."},
        {"id": "e1:s2", "encounter_id": "e1", "sentence_id": "s2", "text": "Aktiivisena diagnoosina on essentiaalinen hypertensio."},
    ]

    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    rule_info = load_term_rules_from_snapshot(data)
    hits, stats = check_sentences(sentences, rule_info["rules"])

    assert len(hits) == 0
    assert stats["sentences_with_hit"] == 0
    assert stats["sentences_not_covered"] == 2
    assert stats["total_hits"] == 0
    assert stats["hits_per_sentence"] == 0.0


def test_absent_snapshot_returns_clean_absent_result(tmp_path: Path):
    non_existent = tmp_path / "does_not_exist.json"
    result = run_check(snapshot_path=non_existent, output_path=None)

    assert result["status"] == "snapshot_absent"
    assert result["exit_code"] == 0
    assert "Snapshot absent" in result["message"]


def test_count_arithmetic_and_structure(tmp_path: Path):
    snapshot_path = _make_snapshot(
        tmp_path,
        {
            "c1": {
                "prefLabel_fi": "käsite 1",
                "avoidable_fi": ["termi A", "termi B"],
                "deprecated_fi": ["termi C"],
            },
            "c2": {
                "prefLabel_fi": "käsite 2",
                "avoidable_fi": [],
                "deprecated_fi": ["termi D"],
            },
            "c3": {
                "prefLabel_fi": "käsite 3",
                "avoidable_fi": [],
                "deprecated_fi": [],
            },
        },
    )

    sentences = [
        {"id": "e1:s1", "encounter_id": "e1", "sentence_id": "s1", "text": "Tämä sisältää termi A ja myös termi B samassa lauseessa."},
        {"id": "e1:s2", "encounter_id": "e1", "sentence_id": "s2", "text": "Tämä sisältää vain termi D."},
        {"id": "e1:s3", "encounter_id": "e1", "sentence_id": "s3", "text": "Täysin puhdas lause ilman yhtäkään osumaa."},
        {"id": "e1:s4", "encounter_id": "e1", "sentence_id": "s4", "text": "Toinen puhdas lause."},
    ]

    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    rule_info = load_term_rules_from_snapshot(data)

    assert rule_info["terms_checked"] == 4
    assert rule_info["distinct_terms"] == 4
    assert rule_info["concepts_covered"] == 2  # c1 and c2 have discouraged/deprecated terms; c3 does not

    hits, stats = check_sentences(sentences, rule_info["rules"])

    # s1 has 2 hits, s2 has 1 hit, s3 and s4 have 0 hits -> total hits = 3
    assert stats["sentences_in_scope"] == 4
    assert stats["sentences_with_hit"] == 2
    assert stats["sentences_not_covered"] == 2
    assert stats["total_hits"] == 3
    assert stats["hits_per_sentence"] == 3 / 4 == 0.75

    out_file = tmp_path / "out.json"
    res = run_check(snapshot_path=snapshot_path, adjudication_path=tmp_path / "empty.json", output_path=out_file)
    assert out_file.exists()
    saved = json.loads(out_file.read_text(encoding="utf-8"))
    assert saved["terms_checked"] == 4
    assert saved["concepts_covered"] == 2


def test_vocabulary_coverage_arithmetic(tmp_path: Path):
    """Assert content word extraction and vocabulary coverage arithmetic."""
    # Synthetic snapshot with known vocabulary words
    snapshot_words = {"lääkitys", "verenpaine", "potilas"}
    sentences = [
        {"id": "s1", "text": "Potilas ilmoittaa että verenpaine on korkea ja lääkitys puuttuu."},
    ]

    cov = measure_vocabulary_coverage(sentences, snapshot_words)

    # Content words in sentence (excluding function words 'että', 'on', 'korkea' is content, 'ja'):
    # raw: potilas, ilmoittaa, että, verenpaine, on, korkea, ja, lääkitys, puuttuu
    # function words filtered: että, on, ja
    # distinct content words: ilmoittaa, korkea, lääkitys, potilas, puuttuu, verenpaine -> 6
    # covered: lääkitys, potilas, verenpaine -> 3
    # coverage_fraction = 3 / 6 = 0.5
    assert cov["narration_content_words"] == 6
    assert cov["covered_by_vocabulary"] == 3
    assert cov["coverage_fraction"] == 0.5
    assert len(cov["top_20_uncovered_words"]) == 3
    assert set(cov["top_20_uncovered_words"]) == {"ilmoittaa", "korkea", "puuttuu"}
