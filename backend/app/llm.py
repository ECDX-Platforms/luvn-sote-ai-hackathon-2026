"""Foundry adapter (LLM-adapter seam).

A missing deployment or credential cannot block the pipeline: `foundry_configured`
gates the call and `pipeline` renders the structured decision without prose.
Target model, account and route are supplied by deployment configuration. The direct
path uses Azure OpenAI v1 chat-completions with strict `json_schema`; the optional
versioned project agent uses Responses `agent_reference`.

The narration contract deliberately omits decision-critical values (situation,
risk level). Those render from the decision object, never from model text.
Only role-visible records are ever sent to the model.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
import contextlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, cast
from pydantic import BaseModel, ConfigDict, Field

from . import source
from .policy import Scoped
# pi-lens-ignore: reportMissingImports
from .telemetry import record as telemetry_record  # type: ignore[reportMissingImports]

log = logging.getLogger(__name__)

FOUNDRY_MODEL = os.environ.get("LUNA_FOUNDRY_MODEL", "").strip()
# Ponytail: bound requests so an unavailable provider cannot hang the pipeline.
FOUNDRY_TIMEOUT_S = float(os.environ.get("LUNA_FOUNDRY_TIMEOUT", "120"))
FOUNDRY_MAX_RETRIES = int(os.environ.get("LUNA_FOUNDRY_MAX_RETRIES", "1"))

MAX_NARRATION_SENTENCES = 7
MAX_SENTENCE_CHARS = 800


class NarrationSentence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sentence_id: str = Field(pattern=r"^s[1-7]$")
    text: str = Field(min_length=1, max_length=MAX_SENTENCE_CHARS)
    # Empty citations are a retryable draft only; grounding validation blocks them from the verifier.
    source_ids: list[str] = Field(max_length=8)
    proposal_ids: list[str] = Field(default_factory=list, max_length=8)


class FreshNarration(BaseModel):
    """Fresh Foundry prose; each renderable sentence owns its evidence IDs."""

    model_config = ConfigDict(extra="forbid", strict=True)

    sentences: list[NarrationSentence] = Field(min_length=1, max_length=MAX_NARRATION_SENTENCES)


class SupportVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sentence_id: str = Field(min_length=1, max_length=40)
    verdict: Literal["supported", "unsupported", "unknown"]
    supporting_record_ids: list[str] = Field(default_factory=list, max_length=8)


class SupportReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    verdicts: list[SupportVerdict] = Field(min_length=1, max_length=MAX_NARRATION_SENTENCES)


@dataclass(frozen=True)
class FoundryResult:
    output: BaseModel
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    # Explicit branch result; configured agent versions never imply an agent call ran.
    path: Literal["agent_reference", "direct_fallback", "direct_chat_completions"] = "direct_chat_completions"


def _validate_route_model(base_url: str, model: str) -> None:
    if "/api/projects/" in base_url and model == "gpt-6-luna":
        raise ValueError("Unsupported route/model pairing: project-scoped endpoint with gpt-6-luna")


def _configured_model() -> str:
    model = os.environ.get("LUNA_FOUNDRY_MODEL", "").strip()
    if not model:
        raise RuntimeError("LUNA_FOUNDRY_MODEL must be configured")
    return model


def _client(timeout_s: float | None = None, max_retries: int | None = None):
    from openai import AsyncOpenAI

    base_url = os.environ["LUNA_FOUNDRY_BASE"]
    _validate_route_model(base_url, _configured_model())
    return AsyncOpenAI(
        base_url=base_url,
        api_key=os.environ["LUNA_FOUNDRY_KEY"],
        timeout=FOUNDRY_TIMEOUT_S if timeout_s is None else timeout_s,
        max_retries=FOUNDRY_MAX_RETRIES if max_retries is None else max_retries,
    )


def _strict_response_format(schema: type[BaseModel]) -> dict:
    """Wire schema for chat-completions strict structured output.

    Azure rejects `strict: true` unless every property is listed in `required` and
    every object forbids extra keys, so this normalizes the Pydantic schema rather
    than hand-maintaining a second copy. Pydantic stays the source of truth.
    """
    raw = schema.model_json_schema()

    def normalize(node: object) -> None:
        if not isinstance(node, dict):
            return
        for definition in (node.get("$defs") or {}).values():
            normalize(definition)
        properties = node.get("properties")
        if isinstance(properties, dict):
            for value in properties.values():
                normalize(value)
            node["required"] = sorted(properties)
            node["additionalProperties"] = False
        if isinstance(node.get("items"), dict):
            normalize(node["items"])

    normalize(raw)
    return {"type": "json_schema",
            "json_schema": {"name": schema.__name__, "strict": True, "schema": raw}}


class ContentFiltered(RuntimeError):
    """The provider's safety layer refused the request or blocked the response.

    A distinct type so `narration` can report a safety hit instead of a generic failure, and so the
    structured deterministic briefing still renders unchanged and without prose.
    """


def _is_content_filter_error(exc: BaseException) -> bool:
    """Azure rejects a filtered request instead of answering it; recognise either shape."""
    if getattr(exc, "code", None) == "content_filter":
        return True
    body = getattr(exc, "body", None)
    if isinstance(body, dict) and body.get("code") == "content_filter":
        return True
    return "content_filter" in str(exc).lower()


def _has_blocked_content_filter(response: object) -> bool:
    filters = getattr(response, "content_filters", None)
    if not isinstance(filters, list):
        return False
    return any(
        isinstance(blocked := (item.get("blocked") if isinstance(item, dict)
                               else getattr(item, "blocked", None)), bool) and blocked
        for item in filters
    )


def _is_timeout_error(exc: BaseException) -> bool:
    return isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.casefold()


def _error_facts(exc: BaseException) -> dict[str, Any]:
    """Scrubbed failure shape for telemetry: type, HTTP status and provider request ID.

    Never the message, body, token or payload. Distinguishes a phase that failed from one
    that was merely slow, which is what a bare timeout cannot tell.
    """
    facts: dict[str, Any] = {"error": type(exc).__name__}
    response = getattr(exc, "response", None)
    status = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        facts["http_status"] = status
    request_id = getattr(exc, "request_id", None)
    if not request_id:
        headers = getattr(response, "headers", None)
        if headers is not None:
            request_id = headers.get("x-request-id") or headers.get("apim-request-id")
    if isinstance(request_id, str) and request_id:
        facts["request_id"] = request_id
    return facts


@dataclass(frozen=True)
class ToolLoopResult:
    """A tool loop's outcome: the final strict-schema output, the model, the tool calls it made, in order."""

    output: BaseModel
    model: str
    tool_calls: tuple[tuple[str, dict], ...]
    input_tokens: int | None = None
    output_tokens: int | None = None


