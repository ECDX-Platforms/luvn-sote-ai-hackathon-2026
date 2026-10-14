"""Challenge 1.1 Rajaukset: integration, write-back/dictation, and contact history."""

import ast
import inspect
import re
from pathlib import Path

from app import fixtures, main, source
from app.pipeline import _build_resolved_picture
from app.picture import Picture, RecordView
from tests.fixtures.bound_encounters import ENCOUNTERS
from app.policy import cut, cut_for_encounter
from app.purpose import palvelukonteksti, palvelukontekstit

ROOT = Path(__file__).resolve().parents[2]


def test_source_readers_are_fixture_and_delivery_seams_without_source_network_clients():
    for relative in ("backend/app/fixtures.py", "backend/app/delivery.py"):
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        imported = {alias.name.split(".")[0] for node in ast.walk(tree)
                    if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                     if isinstance(node, ast.ImportFrom) and node.module}
        assert not imported & {"httpx", "requests", "urllib"}, f"source integration added to {relative}"
    assert callable(fixtures.load_personas)
    from app.delivery import import_delivery
    assert callable(import_delivery)


def test_picture_path_is_read_only_and_does_not_write_source_records():
    picture = inspect.getsource(main.encounter_picture)
    assert not any(token in picture for token in ("write_text", ".unlink(", "open(\"w", "open('w")), \
        "Challenge 1.1 excludes encounter-time documentation and dictation"
    app_text = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")
    assert "@app.get(\"/api/encounters/{encounter_id}/picture\"" in app_text
    assert "BOUND_DATA_DIR" not in picture and "DEFAULT_DELIVERY_DIR" not in picture


def test_contact_history_is_not_admitted_for_any_bound_purpose():
    # The bound fixture corpus, enumerated through the real cut. (The former first assertion compared
    # `source.APPOINTMENT` -- a RECORD TYPE -- against a purpose's TOPIC set: two different vocabularies, so
    # it could never fail. It is removed; the served-corpus test below is the real guard.)
    personas = fixtures.load_personas()
    for encounter in ENCOUNTERS.values():
        persona = next(item for item in personas if item.persona_id == encounter.persona_id)
        scoped = cut(persona, encounter.role, encounter.purpose, encounter.encounter_id)
        assert not any(record.record_type == source.APPOINTMENT for record in scoped.records), \
            "Challenge 1.1 excludes contact/interaction-history aggregation"


def _served_demo_cuts(tmp_path: Path) -> dict:
    """Every encounter of the SERVED demo corpus (`delivery-demo`), cut through the real importer and policy."""
    from app import delivery
    delivery.import_delivery(delivery.DEFAULT_DELIVERY_DIR, tmp_path / "served")
    index = delivery.DeliveryIndex(tmp_path / "served")
    try:
        return {encounter_id: cut_for_encounter(index[encounter.persona_id], encounter)
                for encounter_id, encounter in index.encounter_index().items()}
    finally:
        index.close()


def test_the_served_corpus_admits_no_contact_record_in_any_lens(tmp_path: Path):
    """Challenge 1.2's exclusion, measured on what ships rather than on the bound fixtures.

    Red before 2026-10-12: `demo-p2-visit-1` (demo-enc-02) and `demo-p6-visit-1` (demo-enc-06/07) were
    admitted. They were DELETED from the corpus -- not re-topic'd: an undeclared topic refuses the whole
    persona (both demo-source-06 lenses), and the declared non-purpose topic would be a silent drop.
    """
    cuts = _served_demo_cuts(tmp_path)
    assert len(cuts) == 10, f"the served corpus has {len(cuts)} encounters, not 10"
    admitted = {encounter_id: [r.record_id for r in scoped.records if r.record_type == source.APPOINTMENT]
                for encounter_id, scoped in cuts.items()}
    assert not any(admitted.values()), f"contact records admitted: {admitted}"


def test_an_appointment_stays_purpose_filtered_and_an_unknown_topic_stays_loud():
    """Synthetic: the loud path is covered without touching the demo corpus."""
    from datetime import datetime, timezone
    import pytest
    from app import policy
    from app.upm import Persona, Record
    assert source.APPOINTMENT in policy.PURPOSE_FILTERED_TYPES, \
        "removing it turns a loud refusal into a silent drop"
    persona = Persona(persona_id="p", label="p", records=[Record.model_construct(
        record_id="r", source="synthetic", record_type=source.APPOINTMENT, authored_by_role="sairaanhoitaja",
        recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc), topic="contact_unregistered",
        sensitivity_tag="none", consent_scope="treatment", payload={"teksti": "x"})])
    with pytest.raises(policy.UnknownVocabularyError):
        cut(persona, "sairaanhoitaja", "functioning_service_needs")


