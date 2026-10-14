"""Shared test configuration.

`LUNA_FOUNDRY_MODEL` has no code default (an unset model must fail loudly rather
than silently pick a deployment a route may not serve), so tests that exercise the
generation path set it here once.
"""

import pytest


@pytest.fixture(autouse=True)
def _configured_foundry_model(monkeypatch):
    monkeypatch.setenv("LUNA_FOUNDRY_MODEL", "gpt-6.1-sol")


@pytest.fixture(autouse=True)
def _no_ambient_access_policy(monkeypatch):
    """The suite describes the open demo unless a test configures a policy itself.

    `LUVN_ACCESS_POLICY`/`LUVN_ACCESS_POLICY_FILE` are deployment configuration, and the tests that pin the
    open behaviour (`tests/test_access_policy.py`) assert it with the variables explicitly unset. This
    clears them for every test so that a developer's shell — or a stray variable in a CI environment —
    cannot turn the suite's "no policy configured" cases into scoped ones. A test that wants a policy sets
    it with `monkeypatch`, which restores this state after it.
    """
    monkeypatch.delenv("LUVN_ACCESS_POLICY", raising=False)
    monkeypatch.delenv("LUVN_ACCESS_POLICY_FILE", raising=False)
