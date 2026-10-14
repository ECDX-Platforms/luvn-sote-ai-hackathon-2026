# LUVN — domain language

## Person and encounter

- **Henkilö** — the individual whose existing source information is assembled for one encounter. Two people with the same name or birth year remain distinct people.
- **Lähdehenkilötunniste** — a stable identifier in an originating source that says *whose* Kirjaus it is. It is not inferred from a name, a note's wording or a model judgment. If it cannot be bound unambiguously to the encounter's Henkilö, the record is not safe to use.
- **Kohtaaminen** — a single modelled service occasion binding a Henkilö, Rooli and Palvelukonteksti. It is not a summary of previous contacts. Optional per-record `encounter_id`/`case_id` are attribution provenance only, shown only for selected, permitted records; they do not create a history index or aggregate. A **periytyvä asian rajoitus** recorded at case level silently withholds every record bound to that case.
- **Rooli** — the professional capacity in which the Kohtaaminen is viewed. A simulated role in a demo is not proof of the viewer's authenticated identity or real authorization.
- **Palvelukonteksti** — the current service purpose: why this professional needs information for this encounter. The same person and role may have different relevant information for different purposes.

## Information and preparation

- **Kirjaus** — one dated, source-identified item of existing information: a note, medication, diagnosis, measurement or form result. It retains its source and its subject.
- **Kirjauksen tila** — Luonnos / Valmis / Hyväksytty. Luonnos remains visible but is excluded from narration input: it is unfinished evidence, not a settled fact.
- **Kirjaustiedon provenienssi** — optional source metadata: `käyntiaika`, this entry's `vastaanottajat`, `erikoisala` and `suorituspaikka`. `RecordView` displays these on that entry only; `suorituspaikka` is display-only and no policy axis derives from it. Only the exact owner-approved synthetic Phase 4 rows carry specialty/unit values; visit time and recipients remain absent from product fixtures.
- **Käyntiaika** — optional source time for the visit/event, distinct from `recorded_at` (when the entry was recorded). When supplied it anchors temporal current/history status; absent values fall back to `recorded_at`.
- **Lomake** — a versioned, service-specific structure for entering source facts. A filled form is a Kirjaus. Form type/version/service are provenance; filled-at and filled-by role reuse the Kirjaus timestamp and author. In this synthetic demo, a Lomake is not a legal requirement or an authorization.
- **Käsitetunniste** — a deterministic key explicitly assigned to the same measured fact/observation across forms. Equal keys group both Kirjaukset for display; similar text/value alone never creates a match or selects a winning source. Its shape is **our convention, not a standard's**: `<domain>.<measure>.v<N>` with an optional `.<occasion>`, lowercase and dot-separated — e.g. `neuvola.kasvu.v1.2026-09-tarkastus`, `bp.systolic.v1.observation-1`. It is deliberately *not* an openEHR archetype identifier: we have no archetypes, templates, OPTs or terminology bindings, and a half-conformant identifier would advertise artefacts that do not exist. It carries mapping *intent* only (ADR-0015 §Decision 3), and the key is never rendered — it groups records and nothing else.
- **Kansallinen ja paikallinen otsikko** — one fact may carry both; the organisation maps its local heading to the national one, grouping uses the national heading and the record displays the local heading. Modelled but unused: no product fixture supplies a heading today.
- **Perustieto** — source-backed identity/context information used to confirm the encounter's person. It is a Kirjaus, subject to the same role/purpose eligibility as other records; selecting it does not grant access to other records. Only minimal permitted fields belong in the briefing.
- **Vastuuhenkilö** — `Record.responsible_clinician` is patient-level responsible-clinician text supplied by the record and displayed by `RecordView.responsible_clinician` beside (never instead of) record-level `responsible_approver` (`Vastuullinen hyväksyjä`). It creates no ownership entity, registry or permission meaning, and absence renders nothing.
- **Todistus / Lausunto / Ilmoitus** — Lomakepalvelu document kinds. Named as source-vocabulary constants only (`source.DOCUMENT_KINDS`); no record instance, count or rendering in this scope.
- **Sallittu** — eligible for this role and purpose under the illustrative permission rule; it is not a model score.
- **Poissuljettu** — a Kirjaus not permitted for this encounter. Its existence, identity and count are not disclosed through the briefing (silent absence).
- **Olennaisuus** — whether a *permitted* Kirjaus helps prepare this professional for this Palvelukonteksti. A record can be permitted but irrelevant. Relevance cannot make a withheld record permitted.
- **Esitietokooste / Tilannekuva** — the ready, source-backed pre-visit view assembled for a Kohtaaminen. It distinguishes source facts, dates, missingness and proposals from model narration; it is not an autonomous clinical decision.
- **Kattavuus** — tells the professional how much permitted free-text evidence was assessed, with per-row outcomes `gate_refused`, `not_shortlisted`, `budget_exhausted`, `too_long` and `unavailable`, and an `assessed` count. It never reports a withheld denominator.
- **Tekoälyn vaikutus** — a source-only impact line over permitted selected records: distinct source labels and form types. It is not a time-saving estimate and is never converted to minutes.
- **Lähdeviite** — a reference from a displayed claim to a permitted, selected Kirjaus.
- **Käyttötarkoitus (declared)** — the demo's stated purpose of use, carried as `Picture.intended_use` from
  one module (`app/intended_use.py`) and rendered in the technical disclosure. It is a *product statement*, not
  a legal classification: it names no statute, article, class or deadline, and it is not the same axis as
  **Palvelukonteksti**, which is the per-encounter purpose that `policy.cut` binds. Its denial clause (not a
  clinical decision, not a medical device) is the only status it may state; ADR-0001 owns its limits. A valid reference does not alone prove the claim is true.

