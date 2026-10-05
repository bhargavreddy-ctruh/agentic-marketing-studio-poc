"""Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Fidelity audit (2026-10-05): a known deliverable (e.g. a YouTube thumbnail, 1280x720) specifies
real pixel dimensions, not just an aspect ratio — "approximately 16:9" from the generation model
isn't the same as exactly 1280x720. Verifies: when `brief["deliverable"]` resolves to a spec with
real width/height, `run_visual_design_lead` runs a real `image_crop_resize` pass and returns its
output storage_ref as the final result."""
from unittest.mock import AsyncMock, patch

import pytest

from src.services.leads.visual_design_lead import run_visual_design_lead
from src.services.specialists.runner import AgenticStepResult, ToolCallRecord
from src.services.tools.base import ToolResult


def _illustration_result() -> AgenticStepResult:
    return AgenticStepResult(
        specialist_name="illustrator",
        model="fake-model",
        data={
            "image_prompt": "a phone on a desk, youtube thumbnail style",
            "aspect_ratio": "16:9",
            "brand_facts_used": "",
            "tool_used": "base_image_generator",
            "aesthetic_direction": "bold, high-contrast",
            "palette_direction": "brand blue + white",
        },
        tool_calls=[ToolCallRecord(
            tool_name="base_image_generator", args={"aspect_ratio": "16:9"}, ok=True,
            data={"storage_ref": "storage://raw.png"},
        )],
    )


@pytest.mark.asyncio
async def test_exact_sizing_runs_for_a_known_deliverable() -> None:
    async def _fake_review(specialist_name: str, **kwargs):
        return _illustration_result()

    fake_resize = AsyncMock(return_value=ToolResult(ok=True, data={"storage_ref": "storage://resized_1280x720.png"}))

    with (
        patch("src.services.leads.visual_design_lead.run_specialist_with_review", new=AsyncMock(side_effect=_fake_review)),
        patch("src.services.leads.visual_design_lead.get_tool", return_value=type("T", (), {"run": fake_resize})()),
    ):
        result = await run_visual_design_lead(
            brief={"deliverable": "youtube_thumbnail"}, user_message="generate a youtube thumbnail for my unboxing video",
        )

    fake_resize.assert_awaited_once()
    args = fake_resize.call_args[0][0]
    assert args["target_width"] == 1280
    assert args["target_height"] == 720
    assert args["storage_ref"] == "storage://raw.png"
    assert result.storage_ref == "storage://resized_1280x720.png"
