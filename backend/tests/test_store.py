"""Persistence for the Store seam (ticket 08). No app needed."""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from azure.core.exceptions import ResourceExistsError

from app.store import (
    KIND_CHAT_REVIEW,
    KIND_CHAT_TURN,
    KIND_DECISION_CLAIM,
    KIND_PROPOSAL_FEEDBACK,
    DecisionConflict,
    Store,
    _decision_claim_key,
    _record_kind,
    get_store,
)


def test_feedback_round_trips_through_a_restart(tmp_path: Path):
    p = tmp_path / "feedback.jsonl"
    s = Store([], path=p)
    s.add_feedback({"persona_id": "p1", "role": "laakari", "action": "accept", "note": None})
    s.add_feedback({"persona_id": "p1", "role": "sairaanhoitaja", "action": "modify", "note": "käyttäytyy"})

    # Second process: same path, empty in-memory state — the restart simulation.
    s2 = Store([], path=p)
    reloaded = s2.feedback("p1")
    assert [(e["role"], e["action"], e["note"]) for e in reloaded] == [
        ("laakari", "accept", None),
        ("sairaanhoitaja", "modify", "käyttäytyy"),
    ]
    original = s.feedback("p1")
    assert len(reloaded) == len(original) == 2
    for a, b in zip(original, reloaded):
        assert a["recorded_at"] == b["recorded_at"]
        assert b["recorded_at"].tzinfo is not None


def test_none_path_stays_in_memory():
    s = Store([])
    s.add_feedback({"persona_id": "p1", "role": "laakari", "action": "accept", "note": None})
    assert s.feedback("p1")[0]["action"] == "accept"
    assert Store([]).feedback("p1") == []


def test_torn_last_line_is_quarantined_not_lost_silently(tmp_path: Path, caplog):
    p = tmp_path / "feedback.jsonl"
    Store([], path=p).add_feedback({"persona_id": "p1", "role": "laakari", "action": "accept", "note": None})
    with p.open("a", encoding="utf-8") as f:
        f.write('{"persona_id": "p1", "role": "laakari", "action": "acc')
    assert [e["action"] for e in Store([], path=p).feedback("p1")] == ["accept"]
    # The torn line is preserved in a sidecar and removed from the main file, so a
    # later restart sees a clean file and the count cannot drift between restarts.
    sidecar = p.with_suffix(p.suffix + ".corrupt")
    assert sidecar.exists() and "\"acc" in sidecar.read_text(encoding="utf-8")
    assert "feedback.jsonl" not in p.read_text(encoding="utf-8")
    assert len(p.read_text(encoding="utf-8").splitlines()) == 1
    assert "quarantined 1 unreadable feedback line" in caplog.text


def test_a_deeply_nested_feedback_line_is_quarantined_and_the_person_still_reads(tmp_path: Path, caplog):
    """A deeply nested line raises `RecursionError`, not `ValueError`, so it used to escape the per-line
    handler and make EVERY later read of that file raise. It is a torn line like any other."""
    p = tmp_path / "feedback.jsonl"
    Store([], path=p).add_feedback({"persona_id": "p1", "action": "accept"}, event_id="good")
    deep = '{"persona_id": "p1", "action": "deep", "payload": ' + "[" * 20000 + "]" * 20000 + "}"
    with p.open("a", encoding="utf-8") as f:
        f.write(deep + "\n")
    assert [e["action"] for e in Store([], path=p).feedback("p1")] == ["accept"]
    sidecar = p.with_suffix(p.suffix + ".corrupt")
    assert sidecar.exists() and "deep" in sidecar.read_text(encoding="utf-8")
    assert len(p.read_text(encoding="utf-8").splitlines()) == 1
    # And a read after the repair still works, which is the property the old crash destroyed.
    assert [e["action"] for e in Store([], path=p).feedback("p1")] == ["accept"]


def test_a_deeply_nested_blob_is_refused_rather_than_crashing_the_read():
    blobs = MemoryContainer()
    blobs.blobs["p1__deep.json"] = (
        '{"event_id": "deep", "persona_id": "p1", "action": "x", "recorded_at": "2026-01-01T00:00:00+00:00", '
        '"payload": ' + "[" * 20000 + "]" * 20000 + "}").encode()
    store = Store([], blob_container=blobs)
    with pytest.raises(ValueError, match="invalid stored feedback"):
        store.feedback("p1")


def test_two_instances_writing_the_same_event_id_store_exactly_one_record(tmp_path: Path):
    """The old per-instance lock allowed a check-then-append race across `Store`
    instances on one path; the shared path lock and id set make it exactly-once."""
    import threading

    path = tmp_path / "feedback.jsonl"
    first, second = Store([], path=path), Store([], path=path)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def write(store: Store) -> None:
        barrier.wait()
        try:
            store.add_feedback({"persona_id": "p1", "action": "accept"}, event_id="same-event")
        except BaseException as exc:  # noqa: BLE001 -- surfaced in the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(store,)) for store in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    assert len(Store([], path=path).feedback("p1")) == 1


def test_a_write_sees_an_event_appended_by_another_process(tmp_path: Path):
    """H6: the JSONL id set is per process, so the write decision must re-read the file.

    `registry.events` only knows what this process has appended. A retry landing on another replica — a
    second process on the same file, simulated here by appending behind this process's back — used to append
    the same event a second time, because the cached set did not contain it. The old version of this test
    asserted that the write path did *not* re-read the file, which is exactly the assumption that made the
    second append possible.
    """
    path = tmp_path / "feedback.jsonl"
    store = Store([], path=path)
    store.add_feedback({"persona_id": "p1", "action": "accept"}, event_id="seen-by-this-process")
    # A second process appended an event this process has never seen, without touching its cache.
    foreign = {"kind": "proposal_feedback", "event_id": "written-elsewhere", "persona_id": "p1",
               "action": "dismiss", "recorded_at": datetime.now(timezone.utc).isoformat()}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(foreign) + "\n")
    store.add_feedback({"persona_id": "p1", "action": "dismiss"}, event_id="written-elsewhere")
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert sum(1 for line in lines if "written-elsewhere" in line) == 1


