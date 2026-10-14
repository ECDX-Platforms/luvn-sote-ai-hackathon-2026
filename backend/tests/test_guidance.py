"""Guideline links: citation metadata only, attached only when a visible record supports them.

The licence position is the reason this module is a link map rather than a knowledge base, so these tests pin
the properties that keep it on the permitted side: nothing but codes, titles and URLs is stored, a link is
never attached without a citation, and a link is offered only when a record this role can already see mentions
its topic — a link implied by a withheld record would disclose that the record exists.
"""

from __future__ import annotations

import pytest

from app import fixtures, guidance, policy
from app.picture import GroundingOutcome, Picture, RecordView, Records


def _scoped(persona_id: str = "bound-persona-002-krooninen-ikääntynyt"):
    persona = next(item for item in fixtures.load_personas() if item.persona_id == persona_id)
    return policy.cut(persona, "sairaanhoitaja", "medication_follow_up")


def _picture(scoped):
    """A built picture carrying the fixture's whole permitted set.

    `links_for` now consumes the built picture. These tests are about the licence boundary, not about
    relevance selection, so the picture carries every permitted record as selected; the record set is the
    same one the old `Scoped`-based calls walked.
    """
    views = [RecordView.from_record(record) for record in scoped.records]
    return Picture(role=scoped.role, purpose=scoped.purpose, encounter_id=scoped.encounter_id,
                   situation={"situation_type": "", "confidence": ""},
                   risk={"risk_level": "", "reason": "", "basis": None},
                   actions=[], conflicts=[], narrative=None, citations=[],
                   records=Records(priority=views, accessible_other=[]),
                   trace=[], grounded=False,
                   grounding=GroundingOutcome(dropped_citations=[], dropped_actions=[]))


def test_every_recorded_guideline_carries_complete_citation_metadata():
    assert guidance.metadata_is_complete() == []
    for guideline in guidance.GUIDELINES:
        assert guidance.CODE_SHAPE.match(guideline.code), guideline.code
        assert guideline.title.strip()
        assert guideline.url == f"https://www.kaypahoito.fi/{guideline.code}"
        assert guideline.citation.startswith(f"{guideline.title} — www.kaypahoito.fi")
        assert guideline.http_status == 200 and guideline.verified_at


def test_only_recorded_guidelines_are_ever_offered():
    """Nothing reaches a professional that is not in the verified map."""
    recorded = {item.code for item in guidance.GUIDELINES}
    for link in guidance.links_for(_picture(_scoped())):
        assert link["code"] in recorded
        assert link["url"] == f"https://www.kaypahoito.fi/{link['code']}"


def test_a_link_is_offered_only_when_a_visible_record_supports_its_topic():
    scoped = _scoped()
    links = guidance.links_for(_picture(scoped))
    permitted = {record.record_id for record in scoped.records}
    assert links, "the fixture carries a hypertension diagnosis, so the topic must match"
    for link in links:
        assert link["evidence_record_ids"], "a link must name the visible record that supports it"
        assert set(link["evidence_record_ids"]) <= permitted
        assert "sisältöä ei ole kopioitu" in link["note"]


def test_a_link_is_not_offered_from_a_withheld_record():
    """A persona whose records do not mention the topic gets no link, and no hint that one exists."""
    for persona_id in ("bound-persona-012-neuvola-lapsi", "bound-persona-013-vammaispalvelut",
                       "bound-persona-004-sosiaalipalvelut"):
        persona = next(item for item in fixtures.load_personas() if item.persona_id == persona_id)
        scoped = policy.cut(persona, "sairaanhoitaja", "medication_follow_up")
        permitted = {record.record_id for record in scoped.records}
        for link in guidance.links_for(_picture(scoped)):
            # Whatever is offered, it must be justified by a record this role can see. A persona with no
            # matching visible record simply gets nothing.
            assert set(link["evidence_record_ids"]) <= permitted


