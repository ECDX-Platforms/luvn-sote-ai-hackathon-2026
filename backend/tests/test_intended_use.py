"""The demo's declared purpose of use: served from one module, and honest about its limits.

`Picture.intended_use` is the only place the demo states what it is for. Two failure modes
are worth a test: the document and the served sentence drifting apart, and the sentence
quietly growing a legal claim it is not allowed to make (ADR-0001 amendment, 2026-10-09).
The sentence may name a status only to **deny** it.
"""

import asyncio
import re
from pathlib import Path

from app.intended_use import INTENDED_USE, INTENDED_USE_VERSION
from app.picture import Picture
from app.pipeline import build_picture_for_encounter
from tests.fixtures.bound_encounters import ENCOUNTERS

ROOT = Path(__file__).resolve().parents[2]
POSITION = ROOT / "docs" / "regulatory-position.md"

# A legal or regulatory claim would arrive as one of these. The field is a product statement.
CLAIM_PATTERNS = (
    r"\b\d{1,4}/\d{4}\b",                                    # a statute number, e.g. 703/2023
    r"\b(GDPR|AI[ -]asetus|MDR|IVDR|EHDS)\b",                # a named instrument
    r"\bliite\s*(?:I|II|III|[123])\b|\bannex\b",             # an annex reference
    r"\bartikla\b|\bart\.\s*\d|\barticles?\s+\d",            # an article reference
    r"\bvaatimustenmukaisuus\b|\bconformity\b|\bCE-merkintä\b",
)

# A status word may appear only inside a denial, e.g. "Ei ole kliininen päätös eikä lääkinnällinen laite."
STATUS_WORDS = (
    r"lääkinnällinen laite", r"medical device", r"suuririskinen", r"high-risk",
    r"korkean riskin", r"diagnostiikka", r"diagnosis", r"CE-merkintä",
)
NEGATION = r"(ei|eikä|ei ole|ei ole myöskään|ei kuulu|not|nor|no)\b"


def _normalise(text: str) -> str:
    """Collapse whitespace, and drop markdown quoting: line wrapping and a `>` are not drift, a word is."""
    lines = (line.lstrip("> ").strip() for line in text.splitlines())
    return " ".join(" ".join(lines).split())


def _declaration_blockquote() -> str:
    """The declared-purpose blockquote only.

    The document quotes decks and guidance too, and its `### 1.1` section carries a second blockquote
    (the deck's device-test sentence). The declaration is the **first contiguous blockquote** after the
    heading, so that is what is read here — a blockquote added elsewhere can neither satisfy this nor
    break it.
    """
    document = POSITION.read_text(encoding="utf-8")
    section = document.split("### 1.1", 1)
    assert len(section) == 2, "the declared purpose of use lost its own section in the document"
    block, inside = [], False
    for line in section[1].splitlines():
        quoted = line.lstrip().startswith(">")
        if quoted:
            block.append(line.lstrip()[1:].strip())
            inside = True
        elif inside:
            break
    assert block, "the declared-purpose section carries no blockquote"
    return " ".join(" ".join(block).split())


def test_the_served_sentence_is_the_documented_sentence():
    """Drift is the whole risk of a hand-written position and a code constant in two files."""
    assert _normalise(INTENDED_USE) in _declaration_blockquote(), (
        "docs/regulatory-position.md must carry the served sentence verbatim; regenerate one from "
        "the other rather than paraphrasing either"
    )


def test_the_documented_declaration_is_exactly_the_served_one():
    """The blockquote *is* the declaration, not a sentence inside a longer one.

    Without this, the document could ship a longer declaration that happens to contain the served
    sentence, and a reader would take the longer one as the system's declared purpose — real drift
    that a containment check cannot see.
    """
    quoted = _declaration_blockquote()
    served = _normalise(INTENDED_USE)
    assert quoted.startswith(served), "the documented declaration must begin with the served sentence"
    trailing = quoted[len(served):].strip()
    assert trailing == "", (
        f"the documented declaration carries text the wire does not: {trailing[:80]!r}"
    )


def test_the_served_sentence_makes_no_legal_claim():
    for pattern in CLAIM_PATTERNS:
        assert re.search(pattern, INTENDED_USE, re.IGNORECASE) is None, (
            f"the declared purpose of use must not carry a legal claim (matched {pattern!r}); "
            "classification belongs in docs/regulatory-position.md, not on the wire"
        )


def _denial_before(text: str, index: int) -> re.Match | None:
    """A negation word in the 80 characters before a status mention is what makes it a denial."""
    return re.search(NEGATION, text[max(0, index - 80):index], re.IGNORECASE)


def test_every_named_status_is_denied_not_asserted():
    """Naming a status to deny it is allowed; naming it as a claim is not."""
    non_vacuous = "Ei ole kliininen päätös eikä lääkinnällinen laite."
    assert _denial_before(non_vacuous, non_vacuous.index("lääkinnällinen laite")), (
        "the denial probe would not match a denial"
    )

    for mention in re.finditer(rf"\b(?:{'|'.join(STATUS_WORDS)})\b", INTENDED_USE, re.IGNORECASE):
        assert _denial_before(INTENDED_USE, mention.start()), (
            f"the declared purpose of use names {mention.group(0)!r} without denying it; a status "
            "assertion belongs in docs/regulatory-position.md, not on the wire"
        )


def test_the_declared_purpose_is_versioned_and_not_narration():
    """The version names the declaration a payload was built under — and it must reach the payload.

    The version was once a constant nothing carried, while the module docstring claimed the wire
    carried it. A consumer could then not tell a v1 declaration from a future v2, which is the whole
    reason `AI-Generated-Marking` travels with the marking. This asserts the field exists *and* that
    the built payload carries it, so the claim cannot drift from the wire again.
    """
    assert INTENDED_USE_VERSION == "luvn-intended-use-v1"
    # The field is deliberately not `narration_*`: `test_contract.py` requires that prefix to equal the
    # exact set of narration provenance fields, so a name collision would fail there.
    assert {"intended_use", "intended_use_version"} <= set(Picture.model_fields)
    assert not "intended_use_version".startswith("narration_")


def test_the_built_picture_carries_the_declared_purpose():
    picture = asyncio.run(build_picture_for_encounter("bound-encounter-002-laakitys-sairaanhoitaja", encounters=ENCOUNTERS))
    assert picture.intended_use == INTENDED_USE
    assert picture.intended_use_version == INTENDED_USE_VERSION, (
        "the served payload must name which declaration it carries, not just the sentence"
    )


def test_the_served_sentence_states_that_advice_is_from_named_sources_marked_and_not_a_decision():
    """The round R wording: advice exists, it names its sources, it is machine-drafted and marked, and the sentence
    denies the decision authority rather than claiming it."""
    assert "nimettyihin tietolähteisiin" in INTENDED_USE
    assert "merkitty niiksi" in INTENDED_USE and "koneellisesti" in INTENDED_USE
    assert "ei tee kliinisiä päätöksiä" in INTENDED_USE