def test_missing_file_starts_empty(tmp_path: Path):
    assert Store([], path=tmp_path / "does-not-exist.jsonl").feedback("p1") == []


def test_failed_write_does_not_publish_feedback(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", fail)
    s = Store([], path=tmp_path / "feedback.jsonl")
    with pytest.raises(OSError, match="disk full"):
        s.add_feedback({"persona_id": "p1", "action": "accept"})
    assert s.feedback("p1") == []


class MemoryBlob:
    def __init__(self, container, name):
        self.container = container
        self.name = name

    def upload_blob(self, data, *, overwrite, content_settings):
        assert overwrite is False
        assert content_settings.content_type == "application/json"
        if self.name in self.container.blobs:
            raise ResourceExistsError("already exists")
        if self.container.fail_write:
            raise OSError("offline")
        self.container.blobs[self.name] = data

    def exists(self):
        return self.name in self.container.blobs

    def delete_blob(self):
        self.container.blobs.pop(self.name, None)

    def download_blob(self):
        self.container.downloaded.append(self.name)
        return SimpleNamespace(readall=lambda: self.container.blobs[self.name])


class MemoryContainer:
    def __init__(self):
        self.blobs = {}
        self.fail_write = False
        self.fail_list = False
        self.list_calls = 0
        self.listed_prefixes = []
        self.downloaded = []

    def list_blobs(self, name_starts_with=None):
        self.list_calls += 1
        self.listed_prefixes.append(name_starts_with)
        if self.fail_list:
            raise OSError("container listing unavailable")
        return [SimpleNamespace(name=name) for name in self.blobs
                if name_starts_with is None or name.startswith(name_starts_with)]

    def get_blob_client(self, name):
        return MemoryBlob(self, name)


@pytest.fixture(params=("jsonl", "blob"), ids=("jsonl", "blob"))
def store_factory(request, tmp_path):
    if request.param == "jsonl":
        path = tmp_path / "feedback.jsonl"
        return lambda: Store([], path=path)
    container = MemoryContainer()
    return lambda: Store([], blob_container=container)


def test_torn_multibyte_utf8_line_is_quarantined_too(tmp_path: Path, caplog):
    p = tmp_path / "feedback.jsonl"
    Store([], path=p).add_feedback({"persona_id": "p1", "action": "accept"}, event_id="good")
    with p.open("ab") as f:
        f.write('{"persona_id": "p1", "action": "hyv'.encode("utf-8") + b"\xc3")  # torn multibyte
    assert [e["action"] for e in Store([], path=p).feedback("p1")] == ["accept"]
    sidecar = p.with_suffix(p.suffix + ".corrupt")
    assert sidecar.exists() and b"\xc3" in sidecar.read_bytes()
    assert len(p.read_text(encoding="utf-8").splitlines()) == 1
    assert "quarantined 1 unreadable feedback line" in caplog.text


def test_blob_store_bootstrap_and_write_ack_do_not_list_container():
    blobs = MemoryContainer()
    blobs.fail_list = True
    store = Store([], blob_container=blobs)
    assert blobs.list_calls == 0
    assert store.personas() == []
    store.add_feedback({"persona_id": "p1", "action": "accept"}, event_id="direct-ack")
    stored = store.feedback_event("direct-ack")
    assert stored is not None and stored["event_id"] == "direct-ack"
    assert blobs.list_calls == 0


def test_feedback_idempotency_and_lookup_rules_are_shared(store_factory):
    event = {"persona_id": "p1", "role": "laakari", "action": "accept", "note": "ok"}
    second_event = {**event, "action": "modify"}
    first = store_factory()
    assert first.add_feedback(event, event_id="request-123") == 1
    assert first.add_feedback(second_event, event_id="request-456") == 2
    assert first.add_feedback(event, event_id="request-123") == 2
    written = first.feedback_event("request-123")
    assert written is not None and written["recorded_at"].tzinfo is not None

    restarted = store_factory()
    restarted.add_feedback(event, event_id="request-123")
    assert restarted.feedback_event("request-123") == written
    before = restarted.feedback("p1")
    assert len(before) == 2
    assert restarted.feedback("other") == []

    with pytest.raises(ValueError, match="different feedback"):
        restarted.add_feedback({**event, "action": "dismiss"}, event_id="request-123")
    assert restarted.feedback("p1") == before


def test_one_decision_per_subject_per_actor_on_both_backends(store_factory):
    """H2/B6: the decision slot is per actor.

    A second, different decision for one subject is refused *for the same actor*; a retry under the same id
    is not; a different actor gets their own slot on the same proposal, and the stored feedback names who
    decided. The old expectation — one slot shared by every actor — was wrong: several professional roles
    legitimately see the same proposal, and the first click consumed the only decision anyone could record.
    """
    store = store_factory()
    feedback = {"persona_id": "p1", "encounter_id": "e1", "proposal_id": "pr1", "actor_id": "a1",
                "action": "accept"}
    store.add_feedback(feedback, event_id="event-1")
    store.add_feedback(feedback, event_id="event-1")           # same id retry is idempotent
    with pytest.raises(DecisionConflict, match="proposal already has a decision"):
        store.add_feedback({**feedback, "action": "dismiss"}, event_id="event-2")
    store.add_feedback({**feedback, "actor_id": "a2", "action": "dismiss"}, event_id="event-3")
    rows = store.feedback("p1")
    assert sorted(row["event_id"] for row in rows) == ["event-1", "event-3"]
    assert {row["event_id"]: row["actor_id"] for row in rows} == {"event-1": "a1", "event-3": "a2"}

    review = {"persona_id": "p1", "turn_id": "t1", "suggestion_id": "s0", "action": "accept"}
    store.add_chat_review(review, "review-1")
    store.add_chat_review(review, "review-1")                  # same id retry is idempotent
    with pytest.raises(DecisionConflict, match="suggestion already has a decision"):
        store.add_chat_review({**review, "action": "dismiss"}, "review-2")
    assert [row["review_id"] for row in store.chat_reviews("p1")] == ["review-1"]


def test_a_claim_is_never_read_back_as_a_record_on_either_backend(store_factory):
    """Finding 1: a caller-supplied `event_id` can spell another actor's decision-claim key.

    The claim key is deterministic from `(encounter, proposal, actor)`, and `event_id` is a caller-supplied
    value, so a caller can choose exactly that string. The old `read_by_id` tried the ownerless name first
    and returned the claim, so a successful 200 acknowledgement carried the *other* actor's `recorded_at`
    and the caller's exact retry then answered 409. A claim is a uniqueness marker, not a record read: no
    record read may ever return one.
    """
    store = store_factory()
    store.add_feedback({"persona_id": "p1", "encounter_id": "e1", "proposal_id": "pr1",
                        "actor_id": "a1", "action": "accept"}, event_id="a-decision")
    claim_key = _decision_claim_key("proposal\0e1\0pr1\0a1")

    # The claim exists, but it is not a record: an unscoped by-id read must not return it.
    assert store.feedback_event(claim_key) is None

    mine = {"persona_id": "p1", "encounter_id": "e1", "proposal_id": "pr1", "actor_id": "b2",
            "action": "dismiss"}
    store.add_feedback(mine, event_id=claim_key)
    acknowledged = store.feedback_acknowledgement(claim_key, "p1", "b2", "e1")
    assert acknowledged is not None, "the caller's own record was not acknowledged"
    assert acknowledged["actor_id"] == "b2"
    assert _record_kind(acknowledged) == KIND_PROPOSAL_FEEDBACK
    # A same-id, same-content retry by the same actor stays idempotent (a 200, not a 409).
    store.add_feedback(mine, event_id=claim_key)
    assert store.feedback_acknowledgement(claim_key, "p1", "b2", "e1") == acknowledged
    # The claim never appears in the caller's own decisions.
    assert sorted(row["event_id"] for row in store.feedback("p1")) == ["a-decision", claim_key]
    assert all(_record_kind(row) == KIND_PROPOSAL_FEEDBACK for row in store.feedback("p1"))


def test_a_conflicting_event_id_costs_the_caller_nothing_on_either_backend(store_factory):
    """Finding 2: the event identity is checked before the decision slot is reserved.

    The old order claimed the slot first, so a caller who reused an id already written with different
    content had their one slot consumed by a write that was then refused: their next attempt with a fresh
    id answered "already has a decision" although they had recorded nothing. A conflicting id must record
    nothing and cost nothing.
    """
    store = store_factory()
    mine = {"persona_id": "p1", "encounter_id": "e1", "proposal_id": "pr1", "actor_id": "b2",
            "action": "accept"}
    store.add_feedback({**mine, "actor_id": "a1"}, event_id="a-decision")

    with pytest.raises(ValueError, match="different feedback"):
        store.add_feedback({**mine, "action": "dismiss"}, event_id="a-decision")
    # The conflicting attempt recorded nothing and left the slot free: the fresh id still succeeds.
    store.add_feedback(mine, event_id="b-fresh")
    assert sorted(row["event_id"] for row in store.feedback("p1")) == ["a-decision", "b-fresh"]
    assert {row["event_id"]: row["actor_id"] for row in store.feedback("p1")} == {
        "a-decision": "a1", "b-fresh": "b2"}


def test_blob_acknowledgement_refuses_a_record_of_another_kind():
    """Blob-specific: `kind` is part of the verification, not decorative.

    A record can sit at the caller's exact owner-scoped name and still not be an acknowledgement of a
    feedback write (the chat and feedback stores share the container). The kind check refuses it.
    """
    blobs = MemoryContainer()
    blobs.blobs["p1__ev.json"] = json.dumps(
        {"event_id": "ev", "persona_id": "p1", "kind": "chat_turn", "turn_id": "ev",
         "recorded_at": "2026-01-01T00:00:00+00:00"}).encode()
    store = Store([], blob_container=blobs)
    assert store.feedback_acknowledgement("ev", "p1", "a1", "e1") is None
    assert store.feedback_event("ev") is None


def test_feedback_acknowledgement_verifies_kind_actor_and_encounter_on_both_backends(store_factory):
    """The acknowledgement must VERIFY what it returns, not just find a record at a key.

    A record that is not proposal feedback, not this actor's, or not this encounter's is not an
    acknowledgement of this call, and returning any of them would be a disclosure (at minimum of another
    actor's `recorded_at`). Every mismatch falls through to None, which the route turns into a non-ack.
    """
    store = store_factory()
    store.add_feedback({"persona_id": "p1", "encounter_id": "e1", "proposal_id": "pr1",
                        "actor_id": "a1", "action": "accept"}, event_id="ev")
    assert store.feedback_acknowledgement("ev", "p1", "a1", "e1") is not None
    assert store.feedback_acknowledgement("ev", "p1", "a2", "e1") is None       # another actor
    assert store.feedback_acknowledgement("ev", "p1", "a1", "e2") is None       # another encounter
    assert store.feedback_acknowledgement("ev", "p2", "a1", "e1") is None       # another persona


def test_blob_decision_claims_live_in_their_own_namespace():
    """Finding 1's storage half, pinned on the Blob backend: claims and records never share a name.

    A record's name is `<persona>__<event_id>.json` and an event id cannot contain `/`, so a claim under
    `claims/` is unreachable from any caller-supplied `event_id` — the disjoint key space is structural,
    not a promise that the caller will not guess a claim key.
    """
    blobs = MemoryContainer()
    store = Store([], blob_container=blobs)
    store.add_feedback({"persona_id": "p1", "encounter_id": "e1", "proposal_id": "pr1",
                        "actor_id": "a1", "action": "accept"}, event_id="a-decision")
    claim_key = _decision_claim_key("proposal\0e1\0pr1\0a1")
    assert [name for name in blobs.blobs if claim_key in name] == [f"claims/{claim_key}.json"]
    assert "p1__a-decision.json" in blobs.blobs


def test_feedback_never_returns_chat_records(store_factory):
    """B13: the record kind is explicit, so a feedback read cannot return a turn or a review."""
    store = store_factory()
    store.add_feedback({"persona_id": "p1", "encounter_id": "e1", "proposal_id": "pr1"},
                       event_id="event-1")
    store.add_chat_turn({"persona_id": "p1", "turn_id": "t1"}, "t1", content={"turn_id": "t1"})
    store.add_chat_review({"persona_id": "p1", "turn_id": "t1", "suggestion_id": "s0"}, "review-1")
    rows = store.feedback("p1")
    assert [row["event_id"] for row in rows] == ["event-1"]
    assert all(row["kind"] == "proposal_feedback" for row in rows)
    assert [row["event_id"] for row in store.chat_turns("p1")] == ["turn-t1"]
    assert [row["event_id"] for row in store.chat_reviews("p1")] == ["review-review-1"]


def test_next_chat_seq_does_not_enumerate_per_call(store_factory, monkeypatch):
    """B14: the next sequence is derived once, then counted in memory, never re-listed per ask."""
    store = store_factory()
    store.add_chat_turn({"persona_id": "p1", "turn_id": "t1"}, "t1", content={"turn_id": "t1"})
    assert store.next_chat_seq("p1") == 2

    def boom(*_args, **_kwargs):
        raise AssertionError("the store enumerated to derive the next sequence")

    monkeypatch.setattr(store._chat_storage, "read_all", boom)
    monkeypatch.setattr(store._chat_storage, "enumerate_by_owner", boom)
    assert store.next_chat_seq("p1") == 3
    assert store.next_chat_seq("p1") == 4


def test_jsonl_feedback_count_is_restored_after_restart(tmp_path: Path):
    path = tmp_path / "feedback.jsonl"
    first = {"persona_id": "p1", "action": "accept"}
    second = {"persona_id": "p1", "action": "modify"}
    third = {"persona_id": "p1", "action": "dismiss"}
    store = Store([], path=path)
    assert store.add_feedback(first, event_id="first") == 1
    assert store.add_feedback(second, event_id="second") == 2

    restarted = Store([], path=path)
    assert restarted.add_feedback(first, event_id="first") == 2
    assert restarted.add_feedback(second, event_id="second") == 2
    assert restarted.add_feedback(third, event_id="third") == 3


def test_blob_feedback_orders_by_server_time_then_event_id():
    blobs = MemoryContainer()
    for event_id in ("z", "a"):
        blobs.blobs[f"{event_id}.json"] = json.dumps(
            {"event_id": event_id, "persona_id": "p1", "action": event_id,
             "recorded_at": "2026-01-01T00:00:00+00:00"}
        ).encode()
    assert [event["event_id"] for event in Store([], blob_container=blobs).feedback("p1")] == ["a", "z"]


def test_blob_write_failure_is_not_acknowledged_or_cached():
    blobs = MemoryContainer()
    blobs.fail_write = True
    store = Store([], blob_container=blobs)
    with pytest.raises(OSError, match="offline"):
        store.add_feedback({"persona_id": "p1", "action": "accept"}, event_id="request-123")
    assert store.feedback("p1") == []


def test_blob_event_id_is_a_single_safe_name():
    store = Store([], blob_container=MemoryContainer())
    with pytest.raises(ValueError, match="event_id"):
        store.add_feedback({"persona_id": "p1"}, event_id="../other")


def test_hosted_store_fails_closed_without_blob_backend(monkeypatch):
    get_store.cache_clear()
    monkeypatch.setenv("LUVN_HOSTED", "1")
    monkeypatch.delenv("LUVN_STORE_BACKEND", raising=False)
    with pytest.raises(RuntimeError, match="must use.*blob"):
        get_store()
    get_store.cache_clear()


def test_blob_store_uses_assigned_managed_identity(monkeypatch):
    import app.store as store_module

    class Credential:
        def __init__(self, *, client_id):
            assert client_id == "assigned-client"

    class Service:
        def __init__(self, *, account_url, credential):
            assert account_url == "https://example.blob.core.windows.net/"
            assert isinstance(credential, Credential)

        def get_container_client(self, name):
            assert name == "feedback"
            return MemoryContainer()

    get_store.cache_clear()
    monkeypatch.setenv("LUVN_HOSTED", "1")
    monkeypatch.setenv("LUVN_STORE_BACKEND", "blob")
    monkeypatch.setenv("LUVN_STORAGE_ACCOUNT_URL", "https://example.blob.core.windows.net/")
    monkeypatch.setenv("LUVN_FEEDBACK_CONTAINER", "feedback")
    monkeypatch.setenv("LUVN_STORAGE_CLIENT_ID", "assigned-client")
    monkeypatch.setattr(store_module, "ManagedIdentityCredential", Credential)
    monkeypatch.setattr(store_module, "BlobServiceClient", Service)
    assert get_store().feedback("p1") == []
    get_store.cache_clear()


# --- blob feedback is owner-scoped, so one persona's read does not drain the container -------------------

def test_a_personas_blob_read_does_not_download_another_personas_records():
    """L4: the read used to list and DOWNLOAD every blob and filter afterwards.

    The names now carry the owner (`<persona>__<event>.json`), so the read scans only that persona's names.
    Asserted on the bytes the process actually pulled, not only on the response: the finding is about
    other personas' data passing through the process.
    """
    blobs = MemoryContainer()
    store = Store([], blob_container=blobs)
    store.add_feedback({"persona_id": "p1", "action": "accept"}, event_id="e-p1")
    store.add_chat_turn({"persona_id": "p1", "turn_id": "t1"}, "t1", content={"turn_id": "t1"})
    store.add_feedback({"persona_id": "p2", "action": "dismiss"}, event_id="e-p2")
    store.add_chat_turn({"persona_id": "p2", "turn_id": "t2"}, "t2", content={"turn_id": "t2"})

    blobs.downloaded.clear()
    assert [turn["turn_id"] for turn in store.chat_turns("p1")] == ["t1"]

    downloaded = set(blobs.downloaded)
    assert downloaded, "the read downloaded something"
    assert all(name.startswith("p1__") or "__" not in name for name in downloaded), downloaded
    assert not any("p2__" in name for name in downloaded), (
        f"persona p2's blobs must not be downloaded to answer p1: {sorted(downloaded)}")


def test_a_legacy_flat_blob_is_still_readable_and_is_not_fetched_per_call():
    """Records written before the owner segment existed keep working, without a listing per call."""
    blobs = MemoryContainer()
    blobs.blobs["legacy-1.json"] = json.dumps(
        {"event_id": "legacy-1", "persona_id": "p1", "action": "accept", "kind": "proposal_feedback",
         "recorded_at": "2026-01-01T00:00:00+00:00"}).encode()
    store = Store([], blob_container=blobs)
    assert [entry["event_id"] for entry in store.feedback("p1")] == ["legacy-1"]

    listings = blobs.list_calls
    assert [entry["event_id"] for entry in store.feedback("p1")] == ["legacy-1"]
    assert blobs.list_calls == listings, "the second read re-used the cached listing"
    # A by-id read of a record this process did not write finds it through the cached listing too.
    assert store.feedback_event("legacy-1") is not None


def test_a_read_write_read_on_blob_sees_the_write_and_leaves_another_personas_cache_alone():
    """Finding 1: a successful write evicts the listing it changed, and only that listing.

    `create_if_absent` updated `_owner_hints` (which serves `read_by_id`) but never `_names_cache`, so
    `enumerate_by_owner` -- and therefore `/api/encounters/{id}/decisions` -- answered from the listing
    taken BEFORE the write for up to `_index_ttl` seconds. On this backend the presenter clicked *accept*,
    reloaded, and saw nothing decided. The eviction must be targeted: a read for another persona was
    cached too, a write under `p1` cannot change it, and re-listing it per write would be the cost the
    cache exists to avoid.
    """
    blobs = MemoryContainer()
    store = Store([], blob_container=blobs)
    store.add_feedback({"persona_id": "p2", "action": "dismiss"}, event_id="p2-event")

    assert store.proposal_decisions("p1", "e1", "a1") == []
    assert store.feedback("p2")[0]["event_id"] == "p2-event"
    before = list(blobs.listed_prefixes)

    store.add_feedback({"persona_id": "p1", "encounter_id": "e1", "proposal_id": "pr1", "actor_id": "a1",
                        "action": "accept"}, event_id="p1-event")

    # The very first read after the write sees it -- no sleep, no TTL expiry.
    assert [row["event_id"] for row in store.proposal_decisions("p1", "e1", "a1")] == ["p1-event"]
    assert blobs.listed_prefixes == before + ["p1__"], (
        "exactly the written persona's prefix was re-listed, not the whole container")
    # p2's cached listing survived the write: its read neither re-lists nor returns the new record.
    assert [row["event_id"] for row in store.feedback("p2")] == ["p2-event"]
    assert blobs.listed_prefixes == before + ["p1__"], "another persona's cached listing was invalidated"


# --- item 1: a pre-namespace claim is still consulted, but only when it is really a claim -----------------

@pytest.fixture(params=("jsonl", "blob"), ids=("jsonl", "blob"))
def legacy_store(request, tmp_path):
    """A store factory plus writers that plant a record the way the PREVIOUS revision named it.

    Before claims moved into `claims/` and before chat records got a key namespace, an ownerless record was
    stored at `<event_id>.json` (Blob) or as a row keyed by `event_id` (JSONL). The plant helpers write
    exactly that legacy shape on whichever backend the test is running, so the legacy-read paths are
    exercised on both backends rather than assumed to work on the one the unit tests happen to use.

    `plant` writes into the feedback storage (where an old proposal decision/record lived) and
    `plant(..., chat=True)` into the chat storage (where an old suggestion decision/chat turn lived). On
    Blob both were ownerless names in the one container, so the two coincide there.
    """
    if request.param == "jsonl":
        path = tmp_path / "feedback.jsonl"
        chat_path = tmp_path / "feedback-chat.jsonl"

        def plant(entry: dict, *, chat: bool = False) -> None:
            with (chat_path if chat else path).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry) + "\n")

        return (lambda: Store([], path=path)), plant

    container = MemoryContainer()

    def plant(entry: dict, *, chat: bool = False) -> None:
        assert "/" not in entry["event_id"], "a legacy name has no namespace segment"
        container.blobs[f"{entry['event_id']}.json"] = json.dumps(entry).encode()

    return (lambda: Store([], blob_container=container)), plant


