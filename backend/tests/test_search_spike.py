"""Offline tests for the search spike's invariants — no network, no service required.

The spike's absolute property is that a query cannot be issued without a cut-derived allowlist, so that
is asserted here rather than only in the live run. The ranking helpers are also pinned so a later change
to them cannot silently change a recorded comparison.
"""

from __future__ import annotations

import pytest

from scripts.run_search_spike import SearchClient, SearchError, _bm25_scores, _escaped, _query_text


def test_a_query_without_an_allowlist_is_refused_before_any_request():
    client = SearchClient("https://example.search.windows.net", "not-a-real-key")
    try:
        with pytest.raises(SearchError, match="no record-id allowlist"):
            client.search("anything", [], top=5)
    finally:
        client.close()


def test_the_unfiltered_path_is_explicit_and_separate():
    """The negative control is the only caller that may omit the filter, and it says so at the call site."""
    client = SearchClient("https://example.search.windows.net", "not-a-real-key")
    try:
        # Still a network call, so it must fail at the transport rather than raise the filter guard.
        with pytest.raises(Exception) as error:
            client.search("anything", [], top=5, unfiltered=True)
        assert "no record-id allowlist" not in str(error.value)
    finally:
        client.close()


def test_odata_literals_escape_a_single_quote():
    assert _escaped(["a", "o'brien"]) == "a,o''brien"


def test_odata_literals_separate_ids_with_a_comma():
    assert _escaped(["n-1", "n-2"]) == "n-1,n-2"


def test_bm25_ranks_the_note_that_matches_the_purpose_terms_first():
    query = "Lääkityksen seuranta: toteutuminen, annos ja haitat"
    names = ["a", "b", "c"]
    texts = ["Naapurin koira haukkui öisin ja isännöitsijä tarvitaan.",
             "Asiakas jätti lääkityksen ottamatta; annos ja haitat tarkistetaan huimauksen vuoksi.",
             "Lääkityksen seurantalomake arkistoon, ei havaintoja voinnista."]
    scores = _bm25_scores(query, names, texts)
    assert scores["b"] > scores["c"] > scores["a"]


def test_the_query_text_carries_the_purpose_and_the_role():
    class _Encounter:
        role = "sairaanhoitaja"
        purpose = "medication_follow_up"

    text = _query_text(_Encounter())
    assert "sairaanhoitaja" in text
    assert "Lääkityksen seuranta" in text
    assert "verenpaine" in text
