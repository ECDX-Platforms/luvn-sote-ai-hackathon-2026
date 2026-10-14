# ADR-0008 — Challenge 1.1: purpose-aware pre-visit briefing

Status: accepted architecture direction by the owner. Purpose relevance is read by Decision-1, recorded in [ADR-0009](0009-decision-purpose-relevance-selection.md). Hosted clinical-use approval is not granted.

## Decision and scope

LUVN assembles a ready pre-visit Tilannekuva from modelled systems and forms for a professional's **Rooli and Palvelukonteksti** (current service/encounter purpose). The primary outcome is finding the right information without manual searching, not clinical triage. Information must be both permitted for that purpose and relevant to that encounter; permission alone is not relevance. The encounter supplies context automatically in the modelled workflow.

The owner supplied the Challenge 1.1 brief and explicitly accepted this direction.

## Target workflow

The target is automatically supplied medication, mental-health and functioning/service-needs contexts; additive per-note Decision-1 relevance selection of eligible free text; existing structured assessments and source-linked professional-review proposals in the main briefing.

```text
Modelled sources + role + encounter/service context
  → deterministic permission/purpose cut
  → current/history interpretation of eligible records
  → additive Decision-1 per-note relevance selection of eligible free text
  → explicit role/service relevance selection rules
  → Foundry narration of selected source records
  → citation/action validation
  → ready professional briefing with sources, dates and selection provenance
```

Per-note Decision-1 relevance is a functional pre-generation input to information selection/emphasis, not merely a displayed second opinion. AI must never authorize records or invent patient facts. Its relevance read is not a validated clinical model. The local implementation has a bounded mapping and record-level input/coverage contract; the frozen stubbed ID matrix and fail-closed/invariance tests cover that selection contract. A real hosted usefulness gate is still blocked; the synthetic evidence, its limits and the independent review it needs are recorded in [ADR-0009](0009-decision-purpose-relevance-selection.md). Essential facts are protected by explicit rules; abstain, invalid output or unavailable service uses deterministic selection. Historical relevance does not make a historical symptom current. Foundry narrates, not authorizes or clinically decides; deterministic source-membership checks and the per-sentence Decision-1 grounding verifier do not establish universal semantic truth. The Decision-1 verifier and its abstention contract are recorded in ADR-0009.

## Boundaries

- Model source connections only: no real integrations, appointment recording/dictation or Challenge 1.2 contact/interaction-history aggregation.
- Include relevant existing PHQ-9/IADL results and professional-controlled proposed next steps in the ready briefing. Preserve source/version/date/completion; never synthesize missing answers. RAI may be presented as a supplied source result, not a new full scoring engine or certified mock instrument. Universal risk scoring and expanded clinical action protocols are not required.
- Show health and social-care use, and service-context differences within the same role. The default briefing must omit allowed-but-irrelevant information, not merely move all of it into another default tier.
- Synthetic-only hosted demo; retain access/secret protection, silent absence, source provenance and structured fallback. No clinical validation, calibration, production-readiness or real-world time-saving claims from synthetic task results.

## Relationship to existing architecture

`docs/architecture-as-built.md` records the implementation: immutable encounter resolution, role/purpose permission cut, deterministic baseline, additive per-note Decision-1 relevance selection, decision-independent assessments/proposals, selected-set Foundry narration with a per-sentence Decision-1 grounding verifier, and the typed runtime UI. The app serves the encounter routes only; no role-only route remains. Clinical situation/risk/action features remain advisory-free. Selection influence has its own synthetic-demo acceptance gates, which remain blocked on representative live usefulness and required review. No clinical classifier feeds the decision tables.
