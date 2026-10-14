"""LLM adapter seam: the configuration gate and client construction, no network."""

import asyncio
import json
import logging
from datetime import date

import pytest

from app import llm, telemetry
from app.fixtures import load_personas
from app.policy import cut


def test_validate_route_model_pairings():
    llm._validate_route_model("https://account.example/openai/v1", "gpt-6-luna")
    llm._validate_route_model("https://account.example/api/projects/p/openai/v1", "gpt-6.1-sol")
    with pytest.raises(ValueError, match="project-scoped.*gpt-6-luna"):
        llm._validate_route_model("https://account.example/api/projects/p/openai/v1", "gpt-6-luna")


def test_missing_model_fails_loudly(monkeypatch):
    monkeypatch.delenv("LUNA_FOUNDRY_MODEL")
    with pytest.raises(RuntimeError, match="LUNA_FOUNDRY_MODEL must be configured"):
        llm._configured_model()


def test_half_configured_env_is_not_configured(monkeypatch):
    """Both vars are needed: gating on one lets a half-configured environment
    report a model call that never happened."""
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "k")
    monkeypatch.delenv("LUNA_FOUNDRY_BASE", raising=False)
    assert llm.foundry_configured() is False
    monkeypatch.setenv("LUNA_FOUNDRY_BASE", "https://example.test/v1")
    assert llm.foundry_configured() is True
    monkeypatch.delenv("LUNA_FOUNDRY_BASE")
    monkeypatch.delenv("LUNA_FOUNDRY_KEY")
    monkeypatch.setenv("LUNA_FOUNDRY_PROJECT_ENDPOINT", "https://project.example/api/projects/demo")
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_NAME", "narration")
    assert llm.foundry_configured() is True


def test_prompt_agent_uses_the_canonical_instruction_file():
    from pathlib import Path

    canonical = Path(llm.__file__).parent / "prompts" / "narration_instructions.md"
    assert llm.FRESH_NARRATION_PROMPT == canonical.read_text(encoding="utf-8").strip()


def test_prompt_agent_is_optional_and_direct_fallback_remains_available(monkeypatch):
    scoped = cut(load_personas()[0], "sairaanhoitaja")
    source_id = scoped.ids[0]
    candidate = llm.FreshNarration.model_validate({"sentences": [{
        "sentence_id": "s1", "text": "Synteettinen lause.", "source_ids": [source_id],
    }]})
    monkeypatch.setenv("LUNA_FOUNDRY_PROJECT_ENDPOINT", "https://project.example/api/projects/demo")
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_NAME", "luvn-narration")
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_VERSION", "4")
    monkeypatch.setenv("LUNA_FOUNDRY_MODEL", "gpt-6.1-sol")
    monkeypatch.delenv("LUNA_FOUNDRY_BASE", raising=False)
    monkeypatch.delenv("LUNA_FOUNDRY_KEY", raising=False)
    seen = {}

    async def agent(schema, user, timeout_s):
        seen.update(schema=schema, payload=json.loads(user), timeout=timeout_s)
        return llm.FoundryResult(output=candidate, model="gpt-6.1-sol", input_tokens=20,
                                 output_tokens=10, path="agent_reference")

    async def no_direct(*_args, **_kwargs):
        raise AssertionError("direct path must not run when agent succeeds")

    monkeypatch.setattr(llm, "_complete_prompt_agent", agent)
    monkeypatch.setattr(llm, "_complete_bounded", no_direct)
    result = asyncio.run(llm.generate_fresh_narration(scoped, date(2026, 9, 30), 4.0))
    assert result.model == "gpt-6.1-sol"
    assert result.path == "agent_reference"
    assert seen["schema"] is llm.FreshNarration
    assert seen["timeout"] == pytest.approx(3.2)
    assert set(record["record_id"] for record in seen["payload"]["selected_evidence"]) == set(scoped.ids)


@pytest.mark.parametrize("configured_version", ["4", "99"])
def test_agent_result_path_is_independent_of_configured_version(monkeypatch, configured_version):
    from types import SimpleNamespace

    monkeypatch.setenv("LUNA_FOUNDRY_PROJECT_ENDPOINT", "https://project.example/api/projects/demo")
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_NAME", "luvn-narration")
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_VERSION", configured_version)
    monkeypatch.setenv("LUNA_FOUNDRY_MODEL", "same-model")
    seen = {}

    class Credential:
        async def get_token(self, _scope):
            return SimpleNamespace(token="test-token")

        async def close(self):
            pass

    class Responses:
        async def create(self, **kwargs):
            seen["agent_version"] = kwargs["extra_body"]["agent_reference"]["version"]
            return SimpleNamespace(
                output_text=json.dumps({"sentences": [{
                    "sentence_id": "s1", "text": "Synteettinen lause.", "source_ids": ["source"],
                }]}),
                model="same-model", usage=SimpleNamespace(input_tokens=1, output_tokens=2),
            )

    class Client:
        responses = Responses()

        async def close(self):
            pass

    monkeypatch.setattr(llm, "_agent_token_credential", lambda: Credential())
    monkeypatch.setattr("openai.AsyncOpenAI", lambda **_kwargs: Client())
    result = asyncio.run(llm._complete_prompt_agent(llm.FreshNarration, "{}", 1.0))
    assert seen["agent_version"] == configured_version
    assert result.path == "agent_reference"


