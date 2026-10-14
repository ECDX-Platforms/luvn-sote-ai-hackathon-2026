// Worklist gate: Rooli is a first-class axis, and nothing in the worklist is authored.
//
// Every rule here is a rule that can fail. The fixture is the real product catalogue transcribed from
// `backend/app/encounters.py` / `main.EXAMPLE_LABELS`, so the role scoping is checked against the
// real encounter set.
import assert from 'node:assert/strict';
import {
  activeWorklistRole, attentionChips, byLabel, isProfessionalRole, roleLabelOf, roleRoster, worklistFor,
} from './worklist.mjs';

// The six walkthrough encounters the hosted product binds, plus the value fixture, exactly as
// `encounters.ENCOUNTERS` and `main.ENCOUNTER_ORDER`/`EXAMPLE_LABELS` define them.
const CATALOGUE = [
  { encounter_id: 'bound-encounter-002-laakitys-sairaanhoitaja', role: 'sairaanhoitaja', role_label: 'Sairaanhoitaja', purpose: 'medication_follow_up', purpose_label: 'Lääkityksen seuranta', example_label: 'Aino Virtanen', picker_label: 'Lääkityksen seuranta · erillinen tapaus (Aino Virtanen, sairaanhoitaja)' },
  { encounter_id: 'bound-encounter-003-mielenterveys-sairaanhoitaja', role: 'sairaanhoitaja', role_label: 'Psykiatrinen sairaanhoitaja', purpose: 'mental_health_assessment', purpose_label: 'Mielialan arviointi', example_label: 'Elias Korhonen', picker_label: 'Mielialan arvio · vertailun lähtökohta (Elias Korhonen, sairaanhoitaja)' },
  { encounter_id: 'bound-encounter-003-toimintakyky-sairaanhoitaja', role: 'sairaanhoitaja', role_label: 'Sairaanhoitaja', purpose: 'functioning_service_needs', purpose_label: 'Palvelutarpeen arviointi', example_label: 'Elias Korhonen', picker_label: 'Toimintakyvyn arvio · sama henkilö, eri tilanne (003, sairaanhoitaja)' },
  { encounter_id: 'bound-encounter-003-mielenterveys-laakari', role: 'laakari', role_label: 'Lääkäri', purpose: 'mental_health_assessment', purpose_label: 'Mielialan arviointi', example_label: 'Elias Korhonen', picker_label: 'Mielialan arvio · sama henkilö ja tilanne, eri ammattilainen (003, lääkäri)' },
  { encounter_id: 'bound-encounter-004-sosiaalipalvelut-sosiaalityontekija', role: 'sosiaalityontekija', role_label: 'Sosiaalityöntekijä', purpose: 'functioning_service_needs', purpose_label: 'Palvelutarpeen arviointi', example_label: 'Sanni Lehtinen', picker_label: 'Toimintakyvyn arvio · sama tilanne, eri henkilö ja ammattilainen (004; ei puhdas roolivertailu)' },
  { encounter_id: 'bound-encounter-005-laakitys-sairaanhoitaja', role: 'sairaanhoitaja', role_label: 'Sairaanhoitaja', purpose: 'medication_follow_up', purpose_label: 'Lääkityksen seuranta', example_label: 'Taisto Mäkinen', picker_label: 'Lääkityksen seuranta · erillinen tapaus (Taisto Mäkinen, sairaanhoitaja)' },
  { encounter_id: 'bound-encounter-012-neuvola-terveydenhoitaja', role: 'terveydenhoitaja', role_label: 'Terveydenhoitaja', purpose: 'child_health_checkup', purpose_label: 'Neuvolan terveystarkastus', example_label: 'Onni Laakso', picker_label: 'Neuvolan terveystarkastus · huoltajan kanssa (Onni Laakso, terveydenhoitaja)' },
];

const PROFESSIONAL_ROLES = ['sairaanhoitaja', 'laakari', 'sosiaalityontekija', 'terveydenhoitaja'];

// ---------- the roster is derived, not asserted into existence ----------

const roster = roleRoster(CATALOGUE);
assert.deepEqual(roster.map(entry => entry.role), PROFESSIONAL_ROLES,
  'the roster carries the catalogue roles once each, in catalogue order');
assert.equal(roster.find(entry => entry.role === 'sairaanhoitaja').label, 'Sairaanhoitaja');
assert.equal(roster.length, PROFESSIONAL_ROLES.length,
  'four roles are offered for seven encounters: the axis is the role, not the row');

// Non-vacuity: the roster is a filter over the input, so an input with no rows offers no role. If this
// returned a hardcoded list, an empty catalogue would still offer lenses the API never returned.
assert.deepEqual(roleRoster([]), [], 'no returned rows means no offered role');
assert(!isProfessionalRole('laakari', []), 'a role absent from the catalogue is not an admissible lens');
assert(isProfessionalRole('laakari', CATALOGUE), 'a role present in the catalogue is admissible');

