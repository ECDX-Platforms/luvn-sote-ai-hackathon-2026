# Regulatory position — LUVN

**Status:** internal reading for this repository.
**Authority:** this document is **not** legal advice, **not** a conformity assessment and **not** a statement of
compliance with any regulation. It records what the built system is, what the source material says about the rules
around it, and which questions remain open. Dates and citations must be verified against the regulation text
before they are repeated outside this repository.

**Reference set.** The primary sources behind the dates and quotes below, with what each one does *not*
establish and a marked unverified list, are in [`docs/references.md`](references.md). That file is the citation
layer for this one; where the two disagree, the reference set is right and this document is stale.
*Why this note exists: the citations here were previously carried from secondary material; the reference set names the primary source for each one.*

**Why it exists.** The source material (Nelli Karhu, *Sote-AI -ideasta kokeiluun*; Jouni Meriluoto,
*GDPR, EU AI Act ja EHDS SoTe AI:ssa*) states the developer's first move in its own words:

> "Käytännön ensiaskel on kirjata kullekin tekoälyratkaisulle käyttötarkoitus, toimittaja, organisaation rooli,
> mahdollinen laitestatus ja soveltuvat määräajat." — *"The practical first step is to record, for each AI
> solution, its purpose of use, the vendor, the organisation's role, any device status, and the applicable
> deadlines."*

> "Käyttötarkoitus ratkaisee: se määrää roolin, luokituksen ja velvoitteet — ja sen takia se kannattaa määritellä
> ensin." — *"Purpose of use decides: it determines the role, the classification and the obligations — which is
> why it should be defined first."*

This document is that record for this repository, and it is the boundary register for the *regulatory* reading.

---

## 1. System register

One row per AI system this repository depends on, in the shape the source material asks for. "Our role" is the
role of the organisation running this demo, not of a vendor.

| # | System | Purpose of use (käyttötarkoitus) | Vendor | Our role | Device status | Annex III relevance | Applicable deadline |
|---|---|---|---|---|---|---|---|
| A | **LUVN situation picture** (`policy.cut` → `decide` → Decision-1 → Foundry narration) | Assemble the *permitted and relevant* existing source facts for one professional in one service situation, with a citation for every claim; no diagnosis, no treatment recommendation. The system **orders already permitted and selected records by a validated scheduling-urgency score**; urgency **never** selects, withholds or omits a record, and inclusion and omission are decided before it runs. **Decided:** the system derives totals for two structured instruments (PHQ-9, IADL) deterministically from recorded item answers, and prints instrument facts rather than clinical severity characterisations. The computation is the system's own, on synthetic data, and nothing is certified. The publisher's own PHQ-9 page states that its printable PDF form does **not** calculate automatically, citing MDR 2017/745 — quoted with its URL and retrieval date in the repository's source notes | none — built in-house in this repository | **Provider** of the briefing (LUVN builds and offers it); also deployer of the two vendor layers inside it | **None claimed.** No medical purpose is declared; no device is placed on the market; nothing is CE-marked | **Reviewed and not relied upon:** the demo runs now, and no Annex III obligation attaches today (see §3) | none pending — the demo is in service as a synthetic tool |
| B | **Microsoft-Decision-1** (a Foundry deployment, `<foundry-host>/providers/microsoft/v1/systemone`) | Classify one *already permitted* free-text note as relevant / irrelevant / unclear for a given role and service purpose, and judge whether a proposed sentence is supported by its cited sources | Microsoft (external processor, EU data zone) | **Deployer** of an external classifier | not applicable (a classifier, not a device) | not relied upon | classification itself is not a deferred obligation |
| C | **Azure AI Foundry** (`gpt-6.1-sol`, Prompt Agent `luvn-narration`) | Turn already-selected, already-permitted evidence into ≤4 Finnish sentences, each citing its sources | Microsoft (external) | **Deployer** of an external generative model | not applicable (prose generation, not a device) | not relied upon | none pending |