def test_retry_feedback_payload_has_only_sentence_ids_and_permitted_citations(monkeypatch):
    scoped = cut(load_personas()[0], "sairaanhoitaja")
    source_id = scoped.ids[0]
    candidate = llm.FreshNarration(sentences=[llm.NarrationSentence(
        sentence_id="s1", text="Synteettinen lause.", source_ids=[source_id])])
    monkeypatch.setenv("LUNA_FOUNDRY_PROJECT_ENDPOINT", "https://project.example/api/projects/demo")
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_NAME", "luvn-narration")
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_VERSION", "4")
    monkeypatch.setenv("LUNA_FOUNDRY_MODEL", "gpt-6.1-sol")
    monkeypatch.delenv("LUNA_FOUNDRY_BASE", raising=False)
    monkeypatch.delenv("LUNA_FOUNDRY_KEY", raising=False)
    seen = {}

    async def agent(_schema, user, _timeout):
        seen.update(payload=json.loads(user), user=user)
        return llm.FoundryResult(output=candidate, model="gpt-6.1-sol", path="agent_reference")

    monkeypatch.setattr(llm, "_complete_prompt_agent", agent)
    feedback = [{"sentence_id": "s2", "reason": "unsupported",
                 "permitted_citation_ids": [source_id]}]
    asyncio.run(llm.generate_fresh_narration(
        scoped, date(2026, 9, 30), 4.0, rejection_feedback=feedback))
    assert seen["payload"]["rejected_sentences"] == feedback
    assert "retry_instruction" in seen["payload"]
    assert "unverified candidate prose" not in seen["user"]
    assert source_id in {row["record_id"] for row in seen["payload"]["selected_evidence"]}


def test_prompt_agent_failure_falls_back_to_direct_call(monkeypatch, caplog):
    scoped = cut(load_personas()[0], "sairaanhoitaja")
    source_id = scoped.ids[0]
    candidate = llm.FreshNarration.model_validate({"sentences": [{
        "sentence_id": "s1", "text": "Synteettinen lause.", "source_ids": [source_id],
    }]})
    monkeypatch.setenv("LUNA_FOUNDRY_PROJECT_ENDPOINT", "https://project.example/api/projects/demo")
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_NAME", "luvn-narration")
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_VERSION", "8")
    monkeypatch.setenv("LUNA_FOUNDRY_BASE", "https://direct.example/v1")
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "test-key")
    seen = {}

    async def broken_agent(*_args, **_kwargs):
        await asyncio.sleep(0.01)
        raise RuntimeError("synthetic service failure")

    async def direct(schema, instructions, user, timeout_s):
        seen.update(schema=schema, instructions=instructions, timeout=timeout_s)
        return llm.FoundryResult(output=candidate, model="gpt-6.1-sol", input_tokens=15, output_tokens=9)

    monkeypatch.setattr(llm, "_complete_prompt_agent", broken_agent)
    monkeypatch.setattr(llm, "_complete_bounded", direct)
    with caplog.at_level(logging.ERROR, logger="app.llm"):
        result = asyncio.run(llm.generate_fresh_narration(scoped, date(2026, 9, 30), 4.0))
    assert result.model == "gpt-6.1-sol"
    assert result.path == "direct_fallback"
    assert any(record.levelno == logging.ERROR and "using direct-call fallback" in record.message
               for record in caplog.records)
    assert seen["instructions"] == llm.FRESH_NARRATION_PROMPT
    assert 0 < seen["timeout"] < 4.0


def test_agent_failure_without_direct_fallback_is_reported_honestly(monkeypatch):
    scoped = cut(load_personas()[0], "sairaanhoitaja")
    monkeypatch.setenv("LUNA_FOUNDRY_PROJECT_ENDPOINT", "https://project.example/api/projects/demo")
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_NAME", "luvn-narration")
    monkeypatch.delenv("LUNA_FOUNDRY_BASE", raising=False)
    monkeypatch.delenv("LUNA_FOUNDRY_KEY", raising=False)

    async def broken_agent(*_args, **_kwargs):
        raise RuntimeError("synthetic agent failure")

    async def should_not_call_direct(*_args, **_kwargs):
        raise AssertionError("direct fallback credentials are absent")

    monkeypatch.setattr(llm, "_complete_prompt_agent", broken_agent)
    monkeypatch.setattr(llm, "_complete_bounded", should_not_call_direct)
    with pytest.raises(RuntimeError, match="direct-call fallback is not configured"):
        asyncio.run(llm.generate_fresh_narration(scoped, date(2026, 9, 30), 2.0))


