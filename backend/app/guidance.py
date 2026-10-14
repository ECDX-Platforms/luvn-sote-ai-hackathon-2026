"""Käypä hoito guidance as deep links, never as content.

**What the terms allow, and why this module is a link map rather than a knowledge base.** The Finnish terms of
use (dated 2.2.2026) prohibit using, copying, sharing or otherwise exploiting the content *to train, develop
or improve any AI application*, and the only carve-out is a minor amount for the user's own private use in an
application that neither stores the data permanently nor uses it for training. A multi-user product with a
database satisfies neither limb. Linking, by contrast, is **explicitly permitted** in a prescribed form —
`https://www.kaypahoito.fi/hoiNNNNN`, with `www.kaypahoito.fi` displayed and the latest published version
cited. So this module stores codes, titles and URLs, and nothing else: no recommendation text, no summary, no
paraphrase. `docs/regulatory-position.md` records the position and issue 12 records how it was established.

**Where the codes came from.** They are not enumerable from any permitted metadata channel, and this is a
trap worth recording: `/wp-sitemap.xml` lists 684 news posts and 60 pages and **zero** guidelines, the
guidelines are not a WordPress REST post type, and the site's own search endpoint returns news rather than
guidelines. The set here was enumerated from search-engine and site-search *metadata* (URL plus title only),
then every code was fetched and kept only if the page served a real guideline heading and a publication date.

**A 200 is not liveness, and that is the second trap.** Unknown `/hoiNNNNN` codes return HTTP 200 while serving
the site's generic 404 page — 36 such codes were found — so a status-code-only link checker reports dead
clinical links as healthy. `scripts/verify_guideline_links.py` decides liveness from the page: no 404 marker, a
real heading, no redirect, and the heading must still match the title recorded here. `hoi50078` redirects to
`hoi50127` and the target is what is recorded; `hoi50111` is titled "(ylläpito lopetettu)" and is not carried.

**Topics with no guideline are recorded as having none.** `NO_GUIDELINE_TOPICS` names them — social services,
disability services, income support, vaccination, home care, generic functional ability — because an empty
mapping is the honest answer and a merely plausible one is a clinical error waiting to be read as guidance.

**Nothing here is fetched at runtime.** The surface renders the deep link; it never retrieves a guideline. That
is both the licence position and a simpler failure mode: a link that cannot be reached is a link, not an
outage.

"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from . import source
from .picture import Picture

CITATION_HOST = "www.kaypahoito.fi"


@dataclass(frozen=True)
class Guideline:
    """One guideline, as a citation: code, title, publication date and the visible terms for its topic."""

    code: str
    title: str
    published: str
    topics: tuple[str, ...]
    verified_at: str
    http_status: int = 200

    @property
    def url(self) -> str:
        return f"https://{CITATION_HOST}/{self.code}"

    @property
    def citation(self) -> str:
        # The terms require the latest published version to be named and www.kaypahoito.fi to be visible.
        return f"{self.title} — {CITATION_HOST} (julkaistu {self.published})"


# Verified at submission: every code below was fetched and returned a real guideline page with the title and
# publication date recorded here. Two traps this list is built to avoid:
#
#   * **A 200 is not liveness.** Unknown /hoiNNNNN codes return HTTP 200 while serving the site's generic
#     404 page, so a status-code-only checker stores dead clinical links. 36 such codes were found and are
#     excluded; `scripts/verify_guideline_links.py` refuses one by looking for the error marker in the page.
#   * **A redirect breaks the one-code-one-guideline property.** hoi50078 answers 307 to hoi50127, so the
#     target is recorded, not the redirect. hoi50111 is titled "(ylläpito lopetettu)" — maintenance
#     discontinued — and is not carried at all.
#
# Only guidelines that map onto a topic this product actually holds are listed. Topics with no guideline are
# left empty rather than filled with something plausible: sosiaalipalvelut, vammaispalvelut, toimeentulo,
# rokotus and generic "toimintakyky" have no Käypä hoito counterpart, and an empty mapping is the honest
# answer.
GUIDELINES: tuple[Guideline, ...] = (
    Guideline(code="hoi04010", title="Kohonnut verenpaine", published="21.09.2026",
              topics=("verenpaine", "hypertensio", "i10", "i11", "i12", "i13", "i15"),
              verified_at="at submission"),
    Guideline(code="hoi50056", title="Tyypin 2 diabetes", published="18.03.2024",
              topics=("tyypin 2 diabetes", "tyypin 2", "e11", "diabetes"),
              verified_at="at submission"),
    Guideline(code="hoi50116", title="Insuliininpuutosdiabetes", published="12.11.2025",
              topics=("insuliininpuutosdiabetes", "tyypin 1 diabetes", "e10"),
              verified_at="at submission"),
    Guideline(code="hoi50025", title="Dyslipidemiat", published="24.09.2026",
              topics=("dyslipidemia", "kolesteroli", "ldl", "e78"),
              verified_at="at submission"),
    Guideline(code="hoi06030", title="Astma", published="29.03.2022",
              topics=("astma", "j45"), verified_at="at submission"),
    # PHQ-9's own instrument link: a site search for "phq-9" returns this guideline first.
    Guideline(code="hoi50023", title="Depressio", published="25.06.2026",
              topics=("depressio", "masennus", "phq", "f32", "f33"),
              verified_at="at submission"),
    Guideline(code="hoi50119", title="Ahdistuneisuushäiriöt", published="25.06.2026",
              topics=("ahdistus", "ahdistuneisuus", "f40", "f41"),
              verified_at="at submission"),
    Guideline(code="hoi50122", title="Itsemurhien ehkäisy ja itsemurhaa yrittäneen hoito", published="18.02.2022",
              topics=("itsetuhoisuus", "itsemurha", "x60", "x61", "x62", "x63", "x64", "x70", "x71", "x72",
                      "x73", "x74"),
              verified_at="at submission"),
    Guideline(code="hoi50067", title="Unettomuus", published="25.06.2026",
              topics=("unettomuus", "univaikeus", "g47"), verified_at="at submission"),
    # The only guideline that mentions IADL, which is why it is here — and why it must only be attached when
    # the visible records indicate cognitive decline. A low IADL score alone does not make this the right
    # guidance, and attaching it to every functional-ability record would be clinically wrong.
    Guideline(code="hoi50044", title="Muistisairaudet", published="08.04.2026",
              topics=("muistisairaus", "muisti", "dementia", "alzheimer", "f00", "f01", "f02", "f03", "g30"),
              verified_at="at submission"),
    Guideline(code="hoi50126", title="Monisairas potilas", published="16.03.2021",
              topics=("monisairas", "monilääkitys", "polyfarmasia"), verified_at="at submission"),
    # Caveat carried in the note: this is about self-medication, not adherence, and no adherence guideline
    # exists. It is attached only on the word itself rather than standing in for adherence generally.
    Guideline(code="hoi50106", title="Itselääkitys", published="14.05.2024",
              topics=("itselääkitys",), verified_at="at submission"),
    Guideline(code="hoi50028", title="Alkoholiongelmat", published="21.08.2018",
              topics=("alkoholi", "päihde", "f10"), verified_at="at submission"),
    Guideline(code="hoi40020", title="Tupakka- ja nikotiiniriippuvuuden ehkäisy ja hoito", published="19.08.2026",
              topics=("tupakka", "nikotiini", "tupakointi", "f17"), verified_at="at submission"),
    Guideline(code="hoi50124", title="Lihavuus (lapset, nuoret ja aikuiset)", published="17.04.2025",
              topics=("lihavuus", "painoindeksi", "e66"), verified_at="at submission"),
    Guideline(code="hoi50085", title="Kehityksellinen kielihäiriö (kielellinen erityisvaikeus, lapset ja "
                                           "nuoret)", published="24.08.2026",
              topics=("kielihäiriö", "kielellinen erityisvaikeus", "f80"), verified_at="at submission"),
    Guideline(code="hoi50132", title="Lasten ja nuorten kehityksellinen koordinaatiohäiriö (DCD)",
              published="13.02.2026", topics=("koordinaatiohäiriö", "dcd", "f82"), verified_at="at submission"),
    Guideline(code="hoi50118", title="Käytöshäiriöt (lapset ja nuoret)", published="12.12.2018",
              topics=("käytöshäiriö", "f91"), verified_at="at submission"),
)

# Topics this product holds for which **no** guideline exists. Recorded so the absence is a decision rather
# than an oversight, and so nobody fills them later with something merely plausible.
NO_GUIDELINE_TOPICS: tuple[str, ...] = ("sosiaalipalvelut", "vammaispalvelut", "toimeentulo", "perhetyö",
                                        "rokotus", "kotihoidon palvelu", "toimintakyky", "neuvola",
                                        "terveystarkastus")

# The site's own 404 marker. A code can answer 200 and still serve this page, which is why liveness is
# decided by the page's content and not by its status.
SOFT_404_MARKERS: tuple[str, ...] = ("etsimääsi sivua ei löytynyt", "virhe 404")

# Article families published by Käypä hoito (the accepted set is recorded with the citation metadata).
# The express linking permission (Linkittäminen, 7.12.2023) prescribes the hoiNNNNN form and names
# suositukset. The accepted/linkable set is intentionally conservative: only hoi, gvr, khp, and khr
# match CODE_SHAPE today. dnd (Vältä viisaasti) is named a suositus in the publisher's liability block
# but has a different URL shape, and pgr (Apuohjelmat) is not a suositus so it stays un-linkable.
@dataclass(frozen=True)
class ArticleFamily:
    prefix: str
    label: str
    is_suositus: bool
    linkable: bool
    basis: str


ARTICLE_FAMILIES: dict[str, ArticleFamily] = {
    "hoi": ArticleFamily(
        prefix="hoi",
        label="Käypä hoito -suositukset",
        is_suositus=True,
        linkable=True,
        basis="express permission prescribes the hoiNNNNN form and names suositukset",
    ),
    "gvr": ArticleFamily(
        prefix="gvr",
        label="Swedish guidelines (God medicinsk praxis)",
        is_suositus=True,
        linkable=True,
        basis="Swedish recommendation sibling of Käypä hoito -suositukset",
    ),
    "khp": ArticleFamily(
        prefix="khp",
        label="Potilasversiot (suomi)",
        is_suositus=False,
        linkable=True,
        basis="patient version of recommendation, accepted by CODE_SHAPE",
    ),
    "khr": ArticleFamily(
        prefix="khr",
        label="Potilasversiot (ruotsi)",
        is_suositus=False,
        linkable=True,
        basis="Swedish patient version of recommendation, accepted by CODE_SHAPE",
    ),
    "kht": ArticleFamily(
        prefix="kht",
        label="Käyvän hoidon tiivistelmät",
        is_suositus=False,
        linkable=False,
        basis="condensed professional summary, outside express hoi permission",
    ),
    "dnd": ArticleFamily(
        prefix="dnd",
        label="Vältä viisaasti",
        is_suositus=True,
        linkable=False,
        basis="named a suositus by publisher's liability block, but uses non-hoi URL form; kept un-linkable under conservative reading",
    ),
    "nak": ArticleFamily(
        prefix="nak",
        label="Näytönastekatsaukset",
        is_suositus=False,
        linkable=False,
        basis="evidence review attached to a guideline; cited via the parent",
    ),
    "nix": ArticleFamily(
        prefix="nix",
        label="Lisätietoa aiheesta",
        is_suositus=False,
        linkable=False,
        basis="supplementary article, not an independent suositus",
    ),
    "imk": ArticleFamily(
        prefix="imk",
        label="Käypä hoito -kuvat",
        is_suositus=False,
        linkable=False,
        basis="figures; republication and linking always need separate permission",
    ),
    "khl": ArticleFamily(
        prefix="khl",
        label="Luentomateriaalit",
        is_suositus=False,
        linkable=False,
        basis="lecture material, not a suositus",
    ),
    "khk": ArticleFamily(
        prefix="khk",
        label="Käsikirjat (Hoitosuositustyöryhmien käsikirja)",
        is_suositus=False,
        linkable=False,
        basis="authoring manual, not a clinical recommendation",
    ),
    "ook": ArticleFamily(
        prefix="ook",
        label="Opi ja ota käyttöön",
        is_suositus=False,
        linkable=False,
        basis="implementation package, not a suositus",
    ),
    "ccs": ArticleFamily(
        prefix="ccs",
        label="Current Care Summary (English)",
        is_suositus=False,
        linkable=False,
        basis="English summary, outside express hoi permission",
    ),
    "inf": ArticleFamily(
        prefix="inf",
        label="Info",
        is_suositus=False,
        linkable=False,
        basis="meta-information page, not a suositus",
    ),
    "pgr": ArticleFamily(
        prefix="pgr",
        label="Apuohjelmat",
        is_suositus=False,
        linkable=False,
        basis="not a suositus (tool/form article); outside express hoi permission so pgr00035 stays uncited",
    ),
}

CODE_SHAPE = re.compile(r"^(?:hoi|khp|khr|gvr)\d{5}$")


def links_for(picture: Picture, *, limit: int = 2) -> list[dict]:
    """The attached guidelines whose topic a record **in the built picture** supports.

    Two rules, the same two the rest of the surface obeys. Only attached guidelines are ever returned, so an
    unconfirmed page type cannot reach a professional. And a link is offered only when a record the picture
    carries mentions its topic — a link implied by a withheld, or permitted-but-unselected, record would
    disclose that the record exists or disagree with the briefing about relevance, and either is the
    differential-oracle failure the cause-neutral refusal exists to avoid. The candidates are exactly
    `picture.records.priority + picture.records.accessible_other`, never the wider permitted set: the picture
    is what this downstream module receives (CONTRACT rule 7), not a privileged upstream handle.

    **A link is a reference, not an input.** This is the licence boundary, and it has two sides. What is
    returned here is displayed to the professional: the rail's evidence panel and, when the answer's own
    record sentences cite the same picture record, an `external_evidence` statement in `chat.py`. What must
    never happen is guideline *content* reaching a model, and this picture-derived link list never does
    either: the assistant's record-reading tools (`assistant.build_tools`) hand the model the picture's
    records alone, and `tests/test_chat.py::test_no_guideline_metadata_reaches_the_model_payload` pins that
    rather than trusting it. The one deliberate, narrow exception is `search_knowledge`: through
    `knowledge_search`, a query the model makes can return a guideline's **citation** (code, title, URL)
    resolved from `GUIDELINES`, as a reference it may cite -- the same metadata the rail shows, and nothing
    more (ADR-0027). Duodecim's terms permit the deep link and prohibit using the content to develop or
    improve an AI application (`docs/references.md` §6); a recorded title, date and URL are on the permitted
    side of that line and a summary or an excerpt is not. Do not "improve" an answer by passing guideline
    content into a prompt.
    """
    visible: list[tuple[str, str]] = []
    for record in [*picture.records.priority, *picture.records.accessible_other]:
        text = " ".join([record.topic or "", record.record_type or "", source.note_text(record),
                         " ".join(source.string_values(record))]).casefold()
        visible.append((record.record_id, text))
    links: list[dict] = []
    for guideline in GUIDELINES:
        matched = [record_id for record_id, text in visible
                   if any(topic in text for topic in guideline.topics)]
        if not matched:
            continue
        links.append({"code": guideline.code, "title": guideline.title, "url": guideline.url,
                      "citation": guideline.citation, "published": guideline.published,
                      "evidence_record_ids": sorted(matched), "verified_at": guideline.verified_at,
                      "http_status": guideline.http_status,
                      "note": ("Käypä hoito -suositus. Avautuu Käypä hoito -sivustolla; suosituksen sisältöä ei "
                               "ole kopioitu tähän.")})
        if len(links) >= limit:
            break
    return links


def topics_without_guidance() -> list[str]:
    """The product topics for which no guideline exists, so the gap is stated rather than papered over."""
    return list(NO_GUIDELINE_TOPICS)


def metadata_is_complete() -> list[str]:
    """Every entry must carry a code the prescribed form allows, a title and a verification record."""
    problems: list[str] = []
    for guideline in GUIDELINES:
        if not CODE_SHAPE.match(guideline.code):
            problems.append(f"{guideline.code}: not a recognised guideline code shape")
        if not guideline.title.strip():
            problems.append(f"{guideline.code}: no title, and a link without a citation may not be shown")
        if guideline.http_status != 200:
            problems.append(f"{guideline.code}: recorded status is {guideline.http_status}, not 200")
        if not guideline.published:
            problems.append(f"{guideline.code}: no publication date, and the citation rules require the "
                            f"latest published version to be named")
        if not guideline.verified_at:
            problems.append(f"{guideline.code}: no verification date")
    return problems


def today() -> str:
    return date.today().isoformat()
