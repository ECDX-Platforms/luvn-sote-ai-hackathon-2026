"""The API contract of L1C: `cursor` is refused, and `GET /api/encounters/{id}` is the worklist row by id.

Two defects are pinned. A `cursor` used to be silently ignored, so a client paging by cursor re-read page one
forever; it is now refused with 422 until keyset paging exists. And ADR-0025 §4's single-encounter route did
not exist; it now returns exactly the worklist row, built by the same function, under the catalogue's
authorization, and it reads no record.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import access
from app.fixtures import load_personas
from app.sqldelivery import SqlDeliveryIndex
from app.store import Store, get_store
from tests.fixtures.bound_encounters import ENCOUNTERS, EXAMPLE_LABELS

NURSE = {"x-ms-client-principal-id": "oid-1234", "x-ms-client-principal-name": "nurse@example.fi"}
NURSES_OWN = "bound-encounter-002-laakitys-sairaanhoitaja"
OUT_OF_SCOPE = "bound-encounter-013-vammaispalvelut-sosiaalityontekija"
UNKNOWN = "bound-encounter-999-olematon"


@pytest.fixture
def store():
    return Store(personas=load_personas(), path=None, encounters=ENCOUNTERS, example_labels=EXAMPLE_LABELS)


@pytest.fixture
def api(store):
    main.app.dependency_overrides[get_store] = lambda: store
    with TestClient(main.app) as client:
        yield client
    main.app.dependency_overrides.clear()


def _nurse_policy(monkeypatch) -> None:
    policy = {"version": 1, "rules": [{"objects": ["oid-1234"], "upns": ["nurse@example.fi"],
                                       "encounters": [NURSES_OWN]}]}
    monkeypatch.setenv(access.POLICY_ENV, json.dumps(policy))


def _worklist_rows(api: TestClient, **headers) -> dict[str, dict]:
    response = api.get("/api/encounters", params={"limit": main.MAX_ENCOUNTER_LIMIT}, headers=headers)
    assert response.status_code == 200, response.text
    return {row["encounter_id"]: row for row in response.json()["encounters"]}


# 1. cursor is refused, and the unpaged request is untouched.
@pytest.mark.parametrize("cursor", ["abc", "5", ""])
def test_a_cursor_is_refused_with_422_naming_keyset_paging(api, cursor):
    response = api.get("/api/encounters", params={"limit": 2, "cursor": cursor})
    assert response.status_code == 422, response.text
    assert "keyset paging" in response.json()["detail"]


def test_a_request_without_cursor_is_unchanged(api):
    response = api.get("/api/encounters", params={"limit": 2})
    assert response.status_code == 200
    assert response.json()["count"] == 2
    assert response.json()["total"] == 9


# 2. the metadata row is the worklist row, key for key and value for value.
def test_the_metadata_row_is_byte_identical_in_shape_to_the_worklist_row(api):
    rows = _worklist_rows(api)
    assert len(rows) == 9
    for encounter_id, expected in rows.items():
        detail = api.get(f"/api/encounters/{encounter_id}")
        assert detail.status_code == 200, detail.text
        assert set(detail.json()) == set(expected) == {
            "encounter_id", "role", "role_label", "purpose", "purpose_label", "example_label", "picker_label",
        }
        assert detail.json() == expected


# 3. unknown and out-of-scope are one refusal: same status, same body.
def test_an_unknown_id_and_an_out_of_scope_id_are_indistinguishable(api, monkeypatch):
    _nurse_policy(monkeypatch)
    out_of_scope = api.get(f"/api/encounters/{OUT_OF_SCOPE}", headers=NURSE)
    unknown = api.get(f"/api/encounters/{UNKNOWN}", headers=NURSE)
    assert out_of_scope.status_code == unknown.status_code == 403
    assert out_of_scope.content == unknown.content
    assert out_of_scope.json()["detail"] == main.ENCOUNTER_FORBIDDEN_DETAIL


# 4. the route reads no record file and issues no records statement. A recording connection stands in for
# the SQL index, so any statement that names `records` (or `record_json`) fails the test at the statement.
class _RecordingCursor:
    def __init__(self, connection: _RecordingConnection) -> None:
        self._connection = connection
        self._rows: list = []

    def execute(self, sql: str, *params) -> None:
        self._connection.statements.append(sql)
        if "record" in sql.lower():
            raise AssertionError(f"the metadata route issued a record statement: {sql}")
        if not sql.startswith("SELECT e.encounter_id, e.source_person_id, e.role, e.purpose"):
            raise AssertionError(f"the metadata route issued a statement this test does not know: {sql}")
        self._rows = self._connection.metadata_rows

    def fetchall(self) -> list:
        return list(self._rows)

    def close(self) -> None:
        pass


class _RecordingConnection:
    def __init__(self, metadata_rows: list) -> None:
        self.metadata_rows = metadata_rows
        self.statements: list[str] = []

    def cursor(self) -> _RecordingCursor:
        return _RecordingCursor(self)


def test_the_metadata_route_reads_no_record_and_resolves_no_picture(store, monkeypatch):
    rows = [(e.encounter_id, e.source_person_id, e.role, e.purpose, e.example_label, e.demo_name,
             ",".join(e.consent_holders), e.life_stage) for e in store.encounter_metadata()]
    connection = _RecordingConnection(rows)
    store._delivery_index = SqlDeliveryIndex(connection)

    def forbidden(name: str):
        def _refuse(*args, **kwargs):
            raise AssertionError(f"the metadata route touched {name}")
        return _refuse

    monkeypatch.setattr(main, "_resolve", forbidden("_resolve"))
    monkeypatch.setattr(main, "resolve_encounter", forbidden("resolve_encounter"))
    monkeypatch.setattr(main, "build_picture_for_encounter", forbidden("build_picture_for_encounter"))
    monkeypatch.setattr(Store, "persona_index", forbidden("persona_index"))
    monkeypatch.setattr(Store, "persona", forbidden("persona"))

    main.app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(main.app) as client:
            response = client.get(f"/api/encounters/{NURSES_OWN}")
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    assert response.json()["encounter_id"] == NURSES_OWN
    assert connection.statements == [connection.statements[0]]
    assert "records" not in connection.statements[0].lower()


# 5. no policy: the catalogue's open behaviour. A policy: the caller's scope, and nothing else.
def test_with_no_policy_any_caller_reads_any_encounter_as_the_catalogue_does(api, monkeypatch):
    monkeypatch.delenv(access.POLICY_ENV, raising=False)
    monkeypatch.delenv(access.POLICY_FILE_ENV, raising=False)
    rows = _worklist_rows(api)
    for headers in ({}, NURSE):
        for encounter_id in (NURSES_OWN, OUT_OF_SCOPE):
            response = api.get(f"/api/encounters/{encounter_id}", headers=headers)
            assert response.status_code == 200, response.text
            assert response.json() == rows[encounter_id]
        assert api.get(f"/api/encounters/{UNKNOWN}", headers=headers).status_code == 404


def test_with_a_policy_an_in_scope_id_is_returned_and_an_out_of_scope_id_is_refused(api, monkeypatch):
    _nurse_policy(monkeypatch)
    in_scope = api.get(f"/api/encounters/{NURSES_OWN}", headers=NURSE)
    assert in_scope.status_code == 200
    assert in_scope.json()["encounter_id"] == NURSES_OWN
    assert api.get(f"/api/encounters/{OUT_OF_SCOPE}", headers=NURSE).status_code == 403
    # An unnamed caller resolves to the empty scope: refused, not served and not a 401.
    assert api.get(f"/api/encounters/{NURSES_OWN}").status_code == 403
