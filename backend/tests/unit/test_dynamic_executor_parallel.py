"""
Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Verifies the parallel dynamic-plan dispatch added to `_dynamic_executor_node` (2026-09-26,
parallel-dispatch investigation): the orchestrator's own `parallel_group` claim on a plan step is
verified in code before being trusted — a group of genuinely independent fresh-generation steps
is dispatched via `run_concurrent_specialists`, but a group containing an asset-mutating
specialist (which edits an EXISTING asset) is never trusted, falling back to sequential execution.
"""
from unittest.mock import AsyncMock, patch

import pytest

from src.services.orchestration.graph import _dynamic_executor_node
from src.services.specialists.runner import AgenticStepResult, ToolCallRecord


def _fake_result(specialist_name: str, storage_ref: str) -> AgenticStepResult:
    return AgenticStepResult(
        specialist_name=specialist_name,
        model="fake-model",
        data={"image_prompt": f"{specialist_name} prompt", "aspect_ratio": "1:1", "brand_facts_used": ""},
        tool_calls=[
            ToolCallRecord(
                tool_name="base_image_generator",
                args={},
                ok=True,
                data={"storage_ref": storage_ref},
            )
        ],
    )


@pytest.mark.asyncio
async def test_verified_independent_steps_run_via_run_concurrent_specialists():
    plan = [
        {"specialist": "illustrator", "instruction": "variant A", "parallel_group": 1},
        {"specialist": "illustrator", "instruction": "variant B", "parallel_group": 1},
    ]
    state = {"dynamic_plan": plan, "user_message": "make two hero shot variants", "brief": {"idea": "two variants"}}

    with patch(
        "src.services.orchestration.graph.run_concurrent_specialists",
        new=AsyncMock(
            return_value={
                "step_0_illustrator": _fake_result("illustrator", "ref-a"),
                "step_1_illustrator": _fake_result("illustrator", "ref-b"),
            }
        ),
    ) as mock_concurrent, patch(
        "src.services.orchestration.graph.run_specialist_with_review",
        new=AsyncMock(side_effect=AssertionError("should not run sequentially for a verified-safe group")),
    ):
        result_state = await _dynamic_executor_node(state)

    mock_concurrent.assert_awaited_once()
    assert result_state["result"]["storage_ref"] in ("ref-a", "ref-b")


@pytest.mark.asyncio
async def test_group_with_asset_mutating_specialist_falls_back_to_sequential():
    # composition_artist EDITS an existing asset — must never be trusted into a concurrent group,
    # even if the orchestrator itself claimed parallel_group.
    plan = [
        {"specialist": "illustrator", "instruction": "base image", "parallel_group": 1},
        {"specialist": "composition_artist", "instruction": "edit it", "parallel_group": 1},
    ]
    state = {"dynamic_plan": plan, "user_message": "make an image then edit it", "brief": {"idea": "edit flow"}}

    with patch(
        "src.services.orchestration.graph.run_concurrent_specialists",
        new=AsyncMock(side_effect=AssertionError("must not run this unsafe group concurrently")),
    ), patch(
        "src.services.orchestration.graph.run_specialist_with_review",
        new=AsyncMock(
            side_effect=[
                _fake_result("illustrator", "ref-base"),
                _fake_result("composition_artist", "ref-edited"),
            ]
        ),
    ) as mock_sequential:
        result_state = await _dynamic_executor_node(state)

    assert mock_sequential.await_count == 2
    assert result_state["result"]["storage_ref"] == "ref-edited"