def test_a_link_is_not_offered_from_a_permitted_but_unselected_record():
    """The rail walks the picture's records, not the wider permitted set (`CONTRACT` rule 7)."""
    scoped = _scoped()
    picture = _picture(scoped)
    links = guidance.links_for(picture)
    assert links, "the fixture must attach a guideline, or this test proves nothing"
    # Every record that supports *any* recorded guideline, so nothing is left to justify a link below.
    justified = {record_id for link in guidance.links_for(picture, limit=len(guidance.GUIDELINES))
                 for record_id in link["evidence_record_ids"]}
    assert justified, "the fixture must justify a link with a record, or this test proves nothing"
    # Remove exactly the records that justify a link while leaving the permitted set untouched: if `links_for`
    # walked the permitted set rather than the picture, the link would still be offered.
    kept = [view for view in [*picture.records.priority, *picture.records.accessible_other]
            if view.record_id not in justified]
    narrowed = picture.model_copy(update={"records": Records(priority=kept, accessible_other=[])})
    assert guidance.links_for(narrowed) == []


def test_the_module_stores_no_guideline_content():
    """The permitted use is citation. The module's own text must not carry recommendations."""
    source = (guidance.__file__ and open(guidance.__file__, encoding="utf-8").read()) or ""
    body = source.split('"""', 2)[-1]  # skip the module docstring, which explains the licence position
    forbidden = ("suositellaan", "tulee aloittaa", "annos on", "mg ", "hoitotavoite")
    for phrase in forbidden:
        assert phrase not in body, f"guideline content appears to have been reproduced: {phrase!r}"


def test_topics_with_no_guideline_are_recorded_rather_than_papered_over():
    """An empty mapping is the honest answer where no guideline exists."""
    topics = guidance.topics_without_guidance()
    for expected in ("sosiaalipalvelut", "vammaispalvelut", "rokotus", "toimintakyky"):
        assert expected in topics
    # And none of those words may have been quietly given a guideline by keyword accident.
    for link in guidance.links_for(_picture(_scoped())):
        assert "rokotus" not in link["title"].casefold()


def test_a_citation_names_the_published_version_and_the_publisher():
    for guideline in guidance.GUIDELINES:
        assert guideline.published, guideline.code
        assert guideline.citation.endswith(f"julkaistu {guideline.published})")
        assert "www.kaypahoito.fi" in guideline.citation


def test_family_table_covers_every_known_prefix():
    """Ticket 12: The family table covers every prefix in kaypahoito-nuggets-2026-10-12.md."""
    known_prefixes = {
        "hoi", "gvr", "khp", "khr", "kht", "dnd", "nak", "nix",
        "imk", "khl", "khk", "ook", "ccs", "inf", "pgr",
    }
    assert set(guidance.ARTICLE_FAMILIES.keys()) == known_prefixes
    for prefix, fam in guidance.ARTICLE_FAMILIES.items():
        assert fam.prefix == prefix
        assert fam.label.strip()
        assert isinstance(fam.is_suositus, bool)
        assert isinstance(fam.linkable, bool)
        assert fam.basis.strip()


def test_no_family_marked_unlinkable_appears_in_guidelines():
    """Ticket 12: Only guidelines from linkable families may appear in GUIDELINES."""
    for guideline in guidance.GUIDELINES:
        prefix = guideline.code[:3]
        fam = guidance.ARTICLE_FAMILIES.get(prefix)
        assert fam is not None, f"guideline {guideline.code} has unrecorded family {prefix}"
        assert fam.linkable is True, f"guideline {guideline.code} belongs to un-linkable family {prefix}"


def test_code_shape_accepts_every_recorded_guideline_code():
    """Ticket 12: CODE_SHAPE accepts every recorded guideline code."""
    for guideline in guidance.GUIDELINES:
        assert guidance.CODE_SHAPE.match(guideline.code), f"CODE_SHAPE rejected {guideline.code}"


def test_pgr00035_is_explicitly_not_linkable():
    """Ticket 12: pgr00035 is an Apuohjelmat item, not linkable, outside CODE_SHAPE and marked un-linkable in family table."""
    assert guidance.ARTICLE_FAMILIES["pgr"].linkable is False
    assert guidance.ARTICLE_FAMILIES["pgr"].is_suositus is False
    assert not guidance.CODE_SHAPE.match("pgr00035")
    assert not any(g.code == "pgr00035" for g in guidance.GUIDELINES)