async def _complete_tool_loop(
    schema: type[BaseModel], instructions: str, user: str, tools: list[dict],
    handlers: dict[str, Callable[[dict], Any]], timeout_s: float, *, max_rounds: int = 6,
    on_tool: Callable[[str, dict], Awaitable[None]] | None = None,
) -> ToolLoopResult:
    """A bounded function-calling loop ending in one strict-schema answer. The assistant's transport.

    On the Responses route, because the deployment refuses function tools on chat completions for this model
    ("Function tools with reasoning_effort are not supported ... use /v1/responses"). The same boundary as
    `_complete_bounded`: every outbound body -- the first and every tool result that is appended -- passes
    `source.assert_model_safe` immediately before it can leave the process, so a tool whose result carried an
    identity key would be refused at the wire, not trusted. `handlers` are plain functions over data the caller
    already decided was permitted; the loop never reaches past them. After `max_rounds` tool rounds the model is
    asked to answer without further tools. `tools` are given in the chat-completions shape
    (`{"type": "function", "function": {...}}`) and translated here, so the caller's definition stays one.
    """
    if timeout_s <= 0:
        raise TimeoutError("Foundry stage budget exhausted")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    cleanup_reserve = min(0.25, timeout_s * 0.5)
    client = _client(timeout_s=max(0.001, timeout_s - cleanup_reserve), max_retries=0)
    response_tools = [{"type": "function", "name": tool["function"]["name"],
                       "description": tool["function"].get("description", ""),
                       "parameters": tool["function"].get("parameters", {"type": "object", "properties": {}}),
                       "strict": False} for tool in tools]
    raw_schema = _strict_response_format(schema)["json_schema"]
    text_format = {"format": {"type": "json_schema", "name": raw_schema["name"], "schema": raw_schema["schema"],
                              "strict": True}}
    items: list[dict[str, Any]] = [{"role": "user", "content": user}]
    calls: list[tuple[str, dict]] = []
    input_tokens = output_tokens = 0
    try:
        for round_index in range(max_rounds + 1):
            body = json.dumps({"instructions": instructions, "input": items}, ensure_ascii=False)
            source.assert_model_safe(body)
            allow_tools = round_index < max_rounds and bool(response_tools)
            call_started = time.monotonic()
            try:
                async with asyncio.timeout_at(deadline - cleanup_reserve):
                    response = await client.responses.create(
                        model=_configured_model(),
                        instructions=instructions,
                        input=cast(Any, items),
                        tools=cast(Any, response_tools) if allow_tools else cast(Any, []),
                        tool_choice="auto" if allow_tools else "none",
                        text=cast(Any, text_format),
                    )
            except Exception as exc:
                if _is_content_filter_error(exc):
                    raise ContentFiltered("Foundry content filter rejected the request") from exc
                telemetry_record("foundry.tool_loop.call", call_started, model=_configured_model(),
                                 status="timeout" if _is_timeout_error(exc) or isinstance(exc, TimeoutError) else "failed",
                                 **_error_facts(exc))
                raise
            if _has_blocked_content_filter(response):
                raise ContentFiltered("Foundry content filter blocked the response")
            usage = getattr(response, "usage", None)

            def token_count(name: str) -> int:
                value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
                return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

            input_tokens += token_count("input_tokens")
            output_tokens += token_count("output_tokens")
            function_calls = [item for item in (getattr(response, "output", None) or [])
                              if getattr(item, "type", None) == "function_call"]
            telemetry_record("foundry.tool_loop.call", call_started, model=getattr(response, "model", None),
                             status="tool_calls" if function_calls else "ok",
                             input_tokens=token_count("input_tokens"), output_tokens=token_count("output_tokens"))
            if function_calls and allow_tools:
                for call in function_calls:
                    name = str(getattr(call, "name", "") or "")
                    try:
                        arguments = json.loads(getattr(call, "arguments", None) or "{}")
                    except ValueError:
                        arguments = {}
                    if not isinstance(arguments, dict):
                        arguments = {}
                    calls.append((name, arguments))
                    if on_tool is not None:
                        await on_tool(name, arguments)
                    handler = handlers.get(name)
                    try:
                        result = handler(arguments) if handler is not None else {"error": f"unknown tool {name}"}
                    except Exception as exc:  # noqa: BLE001 -- a tool failure is a result the model reads, not a crash
                        result = {"error": f"{type(exc).__name__}"}
                    items.append({"type": "function_call", "call_id": call.call_id, "name": name,
                                  "arguments": getattr(call, "arguments", None) or "{}"})
                    items.append({"type": "function_call_output", "call_id": call.call_id,
                                  "output": json.dumps(result, ensure_ascii=False)})
                continue
            content = getattr(response, "output_text", None)
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Foundry returned no parsed output")
            parsed = schema.model_validate_json(content)
            actual_model = getattr(response, "model", None)
            if not isinstance(actual_model, str) or not actual_model.strip():
                raise ValueError("Foundry response omitted model identity")
            return ToolLoopResult(output=parsed, model=actual_model, tool_calls=tuple(calls),
                                 input_tokens=input_tokens, output_tokens=output_tokens)
        raise ValueError("the tool loop ended without an answer")
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                remaining = max(0.0, deadline - loop.time())
                with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                    await asyncio.wait_for(result, timeout=remaining)


