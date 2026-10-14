"""The per-actor access policy: the contract, the refusals, and the open demo it must not break.

Two behaviours are equally load-bearing and both are pinned here. With no policy configured the product
behaves exactly as it did before authorization existed — authentication is the boundary and any authenticated
presenter may read any encounter — which is what keeps the demo working out of the box. With a policy
configured it fails closed: the catalogue is the caller's scope and every encounter-scoped route refuses an
encounter outside it, with one body whether or not that encounter exists.
"""

import json

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import access
from app.actor import Actor
from app.fixtures import load_personas
from app.store import Store, get_store
from tests.fixtures.bound_encounters import ENCOUNTERS, EXAMPLE_LABELS

# Every write route records the platform principal; these are the headers Entra Easy Auth injects.
NURSE = {"x-ms-client-principal-id": "oid-1234", "x-ms-client-principal-name": "nurse@example.fi"}
OTHER = {"x-ms-client-principal-id": "oid-other", "x-ms-client-principal-name": "other@example.fi"}
# A real catalogue encounter, and one the policies below never grant.
NURSES_OWN = "bound-encounter-002-laakitys-sairaanhoitaja"
OUT_OF_SCOPE = "bound-encounter-013-vammaispalvelut-sosiaalityontekija"

ALL_ENCOUNTER_IDS = {
    "bound-encounter-002-laakitys-sairaanhoitaja",
    "bound-encounter-003-mielenterveys-sairaanhoitaja",
    "bound-encounter-003-toimintakyky-sairaanhoitaja",
    "bound-encounter-003-mielenterveys-laakari",
    "bound-encounter-003-toimintakyky-fysioterapeutti",
    "bound-encounter-004-sosiaalipalvelut-sosiaalityontekija",
    "bound-encounter-005-laakitys-sairaanhoitaja",
    "bound-encounter-012-neuvola-terveydenhoitaja",
    "bound-encounter-013-vammaispalvelut-sosiaalityontekija",
}


@pytest.fixture
def runtime_client():
    store = Store(personas=load_personas(), path=None, encounters=ENCOUNTERS, example_labels=EXAMPLE_LABELS)
    main.app.dependency_overrides[get_store] = lambda: store
    with TestClient(main.app) as client:
        yield client, store
    main.app.dependency_overrides.clear()


def _policy(*rules: dict) -> str:
    return json.dumps({"version": 1, "rules": list(rules)})


def _nurse_scope(**overrides) -> str:
    """A policy that grants the nurse exactly one encounter, by object id and by UPN."""
    rule = {"objects": ["oid-1234"], "upns": ["nurse@example.fi"],
            "encounters": [NURSES_OWN], **overrides}
    return _policy(rule)


# -------------------------------------------------------------------------------------------------
# The open half. These are the assertions that keep the mechanism opt-in.
# -------------------------------------------------------------------------------------------------

def test_no_policy_configured_keeps_the_catalogue_open_to_any_caller(runtime_client, monkeypatch):
    """Unset policy = today's behaviour, exactly, and this test is the one that pins it.

    This is the constraint that keeps the demo working out of the box, so it is asserted here explicitly
    and not merely implied by the older tests that happen not to configure a policy. The unauthenticated
    request stands in for "the ingress let this presenter through": the header is the platform's, and the
    application's open path is defined on the absence of a *configured policy*, not on the presence of a
    header, which is exactly the pre-change behaviour.
    """
    monkeypatch.delenv(access.POLICY_ENV, raising=False)
    monkeypatch.delenv(access.POLICY_FILE_ENV, raising=False)
    client, _ = runtime_client

    for headers in ({}, NURSE):
        response = client.get("/api/encounters", headers=headers)
        assert response.status_code == 200
        assert {row["encounter_id"] for row in response.json()["encounters"]} == ALL_ENCOUNTER_IDS
        assert response.json()["total"] == len(ALL_ENCOUNTER_IDS)

    # The briefing read now needs a principal (Change 1), but with no policy any principal reads any encounter.
    assert client.get(f"/api/encounters/{NURSES_OWN}/picture").status_code == 401
    assert client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=NURSE).status_code == 200
    assert client.get(f"/api/encounters/{OUT_OF_SCOPE}/picture", headers=OTHER).status_code == 200


def test_an_empty_policy_variable_is_absent_not_a_policy(runtime_client, monkeypatch):
    """Container Apps can materialise an unset variable as ""; that must not read as "configured"."""
    monkeypatch.setenv(access.POLICY_ENV, "   ")
    client, _ = runtime_client
    response = client.get("/api/encounters", headers=NURSE)
    assert response.status_code == 200
    assert {row["encounter_id"] for row in response.json()["encounters"]} == ALL_ENCOUNTER_IDS


# -------------------------------------------------------------------------------------------------
# The closed half: the catalogue is scoped, and every encounter-scoped route refuses.
# -------------------------------------------------------------------------------------------------

def test_configured_policy_returns_only_the_callers_encounters_in_the_catalogue(runtime_client, monkeypatch):
    monkeypatch.setenv(access.POLICY_ENV, _nurse_scope())
    client, _ = runtime_client

    mine = client.get("/api/encounters", headers=NURSE).json()
    assert [row["encounter_id"] for row in mine["encounters"]] == [NURSES_OWN]
    assert mine["total"] == 1
    # The counts are the caller's counts, not the operator's corpus: a withheld encounter must not reach the
    # response even as a number.
    assert mine["aggregate"]["catalogue"]["encounters"] == 1
    assert mine["aggregate"]["catalogue"]["people"] == 1

    # A principal the policy does not name gets nothing — not the open fallback, and not an error page.
    unknown = client.get("/api/encounters", headers=OTHER)
    assert unknown.status_code == 200
    assert unknown.json()["encounters"] == []
    assert unknown.json()["total"] == 0
    assert unknown.json()["aggregate"]["catalogue"]["encounters"] == 0

    # An anonymous caller under a configured policy gets nothing either; with the policy unset it read the
    # whole catalogue, which is the difference the policy is for.
    anonymous = client.get("/api/encounters")
    assert anonymous.status_code == 200
    assert anonymous.json()["encounters"] == []
    assert OUT_OF_SCOPE not in anonymous.text


