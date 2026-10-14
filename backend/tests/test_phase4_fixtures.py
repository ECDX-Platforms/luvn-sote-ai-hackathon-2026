from app.fixtures import load_personas
from app.upm import Record

EXISTING_IDS = {
    "bound-persona-002-krooninen-ikääntynyt": {
        "bound-demo-002", "bound-dx-002", "bound-dx-003", "bound-med-002", "bound-med-003",
        "bound-note-002", "bound-asiointi-002", "bound-asiointi-003",
    },
    "bound-persona-003-mielenterveys-sosiaali": {
        "bound-demo-003", "bound-note-003", "bound-note-010", "bound-dx-004", "bound-med-004",
        "bound-note-004", "bound-asiointi-004", "bound-arviointi-003", "bound-asiointi-005",
        "bound-note-019",
    },
    "bound-persona-004-sosiaalipalvelut": {
        "bound-demo-004", "bound-note-005", "bound-arviointi-006", "bound-note-009", "bound-paatos-005",
        "bound-paatos-007", "bound-arviointi-007", "bound-asiointi-006", "bound-asiointi-007",
    },
    "bound-persona-005-omahoidon-tuki": {
        "bound-demo-005", "bound-dx-005", "bound-med-005", "bound-note-011", "bound-note-012",
        "bound-note-013",
    },
    "bound-persona-012-neuvola-lapsi": {
        "bound-demo-012", "bound-mittaus-012", "bound-mittaus-013", "bound-note-020", "bound-self-012",
    },
}
ADDITIONS = {
    "bound-persona-002-krooninen-ikääntynyt": {"bound-note-002-alignment", "bound-note-002-draft"},
    "bound-persona-003-mielenterveys-sosiaali": {"bound-note-003-alignment", "bound-note-003-draft"},
    "bound-persona-004-sosiaalipalvelut": {"bound-note-004-alignment", "bound-note-004-draft"},
    "bound-persona-005-omahoidon-tuki": {"bound-lab-005-alignment"},
    "bound-persona-012-neuvola-lapsi": set(),
}


def test_authorized_fixture_additions_are_exact_and_source_provenance_only():
    personas = {row.persona_id: row for row in load_personas()}
    for persona_id, existing_ids in EXISTING_IDS.items():
        persona = personas[persona_id]
        by_id = {record.record_id: record for record in persona.records}
        assert set(by_id) == existing_ids | ADDITIONS[persona_id]
        if persona_id in {
            "bound-persona-005-omahoidon-tuki", "bound-persona-012-neuvola-lapsi"
        }:
            assert all(record.erikoisala and record.suorituspaikka for record in persona.records)
        else:
            assert all(by_id[record_id].erikoisala is None and by_id[record_id].suorituspaikka is None
                       for record_id in existing_ids)
            assert all(by_id[record_id].erikoisala and by_id[record_id].suorituspaikka
                       for record_id in ADDITIONS[persona_id])
        assert all(record.case_id is None and record.legal_basis is None for record in persona.records)
        assert all(record.käyntiaika is None and not record.vastaanottajat for record in persona.records)

    drafts = [record for persona in personas.values() for record in persona.records if record.state == "draft"]
    assert {record.record_id for record in drafts} == {
        "bound-note-002-draft", "bound-note-003-draft", "bound-note-004-draft",
    }
    assert all(record.record_type == "vapaateksti" for record in drafts)
    lab_rows = [record for persona in personas.values() for record in persona.records
                if record.record_type == "tutkimustulos"]
    assert [record.record_id for record in lab_rows] == ["bound-lab-005-alignment"]
    assert lab_rows[0].payload["tulkinta"] == "poikkeava"
    assert "viitearvo" not in lab_rows[0].payload
    assert "toiminto" not in Record.model_fields