async def _complete_bounded(
    schema: type[BaseModel], instructions: str, user: str, timeout_s: float
) -> FoundryResult:
    if timeout_s <= 0:
        raise TimeoutError("Foundry stage budget exhausted")
    # Enforcement at the transport boundary (work order L2B): every outbound body, however it was built,
    # passes this check immediately before it can leave the process. A call site that forgot the model
    # projection is caught here rather than trusted to have remembered it.
    source.assert_model_safe(user)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    cleanup_reserve = min(0.25, timeout_s * 0.5)
    client = _client(timeout_s=max(0.001, timeout_s - cleanup_reserve), max_retries=0)
    try:
        work_deadline = deadline - cleanup_reserve
        direct_started = time.monotonic()
        try:
            async with asyncio.timeout_at(work_deadline):
                try:
                    response = await client.chat.completions.create(
                        model=_configured_model(),
                        messages=[{"role": "system", "content": instructions},
                                  {"role": "user", "content": user}],
                        # The SDK's static type union does not accept a plain dict; the wire shape
                        # is the documented `ResponseFormatJSONSchema`.
                        response_format=cast(Any, _strict_response_format(schema)),
                    )
                except Exception as exc:
                    if _is_content_filter_error(exc):
                        raise ContentFiltered("Foundry content filter rejected the request") from exc
                    if not isinstance(exc, TimeoutError):
                        telemetry_record("foundry.direct.call", direct_started, model=_configured_model(),
                                         status="timeout" if _is_timeout_error(exc) else "failed",
                                         **_error_facts(exc))
                    raise
        except TimeoutError as exc:
            telemetry_record("foundry.direct.call", direct_started, model=_configured_model(),
                             status="timeout", **_error_facts(exc))
            raise
        choice = response.choices[0] if response.choices else None
        if getattr(choice, "finish_reason", None) == "content_filter":
            raise ContentFiltered("Foundry content filter blocked the response")
        content = choice.message.content if choice is not None else None
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Foundry returned no parsed output")
        parsed = schema.model_validate_json(content)
        actual_model = getattr(response, "model", None)
        if not isinstance(actual_model, str) or not actual_model.strip():
            raise ValueError("Foundry response omitted model identity")
        usage = getattr(response, "usage", None)
        def token_count(name: str) -> int | None:
            value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        telemetry_record("foundry.direct.call", direct_started, model=actual_model, status="ok",
                         input_tokens=token_count("prompt_tokens"),
                         output_tokens=token_count("completion_tokens"),
                         request_id=getattr(response, "_request_id", None))
        return FoundryResult(output=parsed, model=actual_model,
                             input_tokens=token_count("prompt_tokens"),
                             output_tokens=token_count("completion_tokens"))
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                remaining = max(0.0, deadline - loop.time())
                with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                    await asyncio.wait_for(result, timeout=remaining)