def test_configured_policy_refuses_every_encounter_scoped_route_outside_the_scope(runtime_client, monkeypatch):
    """`/picture`, `/chat/prompts`, `/decisions` and `POST /chat/stream`, `POST /feedback` — one body for all.

    The refusal is checked for absence of disclosure as well as for its status: a withheld encounter must
    not appear as an id, a label or a person's name in the body of a refusal.
    """
    monkeypatch.setenv(access.POLICY_ENV, _nurse_scope())
    client, _ = runtime_client
    forbidden = "this encounter is outside the scope configured for this caller"
    probes = [
        ("get", f"/api/encounters/{OUT_OF_SCOPE}/picture", None),
        ("get", f"/api/encounters/{OUT_OF_SCOPE}/chat/prompts", None),
        ("get", f"/api/encounters/{OUT_OF_SCOPE}/decisions", None),
        ("post", f"/api/encounters/{OUT_OF_SCOPE}/chat/stream",
         {"turn_id": "probe-turn", "question": "mitkä ovat seuraavat toimenpiteet"}),
        ("post", f"/api/encounters/{OUT_OF_SCOPE}/feedback",
         {"event_id": "probe-feedback", "proposal_id": "anything", "rule_version": "anything",
          "action": "accept"}),
    ]
    for method, path, body in probes:
        call = getattr(client, method)
        response = call(path, json=body, headers=NURSE) if body is not None else call(path, headers=NURSE)
        assert response.status_code == 403, (method, path, response.status_code, response.text)
        assert response.json() == {"detail": forbidden}, (method, path, response.text)
        assert OUT_OF_SCOPE not in response.text
        assert "Juho Nieminen" not in response.text
        assert "vammaispalvelut" not in response.text

    # The caller's own encounter still answers on every one of them, so the policy narrowed access rather
    # than turning the route off.
    assert client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=NURSE).status_code == 200
    assert client.get(f"/api/encounters/{NURSES_OWN}/chat/prompts", headers=NURSE).status_code == 200
    assert client.get(f"/api/encounters/{NURSES_OWN}/decisions", headers=NURSE).status_code == 200
    assert client.post(f"/api/encounters/{NURSES_OWN}/chat/stream",
                       json={"turn_id": "mine", "question": "mitkä ovat seuraavat toimenpiteet"},
                       headers=NURSE).status_code == 200


def test_forbidden_and_absent_encounters_are_indistinguishable_from_a_known_caller(runtime_client, monkeypatch):
    """There is no existence oracle: an unknown id and a withheld id answer the same 403, byte for byte.

    The catalogue is the picker, so a human only ever types an id outside the offered paths; an id-guessing
    caller is the adversary, and this is the pair that would tell them whether a person exists.
    """
    monkeypatch.setenv(access.POLICY_ENV, _nurse_scope())
    client, _ = runtime_client
    withheld = client.get(f"/api/encounters/{OUT_OF_SCOPE}/picture", headers=NURSE)
    absent = client.get("/api/encounters/encounter-removed/picture", headers=NURSE)
    assert withheld.status_code == absent.status_code == 403
    assert withheld.json() == absent.json()


def test_the_one_body_promise_is_bounded_to_ids_on_this_routes_own_path(runtime_client, monkeypatch):
    """The exact shape of the contract, including the part that is *not* it.

    The promise is "the same 403 whether or not the encounter exists" for an encounter id on a scoped
    route's canonical path. A non-canonical spelling matches no route, so the id is never looked at and the
    answer is the application's ordinary unknown-API `404` — the same bytes for an in-scope, an
    out-of-scope and an absent id, so the shape is still not an existence oracle. This test pins both, so
    the wording in `require_encounter` and the behaviour cannot drift apart in either direction: a
    trailing slash must not start answering 403 for a real id, and the canonical path must not start
    answering 404 for one.
    """
    monkeypatch.setenv(access.POLICY_ENV, _nurse_scope())
    client, _ = runtime_client

    # Canonical path: the contract's one body for an out-of-scope id, whether or not it exists, and 200 for
    # the caller's own encounter — the policy narrowed access, it did not close the route.
    for encounter_id in (OUT_OF_SCOPE, "encounter-removed"):
        assert client.get(f"/api/encounters/{encounter_id}/picture", headers=NURSE).status_code == 403
    assert client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=NURSE).status_code == 200

    # Non-canonical shapes: one uniform 404, and no encounter id or scope text in it. An in-scope id, an
    # out-of-scope id and a nonexistent one are byte-identical, which is the property that matters.
    shapes = [
        "/api/encounters/{id}/picture/",
        "/api/encounters//picture",
        "/api/encounters/{id}//picture",
    ]
    for shape in shapes:
        bodies = {
            encounter_id: client.get(shape.format(id=encounter_id), headers=NURSE)
            for encounter_id in (NURSES_OWN, OUT_OF_SCOPE, "encounter-removed")
        }
        for encounter_id, response in bodies.items():
            assert response.status_code == 404, (shape, encounter_id, response.status_code)
            assert response.json() == {"detail": "Not Found"}
        assert len({response.text for response in bodies.values()}) == 1, shape
        assert OUT_OF_SCOPE not in next(iter(bodies.values())).text


def test_configured_policy_refuses_an_unknown_principal_and_an_anonymous_request(runtime_client, monkeypatch):
    """A caller the policy does not name gets nothing, and never falls back to open access."""
    monkeypatch.setenv(access.POLICY_ENV, _nurse_scope())
    client, _ = runtime_client
    # A named-but-unauthorised caller: authenticated, so the refusal is 403 on every scoped route.
    assert client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=OTHER).status_code == 403
    assert client.get(f"/api/encounters/{NURSES_OWN}/chat/prompts", headers=OTHER).status_code == 403
    assert client.get(f"/api/encounters/{NURSES_OWN}/decisions", headers=OTHER).status_code == 403
    # A caller with no principal at all is refused too — never the open fallback — and the routes that need
    # an actor answer their own 401 first, so the auth half of the contract did not move.
    assert client.get(f"/api/encounters/{NURSES_OWN}/picture").status_code == 401
    assert client.get(f"/api/encounters/{NURSES_OWN}/chat/prompts").status_code == 401
    assert client.post(f"/api/encounters/{NURSES_OWN}/chat/stream",
                       json={"turn_id": "anonymous", "question": "x y z"}).status_code == 401
    assert client.get(f"/api/encounters/{NURSES_OWN}/decisions").status_code == 401


def test_a_deny_rule_in_front_of_a_broader_rule_revokes_the_principal(runtime_client, monkeypatch):
    """First match wins, so an operator can revoke one principal without editing the broad rule."""
    monkeypatch.setenv(access.POLICY_ENV, _policy(
        {"description": "revoked", "upns": ["nurse@example.fi"], "action": "deny"},
        {"description": "everyone else in the team", "upns": ["temp@example.fi"],
         "encounter_prefixes": ["bound-encounter-"]},
    ))
    client, _ = runtime_client
    assert client.get("/api/encounters", headers=NURSE).json()["encounters"] == []
    assert client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=NURSE).status_code == 403
    assert client.get("/api/encounters", headers=OTHER).json()["encounters"] == []


