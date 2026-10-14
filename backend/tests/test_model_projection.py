"""The model audience projection: no identity-bearing field reaches the model, for any record type.

`source.basics_payload(record, "model")` is the one seam through which record payloads reach the verifier
and the generator. These tests pin its invariant and the display audience it must not change.
"""

import glob
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest

from app import source
from app.upm import ConsentScope, Record

# The built corpus's people directory; the regression net against it is skipped when it is absent.
CORPUS_DIR = Path("/tmp/luvn-2426-import") / "people"

# Payload keys of every record type, enumerated from the built corpus (401,340 records, 15,000 people).
# `_on_disk_corpus_keys` below re-checks this against the files whenever the corpus is present.
CORPUS_KEYS = {
    "perustieto": {"näyttötunniste", "sukupuoli", "syntymävuosi", "äidinkieli", "kotikunta"},
    "diagnoosi": {"nimi", "icd10", "tila"},
    "lääkitys": {"lääke", "annos", "tila"},
    "asiointitapahtuma": {"kanava", "aihe"},
    "vapaateksti": {"otsikko", "teksti", "käyntityyppi"},
    "tutkimustulos": {"tutkimus", "arvo"},
    "päätös": {"päätös", "voimassa"},
}

# Keys that match the identity pattern below but are clinical substance, reviewed by hand. `diagnoosi.nimi` is
# the diagnosis name ("Muu ja määrittämätön vatsakipu"), not a person's name. A new matching key that is not in
# this set fails the regression net until someone decides whether it identifies the person.
REVIEWED_NOT_IDENTITY = {("diagnoosi", "nimi")}

IDENTITY_PATTERN = re.compile(r"tunnus|nimi|name|henkilö", re.IGNORECASE)

# One valid (topic, authored_by_role, consent_scope) per record type, taken from the corpus.
ROW_SHAPE = {
    "perustieto": ("demografia", "sihteeri", "treatment"),
    "diagnoosi": ("hoito", "laakari", "treatment"),
    "lääkitys": ("lääkitys", "laakari", "treatment"),
    "asiointitapahtuma": ("hoito", "sairaanhoitaja", "treatment"),
    "vapaateksti": ("hoito", "sairaanhoitaja", "treatment"),
    "tutkimustulos": ("hoito", "sairaanhoitaja", "treatment"),
    "päätös": ("palvelutarve", "sosiaalityontekija", "social_care"),
}

# Identity keys injected into every record type, so the invariant is tested against a payload that carries them.
INJECTED_IDENTITY = {"näyttötunniste": "SYN-ASI-1000000001", "henkilötunnus": "010101-123A",
                     "etunimi": "Aino", "sukunimi": "Esimerkki"}

# The assertable substance per type: a field the verifier must still see to check a sentence against the record.
SUBSTANCE = {
    "diagnoosi": ("nimi", "Muu ja määrittämätön vatsakipu"),
    "lääkitys": ("lääke", "essitalopraami"),
    "asiointitapahtuma": ("aihe", "98"),
    "vapaateksti": ("teksti", "Ympärivuorokautisen tuen tarve"),
    "tutkimustulos": ("arvo", "7.457"),
    "päätös": ("päätös", "Ympärivuorokautinen palveluasuminen"),
}

RECORDED = datetime(2024, 6, 23, 12, 29, tzinfo=timezone.utc)


def _record(record_type, payload, record_id=None):
    topic, role, scope = ROW_SHAPE[record_type]
    return Record(record_id=record_id or f"veil-{record_type}-1", source="delivery", record_type=record_type,
                  authored_by_role=role, recorded_at=RECORDED, topic=topic, sensitivity_tag="none",
                  consent_scope=cast(ConsentScope, scope), payload=payload)


@pytest.mark.parametrize("record_type", sorted(CORPUS_KEYS))
def test_model_projection_has_no_identity_key_for_any_record_type(record_type):
    payload = {**dict.fromkeys(CORPUS_KEYS[record_type], "x"), **INJECTED_IDENTITY}
    projection = source.basics_payload(_record(record_type, payload), "model")
    leaked = [key for key in INJECTED_IDENTITY if key in projection]
    assert leaked == [], f"identity keys reached the model for {record_type}: {leaked}"


