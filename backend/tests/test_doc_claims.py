from pathlib import Path

import pytest

from scripts import check_doc_claims

ROOT = Path(__file__).resolve().parents[2]


def test_record_and_view_fields_are_documented_nonvacuously():
    documentation = "\n".join((ROOT / path).read_text(encoding="utf-8")
                               for path in check_doc_claims.SURFACE_DOCUMENTS)
    fields = check_doc_claims._record_surface_fields()
    assert fields
    assert check_doc_claims._assert_surface_coverage(fields, documentation) == len(fields)

    copied_fields = fields.copy()
    copied_fields.add("future_unowned_record_field")
    with pytest.raises(ValueError, match="future_unowned_record_field"):
        check_doc_claims._assert_surface_coverage(copied_fields, documentation)


def test_repoint_repairs_a_second_current_state_paragraph_but_not_the_chain():
    """A document may carry the current-state claim in more than one paragraph.

    The first repoint implementation rewrote only the paragraph matching the *first* claim. When the
    as-built document grew a second one -- a summary bullet that also names the current revision -- the
    bullet kept the previous revision's commit and digest after its revision number had been advanced, so
    the document contradicted itself and no check noticed. This pins both halves of the repair: every
    paragraph naming the *current* revision gets the captured pair, and a paragraph naming an *older*
    revision keeps its own, which is the whole reason the rewrite is windowed at all.
    """
    from scripts import repoint_deployment

    values = {"revision": "luvn-challenge11-product--0000099",
              "digest": "sha256:" + "a" * 64, "commit": "d" * 40}
    older_commit, older_digest = "e" * 40, "sha256:" + "f" * 64
    text = (
        "The currently deployed revision is `luvn-challenge11-product--0000099`.\n"
        "It runs image `sha256:" + "b" * 64 + "`, source commit `" + "c" * 40 + "`.\n\n"
        "- **Current deployed revision `luvn-challenge11-product--0000099`** (source commit `"
        + older_commit + "`, image digest `" + older_digest + "`).\n\n"
        "An earlier revision `luvn-challenge11-product--0000007` was built from commit `"
        + older_commit + "` (image `" + older_digest + "`).\n"
    )
    out = repoint_deployment.repoint_document(text, values, "docs/architecture-as-built.md")

    # Both current-state paragraphs carry the captured pair ...
    assert out.count(values["commit"]) >= 2
    assert out.count(values["digest"]) >= 2
    # ... and the chain paragraph, which names an older revision, still records its own.
    assert f"`luvn-challenge11-product--0000007` was built from commit `{older_commit}`" in out