def test_a_legacy_ownerless_claim_still_closes_the_decision_slot_on_either_backend(legacy_store):
    """Item 1: a decision claimed before the namespace change must still refuse a second, different one.

    The old code stored the deterministic claim at the ownerless `<claim_key>.json`. `_claim_decision` now
    reads only the new `claims/` namespace, so without a legacy read a proposal decided by an earlier
    revision accepted a second decision from the same actor: "one decision per (actor, encounter, proposal)"
    silently stopped holding for records written before the change.
    """
    factory, plant = legacy_store
    encounter_id, proposal_id, actor_id = "e1", "pr1", "a1"
    claim_key = _decision_claim_key(f"proposal\0{encounter_id}\0{proposal_id}\0{actor_id}")
    plant({"event_id": claim_key, "kind": KIND_DECISION_CLAIM, "decision_key": "legacy-decision",
           "recorded_at": "2026-01-01T00:00:00+00:00"})
    store = factory()
    feedback = {"persona_id": "p1", "encounter_id": encounter_id, "proposal_id": proposal_id,
                "actor_id": actor_id, "action": "accept"}

    with pytest.raises(DecisionConflict, match="proposal already has a decision"):
        store.add_feedback(feedback, event_id="a-second-decision")

    # The legacy claim is a uniqueness marker, never a record: no record read and no acknowledgement may
    # return it (its ownerless name is shared with legacy feedback, so this has to be verified by kind).
    assert store.feedback_event(claim_key) is None
    assert store.feedback_acknowledgement(claim_key, "p1", actor_id, encounter_id) is None
    assert store.feedback("p1") == []

    # A retry of the SAME decision (the id the legacy claim was taken under) is not a second decision: the
    # legacy check binds to the stored `decision_key` rather than refusing every later request outright.
    store.add_feedback(feedback, event_id="legacy-decision")
    assert store.feedback_event("legacy-decision") is not None
    assert store.feedback_acknowledgement("legacy-decision", "p1", actor_id, encounter_id) is not None