def _agent_token_credential():
    from azure.identity.aio import AzureCliCredential, ManagedIdentityCredential

    client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()
    return ManagedIdentityCredential(client_id=client_id) if client_id else AzureCliCredential()


async def _complete_prompt_agent(schema: type[BaseModel], user: str, timeout_s: float) -> FoundryResult:
    """Invoke the optional versioned project agent; callers own direct fallback."""
    endpoint = os.environ["LUNA_FOUNDRY_PROJECT_ENDPOINT"].rstrip("/")
    agent_name = os.environ["LUNA_FOUNDRY_AGENT_NAME"]
    agent_version = os.environ.get("LUNA_FOUNDRY_AGENT_VERSION", "").strip()
    if not agent_version:
        raise RuntimeError("LUNA_FOUNDRY_AGENT_VERSION must be configured")
    model = _configured_model()
    _validate_route_model(endpoint, model)
    if timeout_s <= 0:
        raise TimeoutError("Foundry agent budget exhausted")
    # Same enforcement as `_complete_bounded`: this is the agent-reference fallback's own transport.
    source.assert_model_safe(user)
    from openai import AsyncOpenAI

    credential = _agent_token_credential()
    client = None
    phase = "token"
    phase_started = time.monotonic()
    try:
        # Token acquisition is part of the bounded budget: a slow IMDS probe must not
        # extend past the caller's deadline.
        try:
            async with asyncio.timeout(timeout_s):
                phase = "token"
                phase_started = time.monotonic()
                try:
                    token = await credential.get_token("https://ai.azure.com/.default")
                except Exception as exc:
                    if not isinstance(exc, TimeoutError):
                        telemetry_record("foundry.agent.token", phase_started,
                                         status="timeout" if _is_timeout_error(exc) else "failed",
                                         **_error_facts(exc))
                    raise
                telemetry_record("foundry.agent.token", phase_started, status="ok")
                client = AsyncOpenAI(
                    base_url=f"{endpoint}/openai/v1",
                    api_key=token.token,
                    timeout=timeout_s,
                    max_retries=0,
                )
                phase = "call"
                phase_started = time.monotonic()
                call_started = phase_started
                try:
                    response = await client.responses.create(
                        model=model,
                        input=user,
                        extra_body={"agent_reference": {
                            "name": agent_name, "type": "agent_reference", "version": agent_version,
                        }},
                    )
                except Exception as exc:
                    if _is_content_filter_error(exc):
                        raise ContentFiltered("Foundry content filter rejected the agent request") from exc
                    if not isinstance(exc, TimeoutError):
                        telemetry_record("foundry.agent.call", call_started, model=model,
                                         status="timeout" if _is_timeout_error(exc) else "failed",
                                         **_error_facts(exc))
                    raise
                if _has_blocked_content_filter(response):
                    raise ContentFiltered("Foundry content filter blocked the agent response")
                usage = getattr(response, "usage", None)

                def token_count(attr: str) -> int | None:
                    value = usage.get(attr) if isinstance(usage, dict) else getattr(usage, attr, None)
                    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

                telemetry_record("foundry.agent.call", call_started, model=model, status="ok",
                                 input_tokens=token_count("input_tokens"),
                                 output_tokens=token_count("output_tokens"),
                                 request_id=getattr(response, "_request_id", None))
        except TimeoutError as exc:
            stage = "foundry.agent.token" if phase == "token" else "foundry.agent.call"
            telemetry_record(stage, phase_started, model=model if phase == "call" else None,
                             status="timeout", **_error_facts(exc))
            raise
        output_text = getattr(response, "output_text", None)
        if not isinstance(output_text, str) or not output_text.strip():
            raise ValueError("Foundry prompt agent returned no output text")
        parsed = schema.model_validate_json(output_text)
        actual_model = getattr(response, "model", None)
        if not isinstance(actual_model, str) or not actual_model.strip():
            raise ValueError("Foundry prompt agent response omitted model identity")

        return FoundryResult(output=parsed, model=actual_model,
                             input_tokens=token_count("input_tokens"),
                             output_tokens=token_count("output_tokens"),
                             path="agent_reference")
    finally:
        if client is not None:
            await client.close()
        await credential.close()


