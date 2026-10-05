"""Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Fidelity audit (2026-10-05): reference_curator + palette_strategist used to run as two mandatory
LLM hops before every illustrator call, each producing a free-text paraphrase illustrator then had
to re-synthesize — confirmed "prompt-by-committee dilution" root cause of worse-than-direct output.
Verifies: `run_visual_design_lead` only ever calls `illustrator` (+ optionally `composition_artist`),
never `reference_curator`/`palette_strategist`, and still threads illustrator's own
aesthetic_direction/palette_direction through to the final result."""
from unittest.mock import AsyncMock, patch

import pytest

from src.services.leads.visual_design_lead import run_visual_design_lead
from src.services.specialists.runner import AgenticStepResult, ToolCallRecord


def _illustration_result() -> AgenticStepResult:
    return AgenticStepResult(
        specialist_name="illustrator",
        model="fake-model",
        data={
            "image_prompt": "a red sneaker, studio lighting",
            "aspect_ratio": "16:9",
            "brand_facts_used": "",
            "tool_used": "base_image_generator",
            "aesthetic_direction": "clean studio product shot",
            "palette_direction": "brand red + neutral grey",
        },
        tool_calls=[ToolCallRecord(
            tool_name="base_image_generator", args={"aspect_ratio": "16:9"}, ok=True,
            data={"storage_ref": "storage://image.png"},
        )],
    )


@pytest.mark.asyncio
async def test_only_illustrator_runs_for_a_plain_generation_request() -> None:
    calls: list[str] = []

    async def _fake_review(specialist_name: str, **kwargs):
        calls.append(specialist_name)
        assert specialist_name == "illustrator", "only illustrator should run via run_specialist_with_review"
        return _illustration_result()

    async def _fake_agentic(specialist_name: str, **kwargs):
        calls.append(specialist_name)
        raise AssertionError(f"unexpected run_specialist_agentic call: {specialist_name}")

    with (
        patch("src.services.leads.visual_design_lead.run_specialist_with_review", new=AsyncMock(side_effect=_fake_review)),
        patch("src.services.leads.visual_design_lead.run_specialist_agentic", new=AsyncMock(side_effect=_fake_agentic)),
    ):
        result = await run_visual_design_lead(brief={}, user_message="generate a photorealistic hero shot of the sneaker")

    assert calls == ["illustrator"]
    assert "reference_curator" not in calls
    assert "palette_strategist" not in calls
    assert result.metadata["aesthetic_direction"] == "clean studio product shot"
    assert result.metadata["palette_direction"] == "brand red + neutral grey"