## Professional judgment

- **Arviointi** — an existing structured instrument result with its source, date, version and completeness. Missing answers are not manufactured; supported scores come from recorded answers, not a model guess.
- **Päätös** — an illustrative, deterministic situation/risk/action result separate from the relevance selection and narration. It does not replace a professional decision. Its `situation_type` is **not** the service situation (Palvelukonteksti): it is a triage signal computed over the person's records alone (`triage.extract_features` takes the persona, not the encounter's purpose), and the briefing labels it so.
- **Ehdotus** — a bounded, source-linked prompt for professional review. It is **not** an order and **not** a record that anything was done: it is separate from `Päätös` and from the deterministic `actions` list, which are the rule-based assessment rather than a request. (This is the distinction openEHR draws between `INSTRUCTION` — something requested — and `ACTION` — something performed; the naming is borrowed, the conformance is not, and this demo records no performed actions.) Accepting or editing a proposal in the demo is not a clinical order or a source-system writeback.
- **Arvioimaton** — no assessable evidence for a decision; distinct from a finding of low risk.
- **Viitearvo** — optional source-supplied `payload.viitearvo` on a lab-result Kirjaus. The UI displays that value when present; it is never calculated from the result. The assumed delivery alias `viitealue→viitearvo` is provisional, not a claim about the real schema.

## Permission and delivery

- **Permission boundary** — permission is determined once for the Kohtaaminen before relevance assessment and narration; relevance cannot restore a withheld Kirjaus.
- **Delivery fields** — the assumed source field names are provisional aliases until confirmed against the real delivery. A dry run reconciles mappings without importing records; the 50,000-record delivery remains pending.

**Invariant — NO DERIVED NUMBER MAY REQUIRE A WITHHELD DENOMINATOR.** Every displayed derived number must be recomputable using records already rendered in that same view; otherwise it is absent. Source-supplied measurements remain source facts, not derived counts.

**Standing boundary:** demo permission, decision and assessment interpretation rules are illustrative synthetic policy, not law, LUVN guidance or clinically validated thresholds. Historical FinBERT/Laya terminology and metrics are not active domain definitions.