def test_a_prefix_scope_still_excludes_encounters_outside_it(runtime_client, monkeypatch):
    """A prefix rule matches the prefix and nothing else — it is not "unrestricted"."""
    monkeypatch.setenv(access.POLICY_ENV, _policy(
        {"upns": ["nurse@example.fi"], "encounter_prefixes": ["bound-encounter-002-"]}))
    client, _ = runtime_client
    rows = client.get("/api/encounters", headers=NURSE).json()["encounters"]
    assert [row["encounter_id"] for row in rows] == [NURSES_OWN]
    assert client.get("/api/encounters/bound-encounter-005-laakitys-sairaanhoitaja/picture",
                      headers=NURSE).status_code == 403


def test_a_suffix_scope_matches_the_suffix_and_nothing_else(runtime_client, monkeypatch):
    """A suffix rule matches the suffix and nothing else — it is not "unrestricted" either."""
    monkeypatch.setenv(access.POLICY_ENV, _policy(
        {"upns": ["nurse@example.fi"], "encounter_suffixes": ["-sosiaalityontekija"]}))
    client, _ = runtime_client
    rows = client.get("/api/encounters", headers=NURSE).json()["encounters"]
    assert {row["encounter_id"] for row in rows} == {
        "bound-encounter-004-sosiaalipalvelut-sosiaalityontekija",
        "bound-encounter-013-vammaispalvelut-sosiaalityontekija",
    }
    assert client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=NURSE).status_code == 403


# -------------------------------------------------------------------------------------------------
# The decision contract, unchanged for an authorised caller.
# -------------------------------------------------------------------------------------------------

def test_the_per_actor_decision_contract_is_unchanged_inside_the_scope(runtime_client, monkeypatch):
    """H2's contract still holds when a policy is configured: own decisions only, per encounter."""
    monkeypatch.setenv(access.POLICY_ENV, _policy(
        {"objects": ["oid-1234", "oid-other"], "encounters": [NURSES_OWN]}))
    client, store = runtime_client
    proposal = client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=NURSE).json()["proposals"][0]

    mine = client.post(f"/api/encounters/{NURSES_OWN}/feedback", json={
        "event_id": "mine", "proposal_id": proposal["proposal_id"],
        "rule_version": proposal["rule_version"], "action": "dismiss"}, headers=NURSE)
    assert mine.status_code == 200, mine.text
    theirs = client.post(f"/api/encounters/{NURSES_OWN}/feedback", json={
        "event_id": "theirs", "proposal_id": proposal["proposal_id"],
        "rule_version": proposal["rule_version"], "action": "accept"}, headers=OTHER)
    assert theirs.status_code == 200, theirs.text

    page = client.get(f"/api/encounters/{NURSES_OWN}/decisions", headers=NURSE).json()
    assert [row["action"] for row in page["proposals"]] == ["dismiss"]
    assert "accept" not in json.dumps(page)
    assert {row["event_id"]: row["actor_id"]
            for row in store.feedback("bound-persona-002-krooninen-ikääntynyt")} == {
        "mine": "oid-1234", "theirs": "oid-other"}
    # Another encounter in the catalogue is still refused on the decision route.
    assert client.get(f"/api/encounters/{OUT_OF_SCOPE}/decisions", headers=NURSE).status_code == 403


# -------------------------------------------------------------------------------------------------
# Malformed configuration: refused, named, and never a silent fall back to open access.
# -------------------------------------------------------------------------------------------------

_MALFORMED = [
    ("{not json", "not valid JSON"),
    ("[]", "must be a JSON object"),
    ('{"rules": []}', "non-empty 'rules'"),
    ('{"rules": [{"upns": ["nurse@example.fi"]}]}', "no encounter scope"),
    (f'{{"rules": [{{"encounters": ["{NURSES_OWN}"]}}]}}', "names no principal"),
    (f'{{"rules": [{{"upns": ["nurse@example.fi"], "encounters": ["{NURSES_OWN}"], "action": "maybe"}}]}}',
     "must be 'allow' or 'deny'"),
    ('{"rules": [{"upns": ["nurse@example.fi"], "encounters": ["*"]}]}', "exact encounter id"),
    ('{"rules": [{"upns": ["nurse@example.fi"], "encounter_prefixes": ["bound-*"]}]}', "literal id fragment"),
    (f'{{"rules": [{{"upns": ["nurse@example.fi"], "encounters": ["{NURSES_OWN}"], '
     '"encounter_prefixes": ["bound-"]}]}', "exactly one encounter selector"),
    (f'{{"rules": [{{"upns": ["nurse@example.fi"], "encounters": ["{NURSES_OWN}"], "unknown_key": 1}}]}}',
     "unknown key"),
    (f'{{"rules": [{{"upns": ["nurse@example.fi"], "encounters": ["{NURSES_OWN}"]}}], "extra": 1}}',
     "unknown top-level key"),
    (f'{{"rules": [{{"upns": ["nurse@example.fi"], "encounters": ["{NURSES_OWN}"]}}, '
     f'{{"upns": ["nurse@example.fi"], "encounters": ["bound-encounter-005-laakitys-sairaanhoitaja"]}}]}}',
     "same principal"),
]


@pytest.mark.parametrize("document, expected", _MALFORMED)
def test_a_malformed_policy_is_refused_and_names_what_is_wrong(document, expected):
    with pytest.raises(access.AccessPolicyError) as raised:
        access.parse_policy(document)
    assert expected in str(raised.value)


# -------------------------------------------------------------------------------------------------
# `version` is a declaration, not decoration: this module honours the one it implements.
#
# None of the twelve malformed documents above carries a `version` key, so the version check changes no
# existing classification: a document with no version is still parsed as version 1, exactly as it was before
# the key was ever read. (`{"version": 2, ..., "extra": 1}` still fails on the unknown top-level key, because
# the key set is checked first — the comment in `parse_policy` records that ordering.) What changes is the
# document the review found silently accepted: a stated version other than 1.
# -------------------------------------------------------------------------------------------------

def test_a_policy_version_other_than_the_implemented_one_is_refused():
    """The documented shape was decorative; a v2 policy was parsed under v1 semantics with no signal."""
    base = {"objects": ["oid-1234"], "encounters": [NURSES_OWN]}
    for version in (2, 0, -1, 1.0, "1", "banana", None, True, {"major": 2}, [1]):
        document = json.dumps({"version": version, "rules": [base]})
        with pytest.raises(access.AccessPolicyError) as raised:
            access.parse_policy(document)
        assert "version" in str(raised.value), (version, str(raised.value))
        assert "version" in raised.value.public_message()
    # An integer that is not the implemented version is named in the log, which is operator detail and not
    # a value from the policy's scope. The wire rendering names only the property.
    with pytest.raises(access.AccessPolicyError) as two:
        access.parse_policy(json.dumps({"version": 2, "rules": [base]}))
    assert "2" in str(two.value)
    assert access.parse_policy(json.dumps({"version": 1, "rules": [base]})).rules[0].encounter_ids == frozenset(
        {NURSES_OWN})