def test_a_legacy_suggestion_claim_still_closes_the_review_slot_on_either_backend(legacy_store):
    """Item 1, chat half: `_claim_decision` is shared, so a pre-namespace SUGGESTION claim counts too.

    The old code claimed a suggestion through the chat storage (`-chat` on JSONL, an ownerless name in the
    same container on Blob), so the legacy read has to consult that storage as well as the feedback one.
    """
    factory, plant = legacy_store
    turn_id, suggestion_id = "t1", "s0"
    claim_key = _decision_claim_key(f"suggestion\0{turn_id}\0{suggestion_id}")
    plant({"event_id": claim_key, "kind": KIND_DECISION_CLAIM, "decision_key": "legacy-review",
           "recorded_at": "2026-01-01T00:00:00+00:00"}, chat=True)
    store = factory()
    review = {"persona_id": "p1", "turn_id": turn_id, "suggestion_id": suggestion_id, "action": "accept"}

    with pytest.raises(DecisionConflict, match="suggestion already has a decision"):
        store.add_chat_review(review, "a-second-review")

    # A retry of the same suggestion decision, under the id the legacy claim was taken with, is not a second.
    store.add_chat_review(review, "legacy-review")
    stored = store.chat_review("legacy-review")
    assert stored is not None and stored["review_id"] == "legacy-review"


