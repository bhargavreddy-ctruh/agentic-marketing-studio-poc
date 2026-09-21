"""
Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Routing is now a real Tier-1 LLM classification (Memory.md, Phase 3 conformance audit — the
Phase 0 keyword heuristic never got upgraded despite the phase plan implying it would be), so
these tests mock the LLM provider rather than hitting a real network call, restoring the "no
network" unit-test guarantee while still exercising route()'s real parsing/fallback logic.
"""
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import ProviderUnavailable
from src.providers.llm.base import LLMResult
from src.services.orchestration.orchestrator import route, route_condition
from src.services.specialists.registry import load_all_specialists

# SPECIALIST_REGISTRY is populated at app startup (main.py's lifespan), not on import — a
# standalone unit test needs it populated too, since valid target_specialist names are checked
# against it. register_specialist() is idempotent (just (re)sets a dict key), safe to call here.
load_all_specialists()


def _fake_llm(response_json: str) -> AsyncMock:
    provider = AsyncMock()
    provider.complete.return_value = LLMResult(text=response_json, model="fake-model")
    return provider


@pytest.mark.asyncio
async def test_routes_full_video_via_llm_classification():
    state = {"user_message": "I want a video ad for my sneaker", "session_id": "s1"}
    with patch(
        "src.services.orchestration.orchestrator.get_llm_provider",
        return_value=_fake_llm('{"route": "full_video", "target_specialist": ""}'),
    ):
        result = await route(state)
    assert result["route"] == "full_video"
    assert route_condition(result) == "full_video"


@pytest.mark.asyncio
async def test_routes_direct_fix_with_valid_specialist():
    state = {"user_message": "just fix the overlay text", "session_id": "s1"}
    with patch(
        "src.services.orchestration.orchestrator.get_llm_provider",
        return_value=_fake_llm('{"route": "direct_fix", "target_specialist": "overlay_artist"}'),
    ):
        result = await route(state)
    assert result["route"] == "direct_fix"
    assert result["target_specialist"] == "overlay_artist"


@pytest.mark.asyncio
async def test_hallucinated_target_specialist_falls_back_to_full_image():
    """A named, real failure mode: the model claims direct_fix but names a specialist that isn't
    registered — must not route to something that doesn't exist."""
    state = {"user_message": "just fix the overlay text", "session_id": "s1"}
    with patch(
        "src.services.orchestration.orchestrator.get_llm_provider",
        return_value=_fake_llm('{"route": "direct_fix", "target_specialist": "made_up_specialist"}'),
    ):
        result = await route(state)
    assert result["route"] == "full_image"
    assert result["target_specialist"] is None


@pytest.mark.asyncio
async def test_falls_back_to_keyword_heuristic_when_llm_unavailable():
    """Both LLM gateways down — degrades to the old Phase 0 heuristic rather than crashing the
    turn, per the orchestrator's own documented fallback behavior."""
    state = {"user_message": "make me a hero shot for this product", "session_id": "s1"}
    provider = AsyncMock()
    provider.complete.side_effect = ProviderUnavailable("groq", "down")
    with patch("src.services.orchestration.orchestrator.get_llm_provider", return_value=provider):
        result = await route(state)
    assert result["route"] == "full_image"