**Why provider, and what that costs.** The source material's rule: *"Itse kehittävä organisaatio on tarjoaja
(provider), jolla on laajemmat velvoitteet: riskienhallinta, dokumentaatio, lokit ja vaatimustenmukaisuuden
arviointi."* — an organisation that develops the system itself is a **provider** with the wider obligations
(risk management, documentation, logs, conformity assessment; Arts. 8–17 and 43). This repository is an in-house
build, so it does not get to describe itself as a mere deployer. The same material immediately qualifies the
consequence: an in-house product under the health-institution provision generally **does not** meet the
third-party-assessment condition that pulls a device into the Article 6(1) high-risk class, but it **can** be
high-risk under Annex III through its purpose of use (MDCG 2025-6, cited in the material as non-binding
guidance). Both halves are recorded here rather than the convenient one.

**What provider-side practice already exists in the repository** — this is not a claim of conformity, only of
engineering artefacts a provider is expected to hold:

| Expected artefact | Where it already lives here |
|---|---|
| Versioned, reviewable decision logic | `backend/app/tables/*.json`, each carrying `_meta`: *"ILLUSTRATIVE -- synthetic demo policy, pending clinical/legal sign-off. Not law, not LUVN guidance."* |
| A versioned model artefact | Prompt Agent `luvn-narration`, created from the committed prompt in `backend/app/prompts/` |
| Risk handling in code rather than in a prompt | permission precedes every model call (`backend/app/policy.py`); a withheld record is disclosed in no form (ADR-0004) |
| Logs and traceability | scrubbed per-stage telemetry (`backend/app/telemetry.py`), plus the per-request pipeline trace on the wire |
| Honest limits, stated as limits | `docs/architecture-as-built.md` "Standing limitations"; `ARCHITECTURE.md` "Honest limits" |
| No prohibition-shaped uses | the demo has no biometric identification, no emotion inference, no social scoring, no manipulative or exploitative purpose, and no untargeted face scraping (AI Act Art. 5 list, applicable since 2.2.2025) |

### 1.1 The declared purpose of use as served

Row A's purpose is not only prose here: one sentence crosses the `Picture` contract as
`intended_use`, single-sourced from `backend/app/intended_use.py`, and `backend/tests/test_intended_use.py`
fails if this document and the served sentence drift apart.

> Näyttää ammattilaiselle tämän palvelutilanteen kannalta olennaiset ja sallitut esitiedot, jotka on koottu
> lähteistä ja tarkistettu lähdeviitteisiin. Voi ehdottaa seuraavia toimenpiteitä nimettyihin tietolähteisiin
> nojaten; ehdotukset on laadittu koneellisesti ja merkitty niiksi. Ammattilainen tekee päätökset: järjestelmä ei
> tee kliinisiä päätöksiä, ei korvaa ammattilaisen arviota eikä ole lääkinnällinen laite.

*"Show the professional the information that is relevant and permitted for this service situation, assembled
from sources and checked against citations. It may suggest next steps grounded in named knowledge sources; the
suggestions are machine-drafted and marked as such. The professional decides: the system makes no clinical
decisions, does not replace the professional's assessment, and is not a medical device."*

**What that sentence is allowed to contain, and what it is not.** It is a product statement: the only
status it may name is a **denial** ("makes no clinical decisions, is not a medical device"), and it carries no
statute, article, annex, risk class, CE marking or conformity word. A positive classification never travels
on the wire — it lives in this document, where it is labelled a non-authoritative internal reading — and
`legal_basis` stays empty and absent from the rendered record.

---

## 2. Where this system sits relative to the device question

The source material's test is concrete, and it points directly at what this product does:

> "Potilas- tai laboratoriotietojärjestelmä, joka vain siirtää ja säilöö tietoa" is **not** classified as a
> medical device — *"Huom. Jos järjestelmä esim. hälyttää riskitiedoista tai muokkaa tai tiivistää
> potilastietoja, tilanne voi muuttua."* — *"Note: if the system, for example, alerts on risk data or modifies
> or condenses patient data, the situation can change."*

So the honest answer has to address each trigger rather than wave at "synthetic".