// `potilas` is a policy comparison key with no product surface (concept boundary register:
// "Citizen/end-user view | REJECTED for this product").
assert(!isProfessionalRole('potilas', [...CATALOGUE, { encounter_id: 'self', role: 'potilas', role_label: 'Potilas' }]),
  'the citizen/self policy key is never offered as a professional lens');

// ---------- a role's worklist reveals only that role's encounters ----------

const nurseWorklist = worklistFor(CATALOGUE, 'sairaanhoitaja');
assert.equal(nurseWorklist.length, 4, 'the nurse worklist carries the four nurse encounters');
assert(nurseWorklist.every(item => item.role === 'sairaanhoitaja'), 'every offered row is that role\'s');
assert(!nurseWorklist.some(item => item.encounterId === 'bound-encounter-003-mielenterveys-laakari'
  || item.encounterId === 'bound-encounter-004-sosiaalipalvelut-sosiaalityontekija'
  || item.encounterId === 'bound-encounter-012-neuvola-terveydenhoitaja'),
'no other role\'s encounter appears in the nurse worklist — not as a row, not as a count');

const doctorWorklist = worklistFor(CATALOGUE, 'laakari');
assert.deepEqual(doctorWorklist.map(item => item.encounterId), ['bound-encounter-003-mielenterveys-laakari'],
  'the doctor worklist is exactly the one doctor encounter for the same person and purpose');
assert.deepEqual(worklistFor(CATALOGUE, 'terveydenhoitaja').map(item => item.purpose), ['child_health_checkup']);
assert.equal(worklistFor(CATALOGUE, 'elakevirkailija').length, 0,
  'an unknown role yields an empty worklist, never the whole catalogue');
assert.equal(nurseWorklist.length + doctorWorklist.length
  + worklistFor(CATALOGUE, 'sosiaalityontekija').length + worklistFor(CATALOGUE, 'terveydenhoitaja').length,
CATALOGUE.length, 'the role worklists partition the catalogue: no row is lost or duplicated');

// The role and purpose are two explicit axes; grouping must not collapse a same-person pair, because a
// person legitimately has several encounters under one role. The order is by label.
const nurseByPurpose = nurseWorklist.map(item => item.purpose).sort();
assert.deepEqual(nurseByPurpose, ['functioning_service_needs', 'medication_follow_up', 'medication_follow_up',
  'mental_health_assessment'],
'one person can hold two nurse encounters with different purposes for the same role');

// ---------- the lens is chosen, and an unknown choice does not widen it ----------

assert.equal(activeWorklistRole(CATALOGUE, 'laakari'), 'laakari', 'a chosen role is the active lens');
assert.equal(activeWorklistRole(CATALOGUE, ''), 'sairaanhoitaja',
  'with no choice the first catalogue role is the lens, so the default view is a real role and not "all"');
assert.equal(activeWorklistRole(CATALOGUE, 'potilas'), 'sairaanhoitaja',
  'a rejected lens falls back to a professional role instead of a self view');
assert.equal(activeWorklistRole([], 'laakari'), '', 'an empty catalogue has no lens');
assert.equal(roleLabelOf('laakari', CATALOGUE), 'Lääkäri', 'the label comes from the returned row');

// ---------- no authored schedule (plan item E deleted it) ----------

const nurseRows = worklistFor(CATALOGUE, 'sairaanhoitaja');
assert(nurseRows.every(item => !('time' in item) && !('synthetic' in item)),
  'a worklist row carries no clock mark: the authored slot table is gone');
assert.deepEqual(nurseRows.map(item => item.label), [...nurseRows.map(item => item.label)].sort((x, y) => x.localeCompare(y, 'fi')),
  'the worklist is ordered by label, deterministically');
assert(byLabel({ row: { picker_label: 'A' } }, { row: { picker_label: 'B' } }) < 0, 'byLabel orders by label');

// ---------- D3: Decision-1's time-sensitivity chip ----------
//
// `current_by_model` is the briefing's count of rendered rows Decision-1 called `current`; `null` means
// Decision-1 did not answer. Only a positive integer renders, as an info tag, so the chip is never a zero that
// means "not asked" and never a count the row's own briefing does not carry.
const currentChip = chips => chips.find(chip => chip.key === 'current');
assert.deepEqual(currentChip(attentionChips({ current_by_model: 3 })),
  { key: 'current', text: 'Ajankohtaisia kirjauksia: 3', status: 'info' }, 'a positive count renders as an info chip');
assert.equal(currentChip(attentionChips({ current_by_model: 0 })), undefined, 'a zero count renders no chip');
assert.equal(currentChip(attentionChips({ current_by_model: null })), undefined, 'null (Decision-1 did not run) renders no chip');
assert.equal(currentChip(attentionChips({ conflicts: 1 })), undefined, 'an absent field renders no chip');
assert.equal(currentChip(attentionChips({ current_by_model: '2' })), undefined, 'only an integer counts');

console.log('Worklist role-separation, and ordering checks passed.');
