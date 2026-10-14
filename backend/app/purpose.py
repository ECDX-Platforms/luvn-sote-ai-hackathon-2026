"""Palvelukonteksti: the purpose value set and its per-purpose attributes.

One module owns the purpose vocabulary. `policy.cut` reads `roles`/`topics`,
`selection` reads `baseline_terms`, `decision` reads `description` and the
shared answer vocabulary. Finnish display labels stay in `main.py`, keyed off this
value set.

In-process only: no file, no environment, no seam. Required dataclass fields make
an incomplete row loud at import (`TypeError`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RELEVANCE_ANSWERS = ("relevant", "irrelevant", "unclear")

# Shared, not per-row: these keys are the vocabulary `decision_model._parse`
# validates against, so a per-row copy could silently narrow what the parser
# accepts.
RELEVANCE_CRITERIA = {
    "relevant": "Kirjaus kertoo tämän henkilön tämänhetkisestä hoidosta tai tarpeesta, joka liittyy suoraan ilmoitettuun palvelukontekstiin.",
    "irrelevant": "Kirjaus ei tuo henkilön hoitoon tai tarpeeseen liittyvää tietoa tämän palvelukontekstin valmistelemiseksi; pelkkä avainsana ei riitä.",
    "unclear": "Kirjauksesta ei voi ratkaista koskeeko tieto tätä henkilöä tai tätä palvelukontekstia; puuttuva tieto ei ole kielteinen vastaus.",
}

Purpose = Literal[
    "medication_follow_up",
    "mental_health_assessment",
    "functioning_service_needs",
    "child_health_checkup",
]


@dataclass(frozen=True)
class Palvelukonteksti:
    roles: frozenset[str]
    topics: frozenset[str]
    baseline_terms: tuple[str, ...]
    description: str


_TABLE: dict[str, Palvelukonteksti] = {
    "medication_follow_up": Palvelukonteksti(
        roles=frozenset({"laakari", "sairaanhoitaja"}),
        topics=frozenset({"lääkitys", "verenpaine", "diabetes", "astma", "kolesteroli", "hoito"}),
        baseline_terms=("lääk", "verenpaine", "annos", "lääkehoito", "puuttunut"),
        description="Lääkityksen seuranta: toteutuminen, annos ja haitat",
    ),
    "mental_health_assessment": Palvelukonteksti(
        roles=frozenset({"laakari", "sairaanhoitaja"}),
        # The doctor sees the whole clinical record: the mental-health topics and every topic the medication
        # lens admits (owner's ruling 2026-10-14: "the doctor should see everything"). Social-services topics
        # stay outside, as for the nurse.
        topics=frozenset({"mielenterveys", "hoito", "psykiatria",
                          "lääkitys", "verenpaine", "diabetes", "astma", "kolesteroli"}),
        baseline_terms=("mielentervey", "masenn", "mieliala", "ahdist", "uni", "uupum", "phq-9", "lääk"),
        description="Mielenterveyden arvio: henkilön vointi, oireet ja tuki",
    ),
    "functioning_service_needs": Palvelukonteksti(
        # `fysioterapeutti` is the therapist capacity the challenge's Kohderyhmät name and this
        # repository did not model; it joins the one purpose whose subject is functional capacity and
        # support needs, so the role axis gains a contrast that is not a new purpose.
        # `vammaispalvelut` is the fourth named example form's own service domain. It is a topic here for
        # the same reason `sosiaalipalvelut` is: the service domain is what the record is about.
        roles=frozenset({"sairaanhoitaja", "sosiaalityontekija", "fysioterapeutti"}),
        topics=frozenset({"toimintakyky", "palvelutarve", "toimeentulo", "perhetyö",
                          "sosiaalipalvelut", "vammaispalvelut", "kotihoidon palvelu", "hoito"}),
        baseline_terms=("toimintakyky", "palvelutarve", "arki", "perhetyö",
                        "toimeentulo", "tuki", "iadl", "kotihoito", "vammaispalvelu", "apuväline"),
        description="Palvelutarpeen arviointi: arjessa selviytyminen ja tuki",
    ),
    # Illustrative demo context for the Phase 3 guardian lens. The life stage is a separate, declared
    # axis: this purpose alone does not decide which catalogue rows apply.
    "child_health_checkup": Palvelukonteksti(
        roles=frozenset({"terveydenhoitaja"}),
        topics=frozenset({"kasvu", "neuvola", "terveystarkastus", "rokotus", "uni", "hoito",
                          "kouluterveydenhuolto"}),
        baseline_terms=("neuvola", "terveystarkastus", "kasvu", "pituus", "paino", "rokotus",
                        "esitieto", "uni"),
        description="Neuvolan terveystarkastus: lapsen kasvu, kehitys ja arjen tuki",
    ),
}


# Topics a source record may carry that belong to no Palvelukonteksti's relevance set, declared rather
# than tolerated:
#
#   * `demografia` -- the identity basics record. It is admitted by an explicit rule in `policy.cut`
#     (`record_type == perustieto and topic == "demografia"`), not by a purpose's topic set.
#   * `yhteydenotto` -- the contact-record topic the source writes. It is legitimately excluded from
#     every purpose view; it is not an unknown spelling.
#
# The point of declaring them: an UNDECLARED topic is the failure this vocabulary exists to catch. A
# vendor topic nobody declared is silently dropped by the purpose filter, and a briefing that shows
# nothing looks identical to a person who has nothing. `policy.cut` therefore refuses one instead.
NON_PURPOSE_TOPICS = frozenset({"demografia", "yhteydenotto"})


def topics() -> frozenset[str]:
    """Every topic this codebase declares: the union of the purpose sets plus the non-purpose topics."""
    return frozenset().union(*(row.topics for row in _TABLE.values())) | NON_PURPOSE_TOPICS


def palvelukontekstit() -> tuple[str, ...]:
    """The purpose value set, in table order."""
    return tuple(_TABLE)


def palvelukonteksti(purpose: str) -> Palvelukonteksti:
    """The attributes of one Palvelukonteksti."""
    return _TABLE[purpose]