def test_a_legacy_feedback_record_at_the_claim_name_is_not_a_claim_on_either_backend(legacy_store):
    """Item 1: the legacy claim name is SHARED with legacy ownerless feedback, so the check must verify kind.

    `_decision_claim_key` is deterministic, so an ownerless feedback record can sit at exactly the name a
    legacy claim would occupy. Treating any record there as a claim would refuse this actor's FIRST decision
    on the proposal; returning it as one would be a record read leaking through the uniqueness path.
    """
    factory, plant = legacy_store
    encounter_id, proposal_id, actor_id = "e1", "pr1", "a1"
    claim_key = _decision_claim_key(f"proposal\0{encounter_id}\0{proposal_id}\0{actor_id}")
    plant({"event_id": claim_key, "kind": KIND_PROPOSAL_FEEDBACK, "persona_id": "p1", "action": "accept",
           "recorded_at": "2026-01-01T00:00:00+00:00"})
    store = factory()
    feedback = {"persona_id": "p1", "encounter_id": encounter_id, "proposal_id": proposal_id,
                "actor_id": actor_id, "action": "accept"}

    store.add_feedback(feedback, event_id="a-first-decision")

    # The planted record is readable as feedback, and it was never treated as this actor's claim.
    assert store.feedback_event(claim_key)["kind"] == KIND_PROPOSAL_FEEDBACK
    assert "a-first-decision" in [row["event_id"] for row in store.feedback("p1")]