# Canonical narration instructions for the DIRECT-CALL path only.
#
# The versioned prompt agent was created from this file, but its instructions live server-side:
# editing this file does NOT change the agent path, which never receives this text at all
# (`_complete_prompt_agent` takes no instructions argument). A prompt change reaches the hosted
# primary path only after a NEW agent version is created from it and LUNA_FOUNDRY_AGENT_VERSION is
# bumped — otherwise it affects the direct-chat fallback alone. Check both before claiming a prompt
# change is live.
FRESH_NARRATION_PROMPT = (Path(__file__).parent / "prompts" / "narration_instructions.md").read_text(
    encoding="utf-8").strip()

# Deterministic narration order note: sources reach the generator in the order the deterministic
# urgency layer ranked them, so no per-source prose directive is derived from a probabilistic
# label. (A source-wide negation directive was rejected as unsafe; see ADR-0009.)


async def generate_fresh_narration(
    scoped: Scoped, today, timeout_s: float, *, rejection_feedback: list[dict[str, Any]] | None = None
) -> FoundryResult:
    from . import source

    records = [
        {
            "record_id": record.record_id,
            "record_type": record.record_type,
            "topic": record.topic,
            "recorded_at": record.recorded_at.isoformat(),
            "temporal_status": source.temporal_status(record, today),
            "payload": source.basics_payload(record, "model"),
        }
        for record in scoped.records
    ]
    request: dict[str, Any] = {"selected_evidence": records}
    if rejection_feedback:
        request["retry_instruction"] = (
            "Aiempi koosteluonnos hylättiin tarkistuksessa. Muodosta uusi kokonaisuus vain "
            "selected_evidence-aineistosta ja käytä kullekin hylätylle lauseelle vain "
            "permitted_citation_ids-listan tunnisteita. Hylätyn lauseen tekstiä ei toimiteta."
        )
        request["rejected_sentences"] = [
            {"sentence_id": row["sentence_id"], "reason": row["reason"],
             "permitted_citation_ids": row["permitted_citation_ids"]}
            for row in rejection_feedback
        ]
    user = json.dumps(request, ensure_ascii=False)
    endpoint = os.environ.get("LUNA_FOUNDRY_PROJECT_ENDPOINT", "").strip()
    agent_name = os.environ.get("LUNA_FOUNDRY_AGENT_NAME", "").strip()
    if endpoint and agent_name:
        started = time.monotonic()
        agent_budget = min(45.0, timeout_s * 0.8)
        try:
            return await _complete_prompt_agent(FreshNarration, user, agent_budget)
        except ContentFiltered:
            # A provider safety block is terminal: replaying blocked content through the
            # direct path would defeat the filter and misrecord the safety signal.
            raise
        except Exception as exc:  # configured agent failed; direct fallback is explicit below
            direct_configured = bool(os.environ.get("LUNA_FOUNDRY_KEY") and os.environ.get("LUNA_FOUNDRY_BASE"))
            if not direct_configured:
                log.warning("Foundry prompt agent failed without a configured direct fallback: %s",
                            type(exc).__name__)
                raise RuntimeError("Foundry prompt agent failed and direct-call fallback is not configured") from exc
            log.error("Configured Foundry prompt agent failed; using direct-call fallback: %s",
                      type(exc).__name__)
        timeout_s = max(0.0, timeout_s - (time.monotonic() - started))
        result = await _complete_bounded(FreshNarration, FRESH_NARRATION_PROMPT, user, timeout_s)
        return replace(result, path="direct_fallback")
    return await _complete_bounded(FreshNarration, FRESH_NARRATION_PROMPT, user, timeout_s)