def test_an_absent_version_means_the_implemented_version():
    """Absent means v1, like every other optional key: a minimal policy is `{"rules": [...]}` and nothing else.

    This is the behaviour the module already had; the test exists so the choice is a contract rather than an
    accident, and so the version check cannot be read as "the key is required now".
    """
    policy = access.parse_policy(
        json.dumps({"rules": [{"upns": ["nurse@example.fi"], "encounters": [NURSES_OWN]}]}))
    scope = access.resolve_scope(policy, Actor(actor_id="oid-1234", name="nurse@example.fi"))
    assert scope is not None and scope.contains(NURSES_OWN)
    # And it is enforced as v1 through the HTTP path too, which is where the review saw it accepted.
    assert access.parse_policy(_policy({"upns": ["nurse@example.fi"], "encounters": [NURSES_OWN]}))


def test_the_policy_cannot_be_configured_twice():
    """Inline JSON and a mounted file are two policies; the deployment cannot mean both."""
    with pytest.raises(access.AccessPolicyError) as raised:
        access.policy_source_from_env({access.POLICY_ENV: "{}", access.POLICY_FILE_ENV: "/etc/luvn.json"})
    assert "ambiguous" in str(raised.value)


def test_a_malformed_policy_fails_closed_with_a_500_that_names_the_variable(runtime_client, monkeypatch):
    """The dangerous failure is the silent one: a typo must not restore open access."""
    monkeypatch.setenv(access.POLICY_ENV, '{"rules": [{"upns": ["nurse@example.fi"]}]}')
    client, _ = runtime_client
    response = client.get("/api/encounters", headers=NURSE)
    assert response.status_code == 500
    body = response.json()
    assert body["reason"] == "access_policy_unusable"
    assert "no encounter scope" in body["detail"]
    assert "nurse@example.fi" not in response.text
    # The refusal is the same on a scoped route, and it leaks nothing about any encounter.
    refused = client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=NURSE)
    assert refused.status_code == 500
    assert NURSES_OWN not in refused.text


def _failing_client():
    """A client that returns the 500 body instead of re-raising, for the tests that read it.

    Raising would tell the test what the *server* saw; these tests are about what the *caller* receives,
    which is the whole subject of finding 2.
    """
    return TestClient(main.app, raise_server_exceptions=False)


def test_the_policy_fault_body_withholds_the_path_and_the_values_while_the_log_keeps_them(
        monkeypatch, capsys):
    """The 500 is the same refusal for the operator and for a prober, minus what only the prober wants.

    The handler's docstring used to promise "no path or traceback" while the body carried the mounted
    path, the `[Errno 2]` text and the policy's own values. This is that promise made true, and the log
    line is asserted in the same test so "withheld from the response" cannot quietly become "withheld from
    the operator".
    """
    def body(policy: str):
        monkeypatch.setenv(access.POLICY_ENV, policy)
        with _failing_client() as client:
            response = client.get("/api/encounters", headers=NURSE)
        return response, capsys.readouterr().err

    # A value that is a plausible encounter id with a trailing space: the one parser check that can fire on
    # something an attacker would call a valid id. The location stays; the value goes.
    response, logged = body(json.dumps({"rules": [
        {"upns": ["nurse@example.fi"], "encounters": [f"{OUT_OF_SCOPE} extra"]}]}))
    assert response.status_code == 500
    assert response.json()["reason"] == "access_policy_unusable"
    # An inline policy fault is located by its position in the document — `rules[0].encounters[0]` — which is
    # the actionable half; the variable that held it is not part of the body, because the whole document came
    # from one variable and naming it adds nothing a prober did not already know.
    assert "rules[0].encounters[0]" in response.json()["detail"]
    assert "whitespace" in response.json()["detail"]
    assert OUT_OF_SCOPE not in response.text
    assert "nurse@example.fi" not in response.text
    # The operator's own log line still has the value the response withholds.
    assert OUT_OF_SCOPE in logged

    # A pattern selector and a bad action are the other two places a value used to be quoted back.
    response, logged = body(json.dumps({"rules": [
        {"upns": ["nurse@example.fi"], "encounter_prefixes": ["bound-*/secret"]}]}))
    assert response.status_code == 500
    assert "rules[0].encounter_prefixes" in response.json()["detail"]
    assert "bound-*/secret" not in response.text
    assert "bound-*/secret" in logged

    response, logged = body(json.dumps({"rules": [
        {"upns": ["nurse@example.fi"], "encounters": [NURSES_OWN], "action": "maybe"}]}))
    assert response.status_code == 500
    assert "must be 'allow' or 'deny'" in response.json()["detail"]
    assert "maybe" not in response.text

    # An unsupported version is refused through the same fail-closed path, and the body names the property
    # rather than echoing whatever the version key held. The catalogue never comes back.
    response, logged = body(json.dumps({"version": "banana", "rules": [
        {"upns": ["nurse@example.fi"], "encounters": [NURSES_OWN]}]}))
    assert response.status_code == 500
    assert response.json()["reason"] == "access_policy_unusable"
    assert "version" in response.json()["detail"]
    assert "banana" not in response.text
    assert "banana" in logged


def test_a_missing_policy_file_does_not_disclose_the_mount_path(monkeypatch):
    """A misconfigured mount is a 500 that names the variable, not a map of the deployment's filesystem."""
    secret = "/very/secret/mount/luvn-policy.json"
    monkeypatch.delenv(access.POLICY_ENV, raising=False)
    monkeypatch.setenv(access.POLICY_FILE_ENV, secret)
    with pytest.raises(access.AccessPolicyError) as raised:
        access.policy_from_env()
    # The operator's message carries the path; that is what goes to stderr.
    assert secret in str(raised.value)
    assert secret not in raised.value.public_message()
    assert access.POLICY_FILE_ENV in raised.value.public_message()
    assert "Errno" not in raised.value.public_message()

    # And the response is the public rendering, with no path and no `[Errno 2]` text.
    response = _failing_client().get("/api/encounters", headers=NURSE)
    assert response.status_code == 500
    assert response.json()["reason"] == "access_policy_unusable"
    assert access.POLICY_FILE_ENV in response.json()["detail"]
    assert secret not in response.text
    assert "secret" not in response.text
    assert "Errno" not in response.text


