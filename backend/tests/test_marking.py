"""The machine-readable marking for model-drafted text (AI Act Art. 50(2)).

The marking is emitted by the `/picture` route from the generation branch's *actual result*.
Two failures matter: marking a payload that contains no generated text (untrue), and failing to
mark one that does (the obligation missed while still being claimed).
"""

import asyncio

from fastapi.testclient import TestClient

from app import provenance
from app.main import app
from app.picture import Picture
from app.pipeline import build_picture_for_encounter
from tests.fixtures.bound_encounters import ENCOUNTERS

PRINCIPAL = {"x-ms-client-principal-id": "oid-1234", "x-ms-client-principal-name": "nurse@example.fi"}

ENCOUNTER = "bound-encounter-002-laakitys-sairaanhoitaja"
MARKING_HEADERS = (
    provenance.HEADER_AI_GENERATED,
    provenance.HEADER_AI_GENERATED_PARTS,
    provenance.HEADER_AI_GENERATED_MODEL,
    provenance.HEADER_AI_GENERATED_MARKING,
)


def test_no_marking_is_emitted_when_nothing_was_generated():
    """A fail-closed view is entirely deterministic; marking it AI-generated would be untrue."""
    assert provenance.marking_headers(generated=False) == {}
    assert provenance.marking_headers(generated=False, model="m", deployment="d") == {}


def test_marking_names_the_part_and_the_marking_version():
    headers = provenance.marking_headers(generated=True)
    assert headers[provenance.HEADER_AI_GENERATED] == "true"
    assert headers[provenance.HEADER_AI_GENERATED_PARTS] == provenance.PART_NARRATIVE
    assert headers[provenance.HEADER_AI_GENERATED_MARKING] == "luvn-ai-marking-v2"
    # No model was reported by the branch, so no model claim is made.
    assert provenance.HEADER_AI_GENERATED_MODEL not in headers


def test_marking_carries_the_reported_model_as_provenance_only():
    headers = provenance.marking_headers(generated=True, model="gpt-6.1-sol", deployment="proj-default")
    assert headers[provenance.HEADER_AI_GENERATED_MODEL] == "proj-default / gpt-6.1-sol"


def test_an_identical_deployment_and_model_are_not_repeated():
    """An environment that reports one name for both must not render it twice.

    Live verification on the hosted revision caught this: the header read
    "gpt-6.1-sol / gpt-6.1-sol".

    The mechanism is **not** a missing environment variable — there is no
    `LUNA_FOUNDRY_DEPLOYMENT` anywhere in this repository. `narration.py` sets
    `narration_generation_deployment` to `llm.FOUNDRY_MODEL` itself, and Azure echoes that same value
    back as `response.model`, so the two sides of the pair are the same name by construction.

    A wart in the same veins, left as it is because it is not observable here: `llm.FOUNDRY_MODEL` is
    captured at import time while `llm._configured_model()` re-reads the environment per call, so if
    `LUNA_FOUNDRY_MODEL` were changed at runtime the two sides would be read at different times.
    """
    same = provenance.marking_headers(generated=True, model="gpt-6.1-sol", deployment="gpt-6.1-sol")
    assert same[provenance.HEADER_AI_GENERATED_MODEL] == "gpt-6.1-sol"
    # A distinct deployment stays separated: the pair is information, not noise.
    distinct = provenance.marking_headers(generated=True, model="gpt-6.1-sol", deployment="proj-default")
    assert distinct[provenance.HEADER_AI_GENERATED_MODEL] == "proj-default / gpt-6.1-sol"
    # And an absent deployment never renders an empty left side.
    assert provenance.marking_headers(
        generated=True, model="gpt-6.1-sol", deployment=None
    )[provenance.HEADER_AI_GENERATED_MODEL] == "gpt-6.1-sol"


def test_a_control_character_in_the_model_name_drops_the_header():
    """The model name is external input (`llm.py` reads it from the provider's JSON body), and the
    only upstream check is a non-empty string. A CR or LF inside it would inject further headers on
    the wire, because h11 permits CR/LF inside a header value. The value is dropped, not sanitised:
    an unmarked model name beats a forged header, and it fails closed like the rest of the marking.
    """
    inject = provenance.marking_headers(
        generated=True, model="gpt-6.1-sol\r\nX-Injected: 1", deployment="proj-default"
    )
    assert provenance.HEADER_AI_GENERATED_MODEL not in inject
    # The rest of the marking still ships: only the unsafe value is withheld.
    assert inject[provenance.HEADER_AI_GENERATED] == "true"

    for hostile in ("gpt\n6.1", "gpt\r6.1", "gpt\x00sol", "gpt\x7fsol", "\r\n"):
        headers = provenance.marking_headers(generated=True, model=hostile)
        assert provenance.HEADER_AI_GENERATED_MODEL not in headers, f"control character survived: {hostile!r}"
    # A deployment that is itself hostile cannot smuggle a pair either.
    pair = provenance.marking_headers(generated=True, model="gpt-6.1-sol", deployment="proj\r\nX: 1")
    assert pair[provenance.HEADER_AI_GENERATED_MODEL] == "gpt-6.1-sol"