# --- item 2: feedback and chat records no longer share one key space --------------------------------------

def test_a_feedback_id_that_spells_a_chat_key_sits_beside_it_on_either_backend(store_factory):
    """Item 2: a feedback `event_id` of `turn-<x>` no longer collides with a chat turn whose id is `<x>`.

    A feedback `event_id` of `turn-<x>` used to map to the same `<persona>__turn-<x>.json` name as the chat
    turn `<x>` (and `review-<x>` likewise). The second write was refused as a conflicting id, so the refusal
    was an oracle for whether the other kind's record existed. Each record kind now has its own key
    namespace, so the two ids are two names and neither write refuses the other.
    """
    store = store_factory()
    base = {"persona_id": "p1", "encounter_id": "e1", "actor_id": "a1", "action": "accept"}
    store.add_feedback({**base, "proposal_id": "pr-turn"}, event_id="turn-x")
    assert store.add_chat_turn({"persona_id": "p1", "turn_id": "x"}, "x", content={"turn_id": "x"}) is True
    store.add_feedback({**base, "proposal_id": "pr-review"}, event_id="review-x")
    store.add_chat_review({"persona_id": "p1", "turn_id": "x", "suggestion_id": "s0"}, "x")

    # Both kinds are stored and both are readable, and a by-id read returns its own kind.
    assert {row["event_id"]: row["kind"] for row in store.feedback("p1")} == {
        "turn-x": KIND_PROPOSAL_FEEDBACK, "review-x": KIND_PROPOSAL_FEEDBACK}
    assert [row["turn_id"] for row in store.chat_turns("p1")] == ["x"]
    assert [row["review_id"] for row in store.chat_reviews("p1")] == ["x"]
    assert store.feedback_event("turn-x")["kind"] == KIND_PROPOSAL_FEEDBACK
    assert store.feedback_event("review-x")["kind"] == KIND_PROPOSAL_FEEDBACK
    assert store.chat_turn("x")["kind"] == KIND_CHAT_TURN
    assert store.chat_review("x")["kind"] == KIND_CHAT_REVIEW


