from scripts.run_live_encounter_trial import _scrubbed_summary


def test_trial_records_actual_agent_and_fallback_paths_for_same_encounter():
    def mode(path, *, accepted=True):
        return {
            "wall_ms": 1,
            "narration_accepted": accepted,
            "narration_generation_model": "same-model",
            "narration_generation_path": path,
            "configured_agent_version": "8" if path else None,
            "sentences_proposed": 1 if path else 0,
            "sentences_accepted": 1 if path and accepted else 0,
            "sentences_dropped": 0,
            "sentence_drop_reasons": [],
            "priority_record_ids": ["record-a"],
            "sentence_citations": [{"sentence_id": "s1", "source_ids": ["record-a"]}] if path else [],
            "stages": [],
        }

    report = {
        "today": "2026-10-08",
        "deployment": "same-model",
        "preparation_window_s": 90,
        "effective_budgets_s": {"joint": 80, "generation": 70, "agent": 45},
        "results": {"encounter-001": {
            "decision-on-foundry-on": mode("agent_reference"),
            "decision-off": mode("direct_fallback"),
            "foundry-off": mode(None, accepted=False),
        }},
        "verifier": {"per_encounter": {"encounter-001": {
            "sentences_proposed": 1, "sentences_accepted": 1, "sentences_dropped": 0,
            "decision_sentences_checked": 0, "decision_supported": 0, "decision_unknown": 0,
            "sentence_drop_reasons": [],
        }}},
    }

    summary = _scrubbed_summary(report)
    modes = summary["per_encounter"]["encounter-001"]
    assert modes["decision-on-foundry-on"]["generation_path"] == "agent_reference"
    assert modes["decision-off"]["generation_path"] == "direct_fallback"
    assert modes["decision-off"]["generation_source"] == "direct_fallback"
    assert modes["decision-on-foundry-on"]["versioned_agent_result"] is True
    assert modes["decision-off"]["versioned_agent_result"] is False
    assert modes["decision-on-foundry-on"]["configured_agent_version"] == "8"
    assert modes["decision-off"]["configured_agent_version"] == "8"
    assert summary["generation_path_counts"] == {
        "agent_reference": 1, "direct_fallback": 1, "direct_chat_completions": 0,
    }
    assert "agent_version" not in summary
