"""Pre-registered fixture contract: render source category; never derive clinical flags."""

import json
from pathlib import Path

from app import source

ROOT = Path(__file__).resolve().parents[2]


def test_lab_fixture_carries_source_category_and_reference_as_provenance():
    rows = [json.loads(line) for line in
            (ROOT / "data/experimental/delivery-sample/records.jsonl").read_text(encoding="utf-8").splitlines()]
    lab = next(row for row in rows if row["record_type"] == source.LAB_RESULT)
    assert {"tutkimus", "arvo", "yksikkö", "tulkinta", "viitearvo"} <= lab["payload"].keys()
    assert lab["payload"]["tulkinta"] in {
        "viitearvo", "poikkeava", "kliinisesti hälyttävä", "kriittisesti hälyttävä"
    }
    assert isinstance(lab["payload"]["viitearvo"], str) and lab["payload"]["viitearvo"]
