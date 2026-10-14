# Citable reference set

**Status:** internal working reference for this repository. **Not legal advice.**
**Source of this document.** This file is the durable, citable form of the underlying primary-source research,
with the primary sources re-checked against their URLs while this file was written (every quote below was
retrieved from the URL in its row unless the row says otherwise). Edit this one when a claim is corrected.

**How to read a row.** The last column is the important one. A source proves only what its own quote says. The
recurring failure this table exists to prevent is treating *a thing that exists* — an evaluator, a licence
class, a permission sentence — as *a thing that is required, or that permits what we do*. Silence is recorded
as silence, never as permission.

| Question area | Table |
|---|---|
| Grounding and attribution for generated clinical text (Microsoft) | [§1](#1-grounding-and-attribution-for-generated-clinical-text-microsoft) |
| Regulatory marking of AI-generated text (EU AI Act Art. 50) | [§2](#2-regulatory-marking-of-ai-generated-text-eu-ai-act-art-50) |
| Finnish health-data law and this demo's boundary | [§3](#3-finnish-health-data-law-and-this-demos-boundary) |
| FHIR R4 vs openEHR as a mapping target | [§4](#4-fhir-r4-vs-openehr-as-a-mapping-target) |
| Finnish national code services and code-system licences | [§5](#5-finnish-national-code-services-and-code-system-licences) |
| Finnish clinical guidance terms (Käypä hoito) | [§6](#6-finnish-clinical-guidance-terms-käypä-hoito) |
| **Unverified — do not cite as established** | [§7](#7-unverified--do-not-cite-as-established) |
| **Claims in this repository that these sources do NOT support** | [§8](#8-claims-in-this-repository-that-these-sources-do-not-support) |

**Used by:** [`docs/regulatory-position.md`](regulatory-position.md) §3–§5 and
[`docs/adr/README.md`](adr/README.md) reading order; [ADR-0017](adr/0017-machine-readable-ai-marking.md) and
[ADR-0020](adr/0020-fhir-r4-is-the-mapping-target.md) cite it where their support changed.

---

## 1. Grounding and attribution for generated clinical text (Microsoft)

| claim it supports | source title | publisher | date | URL | what it does NOT establish |
|---|---|---|---|---|---|
| Microsoft documents one relevant evaluator, **Groundedness**: "Measures how well the generated response aligns with the given context without fabricating content (precision aspect)". Its documented required inputs are `response` and `context` (optional and recommended), and for multi-chunk retrieval: "concatenate chunks into a single string using a separator such as `\n\n` between chunks". Scores are 1–5 with a default pass threshold of 3. **Groundedness Pro (preview)** is a second, stricter variant ("Detects if the response is strictly consistent with the context"), without an SLA and not recommended for production. | Retrieval-Augmented Generation (RAG) evaluators for Generative AI | Microsoft (Microsoft Learn) | Last updated 2026-06-02 | https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/rag-evaluators | Does **not** make groundedness evaluation mandatory; does **not** require citations in output; does **not** define per-sentence or per-claim checking; **does offer, document or endorse no mode in which the judge sees only the sources a sentence cites** — its `context` is the whole retrieved set. |
| Microsoft's **agent tracing** is documented as capturing "inputs, outputs, tool usage, retries, latencies, and costs", with spans and OpenTelemetry GenAI semantic conventions; its own best-practice list says "**Redact sensitive content**: Remove or mask personal data, secrets, and credentials from prompts, tool arguments, and span attributes before they reach telemetry." | Agent tracing overview | Microsoft (Microsoft Learn) | Last updated 2026-08-28 | https://learn.microsoft.com/en-us/azure/foundry/observability/concepts/trace-agent-concept | Does **not** require tracing; establishes **no** per-sentence citation or attribution requirement; the GenAI semantic conventions it builds on carry "Development status and might change in future releases". |

**The direct answer, for the record: no published Microsoft guidance requires — or even describes — a
per-sentence grounding check that sees only the cited sources.** LUVN's cited-only verifier is LUVN's own
design decision, and it is documented as such in
[`docs/architecture-as-built.md`](architecture-as-built.md) §"Resolution seam" and
[ADR-0018](adr/0018-microsoft-decision-1-replaces-the-vendor-classifier.md).

---

## 2. Regulatory marking of AI-generated text (EU AI Act Art. 50)

| claim it supports | source title | publisher | date | URL | what it does NOT establish |
|---|---|---|---|---|---|
| **Verbatim Art. 50(2):** "Providers of AI systems, including general-purpose AI systems, generating synthetic audio, image, video or text content, shall ensure that the outputs of the AI system are marked in a machine-readable format and detectable as artificially generated or manipulated." Followed by the proportionality clause: "Providers shall ensure their technical solutions are effective, interoperable, robust and reliable as far as this is technically feasible, taking into account the specificities and limitations of various types of content, the costs of implementation and the generally acknowledged state of the art". Editing exemption: "This obligation shall not apply to the extent the AI systems perform an assistive function for standard editing or do not substantially alter the input data provided by the deployer or the semantics thereof". | Regulation (EU) 2024/1689 (Artificial Intelligence Act), Article 50 | European Parliament and Council; OJ L, 2024/1689, 12.7.2024 (reproduction retrieved) | Regulation of 13 June 2024 | https://artificialintelligenceact.eu/article/50/ (OJ text: https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=OJ:L_202401689) | Does **not** say a watermark is required; does **not** say response metadata is sufficient; does **not** define "detectable"; does **not** address short text or token thresholds. The reproduction is not an EU site — prefer the EUR-Lex HTML for a legal quote. |
| **Verbatim Art. 50(1):** providers "shall ensure that AI systems intended to interact directly with natural persons are designed and developed in such a way that the natural persons concerned are informed that they are interacting with an AI system, unless this is obvious from the point of view of a natural person who is reasonably well-informed, observant and circumspect". | Regulation (EU) 2024/1689, Article 50 | as above | 13 June 2024 | https://artificialintelligenceact.eu/article/50/ | Does **not** reach a background classifier; does **not** require a per-turn machine-readable marking (that is Art. 50(2)). |
| **Verbatim Recital 133** lists acceptable techniques: "…taking into account available techniques or a combination of such techniques, such as watermarks, metadata identifications, cryptographic methods for proving provenance and authenticity of content, logging methods, fingerprints or other techniques, as may be appropriate." Also: "this marking obligation should not cover AI systems performing primarily an assistive function for standard editing". | Recital 133 (related to Art. 50(2)), Regulation (EU) 2024/1689 | as above | 13 June 2024 | https://artificialintelligenceact.eu/recital/133/ | Establishes that metadata/provenance is **one accepted technique**; does **not** establish that any particular header set satisfies "detectable"; does **not** rank the techniques; sets **no** token threshold. |
| **Article 50 applies from 2 August 2026.** "Article 50 of the AI Act applies as from 2 August 2026. From that date onwards, providers and deployers of AI systems must comply with the transparency obligations laid down in that provision." | Transparency obligations under Article 50 of the AI Act (FAQ) | European Commission (DG CONNECT) | Last update 24 July 2026 | https://digital-strategy.ec.europa.eu/en/faqs/transparency-obligations-under-article-50-ai-act | Does **not** defer Art. 50 for high-risk-only reasons. |
| **The 2 December 2026 date is a transitional grace period only**, and only for one obligation: "A limited grace period is envisaged only for AI systems placed on the market before 2 August 2026 and only as regards the marking and detection obligation for AI-generated content (Article 50(2) of the AI Act). Providers of such systems must comply with those obligations only as from 2 December 2026." | Transparency obligations under Article 50 of the AI Act (FAQ) | European Commission (DG CONNECT) | Last update 24 July 2026 | https://digital-strategy.ec.europa.eu/en/faqs/transparency-obligations-under-article-50-ai-act | Does **not** give a grace period to a system first hosted in October 2026; does **not** defer Art. 50(1). |
| **The Art. 50(2) exclusions are enumerated and contain no free-form-text token threshold:** "a short sequence of numbers, symbols or letters, source code, outputs of an AI system intended to be exclusively communicated from machine-to-machine and processed automatically without any exposure to humans, or outputs that are only used in closed loop industrial and product development environments… unless they are the final output." Plus "a narrow exemption of the marking obligation … for AI systems that generate outputs used in 'business to business' or 'industrial contexts'". | Transparency obligations under Article 50 of the AI Act (FAQ) | European Commission (DG CONNECT) | Last update 24 July 2026 | https://digital-strategy.ec.europa.eu/en/faqs/transparency-obligations-under-article-50-ai-act | Does **not** mention short prose; note the exclusion is "a short sequence of **numbers, symbols or letters**", not "a short text"; **no token figure appears anywhere in it**. |
| **Regulation (EU) 2026/1744, the "Digital Omnibus on AI"**, amending Regulation (EU) 2024/1689 as regards the simplification of the implementation of harmonised rules on artificial intelligence, was adopted 8 July 2026 and published in the OJ on 24.7.2026; it "should enter into force as a matter of urgency on the third day following that of its publication" (27 July 2026). It **postpones the Chapter III high-risk obligations** — "the date of application of Sections 1, 2 and 3 of Chapter III is set to 2 December 2027 for AI systems classified as high-risk pursuant to Article 6(2) and Annex III, and to 2 August 2028 for AI systems classified as high-risk pursuant to Article 6(1) and Annex I" — and it also introduces "a transitional period of four months for providers who have already placed their systems on the market before the 2 August 2026" for the Art. 50(2) marking duty, i.e. the 2.12.2026 date. | Regulation (EU) 2026/1744 (Digital Omnibus on AI) | European Parliament and Council; OJ L, 2026/1744, 24.7.2026 | Regulation of 8 July 2026; in force 27 July 2026 | https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=OJ:L_202601744 | Does **not** change the Art. 50 transparency dates or the Art. 50(2) duty; read the one precision noted in §8 — recital (41) of the Omnibus **does** amend **Art. 50(7)** (removing the Commission's power to approve the code by implementing act), which is not the Art. 50(2) marking obligation. |
| **A voluntary Code of Practice exists** and is the Commission- and AI-Board-assessed route to demonstrate Art. 50(2), (4) and (5) compliance: "Even though adherence to the code is voluntary, the transparency requirements under article 50 of the AI Act are legal obligations." The final code was published 10 June 2026 ("Publication of the final code of practice"). | Code of Practice on Transparency of AI-generated Content | European Commission (DG CONNECT) | Final code published 10 June 2026; policy page last update 31 July 2026 | https://digital-strategy.ec.europa.eu/en/policies/code-practice-ai-generated-content | Sets **no** free-form-text token threshold in anything retrievable; the linked code PDF (https://ec.europa.eu/newsroom/dae/redirection/document/129555) was **not** retrieved, so no claim is made about its contents beyond the policy page. |
| **Guidelines on transparency obligations** for providers and deployers of AI systems exist and are the Commission's clarification of Art. 50: "The guidelines clarify these obligations, providing legal certainty about the scope"; they cover "definitions of directly interactive AI systems, synthetic content, deepfakes, AI-generated text on matters of public interest and examples of exceptions, such as standard editing." | Guidelines on transparency obligations for providers and deployers of certain AI systems | European Commission (DG CONNECT) | Published 20 July 2026 (library entry); policy page last update 6 August 2026 | https://digital-strategy.ec.europa.eu/en/policies/guidelines-ai-transparency-obligations | The policy page is not the guidelines. The phrase sometimes attributed to them — "AI-generated summaries of text" / "paraphrasing or rewriting text" — is **unverified** (§7). |
| The Art. 50(4) deployer duty reaches AI-generated text **published** to inform the public on matters of public interest; the human-review exemption is substantive: "Superficial, solely formal, or procedural checks (e.g. spell-checking or grammatical correction) are not considered to be human review or editorial control." | Transparency obligations under Article 50 of the AI Act (FAQ) | European Commission (DG CONNECT) | Last update 24 July 2026 | https://digital-strategy.ec.europa.eu/en/faqs/transparency-obligations-under-article-50-ai-act | Does **not** say an internal professional briefing is out of Art. 50(2) scope; Art. 50(2) is a **provider** duty and does not turn on publication. |

---

## 3. Finnish health-data law and this demo's boundary

| claim it supports | source title | publisher | date | URL | what it does NOT establish |
|---|---|---|---|---|---|
| **The act is 703/2023, "Laki sosiaali- ja terveydenhuollon asiakastietojen käsittelystä"**, in force 1 January 2024, consolidated (ajantasaistettu). | Laki sosiaali- ja terveydenhuollon asiakastietojen käsittelystä 703/2023 | Finlex (Ministry of Justice / Oikeusrekisterikeskus) | Statute 703/2023; consolidated text retrieved | https://www.finlex.fi/fi/laki/ajantasa/2023/20230703 | **There is no English translation** — the `/en/` route resolves to no document, so any English rendering is unofficial (§7). |
| **The access-right rule has two limbs.** Verbatim: "Oikeuden käyttää asiakastietoja on perustuttava sosiaali- tai terveydenhuollon ammattihenkilön ja muun asiakastietoja käsittelevän henkilön työtehtävään ja annettavaan palveluun siten, että henkilöllä on oikeus käyttää vain työtehtävissään tarvitsemiansa välttämättömiä asiakastietoja. **Asiakastietojen käsittelyn perusteena on oltava tietoteknisesti varmistettu asiakas- tai hoitosuhde** tai muu asiakkaan sosiaali- ja terveyspalvelun järjestämiseen ja toteuttamiseen liittyvä tehtävä." *[Access must rest on the professional's work task and the service being given, so only the **indispensable** data needed in those tasks is used, **and** processing must have as its basis a **technically verified** client or care relationship — or another task related to organising and delivering the client's social and health service.]* | Laki … 703/2023, access-rights provision | Finlex | retrieved | https://www.finlex.fi/fi/laki/ajantasa/2023/20230703 | Does **not** define role names or a role taxonomy; does **not** mention AI, models or synthetic records; **does not authorise a demo**. The "technically verified client or care relationship" limb is a real-system requirement a synthetic demo cannot satisfy. |
| **Logging is a duty on the service provider**, and the data subject has a statutory route to the log. (Quotes as recorded in the research record, retrieved from the consolidated act.) | Laki … 703/2023, access-log provisions | Finlex | retrieved | https://www.finlex.fi/fi/laki/ajantasa/2023/20230703 | Does **not** prescribe a format or an API; does **not** mention AI; a synthetic demo has no data subject who can exercise the right. |
| **Secondary use is 552/2019, "Laki sosiaali- ja terveystietojen toissijaisesta käytöstä"**, live and amended. Its § 2 scope turns on "henkilötietoja" processed for listed purposes "even if they were not originally stored for that purpose" — research and statistics among them. | Laki sosiaali- ja terveystietojen toissijaisesta käytöstä 552/2019 | Finlex | Statute 26.4.2019; consolidated version current to a 2025 amendment | https://www.finlex.fi/fi/laki/ajantasa/2019/20190552 | Does **not** create a route for product development on real patient data; it is **not** an avenue for a demo to process real Kanta data. Findata's own procedure documentation was **not** retrieved (§7). |
| **The statutes are silent on synthetic data.** 703/2023 § 3's definitions are person-referential (`sosiaalihuollon asiakastieto`, `potilastieto`, `hyvinvointitieto`, …) and 552/2019 § 2 turns on personal data. Neither contains a synthetic-data exemption clause or a synthetic-data prohibition clause. | both acts, definitional provisions | Finlex | retrieved | (both URLs above) | Does **not** establish that a synthetic demo is "out of scope" — only that the law does not address it. **The boundary is not "synthetic vs real": it is whether the system's claims are about a real person's care**, which is when the care-relationship limb bites. |

---

## 4. FHIR R4 vs openEHR as a mapping target

| claim it supports | source title | publisher | date | URL | what it does NOT establish |
|---|---|---|---|---|---|
| FHIR self-description: "FHIR is a standard for health care data exchange, published by HL7®." The R4 landing page is labelled "v4.0.1: R4 - Mixed **Normative** and **STU**" and states: "**The current version which supercedes this version is 5.0.0.**" | Index - FHIR v4.0.1 | HL7 | R4 (4.0.1) technical correction 30 Oct 2019; page generated 1 Nov 2019 | https://hl7.org/fhir/R4/ | Does **not** make R4 the newest release — R5 (5.0.0) supersedes it; does **not** make all of R4 normative. "Current version" is HL7's phrase for the newest release, not a claim that R4 is current. |
| FHIR defines a framework implementers **constrain**: "**StructureDefinition** - provide additional rules that serve to constrain the optionality, cardinality, terminology bindings, data types and extensions defined in the resources used by the implementation"; "**CapabilityStatement** - describes the interfaces that an implementation exposes for exchange of data". | FHIR Overview (v4.0.1) | HL7 | R4, 2019 | https://hl7.org/fhir/R4/overview.html | Does **not** establish that mapping a corpus to FHIR resource vocabulary is the same as being FHIR-conformant; says nothing about openEHR. |
| **What an openEHR conformance claim actually requires.** openEHR's specification set includes a "**conformance specification: defining the basis of conformance testing and product profiling**"; the CNF component "includes a formal definition of the notional **openEHR Platform** and how to test it in a standard way. This is used as the basis for **openEHR product certification** and also for procurement tender specification." Its own component page lists the Conformance Guide, Platform Conformance Test Schedule, Platform Profiles and Conformance Certificate as **DEVELOPMENT**, with Release 1.0.0 listed as Work In Progress. | Conformance Specifications (CNF) Component | openEHR International | CNF Release 1.0.0; component status DEVELOPMENT; page retrieved | https://specifications.openehr.org/releases/CNF/latest/ | Establishes that conformance is a **certifiable, testable product property** against a notional Platform. Does **not** establish that mapping a record envelope to openEHR `Composition`/`Entry` naming gets any part of it. |

**The honest one-line difference, to cite when refusing openEHR conformance:** FHIR R4 is an exchange standard
that defines resources and a framework implementers constrain with profiles, whereas openEHR conformance is
*certification* against the notional openEHR Platform defined in openEHR's own CNF component — so mapping our
fields toward FHIR resource names is a mapping we can cite, while "openEHR conformance" is a certification we
have not undergone and whose artefacts (archetypes, templates, OPTs, terminology bindings) we do not have.
**No primary HL7/openEHR joint statement was retrieved, so no relationship between the two organisations is
asserted anywhere in this repository** (§7).

---

## 5. Finnish national code services and code-system licences

| claim it supports | source title | publisher | date | URL | what it does NOT establish |
|---|---|---|---|---|---|
| **The Code Service's permission is SCOPED, not open.** Verbatim: "Koodistot ovat haettavissa kansallisiin sosiaali- ja terveydenhuollon sähköisiin asiakirjajärjestelmiin maksutta." *[The code lists can be retrieved into the **national** social and health care electronic document systems free of charge.]* The same page restates the division of labour: "Kansaneläkelaitos hoitaa koodistopalvelun tietoteknistä toteutusta." *[Kela handles the technical implementation of the code service.]* | PALVELUKUVAUS KANSALLINEN KOODISTOPALVELU (service description, v1.15) | Kela / Kanta (platform); THL (content) | Dated 21.4.2017; cites the then-current act 1227/2010 | https://www.kanta.fi/documents/20143/106828/Kansallinen+koodistopalvelu+palvelukuvaus.pdf/1069ba48-e80e-71dc-3b77-f4bffe3173a7 | Is **not** an open-data licence — it is a permission **scoped to a class of systems**. A demo that is not a national social/health electronic document system is **not clearly inside** it. **Silent on AI.** |
| The Code Service REST API is real, unauthenticated for reads, and self-documenting. | Elisa CodeServer REST API Documentation | Kela (CodeServer) / THL | retrieved | https://koodistopalvelu.kanta.fi/codeserver/swagger/index.html | **Technical reachability is not a licence.** Silent on AI. |
| **THL open data is CC BY 4.0.** Verbatim: "The license CC-BY-4.0 for THL's open data must be recognised when using the data." | Open data (THL) | THL (Finnish Institute for Health and Welfare) | Page updated 19.8.2026; retrieved | https://thl.fi/en/research/open-data | Covers THL's **open data and statistics**, not the code lists on the Code Service, and not Fimea. CC BY 4.0 has no AI clause and no non-commercial restriction. |
| **ATC restricts through commercial distribution and modification, not through AI.** Verbatim: "Use of all or parts of the material requires reference to the WHO Collaborating Centre for Drug Statistics Methodology. Copying and distribution for commercial purposes is not allowed. Changing or manipulating the material is not allowed." | Copyright/Disclaimer (ATC/DDD) | WHO Collaborating Centre for Drug Statistics Methodology, Norwegian Institute of Public Health | Last updated 2009-12-14; retrieved | https://atcddd.fhi.no/copyright_disclaimer/ | **Silent on AI**, but the modification ban and the commercial-copying ban bite. Whether the Code Service route changes the analysis for ATC-as-served-in-Finland is **unverified** (§7). |
| **WHO's classification licence is CC BY-ND 3.0 IGO and permits commercial use.** "These Classifications … are licensed under the Creative Commons Attribution-NoDerivs 3.0 IGO (CC BY-ND 3.0 IGO) license and may be used for commercial and non-commercial purposes, provided there is no adaptation of the codes and the work is appropriately cited." | Copyright (WHO publishing policies) | World Health Organization | retrieved | https://www.who.int/about/policies/publishing/copyright | The licence sentence is attached to classifications "now available on the ICD-11 platform"; **WHO's ICD-10 licence scope is unverified** (§7). Finnish ICD-10 in practice comes from the Code Service (see row 1). |

### Which of these prohibit use for developing an AI application?

**None of the retrievable sources carries an AI-specific clause.** That is a negative finding:

- **No AI clause found:** THL open data (CC BY 4.0), WHO ICD (CC BY-ND 3.0 IGO), the Code Service service
  description. Silence on AI is **not** permission.
- **Restrictive but not AI-named:** ATC — the one code system here whose own terms would plausibly obstruct a
  shipped product, and it does so through a commercial-distribution and no-modification rule.
- **Unverified:** ICPC-2 (WONCA's page returned 403; WHO publishes no licence text for it), Finnish ICD-10's
  THL terms, SNOMED CT national subsets and FinLOINC on the Code Service, and Fimea's catalogue entry (§7).
- **The one genuinely AI-specific clause in this whole set is Käypä hoito's** (§6) — a clinical-guidance
  publisher, not a code service.

**So the honest conclusion is two-sided: "the code services prohibit AI use" is false, and "the code services
permit AI use" is unsupported.** What is supported is narrower: the code services' terms are scoped to national
social and health document systems rather than licensed as open data; none of them addresses AI; ATC imposes a
commercial-distribution and no-modification restriction; and ICPC-2's terms could not be verified at all.

---

## 6. Finnish clinical guidance terms (Käypä hoito)

| claim it supports | source title | publisher | date | URL | what it does NOT establish |
|---|---|---|---|---|---|
| **Linking is expressly permitted** in the publisher's prescribed form: "Käypä hoito -sivustolla oleviin suosituksiin voidaan tehdä linkkejä myös muilta sivustoilta. Linkitettäessä … tulee käyttää seuraavan muotoista linkkiä: `https://www.kaypahoito.fi/hoi50061`…" | Linkittäminen (Käyttöoikeudet) | Suomalainen Lääkäriseura Duodecim | Päivitetty 7.12.2023 | https://www.kaypahoito.fi/kaypa-hoito/kayttooikeudet/linkittaminen | Does **not** permit copying, framing, excerpting or ingesting the content; establishes linking only. |
| **The AI clause exists and this repository quotes it accurately.** Verbatim: "Käyttäjä sitoutuu siihen, ettei käytä, kopioi, jaa, tai muutoin hyödynnä Käyvän hoidon sisältöä minkään tekoälyohjelmiston tai sovelluksen kouluttamis-, kehittämis- tai parantamistarkoituksessa." | Käyttöoikeudet | Duodecim | Päivitetty **2.2.2026** | https://www.kaypahoito.fi/kaypa-hoito/kayttooikeudet | Does **not** prohibit *linking* (separate page, above); does **not** define "content"; does **not** state a penalty. |
| **The narrow private-use carve-out exists:** "Käyttäjä saa syöttää Käypä hoito -sisältöjä tekoälyä hyödyntäviin sovelluksiin ainoastaan vähäisessä määrin omaa yksityiskäyttöään varten ja vain siinä tapauksessa, että kyseinen sovellus ei tallenna tietoja pysyvästi, eikä käytä niitä sovelluksen kouluttamis-, kehittämis- tai parantamistarkoitukseen." | Käyttöoikeudet | Duodecim | Päivitetty 2.2.2026 | https://www.kaypahoito.fi/kaypa-hoito/kayttooikeudet | Both limbs are cumulative; a multi-user product with a database fails both. |
| **Quotation is permitted for non-commercial purposes with attribution; commercial borrowing needs permission:** "Osittainenkin lainaaminen kaupallisiin tarkoituksiin ilman lupaa on kielletty. … lupa ottaa lainauksia ei-kaupallisiin tarkoituksiin niiden edellyttämässä laajuudessa, kuitenkin niin, että lähde on ilmoitettava." | Käyttöoikeudet | Duodecim | Päivitetty 2.2.2026 | https://www.kaypahoito.fi/kaypa-hoito/kayttooikeudet | Does **not** define "commercial"; a free public demo is **not clearly** non-commercial. |
| **The English rights page omits the AI clause entirely** (it is dated 29.4.2021 and speaks only of unauthorised reproduction for commercial use). | Rights of use | Duodecim | Updated 29.4.2021 | https://www.kaypahoito.fi/en/rights-of-use | A reader who cites the English page **cannot** support an AI clause; cite the Finnish text. |

**Answer for this area directly:** a linking-permission clause exists (7.12.2023) and an AI-development
restriction clause exists (2.2.2026). The English page (29.4.2021) carries no AI clause. This repository's
`backend/app/knowledge_registry.json` and `backend/app/guidance.py` state both clauses accurately, including
the date asymmetry — no change needed there.

---

## 7. Unverified — do not cite as established

1. **A 200-token free-form-text threshold in the Art. 50(2) marking regime or its Code of Practice.** No
   source found: the Commission's own FAQ enumeration of Art. 50(2) exclusions names "a short sequence of
   numbers, symbols or letters" — not short prose — and the Code of Practice PDF was not retrieved. **No
   published threshold supports the number.** What would resolve it: retrieving the code PDF
   (https://ec.europa.eu/newsroom/dae/redirection/document/129555) and searching its text, or a Commission
   statement setting a figure.
2. **The Guidelines' exact sentence naming "AI-generated summaries of text" or "paraphrasing or rewriting
   text".** The Guidelines exist (published 20 July 2026) and their landing page describes their coverage, but
   the guideline document itself was **not** retrieved, so that phrase is not quoted here. What would resolve
   it: retrieving the guidelines PDF and quoting the sentence.
3. **ICPC-2's licence terms and WONCA's permission route.** WHO's page carries no licence text for ICPC-2;
   WONCA's page returned HTTP 403. Treat ICPC-2 permission as **unverified** and do not assume it is permissive.
4. **WHO's ICD-10 (as opposed to ICD-11) licence scope.** The licence sentence on WHO's copyright page is
   attached to classifications "now available on the ICD-11 platform".
5. **THL's own terms for Finnish ICD-10, for ATC as served through the Code Service, and for SNOMED CT
   national subsets / FinLOINC on the Code Service.**
6. **Fimea's CC BY 4.0 catalogue entry.** Carried over from the repository's registry, not re-verified against
   the catalogue in this pass.
7. **Any primary HL7/openEHR joint statement.** No primary source retrieved; no relationship is asserted.
8. **Findata's own procedure documentation** (findata.fi). 552/2019 § 1–2 is sufficient for the boundary claim,
   not for Findata's procedure.
9. **A Finlex English translation of 703/2023 — it does not exist.** The `/en/` route resolves to no document,
   so any English rendering of that act must be labelled an unofficial translation.
10. **The Code of Practice PDF's actual contents** beyond the Commission's policy page (linked above).
11. **The FHIR developer agent skill's value-set tables are not reliable for R4.** The skill is Anthropic's
   — `anthropics/healthcare`, `plugins/healthcare/skills/fhir-developer/` — and its
   resource examples and cardinality claims hold up: 13 of 13 cardinality claims agree with the R4
   StructureDefinitions, its code-system URIs are correct, and its LOINC vital-sign codes are valid. Three
   of its six value-set tables do not hold up: `Observation.status` lists `corrected`, and
   `Condition.clinicalStatus` lists `recurrence`, `relapse`, `remission` and `resolved`, none of which exist
   in the R4 CodeSystem (`hl7.org/fhir/R4/codesystem-*.json`, `content: complete`, `version: 4.0.1`), while
   `Condition.verificationStatus` lists `differential` and `provisional`, which are likewise absent. The
   bindings are `strength: required`, so a validator pinned to R4 rejects those codes. The lesson is not that
   the skill is bad but that an **official-looking artefact is not the same as an artefact for the version we
   target**; do not adopt the skill's enum tables, re-derive them from R4.0.1.
12. **The form-knowledge snapshot carries no codes, so a code-resolution rule would have no effect on it.**
   Every question in `backend/app/knowledge_forms_v1.json` carries exactly `id`, `category_id`, `text_fi`,
   `type`, `is_optional` and `scoring_enabled` — no code field of any kind, across all 14 forms. A proposed
   rule that a cited knowledge item carrying a code must resolve through `app.knowledge.lookup` would match
   zero items and pass vacuously, so it is **not** to be built in advance of coded knowledge. The snapshot's
   provenance is recorded and earned rather than claimed — its source files are SHA-256-digested from their
   bytes on import — and for form prose the form is its own authority, so fidelity to the citation is the
   whole of the check. What would make the rule meaningful: a knowledge item type that cites an external
   code, at which point the rule and its test should land together with that item type.

---

## 8. Claims in this repository that these sources do NOT support

| repository claim | where it was | status against the retrieved sources | what was done |
|---|---|---|---|
| Free-form text under **200 tokens** is treated as not watermarkable, with no watermarking obligation | `docs/regulatory-position.md` §5.2; `docs/adr/0017-machine-readable-ai-marking.md` | **Unverified (§7.1).** The Commission's Art. 50(2) exclusion list names "a short sequence of numbers, symbols or letters", not short prose, and no retrievable source sets a token figure. | The figure was removed as a citation in both documents and replaced with the explicit statement that no published threshold supports it. The *decision* (provenance marking now, watermark if the surface changes) is retained and now rests on the proportionality factors in Art. 50(2) and on Recital 133 alone, which do support it. |
| "Art. 50(1) reaches only systems interacting *directly* with a person" — presented as if it settled the matter for this system | `docs/regulatory-position.md` §3 | **Self-contradicted** by the same document's §5, which correctly records that Art. 50(1) now applies because the conversational surface shipped. | §3 was amended to agree with §5 and to say which limb applies to which surface: the conversational surface is Art. 50(1); the generated prose is Art. 50(2). |
| A per-sentence grounding check that sees only the cited sources is something **Microsoft** requires or recommends | [ADR-0018](adr/0018-microsoft-decision-1-replaces-the-vendor-classifier.md)'s framing of the verifier | **Not supported (§1).** Microsoft documents one Groundedness evaluator whose `context` input is the whole retrieved set, concatenated; it documents no per-sentence mode and no context-restriction recommendation. | Reworded as LUVN's own design decision with its reason (the shipped contract reaches no whole-database query or embedding join, so the verifier is handed the cited records only). The decision itself is unchanged. |
| "AI-generated summaries of text" / "paraphrasing or rewriting text" are expressly inside Art. 50(2) per the Commission's Guidelines | `docs/regulatory-position.md` §5.2 | **Unverified (§7.2).** The Guidelines exist and are cited, but that phrase could not be retrieved, so it is no longer asserted as a quotation. The FAQ does confirm the exemption is for standard editing and non-substantial alteration. | The unsupported quotation was removed; the section now states the scope conclusion on the strength of Art. 50(2)'s editing exemption as published, and marks the phrase unverified. |
| "The code services prohibit using their content to develop an AI application" — stated as a general rule | `docs/regulatory-position.md` §5 (the licence discussion); the wording also appears in `docs/api-endpoints.md`'s chat-prompts row, where it is **accurate as stated** because it describes Duodecim's terms for Käypä hoito, not a code service | **False as a general claim.** No retrievable code-service source carries an AI clause; ATC restricts commercial distribution and modification; the Code Service's permission is scoped to national health IT systems; THL open data is CC BY 4.0. | §5.1 now gives the per-source licence reading and states both directions: "the code services prohibit AI use" is false, and "the code services permit AI use" is unsupported. |
| Kanta Code Service permission rests on a general "free to retrieve" statement | registry `kanta-code-service` (not editable from this workstream) | **Correct but under-stated.** The source sentence scopes free retrieval to *national* social and health electronic document systems. | Stated in §5 of `docs/regulatory-position.md`; the registry text change is recommended but not applied. |
| FHIR R4 is the newest FHIR release (or is called "current") | ADR-0020, and any document calling R4 current | **Unsupported as phrasing.** HL7's R4 page states: "The current version which supercedes this version is 5.0.0." ADR-0020 itself only ever claimed a **mapping target**, so its decision is unaffected. | ADR-0020 now states R5's supersession and the openEHR conformance distinction explicitly, and links this file. No wording in this repository calls R4 current. |
| An "openEHR alignment" as evidence of conformance | an earlier rejected framing; `CONTEXT.md` | **Correctly refused already** — `CONTEXT.md` says "we have no archetypes, templates, OPTs or terminology bindings", and `backend/tests/test_forms.py` pins that an `openEHR-EHR-OBSERVATION…` identifier must not match. | No change to those refusals; the one-line openEHR conformance distinction (§4) was recorded in ADR-0020. |
| THL open data licence class was in doubt | `backend/app/knowledge_registry.json` records no THL open-data licence fact | **Resolved: CC BY 4.0 (§5).** | Recorded in §5 of `docs/regulatory-position.md`; a registry row is recommended but not applied (the registry is outside this document's write scope). |

---

**Maintenance rule.** When a claim in this file is corrected, correct it here first and then in the document
that relied on it, so the change is auditable.
