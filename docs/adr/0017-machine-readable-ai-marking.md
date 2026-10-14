# Machine-readable marking for model-drafted text: provenance now, watermark if the surface changes

**Accepted and implemented.** Requested by the owner after validation of the AI Act
reading: the system's generated narration is synthetic text, Art. 50(2) requires a *provider* of such a
system to ensure outputs are machine-readable and detectable as artificially generated, and a visible
caption does not satisfy that. This record decides the technique and its limits.

## The decision

`backend/app/provenance.py` emits structured-field response headers on the picture endpoint whenever
the generation branch actually produced accepted prose:

| Header | Value | Meaning |
|---|---|---|
| `AI-Generated-Content` | `true` | this payload contains model-drafted text |
| `AI-Generated-Parts` | `narrative` | which part is model-drafted |
| `AI-Generated-Model` | e.g. `<deployment> / gpt-6.1-sol` | the generating model, when the branch reported one; collapses to a single value when the deployment and model names are identical (an environment that reports one name for both) |
| `AI-Generated-Marking` | `luvn-ai-marking-v2` | the marking's version, so its shape can evolve audibly — **v2** because v1's `AI-Generated-Model` had a different value shape (see "Version bump" below) |

The technique is **provenance marking**, which Recital 133 of Regulation (EU) 2024/1689 lists among
acceptable approaches (watermarks, metadata, cryptographic provenance, logging, fingerprints). It is not
a watermark, and this record does not pretend otherwise — see "Limits" below.

Three rules the implementation keeps, each test-pinned:

- **A fail-closed response is never marked.** If `narration_generation_status != "generated"` or no
  prose was accepted, no marking header is emitted. A `foundry-off` view is entirely deterministic, and
  marking it AI-generated would be untrue.
- **The marking keys off the branch's actual result**, never off configuration, so a half-configured
  environment can neither mark an unmarked payload nor leave a generated one unmarked.
- **The marking makes no legal claim.** It states provenance and names the model; no article, no
  "compliant", no "certified", and no reference to a watermark. A test asserts that.

## Why this rather than a watermark

An imperceptible watermark in Finnish clinical prose is research-grade, fragile, and would risk
distorting wording — in exactly the surface where a wrong word matters most. The guidelines' own
proportionality factors (content-specific limitations, implementation cost, state of the art) and
Recital 133's list of acceptable techniques (watermarks, metadata identifications, cryptographic
provenance, logging methods, fingerprints) support a proportionate technique here. **This record does
not rely on any short-text token threshold.** An earlier revision cited a "no watermarking obligation
below 200 tokens" treatment in the voluntary Code of Practice; **no source found supports that figure**,
the Commission's own Art. 50(2) exclusions name "a short sequence of numbers, symbols or letters" rather
than short prose, and the number has therefore been removed as a citation rather than softened — see
[`../references.md`](../references.md) §7.1 and §8. The decision below stands on the proportionality
factors and Recital 133 alone. The marking is machine-readable, which is what the obligation asks; what
it cannot do is survive the text being copied out of its payload.

## Limits — state these, do not smooth them

- **Not a watermark.** Prose copied out of the response loses its marking. Acceptable for a closed,
  authenticated, internal professional surface read under a visible caption and a citation check; not
  acceptable for a public-facing or long-form surface.
- **No conformity claim.** The marking is provenance. Nothing here is an AI Act conformity assessment,
  and `docs/regulatory-position.md` §1 records the classification separately as a non-authoritative
  internal reading.
- **The model field collapses when the two names are the same.** The mechanism is not a missing
  environment variable — there is no `LUNA_FOUNDRY_DEPLOYMENT` in this repository.
  `narration.py` sets `narration_generation_deployment` to `llm.FOUNDRY_MODEL` itself, and the provider
  echoes that same value back as `response.model`, so the pair is one name by construction. The field
  therefore carries the deployment name **whenever it differs** from the model, and only collapses when
  the two are identical. Found live in a deployed revision (the header read `gpt-6.1-sol / gpt-6.1-sol`);
  `marking_headers` is pinned by `tests/test_marking.py` for the same, distinct and absent cases.
- **The model name is external input, and a control character drops it.** It comes from the provider's
  JSON body, and the only upstream check is a non-empty string. A CR or LF inside it would let a header
  value inject further headers on the wire, because h11 permits CR/LF inside a value. The value is
  dropped rather than sanitised — an unmarked model name beats a forged header — and the rest of the
  marking still ships.
- **The marking is exposed across origins.** The CORS middleware lists these headers in
  `expose_headers`; without that, a browser consumer on an allowed origin receives the payload but
  cannot read the marking, because CORS hides every non-safelisted response header from `fetch()`. The
  marking would be on the wire and invisible to the reader it exists for.

## Version bump

`MARKING_VERSION` is **`luvn-ai-marking-v2`**, bumped by the collapse rule above: v1's
`AI-Generated-Model` always rendered `deployment / model` (or the model alone when no deployment was
supplied), so a v1 consumer could not distinguish "deployment and model identical" from "deployment
absent". Bumping is the module's own stated rule — *"Bump when the marking's shape or meaning
changes"* — and it is the only reason a consumer can tell which value shape it is parsing.
- **Re-decide on a stated trigger, not on a date this system does not own.** The trigger is concrete:
  the output growing materially past a few sentences, moving to a public surface, or being published outside
  the payload it is marked on. At that point the marking must become a watermark, or the surface must change.
  An earlier revision of this record named **2.12.2026** here, calling it "the date by which generative
  systems already on the market must comply with Art. 50(2)". That framing was wrong for this system: 2.12.2026
  is a **transitional** date for generative systems already on the market **before 2.8.2026**, whereas this
  system's hosting post-dates that cut-off. The duty therefore applied from its first hosting day rather than
  at the transitional date, and 2.12.2026 is not this system's compliance deadline. It remains a reasonable
  date to *revisit the decision* by, and it is recorded as exactly that rather than as a deadline we are
  answerable to.
- The human-readable caption under the prose remains. It serves different readers: the marking serves
  machines, the caption the professional.

## Consequences

- No `Picture` field changes, so this needs no ADR-0001 amendment; the marking travels on the response.
- No withheld record is touched: the marking says nothing about which records exist, how many were
  considered, or what the cut removed.
- The frontend is unchanged. The marking is for any machine that receives the payload, not for our UI.
- `docs/regulatory-position.md` §5 owns the legal reading; this record owns the technique.