def test_the_marked_part_names_a_real_picture_field():
    """The marking claims which part of the payload a model drafted. If `Picture.narrative` were
    renamed and this constant were not, the marking would point at a field that no longer exists —
    a silent lie in the one place whose whole job is honesty about provenance."""
    assert provenance.PART_NARRATIVE in Picture.model_fields
    assert provenance.PART_NARRATIVE == "narrative"


def test_marking_makes_no_legal_claim():
    """The marking states provenance. A compliance or watermark claim would be a false claim."""
    for value in provenance.marking_headers(
        generated=True, model="gpt-6.1-sol", deployment="proj-default"
    ).values():
        lowered = value.lower()
        for forbidden in ("compliant", "compliance", "art", "watermark", "certified"):
            assert forbidden not in lowered, f"the marking must not claim {forbidden!r}: {value}"


def test_a_generated_picture_is_marked_and_a_fail_closed_one_is_not():
    """The route keys off `narration_generation_status` and the prose, not off configuration."""
    client = TestClient(app)

    # Without live Foundry there is no accepted prose: nothing may be marked.
    plain = client.get(f"/api/encounters/{ENCOUNTER}/picture", headers=PRINCIPAL)
    assert plain.status_code == 200
    assert plain.json()["narration_generation_status"] != "generated"
    assert not any(name in plain.headers for name in MARKING_HEADERS), (
        "a response with no accepted prose must not be marked as AI-generated"
    )

    fail_closed = client.get(f"/api/encounters/{ENCOUNTER}/picture", params={"mode": "foundry-off"}, headers=PRINCIPAL)
    assert fail_closed.status_code == 200
    assert fail_closed.json()["narrative"] is None
    assert not any(name in fail_closed.headers for name in MARKING_HEADERS)


def test_a_rejected_payload_is_not_marked_even_though_its_status_is_generated(monkeypatch):
    """The trap the module warns about, pinned at the route.

    `narration.py`'s `_WIRE_STATUS` maps `candidate_invalid`, `support_timeout`, `support_failed`,
    `support_invalid`, `unsupported` and `unknown` to `generation_status == "generated"` while
    rejecting the payload — no narrative, no accepted prose. A route that keyed on the status alone
    would mark a fail-closed response as AI-generated, which would be untrue. Every other test in
    this file drives the status with accepted prose, so this is the only case that distinguishes
    the two rules.
    """
    base = asyncio.run(build_picture_for_encounter(ENCOUNTER, encounters=ENCOUNTERS))
    rejected = base.model_copy(update={
        "narrative": None,
        "narration_generation_status": "generated",
        "narration_acceptance_status": "rejected",
        "narration_support_status": "unknown",
        "narration_generation_model": None,
        "narration_generation_deployment": "gpt-6.1-sol",
    })

    async def fake_build(encounter_id, **kwargs):
        return rejected

    monkeypatch.setattr("app.main.build_picture_for_encounter", fake_build)
    response = TestClient(app).get(f"/api/encounters/{ENCOUNTER}/picture", headers=PRINCIPAL)
    assert response.status_code == 200
    assert response.json()["narration_generation_status"] == "generated"
    assert response.json()["narrative"] is None
    assert not any(name in response.headers for name in MARKING_HEADERS), (
        "a rejected payload whose status reads `generated` must not be marked as AI-generated"
    )


def test_the_route_marks_a_picture_whose_generation_branch_succeeded(monkeypatch):
    """The positive branch, exercised without a live model: the real deterministic picture, with
    only the generation result changed to what an accepted call produces."""
    base = asyncio.run(build_picture_for_encounter(ENCOUNTER, encounters=ENCOUNTERS))
    generated = base.model_copy(update={
        "narrative": "Hyväksytty lause.",
        "narration_generation_status": "generated",
        "narration_generation_model": "gpt-6.1-sol",
        "narration_generation_deployment": "proj-default",
        "narration_acceptance_status": "accepted",
    })

    async def fake_build(encounter_id, **kwargs):
        return generated

    monkeypatch.setattr("app.main.build_picture_for_encounter", fake_build)
    response = TestClient(app).get(f"/api/encounters/{ENCOUNTER}/picture", headers=PRINCIPAL)
    assert response.status_code == 200
    assert response.headers[provenance.HEADER_AI_GENERATED] == "true"
    assert response.headers[provenance.HEADER_AI_GENERATED_PARTS] == "narrative"
    assert response.headers[provenance.HEADER_AI_GENERATED_MODEL] == "proj-default / gpt-6.1-sol"
    # The marking describes the response, not the records in it.
    assert "source" not in response.headers[provenance.HEADER_AI_GENERATED_PARTS]
