import { Block } from '../vendor/comp-lib/src/index.ts';
import { BUILD_REVISION, decisionStatus, generationStatus, picturePresentation } from './challenge-demo.mjs';
import { AIProvenance, EncounterContext, ImpactEstimate, PresenterTools, SelectionCoverage } from './ChallengeDemo.jsx';

// Tekniset tiedot: a destination of its own (`?page=technical`), not a disclosure at the foot of the briefing.
// Everything here is about how the summary was produced rather than about the person: the coverage and impact
// cards, the presenter's tools (the mode switch, the three-way recomputation, the sibling-lens comparison),
// the Decision-1 trace, the intended-use text and the build stamp. It renders from the same Picture the
// briefing holds and requests nothing of its own, so it can never show a record the briefing did not.
//
// The shell's Header owns the page's h1; this page is one library Block under it, so its own title is an h2
// and every section inside keeps the heading level and the id it had in the briefing (`section-coverage`,
// `section-impact`, `presenter-tools`), which is what keeps the deep links and the attention panel's
// "Siirry kattavuusriveihin" anchor working across the move.
export function TechnicalView({ picture, mode = 'normal', onModeChange, personName, roleLabel, purposeLabel, exampleLabel,
  identityAvailable, failureComparison, comparisonLoading, comparisonError, onRunFailureComparison, lensSiblings = [] }) {
  const presentation = picturePresentation(picture, { personName, roleLabel, purposeLabel, exampleLabel, identityAvailable });
  const { selection, narration } = presentation;
  return <Block title="Tekniset tiedot" headingLevel={2} className="brief-technical-page">
    {/* The same context strip the briefing opens with, so the page says whose production it describes. */}
    <EncounterContext presentation={presentation} />
    <SelectionCoverage presentation={presentation} />
    <ImpactEstimate sources={presentation.sources ?? []} />
    <PresenterTools mode={mode} onModeChange={onModeChange} presentation={presentation}
      comparison={failureComparison} comparisonLoading={comparisonLoading} comparisonError={comparisonError}
      onRunFailureComparison={onRunFailureComparison} lensSiblings={lensSiblings} />
    <p className="t-caption brief-technical-status"><strong>Kerronnan reitti:</strong> Valinta: {selection?.rule_version ?? 'ei tietoa'}
      {' · '}Decision-1 {decisionStatus(selection?.decision_status)} · Foundry {generationStatus(narration.generationStatus)}.</p>
    <AIProvenance presentation={presentation} />
    {presentation.intendedUse && <p className="t-caption">{presentation.intendedUse}</p>}
    {BUILD_REVISION && <p className="t-caption" data-build={BUILD_REVISION}>Käytössä oleva koonti: {BUILD_REVISION} (tämän sivun koonti, ei palvelimen versio)</p>}
  </Block>;
}