def test_blob_record_kinds_live_in_their_own_key_namespaces():
    """Item 2's storage half, pinned on the Blob backend: the namespaces are structurally disjoint.

    The namespace segment is separated by `/`, the one character the event-id alphabet `[A-Za-z0-9_-]`
    excludes (the route models and `_record_name` both enforce it), so no caller-supplied `event_id` can
    spell a namespaced name. `_` would not do it: an event id may contain `_`, even `__`, so the owner
    separator alone cannot make two key spaces disjoint.
    """
    blobs = MemoryContainer()
    store = Store([], blob_container=blobs)
    store.add_feedback({"persona_id": "p1", "encounter_id": "e1", "proposal_id": "pr1", "actor_id": "a1"},
                       event_id="turn-x")
    store.add_chat_turn({"persona_id": "p1", "turn_id": "x"}, "x", content={"turn_id": "x"})
    store.add_feedback({"persona_id": "p1", "encounter_id": "e1", "proposal_id": "pr2", "actor_id": "a1"},
                       event_id="review-x")
    store.add_chat_review({"persona_id": "p1", "turn_id": "x", "suggestion_id": "s0"}, "x")

    # Feedback keeps the owner-scoped name it always had (the pinned namespace contract), and each chat kind
    # is a namespace segment after the owner separator that the feedback key can never spell.
    assert "p1__turn-x.json" in blobs.blobs
    assert "p1__review-x.json" in blobs.blobs
    assert "p1__chat/turn-x.json" in blobs.blobs
    assert "p1__review/review-x.json" in blobs.blobs


# --- a claim whose record was never written is released, not left to lie ----------------------------------
#
# The order of the write path is claim first, record second. When the record upload fails transiently the
# route answers 503 "feedback was not saved" -- and the claim used to stay, so the actor's retry under a
# NEW event id was refused with "this proposal already has a decision" while `proposal_decisions` returned
# nothing. The route and the read disagreed about whether a decision existed, and the refusal was false.
# The fix releases the claim its own writer could not complete, on the narrow terms `_release_claim`
# documents; these tests pin both halves (the recovery and the honest refusal) and the properties that make
# the release safe: no double decision, and never a release while a record exists.


class FailingRecordWrite:
    """A Blob container whose first RECORD write fails, and whose claim writes always succeed.

    The split matters: the report's reproduction forces `create_if_absent` to raise once on the record,
    *after* the claim has been taken. A wrapper that failed every write would fail the claim instead and
    never reproduce the state this section is about. Record names are the ones with no `claims/` prefix,
    which is exactly how `Store._claim_storage` is namespaced (`CLAIM_NAMESPACE`).
    """

    def __init__(self, inner, fail_times: int = 1):
        self._inner = inner
        self.fail_times = fail_times

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def get_blob_client(self, name):
        client = self._inner.get_blob_client(name)
        if name.startswith("claims/"):
            return client
        return _FailingRecordBlob(self, client)


class _FailingRecordBlob:
    """One record blob whose first upload raises, then behaves like the container's own blob client."""

    def __init__(self, owner: FailingRecordWrite, client):
        self._owner = owner
        self._client = client

    def __getattr__(self, name):
        return getattr(self._client, name)

    def upload_blob(self, data, *, overwrite, content_settings):
        if self._owner.fail_times:
            self._owner.fail_times -= 1
            raise OSError("transient storage failure")
        return self._client.upload_blob(data, overwrite=overwrite, content_settings=content_settings)


