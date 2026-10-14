"""Machine-readable marking: this payload contains model-drafted text.

AI Act Art. 50(2) requires a *provider* of an AI system generating synthetic text to ensure
outputs are "marked in a machine-readable format and detectable as artificially generated".
Recital 133 names the acceptable techniques — watermarks, metadata, cryptographic provenance,
logging, fingerprints — so this module emits **provenance marking**, not a watermark: it makes
the payload self-describing for any machine that receives it. That distinction is stated on
purpose; it is the weak point of this approach, and claiming more would be a false claim.

Three rules this module exists to keep, because each is an easy mistake:

1. **Never mark a fail-closed response.** If no model-drafted prose was accepted, nothing here
   is emitted: a `foundry-off` or rejected view is entirely deterministic, and marking it as
   AI-generated would be untrue.
2. **Mark the response, not the sentence.** The marking travels on the response headers, so no
   `Picture` field changes, no withheld record is touched, and no count of records appears.
3. **No legal claim.** The marking says a machine generated some text and which one did it. It
   does not say "compliant", name an article, or assert a watermark.

In-process only: no file, no environment, no seam.
"""

from __future__ import annotations

# Bump when the marking's shape or meaning changes. v2: an identical deployment/model pair collapses
# to one value, so a v1 consumer can no longer distinguish "identical" from "deployment absent".
MARKING_VERSION = "luvn-ai-marking-v2"

# Structured-field style, so a machine can parse it without a bespoke parser. The value is a
# claim about provenance, not a compliance assertion.
HEADER_AI_GENERATED = "AI-Generated-Content"
HEADER_AI_GENERATED_PARTS = "AI-Generated-Parts"
HEADER_AI_GENERATED_MODEL = "AI-Generated-Model"
HEADER_AI_GENERATED_MARKING = "AI-Generated-Marking"

# Which part of the payload is model-drafted. Only one part exists today; naming it keeps the
# marking honest if a second model-written surface is ever added. `test_marking.py` ties this
# constant to `Picture.narrative`'s field name, because a rename would otherwise make the marking
# point at a field that no longer exists — a silent lie.
PART_NARRATIVE = "narrative"

# The names a browser consumer needs exposed to read the marking across origins. Without
# `expose_headers` on the CORS middleware, a fetch from an allowed origin (the Vite dev server)
# sees the response but not these headers — the header would be on the wire and still unreadable.
EXPOSED_HEADERS = (
    HEADER_AI_GENERATED, HEADER_AI_GENERATED_PARTS, HEADER_AI_GENERATED_MODEL, HEADER_AI_GENERATED_MARKING,
)


def _header_safe(value: str) -> str | None:
    """A value fit for a response header, or None if it carries a control character.

    The model name is **external input**: it is taken from the provider's JSON body (`llm.py`), and
    the only upstream check is a non-empty string. A CR or LF inside it would let a header value
    inject further headers on the wire (h11 permits CR/LF inside a value), so this drops the whole
    value rather than sanitising it — an unmarked model name is better than a forged header, and it
    fails closed the same way the rest of the marking does.
    """
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        return None
    return value.strip() or None


def marking_headers(*, generated: bool, part: str | None = None, model: str | None = None,
                    deployment: str | None = None) -> dict[str, str]:
    """The marking headers for one response, or an empty mapping when nothing was generated.

    `generated` is the caller's *actual branch result* — never inferred from configuration — so
    a half-configured environment cannot silently mark an unmarked payload, or the reverse.
    """
    if not generated:
        return {}
    headers = {
        HEADER_AI_GENERATED: "true",
        HEADER_AI_GENERATED_PARTS: part or PART_NARRATIVE,
        HEADER_AI_GENERATED_MARKING: MARKING_VERSION,
    }
    # The generating model is provenance, not a licence or a guarantee; add it only if the
    # generation branch reported one and it is header-safe.
    #
    # `deployment / model` only when that pair actually says two things. The hosted environment
    # reports one name for both — `narration_generation_deployment` is `llm.FOUNDRY_MODEL` itself and
    # the provider echoes that same value back as `response.model` — and "gpt-6.1-sol / gpt-6.1-sol"
    # reads as a defect in a header whose whole job is clean machine-readable provenance. An identical
    # pair collapses to the single true value; distinct values stay separated.
    safe_model = _header_safe(model) if model else None
    safe_deployment = _header_safe(deployment) if deployment else None
    if safe_model:
        headers[HEADER_AI_GENERATED_MODEL] = (
            f"{safe_deployment} / {safe_model}" if safe_deployment and safe_deployment != safe_model
            else safe_model
        )
    return headers
