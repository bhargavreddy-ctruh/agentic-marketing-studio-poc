"""
Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Real, live-found bug (2026-10-07, Monster Energy "SALE" overlay complaint, traced via
correlation_id 160e6c1d6e0240f99f7e4cd636231b56): a dynamic plan's generating specialist
(illustrator) could exhaust its tool-calling attempts without ever calling a real
asset-producing tool — only lookup tools (product_lookup/brand_kit_lookup), which never carry a
storage_ref. Before this fix, `_dynamic_executor_node`'s per-step fold loop silently kept
`latest_storage_ref` at whatever it was before this step (nothing, on a fresh turn) and moved on
to the next step (overlay_artist) as if the illustrator step had succeeded — overlay_artist then
fell back to drawing on a stale, unrelated referenced element already in context, producing a
pasted-looking result with zero real generation behind it. Verifies: when a generating
specialist's step produces no real storage_ref, the plan stops right there with an honest
failure message instead of silently continuing to the next step.
"""
from unittest.mock import AsyncMock, patch

import pytest

from src.services.orchestration.graph import _dynamic_executor_node
from src.services.specialists.registry import load_all_specialists
from src.services.specialists.runner import AgenticStepResult, ToolCallRecord

load_all_specialists()

_PLAN = [
    {"specialist": "illustrator", "instruction": "generate a crazy Monster Energy collab image"},
    {"specialist": "overlay_artist", "instruction": "add a SALE banner"},
]


@pytest.mark.asyncio
async def test_generating_specialist_producing_no_real_asset_stops_the_plan_honestly():
    # illustrator "succeeds" per its own JSON, but its only real tool calls were lookups —
    # never an actual image-generation tool — so it has no storage_ref to show for it.
    illustrator_result = AgenticStepResult(
        specialist_name="illustrator",
        model="fake-model",
        data={"image_prompt": "a crazy collab scene", "aspect_ratio": "1:1", "brand_facts_used": "x"},
        tool_calls=[
            ToolCallRecord(tool_name="product_lookup", args={}, ok=True, data={"configured": True}),
            ToolCallRecord(tool_name="brand_kit_lookup", args={}, ok=True, data={"configured": True}),
        ],
    )

    async def _run_review(specialist, *, context, needs_retry, reminder, brief=None):
        if specialist == "illustrator":
            return illustrator_result
        raise AssertionError("overlay_artist must never run — illustrator never produced a real asset")

    state = {
        "dynamic_plan": _PLAN,
        "user_message": "crazy Monster Energy collab image with a SALE banner",
        "brief": {"idea": "monster energy collab"},
    }
    with patch("src.services.orchestration.graph.run_specialist_with_review", new=AsyncMock(side_effect=_run_review)):
        result_state = await _dynamic_executor_node(state)

    assert "wasn't confident enough" in result_state["result"]["message"]
    assert result_state["result"]["options"][0]["id"] == "retry"
    assert result_state.get("paused_plan") is None