@pytest.mark.parametrize("record_type", sorted(SUBSTANCE))
def test_model_projection_keeps_the_assertable_substance(record_type):
    key, value = SUBSTANCE[record_type]
    payload = {key: value, **{k: "x" for k in CORPUS_KEYS[record_type] if k != key}, **INJECTED_IDENTITY}
    projection = source.basics_payload(_record(record_type, payload), "model")
    assert projection[key] == value, f"{record_type} lost its substance field {key!r}"


def test_model_projection_removes_only_identity_and_narrows_nothing_else():
    # A non-identity payload passes through unchanged, including the diagnosis name that `nimi` would also match.
    payload = {"nimi": "Muu ja määrittämätön vatsakipu", "icd10": "R10.4", "tila": "ei_aktiivinen"}
    assert source.basics_payload(_record("diagnoosi", payload), "model") == payload
    with_identity = {**payload, "näyttötunniste": "SYN-ASI-1000000001"}
    assert source.basics_payload(_record("diagnoosi", with_identity), "model") == payload


def test_basics_model_projection_is_empty_by_design():
    basics = _record("perustieto", {"näyttötunniste": "SYN-ASI-1000000001", "syntymävuosi": 1967,
                                    "kotikunta": "Espoo", "sukupuoli": "mies", "äidinkieli": "suomi"})
    assert source.basics_payload(basics, "model") == {}


def test_display_audience_is_unchanged_for_basics_and_passes_other_types_through():
    basics = _record("perustieto", {"nimi": "Aino Esimerkki", "näyttötunniste": "SYN-ASI-1000000001",
                                    "sukupuoli": "nainen", "syntymävuosi": 1990, "äidinkieli": "suomi",
                                    "kotikunta": "Vantaa", "asumismuoto": "koti", "secret": "never"})
    assert source.basics_payload(basics, "display") == {
        "nimi": "Aino Esimerkki", "näyttötunniste": "SYN-ASI-1000000001", "sukupuoli": "nainen",
        "syntymävuosi": 1990, "äidinkieli": "suomi", "kotikunta": "Vantaa", "asumismuoto": "koti",
    }
    diagnosis = _record("diagnoosi", {"nimi": "Muu", "icd10": "R10.4", "tila": "ei_aktiivinen", **INJECTED_IDENTITY})
    assert source.basics_payload(diagnosis, "display") == diagnosis.payload


def test_unknown_audience_is_still_refused():
    with pytest.raises(ValueError):
        source.basics_payload(_record("diagnoosi", {"nimi": "x"}), "public")
    with pytest.raises(ValueError):
        source.basics_payload(_record("perustieto", {}), "public")


def test_regression_net_no_corpus_identity_key_reaches_the_model():
    """Built from the corpus's own key inventory: a matching key that is not reviewed fails here, in CI or locally."""
    for record_type, keys in CORPUS_KEYS.items():
        for key in keys:
            if not IDENTITY_PATTERN.search(key) or (record_type, key) in REVIEWED_NOT_IDENTITY:
                continue
            projection = source.basics_payload(_record(record_type, {key: "SYN-ASI-1000000001"}), "model")
            assert key not in projection, f"identity-looking key {record_type}.{key} reaches the model"
    # The inventory's own identity fields are exactly the removal list (plus the spare name keys, see source.py).
    assert {"näyttötunniste"} <= source.IDENTITY_KEYS


def _on_disk_corpus_keys():
    keys = {}
    for path in sorted(glob.glob(str(CORPUS_DIR / "*.jsonl"))):
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                keys.setdefault(row["record_type"], set()).update(row["payload"])
    return keys


@pytest.mark.skipif(not CORPUS_DIR.is_dir(), reason="built corpus not present on this host")
def test_regression_net_against_every_key_in_the_corpus_files():
    on_disk = _on_disk_corpus_keys()
    assert {t: sorted(k) for t, k in on_disk.items()} == {t: sorted(k) for t, k in CORPUS_KEYS.items()}, \
        "the corpus carries payload keys this test does not know: review them for identity before updating CORPUS_KEYS"
    for record_type, keys in on_disk.items():
        for key in keys:
            if IDENTITY_PATTERN.search(key) and (record_type, key) not in REVIEWED_NOT_IDENTITY:
                projection = source.basics_payload(_record(record_type, {key: "x"}), "model")
                assert key not in projection