def test_a_parse_fault_inside_a_policy_file_keeps_the_path_out_of_the_500(tmp_path, monkeypatch):
    """Reading the file is only one leak; a parse fault in a file-sourced policy is annotated with the path."""
    path = tmp_path / "mounted" / "access-policy.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"rules": [
        {"upns": ["nurse@example.fi"], "encounters": [f"{OUT_OF_SCOPE} extra"]}]}), encoding="utf-8")
    monkeypatch.delenv(access.POLICY_ENV, raising=False)
    monkeypatch.setenv(access.POLICY_FILE_ENV, str(path))
    with pytest.raises(access.AccessPolicyError) as raised:
        access.policy_from_env()
    assert str(path) in str(raised.value)
    assert str(path) not in raised.value.public_message()
    assert access.POLICY_FILE_ENV in raised.value.public_message()

    response = _failing_client().get("/api/encounters", headers=NURSE)
    assert response.status_code == 500
    assert str(path) not in response.text
    assert OUT_OF_SCOPE not in response.text
    assert access.POLICY_FILE_ENV in response.json()["detail"]


def test_a_malformed_mounted_policy_file_fails_closed(tmp_path, monkeypatch):
    """The file path is the deployment shape; a bad file must be as loud as a bad variable."""
    path = tmp_path / "access-policy.json"
    path.write_text('{"rules": "not-a-list"}', encoding="utf-8")
    monkeypatch.delenv(access.POLICY_ENV, raising=False)
    monkeypatch.setenv(access.POLICY_FILE_ENV, str(path))
    with pytest.raises(access.AccessPolicyError) as raised:
        access.policy_from_env()
    assert "non-empty 'rules'" in str(raised.value)

    with pytest.raises(access.AccessPolicyError) as missing:
        access.policy_from_env({access.POLICY_FILE_ENV: str(tmp_path / "absent.json")})
    assert "could not be read" in str(missing.value)


def test_a_mounted_policy_file_is_read_and_honoured(tmp_path, monkeypatch, runtime_client):
    path = tmp_path / "access-policy.json"
    path.write_text(_nurse_scope(), encoding="utf-8")
    monkeypatch.delenv(access.POLICY_ENV, raising=False)
    monkeypatch.setenv(access.POLICY_FILE_ENV, str(path))
    client, _ = runtime_client
    assert [row["encounter_id"]
            for row in client.get("/api/encounters", headers=NURSE).json()["encounters"]] == [NURSES_OWN]
    assert client.get(f"/api/encounters/{OUT_OF_SCOPE}/picture", headers=NURSE).status_code == 403


# -------------------------------------------------------------------------------------------------
# The resolution unit, without HTTP in the way.
# -------------------------------------------------------------------------------------------------

def test_a_principal_is_matched_by_object_id_or_by_upn():
    by_object = access.resolve_scope(
        access.parse_policy(_policy({"objects": ["OID-1234"], "encounters": [NURSES_OWN]})),
        Actor(actor_id="oid-1234", name=None))
    assert by_object is not None and by_object.contains(NURSES_OWN)

    # The same rule does not match a caller it does not name, by either axis.
    policy = access.parse_policy(_policy(
        {"objects": ["oid-1234"], "encounters": [NURSES_OWN]}))
    assert access.resolve_scope(policy, Actor(actor_id="other-oid", name="other@example.fi")) is None
    assert access.resolve_scope(policy, None) is None


def test_the_upn_selector_matches_the_email_and_is_case_insensitive():
    policy = access.parse_policy(_policy({"upns": ["Nurse@Example.FI"], "encounters": [NURSES_OWN]}))
    scope = access.resolve_scope(policy, Actor(actor_id="whatever", name="nurse@example.fi"))
    assert scope is not None and scope.contains(NURSES_OWN)


def test_the_first_matching_rule_decides():
    """Order is the operator's control: first match wins, and a later rule never widens an earlier one."""
    policy = access.parse_policy(_policy(
        {"upns": ["nurse@example.fi"], "encounters": [NURSES_OWN]},
        {"upns": ["temp@example.fi"], "encounter_prefixes": ["bound-encounter-"]},
    ))
    nurse = access.resolve_scope(policy, Actor(actor_id="oid-1234", name="nurse@example.fi"))
    assert nurse is not None and nurse.contains(NURSES_OWN)
    assert not nurse.contains(OUT_OF_SCOPE), "the other rule must not widen this caller's scope"
    teammate = access.resolve_scope(policy, Actor(actor_id="oid-temp", name="temp@example.fi"))
    assert teammate is not None and teammate.contains(OUT_OF_SCOPE)
    assert access.resolve_scope(policy, Actor(actor_id="nobody", name=None)) is None


def test_scope_for_actor_keeps_open_and_denied_apart():
    """The two answers the routes must never confuse: no policy = open, a policy = a scope, named or empty."""
    assert access.scope_for_actor(None, {}) is None
    assert access.scope_for_actor(Actor(actor_id="anyone", name=None), {}) is None

    environment = {access.POLICY_ENV: _nurse_scope()}
    named = access.scope_for_actor(Actor(actor_id="oid-1234", name=None), environment)
    assert named is not None and named.contains(NURSES_OWN)
    denied = access.scope_for_actor(Actor(actor_id="nobody", name=None), environment)
    assert denied is not None and not denied.contains(NURSES_OWN)
    assert denied.encounter_ids == frozenset()
    assert access.may_read(Actor(actor_id="nobody", name=None), NURSES_OWN, environment) is False
    assert access.may_read(Actor(actor_id="nobody", name=None), NURSES_OWN, {}) is True


def test_an_empty_scope_reads_nothing():
    """`EncounterScope()` is the fail-closed value the routes receive for an unnamed principal."""
    empty = access.EncounterScope()
    assert not empty.contains(NURSES_OWN)
    assert not empty.contains("")


def test_the_scope_is_applied_before_paging_counting_and_searching(runtime_client, monkeypatch):
    """A withheld encounter cannot appear in a total, a page, a people count, a roster or a search match.

    The suffix scope keeps the nurse's four `-sairaanhoitaja` encounters, which belong to three people. The
    other three people (Lehtinen, Laakso, Nieminen) are outside it, so the counts must be 4 encounters, 3
    people and 3 purposes, and a search for a withheld name must find nothing.
    """
    monkeypatch.setenv(access.POLICY_ENV, _policy(
        {"upns": ["nurse@example.fi"], "encounter_suffixes": ["-sairaanhoitaja"]}))
    client, _ = runtime_client
    in_scope = sorted(encounter_id for encounter_id in ALL_ENCOUNTER_IDS
                      if encounter_id.endswith("-sairaanhoitaja"))
    assert len(in_scope) == 4

    first = client.get("/api/encounters", headers=NURSE, params={"limit": 2}).json()
    second = client.get("/api/encounters", headers=NURSE, params={"limit": 2, "offset": 2}).json()
    assert [row["encounter_id"] for row in first["encounters"] + second["encounters"]] == in_scope
    assert first["total"] == second["total"] == 4
    assert first["aggregate"]["catalogue"] == {"encounters": 4, "people": 3, "purposes": 3, "roles": 1}
    assert first["aggregate"]["matching"] == {"encounters": 4, "people": 3, "purposes": 3}
    assert [entry["role"] for entry in first["aggregate"]["roster"]] == ["sairaanhoitaja"]
    for withheld in ("Lehtinen", "Laakso", "Nieminen", "Sanni", "Onni", "Juho"):
        assert withheld not in json.dumps(first) + json.dumps(second), withheld
        hit = client.get("/api/encounters", headers=NURSE, params={"q": withheld}).json()
        assert hit["total"] == 0 and hit["encounters"] == [], withheld
        assert hit["aggregate"]["matching"] == {"encounters": 0, "people": 0, "purposes": 0}, withheld


