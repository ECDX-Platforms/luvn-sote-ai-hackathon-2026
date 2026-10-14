"""The fixture-era bound encounter catalogue, kept for the tests and the offline evaluation scripts.

The product resolves encounters from the corpus it is pointed at (`Store(...).encounter_index()`, an
`encounters.csv` or the SQL `encounters` table). These maps are the four source-keyed bound demo personas'
lenses, compiled here so a test or an evaluation script can build a `Store(personas=load_personas(),
encounters=ENCOUNTERS)` without a delivery on disk. Nothing under `backend/app` imports this module.

    from tests.fixtures.bound_encounters import ENCOUNTERS, EVALUATION_ENCOUNTERS, EXAMPLE_LABELS
"""

from __future__ import annotations

from app.encounters import Encounter

ENCOUNTERS = {
    "bound-encounter-002-laakitys-sairaanhoitaja": Encounter(
        "bound-encounter-002-laakitys-sairaanhoitaja", "bound-persona-002-krooninen-ikääntynyt",
        "sairaanhoitaja", "medication_follow_up", "bound-source-002",
    ),
    "bound-encounter-003-mielenterveys-sairaanhoitaja": Encounter(
        "bound-encounter-003-mielenterveys-sairaanhoitaja", "bound-persona-003-mielenterveys-sosiaali",
        "sairaanhoitaja", "mental_health_assessment", "bound-source-003",
    ),
    "bound-encounter-003-toimintakyky-sairaanhoitaja": Encounter(
        "bound-encounter-003-toimintakyky-sairaanhoitaja", "bound-persona-003-mielenterveys-sosiaali",
        "sairaanhoitaja", "functioning_service_needs", "bound-source-003",
    ),
    # Same person/purpose as the nurse view; only the doctor policy admits authored bound-note-019.
    "bound-encounter-003-mielenterveys-laakari": Encounter(
        "bound-encounter-003-mielenterveys-laakari", "bound-persona-003-mielenterveys-sosiaali",
        "laakari", "mental_health_assessment", "bound-source-003",
    ),
    # The therapist capacity the challenge names and this repository did not model. Same person and
    # purpose as the nurse's toimintakyky lens, so the only axis that varies is the professional -- and
    # because the purpose's topic set does not admit `mielenterveys`, this lens cannot see the PHQ-9 or
    # the mood note the nurse's lens carries. That contrast is the point: a role is a lens, not a filter
    # over the same records, and a smaller permitted set is not a smaller person.
    "bound-encounter-003-toimintakyky-fysioterapeutti": Encounter(
        "bound-encounter-003-toimintakyky-fysioterapeutti", "bound-persona-003-mielenterveys-sosiaali",
        "fysioterapeutti", "functioning_service_needs", "bound-source-003",
    ),
    "bound-encounter-004-sosiaalipalvelut-sosiaalityontekija": Encounter(
        "bound-encounter-004-sosiaalipalvelut-sosiaalityontekija", "bound-persona-004-sosiaalipalvelut",
        "sosiaalityontekija", "functioning_service_needs", "bound-source-004",
    ),
    # Value fixture: the deterministic baseline misses bound-note-011 (its topic "hoito" and its
    # title/body contain none of the medication_follow_up baseline terms), so Decision-1 has a candidate it
    # can add that baseline did not select. bound-note-012 is a deliberately irrelevant control.
    "bound-encounter-005-laakitys-sairaanhoitaja": Encounter(
        "bound-encounter-005-laakitys-sairaanhoitaja", "bound-persona-005-omahoidon-tuki",
        "sairaanhoitaja", "medication_follow_up", "bound-source-005",
    ),
    # Phase 3 illustrative guardian lens: a child's neuvola health check. The lens covers guardian
    # consent only, so a record the child holds under their own confidentiality right is absent from
    # this view and appears only as a neutral absence.
    "bound-encounter-012-neuvola-terveydenhoitaja": Encounter(
        "bound-encounter-012-neuvola-terveydenhoitaja", "bound-persona-012-neuvola-lapsi",
        "terveydenhoitaja", "child_health_checkup", "bound-source-012",
        consent_holders=("guardian",), life_stage="child",
    ),
    # The fourth named example form and its service domain: `vammaispalvelut`. The form is modelled where
    # CONTEXT.md defines a Lomake to live -- as provenance on the Kirjaus that is its instance -- rather
    # than as a row in `pipeline._FORM_CATALOGUE`, which is keyed by (purpose, life_stage) alone and would
    # therefore print an "ei näytetä tässä koosteessa" row on every other functioning_service_needs
    # briefing to say nothing about them.
    "bound-encounter-013-vammaispalvelut-sosiaalityontekija": Encounter(
        "bound-encounter-013-vammaispalvelut-sosiaalityontekija", "bound-persona-013-vammaispalvelut",
        "sosiaalityontekija", "functioning_service_needs", "bound-source-013",
    ),
}