def test_the_flagship_lens_contrast_differs_by_exactly_demo_p6_3(tmp_path: Path):
    """Beat 1 of the flagship demo (plan item A), and the guard against the delivery re-collapsing it.

    Same person, same purpose, two roles: the physiotherapist's permitted set is the nurse's plus exactly
    `demo-p6-3` (topic toimeentulo, excluded by the nurse's purpose rule). This is a PERMISSION difference,
    not a relevance one.
    """
    cuts = _served_demo_cuts(tmp_path)
    nurse = set(cuts["demo-enc-06-functioning-nurse"].ids)
    therapist = set(cuts["demo-enc-07-functioning-therapist"].ids)
    assert "demo-p6-3" in therapist and "demo-p6-3" not in nurse
    assert nurse == {"demo-p6-0", "demo-p6-1", "demo-p6-2", "demo-p6-4", "demo-p6-mittaus-1"}
    assert therapist == nurse | {"demo-p6-3"}
    assert (len(nurse), len(therapist)) == (5, 6)
    assert sorted(nurse ^ therapist) == ["demo-p6-3"]


# The one rule, in two strengths: `SURFACE` is the name of a channel (a route, a wire field), and
# `UI_SURFACE` additionally catches the plural `messages`/`notifications` that only make sense as copy.
# Both lived inline and separately before, so the same prohibition could be loosened in one place and not
# the other. `UI_SURFACE` is the wider of the two, so the UI scan subsumes the route/field check.
SURFACE = re.compile(r"viest(?:i|it)|messages?|messaging|notifications?|notify|ilmoit(?:us|ukset)", re.IGNORECASE)
UI_SURFACE = re.compile(r"viest(?:i|it)|messaging|messages|notifications?|notify|ilmoit(?:us|ukset)", re.IGNORECASE)
assert not SURFACE.search("Oma työlista") and SURFACE.search("messages"), \
    "the surface pattern is non-vacuous in both directions"


def ui_source_files() -> list[Path]:
    """Every file under `frontend/src` that renders or feeds user-visible text.

    Derived from the tree rather than listed by hand. The scan used to name exactly three files, so a new
    module was **outside every static check that reads the UI's words** until someone remembered to add it —
    a new component could have rendered the Finnish word *ilmoitus* and no gate would have failed. The set
    is the point, not that it grows: deriving it keeps the current files covered and lets a new one be
    covered on the commit that adds it. Test files are excluded on purpose — their fixtures deliberately
    contain negative strings, and the probes below are where a planted violation belongs.
    """
    found = sorted(
        path for pattern in ("*.jsx", "*.mjs")
        for path in (ROOT / "frontend/src").glob(pattern)
        if not path.name.endswith(".test.mjs")
    )
    # Anti-vacuity: a collapsed or wrong-rooted sweep would make the scan below pass by reading nothing.
    assert len(found) >= 4, f"the UI sweep found {len(found)} files — a collapsed sweep would pass vacuously"
    names = {path.name for path in found}
    assert {"App.jsx", "ChallengeDemo.jsx", "challenge-demo.mjs"} <= names, f"the UI sweep lost a file: {names}"
    return found


def test_no_messaging_or_notification_surface_can_carry_kirjaus_content():
    """The corpus says verbatim: "Viesteihin ei saa kirjoittaa potilaan hoitoon liittyviä tietoja. Viestiä ei koske lokitus eikä käyttöoikeudet."

    A channel inside the same product is outside logging and access rights, so it must not carry Kirjaus content.
    """
    doc = test_no_messaging_or_notification_surface_can_carry_kirjaus_content.__doc__
    quote = '"Viesteihin ei saa kirjoittaa potilaan hoitoon liittyviä tietoja. Viestiä ei koske lokitus eikä käyttöoikeudet."'
    assert doc is not None and quote in doc
    assert not any(SURFACE.search(getattr(route, "path", "")) for route in main.app.routes)
    fields = set(Picture.model_fields) | set(RecordView.model_fields)
    assert not any(SURFACE.search(name) for name in fields)
    scanned = ui_source_files()
    for path in scanned:
        text = path.read_text(encoding="utf-8")
        assert not UI_SURFACE.search(text), f"messaging/notification surface found in {path.relative_to(ROOT)}"
    # Each clause of the pattern is probed with a word it must reject, so a loosened regex cannot pass by
    # matching nothing. This is the non-vacuity half of the same scan the loop above runs for real.
    for planted in ("Viestit", "messages", "Ilmoitus", "notify", "Notifications"):
        assert UI_SURFACE.search(planted), f"the UI surface pattern must reject {planted!r}"
    assert not UI_SURFACE.search("Oma työlista · Tilannekuva · Tekniset tiedot"), \
        "the UI surface pattern accepts ordinary navigation copy — it is about meaning, not length"


