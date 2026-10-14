// The worklist: Rooli as a first-class axis over the encounter catalogue, with no authored values.
//
// WHY THIS EXISTS. `/api/encounters` returns a *page* of the operator catalogue plus the whole-catalogue
// counts (`total`, `aggregate.catalogue`, `aggregate.roster`, `aggregate.matching`). At the delivery
// scale the owner named ("50000 will become problem, it should be separated more for profession and
// customer") the catalogue itself stays small — an import refuses more than 128 operator encounters, so
// this endpoint can never walk the 50k profiles — but the page is what the browser holds, and the
// worklist must scope and count without ever downloading the catalogue to do it. Nothing in the catalogue
// decides permission by being listed: every row is a *navigation* entry that already passed
// `resolve_encounter`, and opening one runs `policy.cut` exactly as before. This module only decides what
// the professional sees *before* choosing, so it must obey two rules:
//
//   1. A worklist is scoped to one Rooli. The rows offered under a role are exactly the returned rows
//      whose `role` equals that role, so a role's worklist can never reveal an encounter the API did not
//      return for it. `worklistFor` filters; it never adds.
//   2. Only the two explicit axes the wire actually carries — `role` and `purpose` — group the list.
//      There is no schedule data anywhere in this repository: `data/experimental/bound-encounter-map.json`
//      carries only `version`, `provenance` and `persona_mappings`, and the delivery sample's
//      `encounters.csv` has `encounter_id, source_person_id, role, purpose, example_label, demo_name`.
//      No date, no time, no slot. The authored slot table that once filled that gap (and the Aikataulu
//      view it fed) was deleted: it keyed on names the served corpus does not use, so it matched no one.
//
// The role `potilas` lives in `tables/visibility.json` only so `test_decide.py` can prove a self-scope
// excludes high-sensitivity records. It is not on the professional roster and the citizen/end-user view
// is a recorded REJECTION in the concept boundary register, so this module never offers it: the roster
// is derived from the returned rows, which no encounter binds to `potilas`.

/** `potilas` is a policy comparison key, never a professional lens. */
const NON_PROFESSIONAL_ROLES = new Set(['potilas']);

const worklistLabel = row => row.picker_label ?? row.example_label ?? row.encounter_id;

/** Rows sort by their label, deterministically; there is no schedule data to order them by. */
export function byLabel(a, b) {
  return worklistLabel(a.row ?? a).localeCompare(worklistLabel(b.row ?? b), 'fi');
}

/**
 * The professional roles the returned catalogue actually carries, in catalogue order.
 * Derived rather than hardcoded: a role the API did not return cannot be offered, so the roster can
 * neither invent a profession nor drop one the corpus added.
 */
export function roleRoster(encounters = []) {
  const roster = [];
  for (const row of encounters) {
    if (!row?.role || NON_PROFESSIONAL_ROLES.has(row.role)) continue;
    if (roster.some(entry => entry.role === row.role)) continue;
    roster.push({ role: row.role, label: row.role_label ?? row.role });
  }
  return roster;
}

export function isProfessionalRole(role, encounters = []) {
  return roleRoster(encounters).some(entry => entry.role === role);
}

/**
 * The one lens the workspace is looking through: a role the professional chose, else the role of the
 * already-addressed encounter, else the first role the catalogue offers. An unknown role is treated
 * as no choice rather than as a widened view, so the roster is the only source of a valid axis.
 */
export function activeWorklistRole(encounters = [], requested = '') {
  return activeRoleFromRoster(roleRoster(encounters), requested);
}

/**
 * The same choice made from the server's whole-catalogue `aggregate.roster` rather than from whatever page
 * of rows this browser holds: the selector and the lens are then complete even when the page is not. An
 * unknown role still falls back to the first offered role rather than widening the view.
 */
export function activeRoleFromRoster(roster = [], requested = '') {
  if (requested && roster.some(entry => entry.role === requested)) return requested;
  return roster[0]?.role ?? '';
}

/** The last url-safe selection wins; anything the API did not return resolves to '' (no lens). */

export function roleLabelOf(role, encounters = []) {
  return roleRoster(encounters).find(entry => entry.role === role)?.label ?? role ?? '';
}

export function roleLabelFromRoster(roster = [], role = '') {
  return roster.find(entry => entry.role === role)?.label ?? role ?? '';
}

/**
 * One Rooli's worklist: its own rows only, each carrying the source purpose and its
 * label. Rows the API returned for another role are absent, not hidden.
 */
export function worklistFor(encounters = [], role) {
  return encounters
    .filter(row => row?.role === role)
    .map(row => ({
        encounterId: row.encounter_id,
        label: worklistLabel(row),
        person: row.example_label ?? '',
        role: row.role,
        roleLabel: row.role_label ?? row.role,
        purpose: row.purpose,
        purposeLabel: row.purpose_label ?? row.purpose,
      row,
    }))
    .sort(byLabel);
}

const RISK_WORD = { korkea: "Riski: korkea", kohtalainen: "Riski: kohonnut" };
const RISK_STATUS = { korkea: "critical", kohtalainen: "warning" };

// The chips a row carries, each a fact of the person's own cached briefing. A chip renders only for a count
// above zero or a level with a word, so an absent chip says nothing.
export function attentionChips(attention) {
  if (!attention) return [];
  const chips = [];
  if (attention.conflicts > 0) chips.push({ key: "conflict", text: "Lähteiden ristiriita", status: "warning" });
  if (RISK_WORD[attention.risk_level]) chips.push({ key: "risk", text: RISK_WORD[attention.risk_level], status: RISK_STATUS[attention.risk_level] });
  if (attention.active_medications > 0) chips.push({ key: "medications", text: `${attention.active_medications} lääkettä käytössä` });
  if (attention.prefill && attention.prefill.unknown > 0) chips.push({ key: "prefill", text: `Esitiedot: ${attention.prefill.unknown} kysymystä avoinna` });
  // Decision-1's time-sensitivity over the rows the person's own briefing renders. `null` means Decision-1 did
  // not answer, which is not a zero, so only a positive integer renders; a count over withheld records never
  // exists on the wire to begin with.
  if (Number.isInteger(attention.current_by_model) && attention.current_by_model > 0) {
    chips.push({ key: "current", text: `Ajankohtaisia kirjauksia: ${attention.current_by_model}`, status: "info" });
  }
  return chips;
}
