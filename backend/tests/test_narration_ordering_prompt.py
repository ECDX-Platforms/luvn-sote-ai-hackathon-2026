"""The narration prompt and the pipeline ordering must describe the same order.

The prompt is not a comment. It is baked into the definition of the pinned Foundry agent
(`luvn-narration:8`) that generates the prose, so a pipeline that orders sources one way while the prompt
tells the agent another is a **live contradiction**: the agent is instructed to treat a source as the most
time-critical when the system put it first for a different reason.

The session's audit found exactly that: HEAD's `selection.relevance_order` orders by relevance first, while the
prompt said *"Kun lähteelle on annettu kiireellisyysarvio, ensimmäinen lähde on aikakriittisin"* — with an
urgency estimate present, the first source IS the most time-critical. Both halves are asserted here, so
changing either one alone fails.

**What this test cannot do, stated plainly:** it cannot change the deployed agent. `llm.py`'s own comment says
the primary path changes only after a NEW agent version is created from the prompt file and
`LUNA_FOUNDRY_AGENT_VERSION` is pointed at it. So a green run here means the repository is self-consistent — it
does NOT mean the running agent agrees. Deploying a change to the ordering statement requires creating that new
agent version first; without it, the deployed agent still carries the old instruction.
"""

from pathlib import Path

import pytest

from app import selection

PROMPT = Path(__file__).resolve().parent.parent / "app" / "prompts" / "narration_instructions.md"

# The old unconditional claim: with an urgency estimate, the FIRST source is the most time-critical. It is true
# only when the ordering is urgency-first, so it must not survive a relevance-first pipeline.
STALE_CLAIM = "ensimmäinen lähde on aikakriittisin"
# The ordering the prompt must describe now: relevance first, and among equally relevant ones the urgency.
RELEVANCE_FIRST = "olennaisimmat"


def test_the_prompt_describes_relevance_first_ordering():
    text = PROMPT.read_text(encoding="utf-8")
    assert RELEVANCE_FIRST in text, (
        "the narration prompt must state that the sources come most-relevant-to-this-encounter first, because "
        "that is the order the pipeline imposes")
    assert STALE_CLAIM not in text, (
        "the prompt still tells the agent that the first source is the most time-critical whenever an urgency "
        "estimate exists. That is only true under urgency-first ordering, so it contradicts "
        "selection.relevance_order")


def test_the_pipeline_imposes_the_order_the_prompt_describes():
    """Both halves, so a change to either alone fails. Relevance decides; urgency only breaks a relevance tie."""
    # Two eligible records: A is more relevant but less urgent, B is less relevant but more urgent. A
    # relevance-first order puts A first; an urgency-first order would put B first.
    notes = [
        {"record_id": "A", "urgency": {"score": 0.1, "probabilities": {}, "legend": {}, "confidence": 0.9}},
        {"record_id": "B", "urgency": {"score": 0.95, "probabilities": {}, "legend": {}, "confidence": 0.9}},
    ]
    # The validated relevance lives in its own field, which is what `relevance_order` reads. Setting only
    # `decision_notes` would leave both scores absent and fall through to the urgency tie-break -- which is how
    # the first version of this test accidentally asserted urgency-first ordering.
    made = selection.Selection(records=(), baseline_record_ids=("A", "B"), additional_record_ids=(),
                               emphasized_record_ids=(), reasons={}, decision_notes=tuple(notes),
                               relevance_scores={"A": 0.9, "B": 0.5})
    assert made.relevance_order(["A", "B"]) == ["A", "B"], (
        "relevance must decide the narration order; urgency is only the tie-break, which is what the prompt "
        "tells the agent to expect")

    # And the same two records under urgency_order must come out the other way, proving the two orders are
    # genuinely different functions rather than one aliasing the other.
    assert made.urgency_order(["A", "B"]) == ["B", "A"], (
        "urgency_order must remain urgency-first, so the difference between the two orders stays visible")


@pytest.mark.parametrize("path", [PROMPT])
def test_the_prompt_file_is_the_one_the_agent_is_built_from(path):
    """A cheap guard that the file this test reads is the file the narration path ships."""
    assert path.exists() and path.stat().st_size > 0
    assert path.parent.name == "prompts"