def test_a_new_ui_file_cannot_escape_the_messaging_scan():
    """The whole point of deriving the file set: a file that does not exist yet is still covered.

    The scan reads what `ui_source_files()` finds, so this plants the violation in a temporary file inside
    that tree and asserts the real regex reports it — which is only true if the sweep is derived.
    """
    planted = ROOT / "frontend/src/__probe_guardrail.mjs"
    assert planted not in ui_source_files(), "the probe file must start outside the scanned set"
    try:
        planted.write_text("export const viesti = 'ilmoitus';\n", encoding="utf-8")
        assert planted in ui_source_files(), "a newly added UI file is inside the derived sweep"
        offenders = [path for path in ui_source_files()
                     if UI_SURFACE.search(path.read_text(encoding="utf-8"))]
        assert [path.name for path in offenders] == [planted.name], \
            "a banned surface planted in a NEW file is found by the scan, not only in the three original files"
    finally:
        planted.unlink(missing_ok=True)
    assert not planted.exists() and planted not in ui_source_files(), "the probe cleans up after itself"


def test_encounter_policy_cut_is_owned_by_one_seam():
    for path in (ROOT / "backend/app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if path.name != "policy.py":
            policy_imports = [node for node in ast.walk(tree)
                              if isinstance(node, ast.ImportFrom) and node.module in {"app.policy", "policy"}]
            scoped_names = {alias.asname or alias.name for node in policy_imports for alias in node.names
                            if alias.name == "Scoped"}
            assert not any(alias.name == "cut" for node in policy_imports for alias in node.names), (
                f"direct policy.cut import outside the seam: {path.relative_to(ROOT)}"
            )
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
                    assert name not in {"cut", "Scoped"} | scoped_names, (
                        f"direct policy cut/Scoped construction outside policy.py: {path.relative_to(ROOT)}"
                    )


def test_every_narration_path_is_downstream_of_policy_cut():
    call_sites = {}
    for path in (ROOT / "backend/app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
                if name in {"narrate", "generate_fresh_narration", "verify_narration"}:
                    call_sites.setdefault(name, set()).add(path.name)
    assert call_sites["narrate"] == {"pipeline.py"}
    assert call_sites["generate_fresh_narration"] == {"narration.py"}
    # Two surfaces call the verifier now. The rule this test exists for is *downstream of the cut*, not "one
    # file", so the second surface is held to the same rule rather than exempted from the letter of it: it
    # receives a scope that was already cut and never builds one itself.
    # Three surfaces call the verifier: narration, the pre-fill (`prefill.py`, every proposed form answer)
    # and the assistant (`assistant.py`, every sentence the agent writes). Each is held to the same rule: it
    # receives a scope that was already cut and never builds one itself.
    assert call_sites["verify_narration"] == {"narration.py", "prefill.py", "assistant.py"}
    forbidden = {"cut", "cut_for_encounter", "load_personas", "load_decision_eval_personas"}
    for downstream in ("chat.py", "prefill.py", "assistant.py"):
        tree = ast.parse((ROOT / "backend/app" / downstream).read_text(encoding="utf-8"))
        built = [node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
                 for node in ast.walk(tree) if isinstance(node, ast.Call)]
        assert not (set(built) & forbidden), f"{downstream} must receive an already-cut scope rather than build one"

    pipeline_tree = ast.parse(inspect.getsource(_build_resolved_picture))
    encounter_cuts = [node for node in ast.walk(pipeline_tree) if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Name) and node.func.id == "cut_for_encounter"]
    narrate_calls = [node for node in ast.walk(pipeline_tree) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name) and node.func.id == "narrate"]
    assert len(encounter_cuts) == len(narrate_calls) == 1
    assert encounter_cuts[0].lineno < narrate_calls[0].lineno
    assert isinstance(narrate_calls[0].args[0], ast.Name)
    assert narrate_calls[0].args[0].id == "narration_scope"

    cut_tree = ast.parse(inspect.getsource(cut_for_encounter))
    cut_calls = [node for node in ast.walk(cut_tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "cut"]
    assert len(cut_calls) == 1
    assert {keyword.arg for keyword in cut_calls[0].keywords} >= {
        "consent_holders", "inherited_restricted_case_ids", "kypsyysarvio", "age_at_encounter"
    }