@pytest.fixture(params=("jsonl", "blob"), ids=("jsonl", "blob"))
def claim_store(request, tmp_path, monkeypatch):
    """A store whose FIRST record write raises, so the claim-outlives-the-record state can be built.

    Both backends are exercised: the failure is injected into the one call that writes the record (and
    only the first time), leaving the claim in place exactly as a transient upload failure does.
    """
    if request.param == "jsonl":
        path = tmp_path / "feedback.jsonl"
        store = Store([], path=path)
        real = store._feedback_storage.create_if_absent
        calls = {"n": 0}

        def failing_once(event_id, entry):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("transient storage failure")
            return real(event_id, entry)

        monkeypatch.setattr(store._feedback_storage, "create_if_absent", failing_once)
        return store
    blobs = FailingRecordWrite(MemoryContainer())
    return Store([], blob_container=blobs)


def _decision_call(actor_id: str = "a1") -> dict:
    return {"persona_id": "p1", "encounter_id": "e1", "proposal_id": "pr1", "actor_id": actor_id,
            "action": "accept"}


def test_a_failed_record_write_does_not_strand_the_actor_behind_a_false_already_decided(claim_store):
    """Finding 2: the recovery half. A new event_id after the failure must be accepted, not refused.

    Sequence, exactly as reproduced in the report: first write raises -> the route's 503 claims "not
    saved" -> the SECOND attempt uses a NEW `event_id`. It must succeed, because no decision exists; the
    old code answered `DecisionConflict ... already has a decision` while `proposal_decisions` was `[]`.
    """
    store = claim_store
    with pytest.raises(OSError, match="transient"):
        store.add_feedback(_decision_call(), event_id="event-first-fails")
    assert store.feedback("p1") == [], "the failure was not acknowledged and stored nothing"
    assert store.proposal_decisions("p1", "e1", "a1") == []

    # The recovery: a new id, whose claim the failed attempt's release has freed.
    store.add_feedback(_decision_call(), event_id="event-second-succeeds")
    assert [row["event_id"] for row in store.proposal_decisions("p1", "e1", "a1")] == [
        "event-second-succeeds"]
    assert [row["event_id"] for row in store.feedback("p1")] == ["event-second-succeeds"]


def test_after_a_failed_write_a_genuine_second_decision_is_still_refused(claim_store):
    """Finding 2: the refusal half. Once a decision really exists, a new event_id is a second decision.

    The release must not have handed the actor a second slot: the first failed attempt's claim is gone,
    but the successful recovery's record and claim are not, so the next fresh id is refused with the
    honest `already_decided` -- which is now TRUE, because `proposal_decisions` reports the decision.
    """
    store = claim_store
    with pytest.raises(OSError, match="transient"):
        store.add_feedback(_decision_call(), event_id="event-first-fails")
    store.add_feedback(_decision_call(), event_id="event-second-succeeds")

    with pytest.raises(DecisionConflict, match="proposal already has a decision"):
        store.add_feedback({**_decision_call(), "action": "dismiss"}, event_id="event-third-refused")
    # The refusal is honest: a decision exists, and it is the one that was stored.
    assert [row["event_id"] for row in store.proposal_decisions("p1", "e1", "a1")] == [
        "event-second-succeeds"]
    assert [row["event_id"] for row in store.feedback("p1")] == ["event-second-succeeds"]


def test_a_failed_write_leaves_no_claim_behind_to_release_from_another_actors_slot(claim_store):
    """The release is scoped to the failed attempt's own actor, and the other actor keeps their slot.

    One actor's transient failure must not touch another actor's decision on the same proposal: the two
    subjects hash to different claim keys, and `_release_claim` only ever touches the key it was handed.
    """
    store = claim_store
    with pytest.raises(OSError, match="transient"):
        store.add_feedback(_decision_call("a1"), event_id="a1-fails")
    store.add_feedback(_decision_call("a2"), event_id="a2-succeeds")

    assert [row["event_id"] for row in store.proposal_decisions("p1", "e1", "a1")] == []
    assert [row["event_id"] for row in store.proposal_decisions("p1", "e1", "a2")] == ["a2-succeeds"]
    # a1's failed slot was released, so a1's fresh attempt is not told a decision exists.
    store.add_feedback(_decision_call("a1"), event_id="a1-retries")
    assert [row["event_id"] for row in store.proposal_decisions("p1", "e1", "a1")] == ["a1-retries"]


def test_the_store_never_refuses_for_a_decision_that_does_not_exist(claim_store):
    """Finding 2's invariant, stated as the property the route's body depends on.

    `/api/encounters/{id}/decisions` builds its body from `proposal_decisions` and nothing else, so the
    route and the read cannot disagree as long as store-level existence and store-level refusal agree.
    The failed attempt must leave both false (empty decisions, no `already decided`), and the recovery
    must leave both true (a decision, and a refusal naming it).
    """
    store = claim_store

    # Failed write: no decision exists, so a conflict is impossible -- the old code raised one anyway
    # (`proposal_decisions -> []` while a fresh id was told `already has a decision`).
    with pytest.raises(OSError, match="transient"):
        store.add_feedback(_decision_call(), event_id="event-first-fails")
    assert store.proposal_decisions("p1", "e1", "a1") == []
    store.add_feedback(_decision_call(), event_id="event-second-succeeds")

    # A decision now exists, so a conflict is the truth and `proposal_decisions` confirms it.
    decisions = store.proposal_decisions("p1", "e1", "a1")
    assert len(decisions) == 1
    with pytest.raises(DecisionConflict, match="proposal already has a decision"):
        store.add_feedback({**_decision_call(), "action": "dismiss"}, event_id="event-third-refused")
    assert store.proposal_decisions("p1", "e1", "a1") == decisions, (
        "the refused attempt changed nothing, so the read still names the one real decision")