# -------------------------------------------------------------------------------------------------
# Work order L2: the briefing read needs an actor, refuses indistinguishably, and writes one audit row.
# -------------------------------------------------------------------------------------------------

from test_store import MemoryContainer  # noqa: E402  -- the Blob stand-in the store tests already use

NOT_AUTHENTICATED = {"detail": "an authenticated professional is required"}
UNKNOWN_ENCOUNTER = "encounter-removed"
AUDIT_KEYS = {"kind", "event_id", "recorded_at", "actor_id", "actor_name", "actor_source",
              "encounter_id", "corpus_generation", "permitted"}


def _stable_headers(response) -> dict:
    """Every response header except the transport's own clock and length, so two responses compare on
    what the caller can observe — status, body and the cache-relevant headers."""
    return {name: value for name, value in response.headers.items()
            if name not in ("date", "content-length")}


def test_the_briefing_read_requires_a_principal_in_every_policy_state(runtime_client, monkeypatch):
    """Change 1: the same 401 the write routes answer, whether or not a policy is configured."""
    client, store = runtime_client
    monkeypatch.delenv(access.POLICY_ENV, raising=False)
    open_anonymous = client.get(f"/api/encounters/{NURSES_OWN}/picture")
    assert open_anonymous.status_code == 401
    assert open_anonymous.json() == NOT_AUTHENTICATED

    monkeypatch.setenv(access.POLICY_ENV, _nurse_scope())
    scoped_anonymous = client.get(f"/api/encounters/{NURSES_OWN}/picture")
    assert scoped_anonymous.status_code == 401
    assert scoped_anonymous.json() == NOT_AUTHENTICATED
    # A principal the policy does not name is 403, not 401: it is authenticated, it is just out of scope.
    assert client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=OTHER).status_code == 403
    assert store.read_audit() == []


def test_a_forbidden_encounter_is_the_same_refusal_as_an_unknown_one_byte_for_byte(runtime_client, monkeypatch):
    """Change 1's indistinguishability, measured on the whole response for an unmatched principal.

    Status, body and headers for a real encounter the caller may not read must equal those for an id that
    does not exist. Anything else would tell the caller which encounter ids exist.
    """
    client, _ = runtime_client
    monkeypatch.setenv(access.POLICY_ENV, _nurse_scope())
    refusals = {
        encounter_id: client.get(f"/api/encounters/{encounter_id}/picture", headers=OTHER)
        for encounter_id in (NURSES_OWN, OUT_OF_SCOPE, UNKNOWN_ENCOUNTER)
    }
    for encounter_id, response in refusals.items():
        assert response.status_code == 403, encounter_id
    reference = refusals[UNKNOWN_ENCOUNTER]
    for encounter_id, response in refusals.items():
        assert response.content == reference.content, encounter_id
        assert _stable_headers(response) == _stable_headers(reference), encounter_id
    # The known caller's refusal for another real encounter is the same body too.
    own_refused = client.get(f"/api/encounters/{OUT_OF_SCOPE}/picture", headers=NURSE)
    assert own_refused.content == reference.content


def test_unset_policy_keeps_today_s_unknown_encounter_answer_and_open_read(runtime_client, monkeypatch):
    """Change 3, the default: with no policy an authenticated presenter gets today's answers exactly.

    An existing encounter is 200 for any principal; an absent id is the pre-existing 404, not the policy's
    403, because with no policy there is no scope to refuse on.
    """
    client, store = runtime_client
    monkeypatch.delenv(access.POLICY_ENV, raising=False)
    monkeypatch.delenv(access.POLICY_FILE_ENV, raising=False)
    absent = client.get(f"/api/encounters/{UNKNOWN_ENCOUNTER}/picture", headers=NURSE)
    assert absent.status_code == 404
    assert absent.json() == {"detail": "unknown encounter"}
    assert client.get(f"/api/encounters/{OUT_OF_SCOPE}/picture", headers=OTHER).status_code == 200
    # The open read is still audited: the audit does not depend on whether a policy is configured.
    assert len(store.read_audit()) == 1


def test_a_configured_policy_fails_closed_for_an_unmatched_principal_with_the_unknown_id_body(
        runtime_client, monkeypatch):
    """Change 3, case 1: an unmatched principal gets the refusal, and the refusal is the unknown-id body.

    The in-scope principal is still served the encounter the rule allows, so the policy narrowed access
    rather than closing the route.
    """
    client, _ = runtime_client
    monkeypatch.setenv(access.POLICY_ENV, _nurse_scope())
    unknown_id = client.get(f"/api/encounters/{UNKNOWN_ENCOUNTER}/picture", headers=OTHER)
    assert unknown_id.status_code == 403
    assert unknown_id.json() == {"detail": main.ENCOUNTER_FORBIDDEN_DETAIL}
    forbidden = client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=OTHER)
    assert forbidden.content == unknown_id.content
    assert client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=NURSE).status_code == 200


def test_a_malformed_policy_serves_nothing_and_names_the_defect(runtime_client, monkeypatch):
    """Change 3, case 2: a malformed policy is a 500 that names the defect, and no briefing or audit row."""
    client, store = runtime_client
    monkeypatch.setenv(access.POLICY_ENV, '{"rules": [{"upns": ["nurse@example.fi"]}]}')
    authenticated = client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=NURSE)
    assert authenticated.status_code == 500
    assert authenticated.json()["reason"] == "access_policy_unusable"
    assert "no encounter scope" in authenticated.json()["detail"]
    assert "proposals" not in authenticated.json() and "records" not in authenticated.json()
    # Anonymous: refused by the actor check before the policy is read, so still no briefing.
    assert client.get(f"/api/encounters/{NURSES_OWN}/picture").status_code == 401
    assert store.read_audit() == []