def prompt_agent_configured() -> bool:
    return bool(os.environ.get("LUNA_FOUNDRY_PROJECT_ENDPOINT", "").strip()
                and os.environ.get("LUNA_FOUNDRY_AGENT_NAME", "").strip())


async def probe_prompt_agent_token(timeout_s: float = 3.0) -> str:
    """Probe configured-agent token acquisition only; never returns or logs a token."""
    if not prompt_agent_configured():
        return "not_configured"
    if not os.environ.get("LUNA_FOUNDRY_AGENT_VERSION", "").strip():
        return "incomplete_configuration"
    credential = None
    try:
        credential = _agent_token_credential()
        async with asyncio.timeout(timeout_s):
            await credential.get_token("https://ai.azure.com/.default")
        return "ok"
    except TimeoutError:
        return "timeout"
    except Exception as exc:
        log.error("Configured Foundry agent token probe failed: %s", type(exc).__name__)
        return "failed"
    finally:
        if credential is not None:
            try:
                await credential.close()
            except Exception as exc:
                log.debug("Configured Foundry token probe credential cleanup failed: %s", type(exc).__name__)


def foundry_configured() -> bool:
    """Either the direct fallback or the optional project agent can supply prose."""
    direct = bool(os.environ.get("LUNA_FOUNDRY_KEY") and os.environ.get("LUNA_FOUNDRY_BASE"))
    return direct or prompt_agent_configured()


