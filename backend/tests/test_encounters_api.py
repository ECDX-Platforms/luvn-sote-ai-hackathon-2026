"""Paging, search and whole-catalogue aggregation on `GET /api/encounters`.

The catalogue used to be one bare JSON list, so a client could neither page it nor count it without
downloading all of it first. These tests pin the replacement: a page, a matching total, and counts that
describe the whole catalogue whatever the page happens to be.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.encounters import EncounterMetadata
from tests.fixtures.bound_encounters import ENCOUNTERS, EXAMPLE_LABELS
from app.fixtures import load_personas
from app.store import Store, get_store


@pytest.fixture
def api():
    yield from _client(EXAMPLE_LABELS)


def _client(example_labels: dict):
    store = Store(personas=load_personas(), path=None, encounters=ENCOUNTERS, example_labels=example_labels)
    main.app.dependency_overrides[get_store] = lambda: store
    with TestClient(main.app) as client:
        yield client
    main.app.dependency_overrides.clear()


def page(api: TestClient, **params) -> dict:
    response = api.get("/api/encounters", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def ids(payload: dict) -> list[str]:
    return [row["encounter_id"] for row in payload["encounters"]]


# The fixture catalogue, in id order (the worklist's stable order): nine encounters over six people, four
# purposes and five professional roles. Hardcoded here on purpose — a test that read the order from the
# endpoint could not tell a reorder from a paging bug.
CATALOGUE_IDS = [
    "bound-encounter-002-laakitys-sairaanhoitaja",
    "bound-encounter-003-mielenterveys-laakari",
    "bound-encounter-003-mielenterveys-sairaanhoitaja",
    "bound-encounter-003-toimintakyky-fysioterapeutti",
    "bound-encounter-003-toimintakyky-sairaanhoitaja",
    "bound-encounter-004-sosiaalipalvelut-sosiaalityontekija",
    "bound-encounter-005-laakitys-sairaanhoitaja",
    "bound-encounter-012-neuvola-terveydenhoitaja",
    "bound-encounter-013-vammaispalvelut-sosiaalityontekija",
]


# ---------- shape and defaults ----------


def test_the_default_page_carries_the_whole_bounded_catalogue(api):
    payload = page(api)
    assert set(payload) == {"encounters", "total", "count", "limit", "offset", "query", "role", "aggregate"}
    assert payload["limit"] == main.DEFAULT_ENCOUNTER_LIMIT
    assert payload["offset"] == 0
    assert payload["query"] == ""
    assert payload["role"] is None
    assert payload["count"] == len(payload["encounters"]) == len(CATALOGUE_IDS)
    assert payload["total"] == len(CATALOGUE_IDS)
    assert ids(payload) == CATALOGUE_IDS
    # The row contract the SPA navigates by is unchanged by the envelope.
    assert set(payload["encounters"][0]) == {
        "encounter_id", "role", "role_label", "purpose", "purpose_label", "example_label", "picker_label",
    }


# ---------- paging boundaries ----------


def test_pages_partition_the_catalogue_without_a_gap_or_a_repeat(api):
    collected: list[str] = []
    offset = 0
    while True:
        payload = page(api, limit=4, offset=offset)
        collected.extend(ids(payload))
        if offset + payload["count"] >= payload["total"]:
            break
        offset += 4
    assert collected == CATALOGUE_IDS
    assert len(collected) == len(set(collected))


def test_first_page_and_a_short_last_page(api):
    first = page(api, limit=4, offset=0)
    assert first["count"] == 4
    assert first["total"] == 9
    assert ids(first) == CATALOGUE_IDS[:4]

    last = page(api, limit=4, offset=8)
    assert last["count"] == 1
    assert last["total"] == 9
    assert ids(last) == CATALOGUE_IDS[8:]


def test_offset_beyond_the_end_is_an_empty_page_not_an_error(api):
    payload = page(api, offset=9)
    assert payload["count"] == 0
    assert payload["encounters"] == []
    assert payload["total"] == len(CATALOGUE_IDS)
    assert page(api, offset=10_000)["encounters"] == []


def test_the_limit_maximum_is_enforced_and_never_silently_clamped(api):
    assert page(api, limit=main.MAX_ENCOUNTER_LIMIT)["limit"] == main.MAX_ENCOUNTER_LIMIT
    for rejected in (main.MAX_ENCOUNTER_LIMIT + 1, 0, -1):
        assert api.get("/api/encounters", params={"limit": rejected}).status_code == 422, rejected


def test_a_negative_offset_is_rejected(api):
    assert api.get("/api/encounters", params={"offset": -1}).status_code == 422


def test_non_integer_paging_is_rejected(api):
    assert api.get("/api/encounters", params={"limit": "many"}).status_code == 422
    assert api.get("/api/encounters", params={"offset": "1.5"}).status_code == 422


# ---------- search ----------


def test_search_matches_the_person_and_the_purpose_and_reports_the_matching_total(api):
    by_person = page(api, q="Elias")
    assert by_person["total"] == 4
    assert {row["example_label"] for row in by_person["encounters"]} == {"Elias Korhonen"}

    by_purpose = page(api, q="Lääkityksen seuranta")
    assert by_purpose["total"] == 2
    assert {row["example_label"] for row in by_purpose["encounters"]} == {"Aino Virtanen", "Taisto Mäkinen"}

    # The role label is a searchable display field too: it is what the professional reads beside a row.
    assert page(api, q="Fysioterapeutti")["total"] == 1


def test_search_is_case_insensitive(api):
    lower = page(api, q="korhonen")
    upper = page(api, q="KORHONEN")
    mixed = page(api, q="KoRhOnEn")
    assert lower["total"] == upper["total"] == mixed["total"] == 4
    assert ids(lower) == ids(upper) == ids(mixed)


def test_search_handles_finnish_characters_and_a_keyboard_without_them(api):
    """`MÄKINEN` = `mäkinen` = `makinen`: matching folds case and the diacritic, not the meaning."""
    for needle in ("Mäkinen", "mäkinen", "MÄKINEN", "Makinen", "makinen"):
        payload = page(api, q=needle)
        assert payload["total"] == 1, needle
        assert payload["encounters"][0]["example_label"] == "Taisto Mäkinen", needle
    # And an umlaut that is genuinely absent still matches nothing, so the fold is a normaliser, not a
    # wildcard.
    assert page(api, q="Niemelä")["total"] == 0


def test_search_reports_no_match_without_inventing_a_row(api):
    payload = page(api, q="olematon-henkilo")
    assert payload["encounters"] == []
    assert payload["count"] == 0
    assert payload["total"] == 0
    assert payload["aggregate"]["matching"] == {"encounters": 0, "people": 0, "purposes": 0}
    # A search never widens to the internal identifiers a professional is not shown.
    assert page(api, q="bound-")["total"] == 0
    assert page(api, q="bound-persona-003")["total"] == 0


def test_an_over_long_query_is_rejected(api):
    assert api.get("/api/encounters", params={"q": "x" * (main.MAX_ENCOUNTER_SEARCH_CHARS + 1)}).status_code == 422


def test_blank_search_is_not_a_search(api):
    for blank in ("", "   "):
        payload = page(api, q=blank)
        assert payload["total"] == len(CATALOGUE_IDS), repr(blank)


# ---------- the role lens ----------


def test_the_role_filter_pages_one_lens_without_changing_the_whole_catalogue_counts(api):
    first = page(api, role="sairaanhoitaja", limit=2, offset=0)
    second = page(api, role="sairaanhoitaja", limit=2, offset=2)
    assert first["total"] == second["total"] == 4
    assert all(row["role"] == "sairaanhoitaja" for row in first["encounters"] + second["encounters"])
    assert ids(first) + ids(second) == [
        "bound-encounter-002-laakitys-sairaanhoitaja",
        "bound-encounter-003-mielenterveys-sairaanhoitaja",
        "bound-encounter-003-toimintakyky-sairaanhoitaja",
        "bound-encounter-005-laakitys-sairaanhoitaja",
    ]
    # The whole-catalogue aggregate is what the dashboard reads, and a role lens cannot narrow it.
    assert first["aggregate"]["catalogue"]["encounters"] == 9
    assert [entry["role"] for entry in first["aggregate"]["roster"]] == [
        "sairaanhoitaja", "laakari", "fysioterapeutti", "sosiaalityontekija", "terveydenhoitaja",
    ]
    assert first["aggregate"]["matching"]["encounters"] == 4


def test_an_unknown_role_is_an_empty_lens_not_an_error(api):
    payload = page(api, role="elakevirkailija")
    assert payload["encounters"] == []
    assert payload["total"] == 0
    assert payload["aggregate"]["catalogue"]["encounters"] == 9


# ---------- the aggregate is the whole catalogue, never the page ----------


def test_the_aggregate_counts_the_whole_catalogue_not_the_page(api):
    payload = page(api, limit=1)
    assert payload["count"] == 1
    assert payload["aggregate"]["catalogue"] == {
        "encounters": 9, "people": 6, "purposes": 4, "roles": 5,
    }
    # The roster is whole-catalogue too, so a Rooli selector is complete on the first page.
    assert len(payload["aggregate"]["roster"]) == 5
    assert payload["aggregate"]["matching"] == {"encounters": 9, "people": 6, "purposes": 4}


def test_a_narrowed_search_narrows_matching_but_never_the_catalogue_counts(api):
    payload = page(api, q="Elias", limit=1)
    assert payload["count"] == 1
    assert payload["total"] == 4
    assert payload["aggregate"]["matching"] == {"encounters": 4, "people": 1, "purposes": 2}
    # The dashboard counts the corpus, not the search: this is the whole distinction the field exists for.
    assert payload["aggregate"]["catalogue"]["encounters"] == 9


def test_the_summary_counts_agree_with_the_rows_they_describe(api):
    """Non-vacuity: `matching` is derived from the same rows `total` counts, so the two cannot drift."""
    for params in ({}, {"q": "Elias"}, {"role": "sosiaalityontekija"}, {"q": "Lääkitys", "role": "sairaanhoitaja"}):
        payload = page(api, **params)
        # The default page holds the whole match, so the counts and the rendered rows must be one thing.
        assert payload["count"] == payload["total"], params
        assert payload["aggregate"]["matching"] == {
            "encounters": payload["total"],
            # People are source persons (COUNT(DISTINCT source_person_id)), never display labels.
            "people": len({ENCOUNTERS[row["encounter_id"]].source_person_id for row in payload["encounters"]}),
            "purposes": len({row["purpose_label"] for row in payload["encounters"]}),
        }, params


# ---------- people are source persons, never display names ----------


def test_two_people_who_share_a_display_name_are_two_people():
    """`people` counts source persons. Sanni Lehtinen's encounter is relabelled to Elias Korhonen's name:
    two different people now share one label, so the distinct labels fall to 5 and the people stay at 6."""
    api = next(_client({**EXAMPLE_LABELS,
                        "bound-encounter-004-sosiaalipalvelut-sosiaalityontekija": "Elias Korhonen"}))
    payload = page(api)
    labels = {row["example_label"] for row in payload["encounters"]}
    assert len(labels) == 5, "the distinct labels are the five names; a label count would say 5 people"
    assert payload["aggregate"]["catalogue"]["people"] == 6
    assert payload["aggregate"]["matching"]["people"] == 6


def test_one_person_under_two_labels_is_still_one_person():
    """The reverse: Elias's toimintakyky lens carries a different display label. Labels rise, people do not."""
    api = next(_client({**EXAMPLE_LABELS, "bound-encounter-003-toimintakyky-sairaanhoitaja": "Elias K."}))
    payload = page(api)
    labels = {row["example_label"] for row in payload["encounters"]}
    assert len(labels) == 7
    assert payload["aggregate"]["catalogue"]["people"] == 6
    assert page(api, q="Elias")["aggregate"]["matching"]["people"] == 1


def test_the_people_count_is_over_distinct_source_persons_not_labels():
    entries = [
        EncounterMetadata("a", "src-1", "sairaanhoitaja", "medication_follow_up", "Same Name", "", ("self",), "adult"),
        EncounterMetadata("b", "src-2", "sairaanhoitaja", "medication_follow_up", "Same Name", "", ("self",), "adult"),
        EncounterMetadata("c", "src-2", "laakari", "mental_health_assessment", "Other Label", "", ("self",), "adult"),
        EncounterMetadata("d", None, "laakari", "mental_health_assessment", "Unbound", "", ("self",), "adult"),
    ]
    assert main._encounter_summary(entries) == {"encounters": 4, "people": 2, "purposes": 2}
    assert main._encounter_summary([]) == {"encounters": 0, "people": 0, "purposes": 0}