def test_a_resolved_briefing_read_writes_exactly_one_metadata_only_audit_row(runtime_client, monkeypatch):
    """Change 2: one row per released read, carrying the four facts and nothing else."""
    client, store = runtime_client
    monkeypatch.setenv(access.POLICY_ENV, _nurse_scope())
    # The fixture-backed store has no SQL index and so no corpus generation to name (work order L2C):
    # the honest value is the literal "unknown", not the build revision this used to be confused with.
    monkeypatch.setenv("LUVN_BUILD_REVISION", "rev-under-test")

    first = client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=NURSE)
    second = client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=NURSE)
    assert first.status_code == second.status_code == 200
    rows = store.read_audit()
    assert len(rows) == 2, rows
    assert len({row["event_id"] for row in rows}) == 2
    for row in rows:
        assert set(row) == AUDIT_KEYS, sorted(set(row) ^ AUDIT_KEYS)
        assert row["kind"] == "briefing_read_audit"
        assert row["actor_id"] == "oid-1234"
        assert row["actor_name"] == "nurse@example.fi"
        assert row["actor_source"] == "ingress_principal"
        assert row["encounter_id"] == NURSES_OWN
        assert row["corpus_generation"] == "unknown"
        assert row["permitted"] is True


def test_a_refused_or_unknown_read_writes_no_audit_row(runtime_client, monkeypatch):
    """Change 2: a refusal is not this event. Refusals must not become a disclosure channel."""
    client, store = runtime_client
    monkeypatch.setenv(access.POLICY_ENV, _nurse_scope())
    assert client.get(f"/api/encounters/{OUT_OF_SCOPE}/picture", headers=NURSE).status_code == 403
    assert client.get(f"/api/encounters/{UNKNOWN_ENCOUNTER}/picture", headers=OTHER).status_code == 403
    assert client.get(f"/api/encounters/{NURSES_OWN}/picture").status_code == 401
    assert client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=OTHER).status_code == 403
    monkeypatch.delenv(access.POLICY_ENV)
    assert client.get(f"/api/encounters/{UNKNOWN_ENCOUNTER}/picture", headers=NURSE).status_code == 404
    assert store.read_audit() == []


def test_a_briefing_whose_read_cannot_be_recorded_is_withheld(runtime_client, monkeypatch):
    """A released briefing with no audit row would be an unaudited disclosure, so the route refuses instead."""
    client, store = runtime_client
    monkeypatch.setenv(access.POLICY_ENV, _nurse_scope())

    def unavailable(entry, event_id):
        raise OSError("audit store unavailable")

    store.add_read_audit = unavailable
    response = client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=NURSE)
    assert response.status_code == 503
    assert response.json() == {"detail": "the briefing was not released: the read could not be recorded"}
    assert "narrative" not in response.text and "proposals" not in response.text


def test_a_briefing_is_released_when_only_the_generation_lookup_fails(runtime_client, monkeypatch):
    """The generation is enrichment; writing the row is the contract. Conflating them caused a live outage.

    `corpus_generation()` does real work only on a SQL-backed index, so this failure cannot happen in a
    fixture-backed test and always can in a deployment that reads SQL. While it was evaluated inside the same
    `try` as `add_read_audit`, a failed lookup withheld EVERY briefing with a 503 whose body blamed the audit
    rather than the generation. The test above keeps the other half honest: a row that cannot be written still
    refuses the release.
    """
    client, store = runtime_client
    monkeypatch.setenv(access.POLICY_ENV, _nurse_scope())
    written: list[dict] = []
    real_write = store.add_read_audit

    def unreadable():
        raise RuntimeError("load_state is not readable")

    def capture(entry, event_id):
        written.append(entry)
        return real_write(entry, event_id)

    monkeypatch.setattr(store, "corpus_generation", unreadable)
    monkeypatch.setattr(store, "add_read_audit", capture)

    response = client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=NURSE)

    assert response.status_code == 200, response.text
    assert written, "the row must still be written: it is the contract, and it was not what failed"
    assert written[0]["corpus_generation"] == "unknown", (
        "a store that cannot name its generation records 'unknown' rather than withholding the release")


def test_the_audit_row_round_trips_on_the_blob_backend(monkeypatch):
    """The Blob namespace must read back what it wrote, or the audit is write-only in production."""
    monkeypatch.setenv("LUVN_BUILD_REVISION", "blob-rev")
    blobs = MemoryContainer()
    store = Store(personas=load_personas(), blob_container=blobs, encounters=ENCOUNTERS, example_labels=EXAMPLE_LABELS)
    main.app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(main.app) as client:
            response = client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=NURSE)
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 200
    rows = store.read_audit()
    assert len(rows) == 1
    assert rows[0]["actor_id"] == "oid-1234" and rows[0]["encounter_id"] == NURSES_OWN
    # Still no SQL index on this backend, so still the honest "unknown" -- NOT the build revision.
    assert rows[0]["corpus_generation"] == "unknown" and rows[0]["permitted"] is True


# -------------------------------------------------------------------------------------------------
# Work order L2C, defect 1 (REPRODUCED): `/api/chat/reviews` must re-check the caller's CURRENT scope
# for the stored turn's encounter, not just the scope at turn-creation time.
# -------------------------------------------------------------------------------------------------

def _final_turn(client, turn_id: str, *, headers: dict,
                question: str = "mitk\u00e4 ovat seuraavat toimenpiteet") -> dict:
    """Ask the nurse's own encounter one question on the stream route and return the final `turn` payload."""
    from test_assistant import _events

    response = client.post(f"/api/encounters/{NURSES_OWN}/chat/stream",
                           json={"turn_id": turn_id, "question": question}, headers=headers)
    assert response.status_code == 200, response.text
    name, payload = _events(response.text)[-1]
    assert name == "turn", name
    return payload


def _review_body(turn_id: str = "t1", review_id: str = "r1", suggestion_id: str = "s0") -> dict:
    return {"review_id": review_id, "turn_id": turn_id, "suggestion_id": suggestion_id, "action": "accept"}


def test_a_revoked_principal_can_no_longer_review_a_turn_it_previously_owned(runtime_client, monkeypatch):
    """The reproduced case, exactly: ask while in scope, revoke, then the review must refuse like an unknown turn.

    A policy allows the nurse exactly `NURSES_OWN`. The nurse asks a question there, which stores a turn
    carrying that encounter id and the nurse's own actor id -- the turn-ownership check the route already had
    would pass this turn forever. The policy is then changed so the nurse is no longer named at all: `GET
    .../picture` for the same encounter correctly answers 403, and `POST /api/chat/reviews` for the
    previously-owned turn must now answer the SAME 403 body as an unknown turn id, not the 200 it used to
    return and persist.
    """
    client, store = runtime_client
    monkeypatch.setenv(access.POLICY_ENV, _nurse_scope())
    asked = _final_turn(client, "t1", headers=NURSE)
    assert asked["turn"]["turn_id"] == "t1"

    # Revoke: a policy that no longer names the nurse at all (an unmatched principal resolves to an empty
    # scope, same as a `deny` rule in front of a broader one).
    monkeypatch.setenv(access.POLICY_ENV, _policy({"upns": ["someone-else@example.fi"], "encounters": [NURSES_OWN]}))
    revoked_picture = client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=NURSE)
    assert revoked_picture.status_code == 403

    review = client.post("/api/chat/reviews", json=_review_body(), headers=NURSE)
    assert review.status_code == 403
    assert review.json() == {"detail": main.TURN_NOT_AVAILABLE}
    assert store.chat_review("r1") is None

    # Indistinguishable from the existing "no such turn" answer: the route must not become an existence
    # oracle over which turn ids exist for a now-unscoped caller.
    unknown = client.post("/api/chat/reviews", json=_review_body(turn_id="does-not-exist", review_id="r2"),
                          headers=NURSE)
    assert unknown.status_code == review.status_code == 403
    assert unknown.json() == review.json()


