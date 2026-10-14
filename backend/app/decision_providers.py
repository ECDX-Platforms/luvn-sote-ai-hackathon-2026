"""The decision model: one provider, one protocol.

Microsoft-Decision-1 on Foundry accepts a `state` plus typed `questions` (`noul` / `choice` / `score`)
request at `<foundry-host>/providers/microsoft/v1/systemone` and returns an `answers` object with
probabilities and usage. Microsoft's own documentation calls the pattern "similar to a system-one API".

There is exactly one provider. That is the point rather than an omission: the classifier is advisory in two
places — reading permitted notes for selection, and verifying generated prose — and both must be the same
model under the same terms, otherwise a difference in behaviour between the two roles has no owner. Adding a
second provider back would reintroduce a second vendor, a second set of terms and a second jurisdiction to
assess, which is precisely what the single-vendor choice avoids (see ADR-0018).

Auth is Foundry's `api-key` header. The deployment name is configuration (`LUVN_DECISION_DEPLOYMENT`), so a
model version bump is a setting rather than a code change.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx

from . import source

# The live deployment reads its host from the environment (`LUNA_FOUNDRY_BASE`). This placeholder only
# keeps a deployment that is configured without a base URL from silently pointing at someone else's
# resource.
DEFAULT_FOUNDRY_HOST = "https://your-foundry-resource.cognitiveservices.azure.com"
FOUNDRY_DECISION_PATH = "/providers/microsoft/v1/systemone"
# "Microsoft Decision 1 1M Tokens" is listed at 0.042 for swedencentral in the retail price feed. Kept for
# order-of-magnitude cost arithmetic in the evals, not as billing truth.
DECISION_INPUT_USD_PER_MILLION = 0.042
MAX_RESPONSE_BYTES = 65_536
DEFAULT_DEPLOYMENT = "Decision-1"
# The service reports its own identity in the response, which is not the deployment name we sent, so the
# response's `model` is checked against this set rather than against the request.
ACCEPT_MODELS = frozenset({"microsoft-decision-1"})


class ProviderError(RuntimeError):
    """The provider could not return a usable decision."""


@dataclass(frozen=True)
class Provider:
    """The decision endpoint, with the deployment name it expects in `model`."""

    endpoint: str
    model: str
    auth_header: tuple[str, str]
    name: str = "microsoft-decision-1"
    input_usd_per_million: float | None = DECISION_INPUT_USD_PER_MILLION
    accept_models: frozenset[str] = field(default=ACCEPT_MODELS)

    async def call(self, state: dict, questions: dict, *, timeout_s: float = 20.0) -> dict:
        """One typed call. Returns the parsed envelope; the caller applies its own validation."""
        request = {"model": self.model, "state": state, "questions": questions}
        body = json.dumps(request, ensure_ascii=False, sort_keys=True).encode()
        # Transport-boundary enforcement (work order L2B): no caller uses this method today, but it is a
        # second real transport that builds its own request body, so it gets the same guard as
        # `decision_model._post` rather than being trusted to stay dead code.
        source.assert_model_safe(body.decode())
        if len(body) > MAX_RESPONSE_BYTES:
            raise ProviderError(f"{self.name}: oversized request ({len(body)} bytes)")
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=timeout_s, read=timeout_s,
                                                           write=timeout_s, pool=timeout_s),
                                     trust_env=True, verify=True, follow_redirects=False) as client:
            async with client.stream("POST", self.endpoint, content=body,
                                     headers={self.auth_header[0]: self.auth_header[1],
                                              "Content-Type": "application/json"}) as response:
                response.raise_for_status()
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > MAX_RESPONSE_BYTES:
                        raise ProviderError(f"{self.name}: oversized response")
        try:
            data = json.loads(bytes(chunks))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderError(f"{self.name}: invalid JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
            raise ProviderError(f"{self.name}: no answers object")
        if set(data["answers"]) != set(questions):
            raise ProviderError(f"{self.name}: incomplete answers for {sorted(questions)}")
        return data


def _foundry_host(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    base = source.get("LUNA_FOUNDRY_BASE", "").strip()
    project = source.get("LUNA_FOUNDRY_PROJECT_ENDPOINT", "").strip()
    for candidate in (base, project):
        if candidate.startswith("https://"):
            host = candidate.split("/api/")[0].rstrip("/")
            # The project route ends in /openai/v1; the Microsoft provider path hangs off the host root.
            host = host.split("/openai")[0]
            if host:
                return host
    return DEFAULT_FOUNDRY_HOST


def provider(env: Mapping[str, str] | None = None) -> Provider | None:
    """The configured decision model, or None when it is not configured.

    Pure and per-call: a missing key degrades every stage to its own fail-closed path instead of raising at
    import, and configuration can change without a restart.
    """
    source = os.environ if env is None else env
    key = source.get("LUNA_FOUNDRY_KEY", "").strip()
    if not key:
        return None
    deployment = source.get("LUVN_DECISION_DEPLOYMENT", "").strip() or DEFAULT_DEPLOYMENT
    return Provider(endpoint=_foundry_host(source) + FOUNDRY_DECISION_PATH, model=deployment,
                    auth_header=("api-key", key))


def configured(env: Mapping[str, str] | None = None) -> bool:
    return provider(env) is not None


def providers(env: Mapping[str, str] | None = None) -> list[Provider]:
    """Every provider this environment can reach. At most one, kept as a list for the frozen comparison
    harnesses, which read a recorded two-provider artifact through `--check` and are never re-run."""
    found = provider(env)
    return [found] if found else []
