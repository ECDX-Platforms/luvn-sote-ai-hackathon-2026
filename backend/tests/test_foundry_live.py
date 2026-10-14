"""Live purpose-aware Foundry generation/support smoke test.

Run with: uv run --env-file .env pytest -q tests/test_foundry_live.py
"""

import asyncio
import re

import pytest

from app import llm
from app.pipeline import build_picture_for_encounter

pytestmark = pytest.mark.skipif(
    not llm.foundry_configured(), reason="no Foundry credentials (set LUNA_FOUNDRY_BASE and LUNA_FOUNDRY_KEY)"
)


def _model_family(model_id: str) -> str:
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model_id)


def test_live_encounter_narration_is_generated_and_supported(monkeypatch):
    # Exercise the direct path here; the shared live trial separately checks agent_reference.
    monkeypatch.delenv("LUNA_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("LUNA_FOUNDRY_AGENT_NAME", raising=False)
    monkeypatch.delenv("LUNA_FOUNDRY_AGENT_VERSION", raising=False)
    captured_models = []
    captured_client_kwargs = []
    real_client = llm._client

    def _spy_client(*args, **kwargs):
        captured_client_kwargs.append(kwargs.copy())
        client = real_client(*args, **kwargs)
        real_create = client.chat.completions.create

        async def create(*call_args, **call_kwargs):
            response = await real_create(*call_args, **call_kwargs)
            captured_models.append(getattr(response, "model", None))
            return response

        setattr(client.chat.completions, "create", create)
        return client

    monkeypatch.setattr(llm, "_client", _spy_client)

    picture = asyncio.run(build_picture_for_encounter("bound-encounter-002-laakitys-sairaanhoitaja"))
    assert picture.narration_generation_status == "generated"
    assert picture.narration_support_status == "supported"
    assert picture.narration_acceptance_status == "accepted"
    assert picture.narrative
    assert picture.situation.situation_type == "krooninen"
    assert picture.risk.risk_level == "korkea"
    assert picture.citations
    assert len(captured_models) == 1 and all(captured_models)
    assert all(_model_family(model) == _model_family(llm.FOUNDRY_MODEL) for model in captured_models)
    assert len(captured_client_kwargs) == 1
    assert all(kwargs.get("max_retries") == 0 for kwargs in captured_client_kwargs)
    assert all(0 < kwargs.get("timeout_s", 0) <= 70 for kwargs in captured_client_kwargs)