def test_with_no_policy_configured_the_review_route_behaves_exactly_as_before(runtime_client, monkeypatch):
    """The unset-policy case is unaffected: `scope_for_actor` returns `None`, so the new check is a no-op.

    This is the live deployment's configuration today, so the demo must see no change: the same actor that
    asked a question may still review its own turn's suggestions, and another actor still cannot.
    """
    client, store = runtime_client
    monkeypatch.delenv(access.POLICY_ENV, raising=False)
    monkeypatch.delenv(access.POLICY_FILE_ENV, raising=False)
    asked = _final_turn(client, "t1", headers=NURSE)
    suggestions = asked["turn"]["suggestions"]
    if not suggestions:
        pytest.skip("this encounter produced no suggestion to review")
    response = client.post("/api/chat/reviews",
                          json=_review_body(suggestion_id=suggestions[0]["suggestion_id"]),
                          headers=NURSE)
    assert response.status_code == 200, response.text
    assert store.chat_review("r1") is not None
    # A foreign actor is still refused, exactly as before the fix.
    other_turn = _final_turn(client, "t2", headers=NURSE, question="kerro l\u00e4\u00e4kkeist\u00e4")
    assert other_turn["turn"]["turn_id"] == "t2"
    foreign = client.post("/api/chat/reviews", json=_review_body(turn_id="t2", review_id="r3"), headers=OTHER)
    assert foreign.status_code == 403
    assert foreign.json() == {"detail": main.TURN_NOT_AVAILABLE}


# -------------------------------------------------------------------------------------------------
# Work order L2C, defect 2: the audit row's `corpus_generation` must be the actual corpus generation,
# never a proxy, and it must stay metadata-only (ADR-0025 §5).
# -------------------------------------------------------------------------------------------------

class _FakeSqlIndex:
    """A minimal stand-in for `SqlDeliveryIndex`: only `corpus_generation()` is exercised here."""

    def __init__(self, generation: str):
        self._generation = generation

    def corpus_generation(self) -> str:
        return self._generation


def test_the_audit_rows_corpus_generation_is_not_the_build_revision(runtime_client, monkeypatch):
    """The two must differ in this fixture, or the assertion would pass even if they were confused again."""
    client, store = runtime_client
    monkeypatch.delenv(access.POLICY_ENV, raising=False)
    monkeypatch.setenv("LUVN_BUILD_REVISION", "image-rev-does-not-match")
    store._delivery_index = _FakeSqlIndex("corpus-generation-abc123")

    response = client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=NURSE)
    assert response.status_code == 200
    rows = store.read_audit()
    assert len(rows) == 1
    assert rows[0]["corpus_generation"] == "corpus-generation-abc123"
    assert rows[0]["corpus_generation"] != "image-rev-does-not-match"


def test_the_audit_row_still_carries_only_adr_0025_section_5s_metadata(runtime_client, monkeypatch):
    """Fixing the field's source must not grow the row: still exactly the keys ADR-0025 §5 lists."""
    client, store = runtime_client
    monkeypatch.setenv(access.POLICY_ENV, _nurse_scope())
    store._delivery_index = _FakeSqlIndex("corpus-generation-xyz")

    response = client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=NURSE)
    assert response.status_code == 200
    rows = store.read_audit()
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == AUDIT_KEYS, sorted(set(row) ^ AUDIT_KEYS)
    # No clinical text, no persona id, no record/evidence id, no narrative, no proposal.
    blob = json.dumps(row, default=str)
    for leak in ("persona_id", "record_id", "evidence_id", "narrative", "proposal", "label"):
        assert leak not in blob


def test_the_cross_person_disclosure_is_closed_in_the_body_the_cache_keys_and_the_audit(
        runtime_client, monkeypatch):
    """Two people. The caller is scoped to one; the other person's encounter must leave no trace anywhere.

    The forbidden id and a nonexistent id must answer the same bytes, status and headers. The caller's own
    briefing and the audit row for it must carry no record, persona or name of the other person.
    """
    client, store = runtime_client
    monkeypatch.setenv(access.POLICY_ENV, _nurse_scope())
    other_encounter = "bound-encounter-004-sosiaalipalvelut-sosiaalityontekija"
    other_persona = store.encounter_index()[other_encounter].persona_id
    other_records = {record.record_id for record in store.persona_index()[other_persona].records}
    other_markers = {other_persona, "Lehtinen", "Sanni", other_encounter, *other_records}
    assert other_records, "the other person must carry records for this test to mean anything"

    forbidden = client.get(f"/api/encounters/{other_encounter}/picture", headers=NURSE)
    absent = client.get(f"/api/encounters/{UNKNOWN_ENCOUNTER}/picture", headers=NURSE)
    assert forbidden.status_code == absent.status_code == 403
    assert forbidden.content == absent.content
    assert _stable_headers(forbidden) == _stable_headers(absent)

    catalogue = client.get("/api/encounters", headers=NURSE).text
    own = client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=NURSE)
    assert own.status_code == 200
    audit = json.dumps(store.read_audit(), default=str)
    for surface_name, surface in (("catalogue", catalogue), ("own briefing", own.text), ("audit", audit),
                                  ("refusal", forbidden.text)):
        for marker in other_markers:
            assert marker not in surface, (surface_name, marker)


def test_the_audit_row_carries_no_clinical_text_from_the_briefing(runtime_client, monkeypatch):
    """Every long string the caller was shown is absent from the audit row, except the encounter id it names."""
    client, store = runtime_client
    monkeypatch.setenv(access.POLICY_ENV, _nurse_scope())
    picture = client.get(f"/api/encounters/{NURSES_OWN}/picture", headers=NURSE).json()
    audit_text = json.dumps(store.read_audit(), default=str)

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)

    # A substring of the encounter id (the role label inside it) is the id the row is allowed to carry.
    shown = {text for text in strings(picture) if len(text) >= 12 and text not in NURSES_OWN}
    assert shown, "the briefing must show text for this check to mean anything"
    leaked = sorted(text for text in shown if text in audit_text)
    assert leaked == [], leaked[:3]