| Trigger named in the material | What this repository actually does | Where |
|---|---|---|
| Alerts on risk data | The risk level and suggested actions come from deterministic tables marked illustrative, not law; the only *severity* wording rendered on a lab row is **source-supplied** (`tulkinta`, e.g. `kliinisesti hälyttävä`), never computed by us | `backend/app/decide.py`, `backend/app/tables/risk.json` (severity is source-supplied, no threshold inference) |
| Modifies or condenses patient data | The generated prose is a **restatement with citations**, checked sentence-by-sentence against only the records it cites — **LUVN's own design decision**, because the shipped contract performs no whole-database query or embedding join, not a platform requirement; unsupported sentences are dropped and the deterministic briefing remains. The chat's **advice arm** (round R) drafts next steps only from the form-definition knowledge items retrieved for the question, and publishes one only when its citations resolve and a narrow contradiction check over the visible records passes; no clinician has reviewed that advice | `backend/app/grounding.py`, `backend/app/chat.py` (`generate_advice`), [`docs/references.md`](references.md) §1 |
| Manufacturer's declared purpose | The declared purpose is served as a single value and states no diagnostic, preventive, monitoring or treatment purpose | `backend/app/intended_use.py`, and the "Käyttötarkoitus" row in the UI's run-specification disclosure |

**The boundary, stated plainly.** *If this system is ever pointed at real patient data for a clinical purpose,
this reading does not travel with it.* The device question would then be answered by the manufacturer's declared
purpose, the qualification guidance, and MDR/IVDR — and the material's warning applies: in co-development the
manufacturer role must be settled contractually, and the manufacturer is fixed by law (whoever places the product
on the market under its own name), not only by agreement.

---

## 3. Applicable-timeline table

Derived from the source material and labelled with the material's own dates. **The single most dangerous sentence
for a presenter is "the AI Act does not apply yet" — it is false.** Parts of it are already applicable; what is
deferred are the high-risk obligations of Chapter III, Sections 1–3.

| Instrument | Applies from | Applies today? |
|---|---|---|
| **GDPR** (2016/679) | already in force | **Yes** — but see §4: today's payloads are fabricated, so special-category processing is not engaged |
| **AI Act** (EU) 2024/1689 — Art. 5 prohibitions | 2.2.2025 | **Yes** |
| **AI Act** — Art. 4 AI literacy | 2.2.2025; the material records a July 2026 amendment that no longer requires demonstrating a specific sufficient level per employee | **Yes**, duty retained |
| **AI Act** — Art. 50 transparency and machine-readable marking | Art. 50 applies from **2.8.2026**; the **2.12.2026** date is only a transitional grace period, only for systems already placed on the market before 2.8.2026, and only for the Art. 50(2) marking-and-detection duty (Commission FAQ, last update 24.7.2026). Regulation (EU) 2026/1744 (Digital Omnibus on AI, in force 27.7.2026) postpones the **Chapter III high-risk** obligations and does not postpone Art. 50 | **Yes.** Two distinct duties, on two different surfaces: the **conversational surface is Art. 50(1)** — a person interacts directly with the system and must be told so — and **the generated prose is Art. 50(2)**, a provider duty to mark outputs machine-readably. See §5.2 |
| **AI Act** — Art. 5, new prohibition (CSAM / non-consensual intimate imagery) | compliance by 2.12.2026 | **Not yet**, and not applicable to this system |
| **AI Act** — Art. 4a (special-category data for bias detection) | in force with the Omnibus; extended to providers and deployers of non-high-risk systems under a strict-necessity standard | **Yes** — relevant only if we ever process such data |
| **AI Act** — Annex III high-risk obligations | 2.12.2027 (the earlier 2.8.2026 date is superseded for these) | **No** |
| **AI Act** — Annex I product-law high-risk obligations | 2.8.2028 (the earlier 2.8.2027 date is superseded for these) | **No** |
| **MDR** (2017/745) / **IVDR** (2017/746) | in force since 2021 / 2022 | **Yes** — and the Omnibus did **not** defer the device regulation |
| **EHDS** (EU) 2025/327 | in force 25.3.2025 (20 days after publication on 5.3.2025); **general application 26.3.2027** | **No** today, but nearer than the phase dates suggest — the Regulation's general application is 26.3.2027 (§6) |
| **EHDS** — primary and secondary use phases | 26.3.2029 (patient summaries, e-prescriptions, dispensed medication) and 26.3.2031 (imaging, laboratory results, discharge summaries) | **No** |
| **EHDS** — EHR-system manufacturer duties | with the Regulation's application | **Not claimed.** The definition is broad (§6), so this is a decision to record, not an absence to assume |

