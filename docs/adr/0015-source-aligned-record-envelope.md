# ADR-0015 — Source-aligned record envelope: national headings, code systems and legal basis

Status: **accepted; heading pairs and optional provenance projection implemented after the judged event (2026-10-07); source values and legal basis remain DATA-GATED.** Derived from the vendor's Lifecare training corpus (scan 2026-10-07). Illustrative synthetic modelling; the cited statutes are the **vendor's** citations, not our legal position.

## Owner decision, 2026-10-07 — `legal_basis`: ship none

- **The field is kept; no value ships.** `legal_basis` stays in the design and is left **empty** until
  the owner approves one specific basis. The vendor's citations (`703/2023 34 §`, `159/2007`, and the
  HTA timing reference `1326/2010 51 §`) are the **vendor's claim about its own product**, not our legal
  position, and not a basis we may assert.
- **An absent basis beats an unapproved basis.** An empty field is the *correct* state here, not an
  incomplete one. Putting a plausible-looking statute next to a rule in a safety-adjacent document is
  worse than putting nothing, because it borrows authority we have not been given.
- The original deferral past the judged event was honored. Heading pairs and optional entry-provenance paths are now implemented; issue 05's form-prefill/required-field work remains deferred for its existing evidence and owner-decision reasons.

## Context

The original decision identified three missing properties. This follow-up adds the optional heading pair,
code-system intent and provenance paths, but does not author source values or claim a national mapping.

**Records are structured under named national headings, and the professional owns the order.**

> "Kirjausalusta on osa Lifecaren Kertomus-kokonaisuutta, joka koostuu **openEHR-tietomallia**
> hyödyntävistä sovelluksista kuten Kertomuskatselin, Hoidonseuranta ja Kasvunseuranta."

Headings observed: `Nykytila`, `Esitiedot`, `Diagnoosi`, `Lääkehoito`, `Fysiologiset mittaukset`,
`Toimenpiteet`, `Testaus- ja arviointitulokset`, `Hoitotoimet`, `Konsultaatio/Lähete`,
`Ennaltaehkäisy (preventio)`, `Suunnitelma`, `Tulosyy`. The HTA source also maps local headings per
organisation (`Yhteydenotto` → `Etäkontakti`, `Hoidon tarpeen arviointi` → `Suunnitelma`). The
professional reorders headings ("Siirrä Fysiologiset mittaukset -otsikko kirjauksen ylimmäiseksi"); our
`topic` remains a separate coarse decision-table axis.

**Rules cite their statutory basis in the product.** The vendor names
`laki sosiaali- ja terveydenhuollon asiakastietojen sähköisestä käsittelystä (159/2007)` for the
form/plan products and `703/2023 34 §` for the minor-capacity change. Our ADRs and tables say
"illustrative synthetic policy, not law" — true and important. The optional field now exists, but it
stays empty until a specific basis receives owner/legal approval.

**Code systems are properties of the fact and the setting, not of the model.** Observed:
`ICD-10` ("Lääkäreillä ICD-10 on ensisijainen koodi"), `ICPC2` (also permitted, incl. reason for
encounter), `SPAT` vs `THL-toimenpidekoodit` (primary vs specialist care), `FinLOINC` (national
measures), `OID` (service provider), `ATC` (product codes, already in our generator). Our
`concept_key` is a synthetic string with no stated relationship to any of them.

## Decision

1. Add an **optional local/national `heading` pair** to the `Kirjaus` envelope, distinct from `topic`.
   Either may be absent. `topic` stays the coarse keyword axis the decision tables already use.
2. Add an **optional `legal_basis`** field to the rules/records that derive from a stated statute. Our
   own rules remain explicitly illustrative: `legal_basis` records a rule's **stated origin**, and is
   **not** a compliance claim, not legal advice, and not a substitute for owner/legal sign-off.
3. Document in code and fixtures that **`concept_key` is intended to *map to* a national code system**
   (`FinLOINC` / `OID` / `ICD-10` / `ICPC2` / `ATC`), not to *be* one. No renaming of existing keys is
   required by this ADR.
4. **The professional's ordering is preserved.** The briefing must not reorder a `Kirjaus`'s headings
   (or its `tietokokonaisuudet`) without stating that it did. Our own display order is our own choice
   and must be presented as such.

## Boundaries

- **No national specification is claimed.** The heading names are *observed in vendor training
  material* and are illustrative until independently verified against the national specification.
- `legal_basis` strings **require owner approval before they ship**. An absent basis is better than a
  wrong one, and no basis may be inferred by a model. The vendor's 1326/2010 51 § same-day HTA duty is
  recorded here but not adopted: it has no approved basis, and checking its interval needs contact
  timestamps that belong to 1.2. It is never a UI citation.
- The nested `RecordView` wire extension is explicitly approved by the 2026-10-07 owner amendment in
  ADR-0001: optional heading pairs and entry provenance only. Absent values are omitted, no top-level
  `Picture` field is added, and no fixture data is authored by this ADR.
- The `Standing boundary` in `CONTEXT.md` — that demo permission, decision and assessment rules are
  illustrative synthetic policy, not law — is **unchanged and not weakened** by carrying a basis.

## Consequences

- The envelope and view can express a `Kirjaus` heading pair (including `Esitiedot`); fixture values
  remain absent pending separate owner authorization, so no existing record is assigned a heading.
- The ordering rule becomes testable: a snapshot test that our render does not permute source order.
- A follow-up decision is needed on the eventual `concept_key → national code` mapping; this ADR only
  fixes the *intent*, so the drift stops being invisible.
- openEHR alignment is **recorded as context only**. Aligning `Kirjaus` to openEHR Composition/Entry
  is a separate, larger decision and is not decided here.