# Evaluator-only controls for the F2 value inventory. They are intentionally NOT in
# ENCOUNTERS: the hosted product keeps the walkthrough encounters, while the
# offline/live value evaluation resolves these six through the same pipeline.
EVALUATION_ENCOUNTERS: dict[str, Encounter] = {
    "bound-encounter-006-laakitys-sairaanhoitaja": Encounter(
        "bound-encounter-006-laakitys-sairaanhoitaja", "bound-persona-006-other-person-control",
        "sairaanhoitaja", "medication_follow_up", "bound-source-006",
    ),
    "bound-encounter-007-laakitys-sairaanhoitaja": Encounter(
        "bound-encounter-007-laakitys-sairaanhoitaja", "bound-persona-007-administrative-control",
        "sairaanhoitaja", "medication_follow_up", "bound-source-007",
    ),
    "bound-encounter-008-laakitys-sairaanhoitaja": Encounter(
        "bound-encounter-008-laakitys-sairaanhoitaja", "bound-persona-008-historical-control",
        "sairaanhoitaja", "medication_follow_up", "bound-source-008",
    ),
    "bound-encounter-009-laakitys-sairaanhoitaja": Encounter(
        "bound-encounter-009-laakitys-sairaanhoitaja", "bound-persona-009-paraphrase-addition",
        "sairaanhoitaja", "medication_follow_up", "bound-source-009",
    ),
    "bound-encounter-010-laakitys-sairaanhoitaja": Encounter(
        "bound-encounter-010-laakitys-sairaanhoitaja", "bound-persona-010-shortlist-scale",
        "sairaanhoitaja", "medication_follow_up", "bound-source-010",
    ),
    "bound-encounter-011-laakitys-sairaanhoitaja": Encounter(
        "bound-encounter-011-laakitys-sairaanhoitaja", "bound-persona-011-ambiguous-control",
        "sairaanhoitaja", "medication_follow_up", "bound-source-011",
    ),
}


# The display names the fixture corpus' worklist rows carry; a delivery supplies its own in `encounters.csv`.
EXAMPLE_LABELS: dict[str, str] = {
    "bound-encounter-002-laakitys-sairaanhoitaja": "Aino Virtanen",
    "bound-encounter-003-mielenterveys-sairaanhoitaja": "Elias Korhonen",
    "bound-encounter-003-toimintakyky-sairaanhoitaja": "Elias Korhonen",
    "bound-encounter-003-mielenterveys-laakari": "Elias Korhonen",
    "bound-encounter-003-toimintakyky-fysioterapeutti": "Elias Korhonen",
    "bound-encounter-004-sosiaalipalvelut-sosiaalityontekija": "Sanni Lehtinen",
    "bound-encounter-005-laakitys-sairaanhoitaja": "Taisto Mäkinen",
    "bound-encounter-012-neuvola-terveydenhoitaja": "Onni Laakso",
    "bound-encounter-013-vammaispalvelut-sosiaalityontekija": "Juho Nieminen",
}
