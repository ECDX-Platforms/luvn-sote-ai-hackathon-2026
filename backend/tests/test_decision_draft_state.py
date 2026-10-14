"""A decision text that announces itself as a draft is a draft (UI review 2026-10-14).

The VEIL social-work export carries decision drafts whose text begins with `# PÄÄTÖSLUONNOS` and no envelope
`state`, so the briefing rendered them as settled decisions. `delivery.derived_record_state` derives
`state = "draft"` from the text where the record is built from the delivery row -- the file import and the SQL
loader share `_bind_record` -- and where a stored record is read back. A stated state wins; the text is untouched.
"""

from __future__ import annotations

import json

import pytest

from app import delivery, sqldelivery
from app.picture import RecordView
from app.upm import Record

PROFILES = {"SYN-1": {"persona_id": "person-1", "label": "Synthetic"}}
DRAFT_TEXT = "# PÄÄTÖSLUONNOS\n\n## PERUSTIEDOT\n- Päätöspäivämäärä: 4.2.2026\n\n## RATKAISU\nMyönteinen."


def _row(record_type: str, payload: dict, **envelope) -> dict:
    return {"record_id": f"{record_type}-1", "source_person_id": "SYN-1", "record_type": record_type,
            "authored_by_role": "sosiaalityontekija", "recorded_at": "2026-02-04T09:00:00+00:00",
            "topic": "palvelutarve", "sensitivity_tag": "sensitive", "consent_scope": "social_care",
            "payload": payload, **envelope}


@pytest.mark.parametrize("record_type, key", [("päätös", "päätös"), ("vapaateksti", "teksti")])
def test_a_decision_text_headed_paatosluonnos_is_a_derived_draft_with_the_text_untouched(record_type, key):
    record = delivery._bind_record(_row(record_type, {key: DRAFT_TEXT, "otsikko": "Myönteinen"}), PROFILES)
    assert record.state == "draft"
    assert record.payload[key] == DRAFT_TEXT
    assert RecordView.from_record(record).state == "draft"


@pytest.mark.parametrize("text", ["PÄÄTÖSLUONNOS\nTeksti", "  ##  päätösluonnos: kotihoito", "# PÄÄTÖSLUONNOS"])
def test_the_heading_may_carry_hashes_whitespace_and_any_case(text):
    assert delivery._bind_record(_row("päätös", {"päätös": text}), PROFILES).state == "draft"


def test_an_envelope_state_wins_over_the_derived_one():
    record = delivery._bind_record(_row("päätös", {"päätös": DRAFT_TEXT}, state="approved"), PROFILES)
    assert record.state == "approved"


@pytest.mark.parametrize("text", ["Myönteinen päätös säännöllisestä kotihoidosta.", "PÄÄTÖSLUONNOKSEN liite",
                                  "Päätös\n# PÄÄTÖSLUONNOS", "# PÄÄTÖS\nluonnos"])
def test_a_decision_without_the_heading_stays_without_a_state(text):
    assert delivery._bind_record(_row("päätös", {"päätös": text}), PROFILES).state is None


def test_the_rule_reads_only_decision_texts():
    assert delivery._bind_record(_row("asiointitapahtuma", {"kanava": "puhelu", "aihe": DRAFT_TEXT}), PROFILES).state is None


def test_a_stored_record_read_back_from_sql_carries_the_derived_state_without_a_reload():
    stored = Record.model_validate(
        {**_row("päätös", {"päätös": DRAFT_TEXT}), "source": "delivery"}).model_dump_json()
    assert json.loads(stored)["state"] is None
    read = sqldelivery.SqlDeliveryIndex._record(stored, persona_id="person-1")
    assert read.state == "draft"
    assert read.payload["päätös"] == DRAFT_TEXT