# --- embeddings, for the knowledge index ---------------------------------------------------------------------
# `knowledge_search` and `load_knowledge` embed with a separate deployment on the same Foundry endpoint and key.
# An empty deployment name means off: retrieval then falls back to the committed snapshots' token overlap, so
# nothing here can block a briefing or a test. The dimension count is bounded by the SQL column: Azure SQL's
# `VECTOR(n)` allows at most 1,998, and `sqldelivery.KNOWLEDGE_VECTOR_DIMENSIONS` is 1536, so the request asks
# the model for exactly that many (text-embedding-3-large accepts a `dimensions` argument).
EMBEDDING_DEFAULT_DIMENSIONS = 1536
EMBEDDING_DEFAULT_TIMEOUT_S = 10.0


def embedding_deployment() -> str:
    return os.environ.get("LUVN_EMBEDDING_DEPLOYMENT", "").strip()


def embedding_dimensions() -> int:
    raw = os.environ.get("LUVN_EMBEDDING_DIMENSIONS", "").strip()
    return int(raw) if raw else EMBEDDING_DEFAULT_DIMENSIONS


def embedding_timeout_s() -> float:
    raw = os.environ.get("LUVN_EMBEDDING_TIMEOUT", "").strip()
    return float(raw) if raw else EMBEDDING_DEFAULT_TIMEOUT_S


def embedding_base() -> str:
    """The endpoint the embedding request goes to. The chat and tool calls use the project-scoped Responses
    route (`…/api/projects/<name>/openai/v1`), which answers 404 to `/embeddings`; the embedding deployment is
    served by the same resource on the account-level `…/openai/v1` route (verified 2026-10-14 with a 200 on
    the account route and a 404 on the project route). The project segment is dropped unless
    `LUVN_EMBEDDING_BASE` names the route explicitly."""
    explicit = os.environ.get("LUVN_EMBEDDING_BASE", "").strip()
    if explicit:
        return explicit
    return re.sub(r"/api/projects/[^/]+", "", os.environ.get("LUNA_FOUNDRY_BASE", "").strip())


def embedding_configured() -> bool:
    """An embedding deployment is named and the direct Foundry endpoint and key are present."""
    return bool(embedding_deployment() and os.environ.get("LUNA_FOUNDRY_KEY") and os.environ.get("LUNA_FOUNDRY_BASE"))


def embed(texts: Sequence[str]) -> list[list[float]]:
    """One vector per text, in order. Synchronous on purpose: the assistant's tool handlers are plain functions
    called inline by the tool loop, and the knowledge loader is a script. Raises when not configured or when the
    provider fails; the callers decide what a failure means (retrieval falls back, the loader stops)."""
    if not embedding_configured():
        raise RuntimeError("no embedding deployment is configured (LUVN_EMBEDDING_DEPLOYMENT with LUNA_FOUNDRY_BASE/KEY)")
    from openai import OpenAI

    deployment = embedding_deployment()
    client = OpenAI(base_url=embedding_base(), api_key=os.environ["LUNA_FOUNDRY_KEY"],
                    timeout=embedding_timeout_s(), max_retries=FOUNDRY_MAX_RETRIES)
    started = time.monotonic()
    try:
        response = client.embeddings.create(model=deployment, input=list(texts), dimensions=embedding_dimensions())
    except Exception as exc:
        telemetry_record("embedding", started, model=deployment, status="error", **_error_facts(exc))
        raise
    vectors = [list(item.embedding) for item in sorted(response.data, key=lambda item: item.index)]
    if len(vectors) != len(texts):
        raise ValueError(f"embedding returned {len(vectors)} vectors for {len(texts)} texts")
    usage = getattr(response, "usage", None)
    telemetry_record("embedding", started, model=getattr(response, "model", None) or deployment, status="ok",
                     input_tokens=getattr(usage, "prompt_tokens", None))
    return vectors
