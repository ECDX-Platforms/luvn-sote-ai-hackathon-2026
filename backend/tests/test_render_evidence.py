import re
from pathlib import Path

from app.decision_model import COVERAGE_BUCKETS
from scripts.render_evidence import _load, _sentence_order_metrics, artifacts, render

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_KINDS = {"decision-value-eval", "scrubbed-live-encounter-trial-summary", "decision-value-eval-instability",
                  "decision-floor-ablation", "decision-structured-fact-disagreement", "content-safety-probe"}


def test_architecture_diagram_names_every_coverage_bucket_from_code():
    document = (ROOT / "docs" / "architecture-as-built.md").read_text(encoding="utf-8")
    match = re.search(r"```mermaid\s+(.*?)```", document, re.DOTALL)
    assert match is not None, "the built-path diagram is missing"
    diagram = match.group(1)
    assert "M -->|valid membership| V" in diagram
    assert "per-row labels for disclosed sources" in diagram
    for bucket in COVERAGE_BUCKETS:
        assert bucket in diagram, f"COVERAGE_BUCKETS added {bucket!r}; update the Mermaid diagram"


def test_manifest_indexes_every_cited_artifact_by_its_declared_kind():
    """The manifest is the only place a filename lives, so it must not drift from the artifacts."""
    paths = artifacts()
    assert set(paths) >= REQUIRED_KINDS
    for kind, path in paths.items():
        assert path.is_file(), f"{path} is listed in the manifest but missing"
        assert _load(path).get("kind") == kind, f"{path} does not declare kind {kind!r}"


def test_sentence1_primary_match_is_not_any_priority_source_leading():
    trial = {"per_encounter": {
        "primary-not-first": {"decision-on-foundry-on": {
            "generation_path": "agent_reference", "priority_record_ids": ["note-a", "diagnosis-a"],
            "sentence_citations": [
                {"sentence_id": "s1", "source_ids": ["diagnosis-a"]},
                {"sentence_id": "s2", "source_ids": ["note-a"]},
            ],
        }},
        "primary-is-first": {"decision-on-foundry-on": {
            "generation_path": "agent_reference", "priority_record_ids": ["note-b"],
            "sentence_citations": [{"sentence_id": "s1", "source_ids": ["note-b"]}],
        }},
        "direct-fallback": {"decision-on-foundry-on": {
            "generation_path": "direct_chat_completions", "priority_record_ids": ["note-c"],
            "sentence_citations": [{"sentence_id": "s1", "source_ids": ["note-c"]}],
        }},
    }}
    metrics = _sentence_order_metrics(trial)
    assert metrics["eligible_encounters"] == 2
    assert metrics["primary_id_matches"] == 1
    assert metrics["any_priority_leads"] == 2


def test_evidence_uses_attempted_omission_denominator_and_aggregate_coverage():
    report = render()
    # The attempt denominator is the recorded run's, not the previous model's: Decision-1 attempted 34 of
    # the 35 baseline candidates, and none was omitted against the answer key.
    assert "unexpected omissions **0/34** attempted baseline candidates" in report
    # The universe is the fixture's, not the previous model's: 35 baseline candidates, 34 attempted.
    assert "wider selection-layer baseline universe is **35 candidates**" in report
    # Decision-1 assessed 63 of 64 candidates with 0 budget-exhausted and 2 gate refusals. Fewer refusals
    # than the previous model is the verifier/selection difference ADR-0018 records, not a missing gate.
    assert "64 candidates / 63 assessed / 0 budget-exhausted / 2 gate-refused" in report
    # The stage takes longer than the unbounded fan-out did (1154 ms against 409 ms) because the fan-out is
    # now bounded to stay under the service rate limit; it returns every attempted candidate instead of
    # losing most of them to 429s, which is the trade this number records.
    assert ("Decision-1 stage **1154 ms**; 41/42 candidates were attempted, 41 returned usable answers"
            in report)
    assert "statuses irrelevant 21, relevant 1, too_long 1, unclear 19" in report
    assert "observed no unavailable/timeouts or invalid responses (no throttling signal)" in report
    assert "gate-refused is included among assessed, not a disjoint bucket" in report
