"""
Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Routing is now a real Tier-1 LLM classification (Memory.md, Phase 3 conformance audit — the
Phase 0 keyword heuristic never got upgraded despite the phase plan implying it would be), so
these tests mock the LLM provider rather than hitting a real network call, restoring the "no
network" unit-test guarantee while still exercising route()'s real parsing/fallback logic.
"""
import json
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
async def test_falls_back_to_keyword_heuristic_when_llm_and_laya_unavailable():
    """Both the LLM gateways AND Laya are down — degrades to the old Phase 0 heuristic rather than
    crashing the turn, per the orchestrator's own documented fallback chain.

    Real, live-found bug (2026-09-23): `LayaProvider.predict_choice` silently failed on every real
    call (three separate mismatches against the installed `laya` 0.3.7 API — see
    `laya_provider.py`), always returning `None` — so this test passed for the wrong reason: it
    never actually exercised "Laya also down," it accidentally always hit that branch because Laya
    was broken. Now that Laya works, the same test must mock Laya failing explicitly to still test
    the branch its own docstring describes; see the next test for the now-real Laya-succeeds path.

    Also fixes a second, pre-existing, unrelated bug this same test masked: `_keyword_fallback_route`
    (a parallel session's change) now falls through to `"dynamic"` by default for a message that
    matches none of its keyword lists — not `"full_image"`, which this test asserted before this
    fix and which was already stale on a clean base, confirmed via `git stash` earlier this
    session. Updated to match the real current fallback behavior.
    """
    state = {"user_message": "make me a hero shot for this product", "session_id": "s1"}
    provider = AsyncMock()
    provider.complete.side_effect = ProviderUnavailable("groq", "down")
    with (
        patch("src.services.orchestration.orchestrator.get_llm_provider", return_value=provider),
        patch(
            "src.providers.llm.laya_provider.LayaProvider.predict_choice",
            AsyncMock(return_value=None),
        ),
    ):
        result = await route(state)
    assert result["route"] == "dynamic"


@pytest.mark.asyncio
async def test_asks_for_approval_via_laya_when_llm_unavailable_but_laya_isnt():
    """LLM gateways down but Laya IS available: real documented fallback behavior is to ask the
    user to confirm Laya's suggested specialist, not to silently degrade straight to the keyword
    heuristic. Added 2026-09-23 alongside the `laya_provider.py` fix that made this path real."""
    state = {"user_message": "make me a hero shot for this product", "session_id": "s1"}
    provider = AsyncMock()
    provider.complete.side_effect = ProviderUnavailable("groq", "down")
    with (
        patch("src.services.orchestration.orchestrator.get_llm_provider", return_value=provider),
        patch(
            "src.providers.llm.laya_provider.LayaProvider.predict_choice",
            AsyncMock(return_value="composition_artist"),
        ),
    ):
        result = await route(state)
    assert result["route"] == "approval_required"
    assert result["result"]["options"][0]["id"] == "laya_approve_composition_artist"


@pytest.mark.asyncio
async def test_thumbnail_request_with_reference_accepts_dynamic_illustrator_route():
    """Regression test for a confirmed bug: 'make a youtube thumbnail' referencing an existing
    product photo must not be impossible to route to dynamic/illustrator. This verifies route()
    correctly accepts and passes through a dynamic/illustrator decision when the model makes one —
    the real judgment call (is this an in-place edit vs. a new deliverable) lives in the
    orchestrator's prompt (Rule 7b) and can only be fully verified against a live model; this test
    only proves the plumbing doesn't reject a correct decision."""
    state = {
        "user_message": "make a youtube thumbnail out of this phone photo",
        "session_id": "s1",
        "brief": {
            "referenced_elements_context": [
                {"id": "el1", "element_type": "image", "description": "Nothing Phone product shot on white background", "storage_ref": "ref123"}
            ]
        },
    }
    plan = [{"specialist": "illustrator", "instruction": "Use storage_ref ref123 as reference_storage_ref for an image-to-image 16:9 thumbnail generation."}]
    with patch(
        "src.services.orchestration.orchestrator.get_llm_provider",
        return_value=_fake_llm(json.dumps({"route": "dynamic", "target_specialist": None, "plan": plan})),
    ):
        result = await route(state)
    assert result["route"] == "dynamic"
    assert result["dynamic_plan"][0]["specialist"] == "illustrator"