**Citation status.** The material dates the AI Act amendment as *"AIA muutosehdotus 19.11.2025"* (a proposal); it is
in force as **Regulation (EU) 2026/1744**, "Digital Omnibus on AI", adopted 8.7.2026 and published in the OJ on
24.7.2026, entering into force **27.7.2026** on the third day after publication. That citation is now **verified
against the Official Journal text** ([EUR-Lex](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=OJ:L_202601744)),
which sets the Chapter III high-risk application dates at **2.12.2027** (Art. 6(2)/Annex III) and **2.8.2028**
(Art. 6(1)/Annex I) — the dates this table carries. The Omnibus does **not** move Art. 50. One precision the OJ
adds, and this table did not carry: recital (41) of the Omnibus **does** amend **Art. 50(7)** of the AI Act (it
removes the Commission's power to approve the transparency code of practice by implementing act), which is a
procedure change and **not** the Art. 50(2) marking duty. The Art. 50 dates and the transitional period are from
the Commission's own FAQ and guidelines, not from the Omnibus. **Read MDCG 2025-6 with the amendment in hand**,
since the guidance is keyed to the superseded 2.8.2027 date. *Why this changed: the previous text cited
Licentium and Bird & Bird as secondary sources and said the OJ text had not been read; the OJ text has now been
retrieved, and it is the authoritative source for the 2027/2028 dates.*

---

## 4. What leaves this system, and under what condition real data may

The two model layers are external services. That is a factual architectural property, so it is recorded here
rather than discovered by a reviewer.

| Egress | What is sent | Minimisation already in code |
|---|---|---|
| Decision-1 (`<foundry-host>/providers/microsoft/v1/systemone`) | **One note at a time**, already permitted by `policy.cut`, plus a role/purpose `state` and the question `criteria`. Notes are never batched into one `state` | only post-cut records reach the shortlist; a withheld record is never sent and never counted |
| Foundry (`gpt-6.1-sol`) | the selected post-cut evidence payloads | `source.basics_payload(record, "model")` returns `{}` for identity basics, so neither name nor display identifier is sent; a withheld record cannot be selected |
| UpToDate Expert AI (**planned**) | the pathway question, plus the post-cut evidence projected exactly as Foundry's is | **not sent by any shipped code path yet.** That path is an operator-supplied capture held locally; this row must be completed, with the processor and transfer analysis of section 4's condition, before real data reaches it |

**Today's position.** Every fixture is fabricated from first principles, and the delivery corpus is **not loaded**
(no 50 000-profile data in the image or the API). Under the source material's own table, fully invented persons,
events and identifiers do not make the rows personal data — so GDPR special-category processing and cross-border
transfer are not engaged *for the synthetic demo*. The material also warns, and this repository agrees, that
*"synteettisyys ei yksin todista anonymiteettiä"* — synthetic does not by itself prove anonymity — so this position
is a statement about **these payloads**, not a general licence.

**The condition for real data.** A real-data delivery is a different system. Before any of it reaches Decision-1 or
Foundry, the provider/deployer must hold:

1. a lawful basis under **GDPR Art. 6** *and*, for health data, **Art. 9** (the material notes patient consent is
   usually not the basis for care data, and that secondary use needs its own route — Finland's `toisiolaki`
   552/2019 and Findata are the national permit path for secondary use);
2. **Art. 28** processor terms with both vendors, treating them as processors, plus the Chapter V transfer
   analysis for third-country processing;
3. an **Art. 35 DPIA**, which the material's own examples trigger for new technology and large-scale special-category
   processing — and the organisation already has a **DPO** obligation as a public authority (Art. 37(1)(a));
4. a documented health-data-holder answer for **EHDS** before any of its phases bite (26.3.2027 onward).

This is **registered as open work**, not implemented.

**Two national statutes, two different routes — and the care route has a limb this repository had under-used.**
For **care delivery**, the relevant act is **703/2023** (*Laki sosiaali- ja terveydenhuollon asiakastietojen
käsittelystä*, in force 1.1.2024). Its access rule is not one condition but two, and they are cumulative:
the right to use client data "on perustuttava … työtehtävään ja annettavaan palveluun siten, että henkilöllä on
oikeus käyttää vain työtehtävissään tarvitsemiansa **välttämättömiä** asiakastietoja" — it must rest on the work
task and the service being given, so only the **indispensable** data is used — **and** "asiakastietojen
käsittelyn perusteena on oltava **tietoteknisesti varmistettu asiakas- tai hoitosuhde**": processing must rest
on a **technically verified client or care relationship**. This demo models the first limb in `policy.cut`
(role and purpose, then the indispensable set) and has **no mechanism for the second**, because a fabricated
client has no care relationship to verify. For **secondary use**, the act is **552/2019** (*toisiolaki*), whose
scope turns on personal data processed for research, statistics, steering or supervision — it is not a route
for building a product on real patient data.

**What the statutes say about synthetic data: nothing.** Neither 703/2023 nor 552/2019 contains a
synthetic-data exemption clause or a synthetic-data prohibition clause; their definitions are person-referential
(`asiakastieto`, `potilastieto`, `henkilötieto`). So the law is **silent on fabricated persons — it neither
permits nor prohibits this demo**, and no wording in this repository may imply that it blesses one. The boundary
is not "synthetic versus real": it is whether the system's claims are about a **real person's care**, and that
is exactly when the 703/2023 definitions and the technically-verified-care-relationship limb begin to bite. Until
the demo is pointed at a real client record with a real care context, that limb is not engaged; the moment it is,
"synthetic" stops being an answer. *Why this changed: the section previously used only the indispensable-data
limb, which is the weaker half of the rule, and did not state that the statutes are silent on synthetic data.*
Sources: [703/2023](https://www.finlex.fi/fi/laki/ajantasa/2023/20230703) (no official English translation;
the quotations above are the Finnish text, unofficial rendering in brackets — see
[`docs/references.md`](references.md) §3 and §7.9) and
[552/2019](https://www.finlex.fi/fi/laki/ajantasa/2019/20190552).

**Where the code-system and guidance licences stand** is a separate question from the data boundary, and it is
written down in §5.1 — including the one source in the set that names AI in its terms.

---

## 5. The licensing layer and the AI-transparency label, and what neither claims

### 5.1 The licensing layer: what the sources this demo cites actually permit

The demo cites national code systems and clinical guidance from several publishers, and their terms differ in
kind rather than degree. The full per-source reading, with every quotation and its retrieval date, is
[`docs/references.md`](references.md) §5–§6; what matters here is that **this repository must not state a
general rule either way**:

| Source | What its own terms say | AI clause? |
|---|---|---|
| **Kanta Code Service** (THL content, Kela platform) | Free retrieval is **scoped**: "Koodistot ovat haettavissa **kansallisiin** sosiaali- ja terveydenhuollon sähköisiin asiakirjajärjestelmiin maksutta" — into national social and health electronic document systems. It is a permission for a class of systems, **not an open-data licence**. | **None.** The scope limitation is the load-bearing point. |
| **THL open data** | CC BY 4.0: "The license CC-BY-4.0 for THL's open data must be recognised when using the data." No non-commercial restriction, so redistribution with attribution is permitted. | **None.** Covers THL's open data and statistics, not the code lists on the Code Service and not Fimea. |
| **ATC** (WHO Collaborating Centre) | "Copying and distribution for commercial purposes is not allowed. Changing or manipulating the material is not allowed." Use requires reference to the Centre. | **None**, but this is **the one restrictive system** here: it restricts through commercial distribution and no-modification, not through AI. |
| **WHO ICD** | CC BY-ND 3.0 IGO, commercial use permitted without adaptation; the licence sentence is attached to classifications on the ICD-11 platform, so **WHO's ICD-10 scope is unverified**. Finnish ICD-10 in practice comes from the Code Service. | **None.** |
| **ICPC-2, Finnish ICD-10, ATC-on-Code-Service, SNOMED CT national subsets, FinLOINC, Fimea** | **Unverified.** WHO publishes no licence text for ICPC-2 and WONCA's page returned HTTP 403; the other terms were not separately retrievable or not re-verified. | **Unknown — do not assume permissive.** |
| **Käypä hoito** (Duodecim) | The **only** AI-specific clause found: the user undertakes not to use, copy, share or otherwise exploit the content for training, developing or improving any AI application (page dated 2.2.2026). A narrower private-use carve-out exists, and linking is separately permitted (page dated 7.12.2023). | **Yes — prohibits AI development use.** |

**The honest conclusion, stated in both directions: "the code services prohibit AI use" is false, and "the
code services permit AI use" is unsupported.** None of the retrievable code-service sources carries an
AI-specific clause, and silence on AI is not permission. What is supported is narrower — the Code Service's
permission is scoped to national health IT systems rather than licensed as open data, THL open data is
CC BY 4.0, and ATC is the one code system whose own terms would obstruct a shipped product, through
commercial-distribution and no-modification rules. The single genuinely AI-specific restriction in the set is
Käypä hoito's, and it is a **clinical-guidance publisher, not a code service** — which is why the repository's
link-only treatment of Käypä hoito (deep links in the publisher's `/hoiNNNNN` form, no content ingested) is
correct, while a blanket "the code services prohibit AI" claim would not be. This subsection is written down
because a general prohibition claim about the code services was carried in this area of the repository's
documentation; **no source in the set supports it**, and the two-sided statement above is what the licences
actually do support. See [`docs/references.md`](references.md) §5, §6 and §8.*

### 5.2 The Art. 50 label, and the duties on the two surfaces

The generated prose is rendered under a plain-language note that the briefing text is **machine-drafted** from
permitted sources and citation-checked. What that label is worth has to be stated precisely, and an earlier
reading of this section was wrong in both directions.

**What Art. 50 actually reaches.**

- **Art. 50(1)** applies to providers of AI systems *intended to interact directly with natural persons*.
  **It now applies to this system**, because the conversational surface shipped: a professional can put a
  question to it, so a person is interacting directly with an AI system and the transparency duty attaches.
  This paragraph previously said no such duty attached, on the grounds that no conversational surface existed;
  that was true of the revision it described and is false of this one. What is implemented with it:
  every turn returns a visible notice —
  *"Olet yhteydessä tekoälyyn. Vastaus on tarkistettava."* — from the API rather than the interface, so a
  client cannot omit it. **What is not yet done:** the per-turn machine-readable marking of §5.2's second
  half reaches the briefing's accepted prose ([ADR-0017](adr/0017-machine-readable-ai-marking.md)) but not
  yet each chat turn, which is recorded here as an open obligation rather than described as complete.
- **Art. 50(2)** is different and does reach this system. Its actual text: *"Providers of AI systems, including
  general-purpose AI systems, generating synthetic audio, image, video or text content, shall ensure that the
  outputs of the AI system are marked in a machine-readable format and detectable as artificially generated or
  manipulated."* (Regulation (EU) 2024/1689, Art. 50(2), as published.) The exemption it carries is narrow:
  the obligation does not apply "to the extent the AI systems perform an assistive function for standard editing
  or do not substantially alter the input data provided by the deployer or the semantics thereof". This system
  generates synthetic Finnish text rather than standard-editing the deployer's input, so the provider duty —
  this build — attaches. **Not claimed here:** that the Commission's Guidelines expressly name
  "AI-generated summaries of text" or "paraphrasing or rewriting text". That phrase was **unverified** against
  the Guidelines document and has been removed; the Guidelines exist
  ([published 20 July 2026](https://digital-strategy.ec.europa.eu/en/policies/guidelines-ai-transparency-obligations))
  and their landing page describes their coverage, but the quotation is not sourced and is not relied on. The
  Commission FAQ is what supports the reading that the exemption is for standard editing only. *Why this
  changed: a quotation attributed to the Guidelines could not be retrieved from any Commission source; see
  [`docs/references.md`](references.md) §7.2 and §8.*
- **Art. 50(4)** addresses deployers: deepfake disclosure (image, audio, video — text is not a deepfake), and
  separately AI-generated **text published to inform the public on matters of public interest that has not
  undergone human review or editorial control**. An internal professional briefing reviewed by the professional
  is not that publication.
- The marking duty is **in force since 2.8.2026**, with a transitional date of **2.12.2026** for generative
  systems already on the market **before 2.8.2026** (Commission FAQ, last update 24.7.2026). It is **not**
  deferred to 2027–2028, and it is not satisfied by a visible caption: the requirement is a machine-readable
  marking, detectable as AI-generated or manipulated.
- **This system does not qualify for that transition.** Its hosting began after 2.8.2026, so the duty
  applied to it from its first hosting day. **2.12.2026 is not its compliance deadline** — it is the date this
  repository chose to revisit the marking decision by
  ([ADR-0017](adr/0017-machine-readable-ai-marking.md), "Limits"). Stating it as a deadline we are answerable
  to would misattribute a date the system does not own.

**Where this system stands, today.** The payload **is marked**, and the limit of that marking is
stated plainly: `backend/app/provenance.py` emits structured-field response headers on
`/api/encounters/{id}/picture` whenever the generation branch actually produced accepted prose —
`AI-Generated-Content: true`, `AI-Generated-Parts: narrative`, `AI-Generated-Model`, and the marking
version. That is machine-readable and detectable by any machine receiving the payload, which is what
Art. 50(2) asks for and what Recital 133 lists as acceptable (metadata/provenance rather than a
watermark).

**What it does not do.** It is **not** a watermark: the marking rides on the response, so prose copied
out of the payload loses it. For an internal professional surface whose text is read under a visible
caption and a citation check, that is the proportionate technique; for a public-facing or long-form
surface it would not be, and the marking would have to become a watermark. The marking also claims no
compliance and names no article — it states provenance and nothing else, and a test forbids it from
carrying words like "compliant" or "watermark".

Two further facts bound the argument, and neither weakens the implementation now in place:

1. The obligation's proportionality factors include content-specific limitations, implementation cost
   and the state of the art, and Recital 133 lists the acceptable techniques — "watermarks, metadata
   identifications, cryptographic methods for proving provenance and authenticity of content, logging
   methods, fingerprints or other techniques, as may be appropriate" — among which metadata/provenance
   is what ships. The Guidelines also note narrowly-defined controlled and closed environments and B2B
   use where limited benefits can preclude the obligation. This demo is a closed, authenticated presenter
   context.
2. **No published threshold exempts short free-form text, and this section may not imply one.** An
   earlier revision of this section said "under the voluntary Code of Practice, free-form text under
   **200 tokens** is treated as not watermarkable, with no watermarking obligation". **No source found
   supports that number.** The Commission's own enumeration of what falls outside Art. 50(2) names "a
   short sequence of numbers, symbols or letters, source code, outputs … exclusively communicated from
   machine-to-machine, or outputs that are only used in closed loop industrial and product development
   environments" — and the Code of Practice document itself was not retrieved. The number has therefore
   been **removed as a citation** rather than softened. What the shipped narration's brevity supports is
   a *proportionality* judgment — a handful of internal sentences does not justify a research-grade
   Finnish watermark — and that judgment rests on Art. 50(2)'s own factors and Recital 133, not on any
   Commission-set token figure. *Why this changed: unverified, therefore removed — see
   [`docs/references.md`](references.md) §7.1.*

**The decision is recorded** in [`docs/adr/0017-machine-readable-ai-marking.md`](adr/0017-machine-readable-ai-marking.md):
ship provenance marking now, and keep the watermark question open on a **surface** trigger — output growing
materially past a few sentences, moving to a public or long-form surface, or being published outside the payload
it is marked on — rather than on a token count or a date this system does not own.

**What the label is for, beyond that.** It earns its place through the *Tekoälyn hyödyntäminen* criterion: a
reviewer can see what the model produced, and that a human — not the model — decides. The disclosure an internal
clinical tool owes to its staff and patients comes from professional accountability and GDPR Articles 13–15, and
this demo claims none of that either.

`docs/architecture-as-built.md` remains the fuller "what the AI did" panel.

---

## 6. Explicitly out of scope / not claimed

- No MDR or IVDR conformity assessment, no CE marking, no notified body involvement.
- No AI Act conformity assessment, no EU-database registration (Art. 49) and no fundamental-rights impact
  assessment (Art. 27). The material's caution is recorded: a public body's FRIA attaches to Annex III systems,
  and it should **not** be assumed merely because something might be a device.
- No EHDS interoperability, no EHRxF exchange-format support, no secondary-use readiness, and no claim to be an
  EHR system or a health-data holder.
- **EHR system: considered, and not claimed.** The EHDS definition is broad — software that enables the
  *storage, intermediation, export, import, conversion, editing or viewing* of personal electronic health data
  from a priority category, *intended by the manufacturer for use by healthcare providers in patient care*. A
  read-only briefing view is closer to that definition than the word "EHR" suggests, and the duty it triggers
  would be manufacturer's duties (technical documentation, EU declaration of conformity, CE marking, EU database
  registration — self-assessed, no notified body). The position recorded here is that this demo has no such
  intended use: synthetic data, no manufacturer's declaration of a patient-care purpose, no interoperability
  claim. That is why the point is written down rather than left implicit, and it does not survive a real
  clinical deployment.
- No `legal_basis` value ships: the field stays empty and is absent from the rendered record, and no candidate
  statute is reachable from the UI. Absent beats unapproved.
- No clinical validation, no measured accuracy, no legal compliance, no per-professional authorization and no
  50 000-profile throughput claim.
- The demo's model layers are **optional**: either can be absent and the deterministic briefing still renders.

---

## 7. Limits and sources

- **Advisory review input.** Three independent read-only reviews were run over the two decks and this repository
  before this document was written (falsification, legal-accuracy, repository-safety lenses), and their
  corrections are folded in: the "classification is not deferred" framing, the provider nuance that keeps both
  halves of the in-house-device question, and the warning that a repo-silent topic is not a repo-error.
- **One of those corrections was itself wrong, and was found by validating it.** An earlier version of §5 (now
  §5.2) said Art. 50 reaches only systems conversing directly with a person, and that no marking duty applied here.
  Checking Art. 50(2) against its published text showed the opposite for the *provider* duty: generated text is
  in scope, and the exemption is confined to standard editing or non-substantial alteration. The section now
  states both duties separately — Art. 50(1) for the conversational surface, Art. 50(2) for the generated
  prose — and records that 2.12.2026 is a transitional date only for systems already on the market before
  2.8.2026, which this system is not. Whether a short professional briefing must in fact be marked is **decided
  by nobody yet** — hence an open question rather than a claim in either direction.
- **The citation layer was wrong in three places, and this pass corrected them.** A "200-token" short-text
  threshold is not in any retrievable Commission document and has been **removed as a citation** (§5.2,
  ADR-0017); a quotation attributed to the Commission's Guidelines ("AI-generated summaries of text") could not
  be retrieved and has been **removed** (§5.2); and a claim that the Finnish code services prohibit AI
  development use is **false as a general statement** and has been replaced with the per-source licence reading
  (§5.1). Each change carries its own one-line justification where it was made, and all three are listed in
  [`docs/references.md`](references.md) §8.
- **Verification status.** Regulation (EU) 2026/1744, the Art. 50 application and transitional dates, the
  Finnish acts 703/2023 and 552/2019, the Code Service service description, THL's CC BY 4.0 statement, the ATC
  terms and HL7's R4 page were checked **against the primary source** while
  [`docs/references.md`](references.md) was written, and that file carries each URL and date. What remains
  unverified is listed there in §7 (the 200-token threshold; the Guidelines' summary/paraphrase sentence;
  ICPC-2; WHO's ICD-10 scope; THL's terms for Finnish ICD-10, ATC-on-Code-Service, SNOMED CT subsets and
  FinLOINC; Fimea's catalogue entry; any HL7/openEHR joint statement; Findata's procedure docs; and the
  non-existence of a Finlex English translation of 703/2023). This is **not** a lawyer's opinion: it has not had
  legal review. Verify before quoting a date or an article number outside this repository.
- **Open questions** are tracked outside this file: the real-data gate, the EHDS position
  and the Art. 50(2) marking decision.
- **Sources.** The primary-source citation layer is [`docs/references.md`](references.md). The source material
  is Nelli Karhu, *Sote-AI -ideasta kokeiluun – mitä sääntelystä kannattaa tietää?*, and Jouni
  Meriluoto, *GDPR, EU AI Act ja EHDS SoTe AI:ssa*; regulation
  references are quoted as the material gives them, with the exceptions corrected in §3 and §5 against the
  primary sources linked in the reference set.
