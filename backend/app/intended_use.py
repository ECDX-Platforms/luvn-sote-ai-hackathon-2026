"""The demo's declared purpose of use (käyttötarkoitus), as one served sentence.

One module owns the declaration so the served value and the repository's regulatory
position cannot drift: `tests/test_intended_use.py` reads
`docs/regulatory-position.md` and asserts this exact sentence appears there.

The declaration is a **product statement**, not a legal classification. It carries
no statute, regulation article, risk class, device class, conformity statement or
deadline, because none of those is approved and `docs/architecture-as-built.md`
(standing limits) forbids the claim. The classification reading lives only in
`docs/regulatory-position.md`, explicitly labelled non-authoritative.

In-process only: no file, no environment, no seam.
"""

from __future__ import annotations

# Bump when the declared purpose changes. Both the sentence and this version are served: a
# consumer holding a payload must be able to tell which declaration it was built under, exactly
# as `AI-Generated-Marking` lets one tell which marking shape it is parsing. The version was
# previously a constant nothing carried — the docstring claimed the wire carried it while
# `Picture` had only the sentence. `Picture.intended_use_version` now carries it.
INTENDED_USE_VERSION = "luvn-intended-use-v1"

INTENDED_USE = (
    "Näyttää ammattilaiselle tämän palvelutilanteen kannalta olennaiset ja sallitut "
    "esitiedot, jotka on koottu lähteistä ja tarkistettu lähdeviitteisiin. Voi ehdottaa "
    "seuraavia toimenpiteitä nimettyihin tietolähteisiin nojaten; ehdotukset on laadittu "
    "koneellisesti ja merkitty niiksi. Ammattilainen tekee päätökset: järjestelmä ei tee "
    "kliinisiä päätöksiä, ei korvaa ammattilaisen arviota eikä ole lääkinnällinen laite."
)