def test_agent_content_filter_does_not_fall_back_to_the_direct_path(monkeypatch):
    """A safety block is terminal: replaying the same prompt through direct chat for the same
    content would defeat the filter and misrecord the safety signal as a transport failure."""
    scoped = cut(load_personas()[0], "sairaanhoitaja")
    monkeypatch.setenv("LUNA_FOUNDRY_PROJECT_ENDPOINT", "https://project.example/api/projects/demo")
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_NAME", "luvn-narration")
    monkeypatch.setenv("LUNA_FOUNDRY_BASE", "https://direct.example/v1")
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "test-key")
    calls: list[str] = []

    async def filtered_agent(*_args, **_kwargs):
        calls.append("agent")
        raise llm.ContentFiltered("blocked by the safety layer")

    async def should_not_call_direct(*_args, **_kwargs):
        calls.append("direct")
        raise AssertionError("a filtered prompt must not be replayed through the direct path")

    monkeypatch.setattr(llm, "_complete_prompt_agent", filtered_agent)
    monkeypatch.setattr(llm, "_complete_bounded", should_not_call_direct)
    with pytest.raises(llm.ContentFiltered):
        asyncio.run(llm.generate_fresh_narration(scoped, date(2026, 9, 30), 2.0))
    assert calls == ["agent"]


def test_agent_token_uses_managed_identity_in_host_and_explicit_azure_cli_locally(monkeypatch):
    managed_client_ids: list[str] = []

    class Managed:
        def __init__(self, *, client_id):
            managed_client_ids.append(client_id)

    class Cli:
        pass

    monkeypatch.setattr("azure.identity.aio.ManagedIdentityCredential", Managed)
    monkeypatch.setattr("azure.identity.aio.AzureCliCredential", Cli)
    monkeypatch.setenv("AZURE_CLIENT_ID", "container-identity-client")
    llm._agent_token_credential()
    assert managed_client_ids == ["container-identity-client"]
    monkeypatch.delenv("AZURE_CLIENT_ID")
    assert type(llm._agent_token_credential()) is Cli


def test_prompt_agent_token_probe_returns_status_only_and_closes_credential(monkeypatch, caplog):
    monkeypatch.setenv("LUNA_FOUNDRY_PROJECT_ENDPOINT", "https://project.example/api/projects/demo")
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_NAME", "luvn-narration")
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_VERSION", "8")
    closed = []

    class Credential:
        async def get_token(self, _scope):
            return object()

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(llm, "_agent_token_credential", Credential)
    assert asyncio.run(llm.probe_prompt_agent_token()) == "ok"
    assert closed == [True]

    class BrokenCredential(Credential):
        async def get_token(self, _scope):
            raise RuntimeError("sensitive provider details")

    monkeypatch.setattr(llm, "_agent_token_credential", BrokenCredential)
    assert asyncio.run(llm.probe_prompt_agent_token()) == "failed"
    assert "sensitive provider details" not in caplog.text


def test_prompt_agent_token_probe_never_invokes_generation(monkeypatch):
    monkeypatch.setenv("LUNA_FOUNDRY_PROJECT_ENDPOINT", "https://project.example/api/projects/demo")
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_NAME", "luvn-narration")
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_VERSION", "8")

    class Credential:
        async def get_token(self, _scope):
            return object()

        async def close(self):
            pass

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("diagnostics token probe must never invoke generation")

    monkeypatch.setattr(llm, "_agent_token_credential", Credential)
    monkeypatch.setattr(llm, "_complete_prompt_agent", forbidden)
    monkeypatch.setattr(llm, "_complete_bounded", forbidden)
    monkeypatch.setattr(llm, "generate_fresh_narration", forbidden)
    assert asyncio.run(llm.probe_prompt_agent_token(timeout_s=0.2)) == "ok"


def test_agent_timeout_records_the_timed_out_phase(monkeypatch):
    from types import SimpleNamespace

    class Credential:
        async def get_token(self, _scope):
            return SimpleNamespace(token="token")

        async def close(self):
            pass

    class Responses:
        async def create(self, **_kwargs):
            await asyncio.sleep(1)

    class Client:
        responses = Responses()

        async def close(self):
            pass

    monkeypatch.setenv("LUNA_FOUNDRY_PROJECT_ENDPOINT", "https://project.example/api/projects/demo")
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_NAME", "luvn-narration")
    monkeypatch.setenv("LUNA_FOUNDRY_AGENT_VERSION", "7")
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.setattr("azure.identity.aio.AzureCliCredential", Credential)
    monkeypatch.setattr("openai.AsyncOpenAI", lambda **_kwargs: Client())

    async def run():
        with telemetry.request_trace() as trace:
            with pytest.raises(TimeoutError):
                await llm._complete_prompt_agent(llm.FreshNarration, "{}", 0.05)
        return trace.events

    events = asyncio.run(run())
    assert [(row["stage"], row["status"]) for row in events] == [
        ("foundry.agent.token", "ok"), ("foundry.agent.call", "timeout"),
    ]


def test_luna_environment_builds_foundry_client(monkeypatch):
    monkeypatch.setenv("LUNA_FOUNDRY_MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("LUNA_FOUNDRY_KEY", "test-key")
    monkeypatch.setenv("LUNA_FOUNDRY_BASE", "https://example.test/v1")
    client = llm._client()
    assert str(client.base_url).rstrip("/") == "https://example.test/v1"
    assert client.api_key == "test-key"
    asyncio.run(client.close())
