"""The decision provider: one model, one key, one endpoint.

This is the configuration that decides which model reads a person's notes, so the properties that matter are
pinned here rather than implied: there is exactly one provider, it is Foundry's, it is absent rather than
broken when unconfigured, and its credential cannot travel to another host.
"""

from __future__ import annotations

from app.decision_providers import Provider, configured, provider, providers

FOUNDRY = {"LUNA_FOUNDRY_KEY": "fake",
           "LUNA_FOUNDRY_BASE": "https://x.services.ai.azure.com/api/projects/p/openai/v1"}


def test_the_provider_is_foundry_and_is_configured_from_the_foundry_key():
    found = provider(FOUNDRY)
    assert isinstance(found, Provider)
    assert found.name == "microsoft-decision-1"
    assert found.endpoint == "https://x.services.ai.azure.com/providers/microsoft/v1/systemone"
    assert found.auth_header == ("api-key", "fake")
    assert found.accept_models == frozenset({"microsoft-decision-1"})


def test_an_unconfigured_provider_is_absent_rather_than_broken():
    """Every stage has its own fail-closed path; a missing key must reach it instead of raising at import."""
    assert provider({}) is None
    assert configured({}) is False
    assert providers({}) == []


def test_there_is_exactly_one_provider_and_no_second_vendor():
    """One model for both roles. A second provider would reintroduce a second vendor and a second set of
    terms, which is the thing the single-vendor choice exists to avoid (ADR-0018)."""
    available = providers(FOUNDRY)
    assert len(available) == 1
    assert {found.name for found in available} == {"microsoft-decision-1"}


def test_the_deployment_name_is_configuration_not_code():
    found = provider({**FOUNDRY, "LUVN_DECISION_DEPLOYMENT": "Decision-1-eu"})
    assert found is not None and found.model == "Decision-1-eu"


def test_the_host_is_derived_from_either_foundry_route_shape():
    project = provider({"LUNA_FOUNDRY_KEY": "k",
                        "LUNA_FOUNDRY_PROJECT_ENDPOINT": "https://y.services.ai.azure.com/api/projects/p"})
    assert project is not None
    assert project.endpoint == "https://y.services.ai.azure.com/providers/microsoft/v1/systemone"


def test_the_key_travels_as_a_foundry_header_and_never_as_a_bearer_token():
    found = provider(FOUNDRY)
    assert found is not None
    assert found.auth_header[0] == "api-key"
    assert "Bearer" not in found.auth_header[1]
