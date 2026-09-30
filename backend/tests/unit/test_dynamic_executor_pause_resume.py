"""
Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Real, live-found gap (2026-09-30, explicit user ask: "multi step plans also can stop and ask,
doesn't have to be restart"): a specialist facing genuine ambiguity mid-plan had no way to pause
and truly resume — every "pause" ended the turn and the next message restarted the whole plan
from scratch. Verifies: a step raising SpecialistNeedsClarification stops the loop (no later step
runs), persists exactly enough state to resume at that step, and a resume with the same
persisted state skips the already-completed step, reusing its real result, rather than re-running it.
"""
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import SpecialistNeedsClarification
from src.services.orchestration.graph import _dynamic_executor_node
from src.services.specialists.runner import AgenticStepResult, ToolCallRecord


def _fake_result(specialist_name: str, storage_ref: str) -> AgenticStepResult:
    return AgenticStepResult(
        specialist_name=specialist_name,
        model="fake-model",
        data={"image_prompt": f"{specialist_name} prompt", "aspect_ratio": "1:1", "brand_facts_used": ""},
        tool_calls=[ToolCallRecord(tool_name="base_image_generator", args={}, ok=True, data={"storage_ref": storage_ref})],
    )


_PLAN = [
    {"specialist": "reference_curator", "instruction": "gather references"},
    {"specialist": "illustrator", "instruction": "which product though?"},
    {"specialist": "composition_artist", "instruction": "final composite"},
]


@pytest.mark.asyncio
async def test_step_raising_clarification_stops_the_loop_and_persists_resume_state():
    step0_result = _fake_result("reference_curator", "ref-step0")

    async def _run_review(specialist, *, context, needs_retry, reminder, brief=None):
        if specialist == "reference_curator":
            return step0_result
        if specialist == "illustrator":
            raise SpecialistNeedsClarification(
                "illustrator", "Which linked product is this about?",
                options=[{"id": "p1", "label": "2a"}, {"id": "p2", "label": "4a"}],
            )
        raise AssertionError("composition_artist must never run — it comes after the paused step")

    state = {"dynamic_plan": _PLAN, "user_message": "make the campaign image", "brief": {"idea": "campaign"}}
    with patch("src.services.orchestration.graph.run_specialist_with_review", new=AsyncMock(side_effect=_run_review)):
        result_state = await _dynamic_executor_node(state)

    assert result_state["result"]["message"] == "Which linked product is this about?"
    assert result_state["result"]["options"] == [{"id": "p1", "label": "2a"}, {"id": "p2", "label": "4a"}]

    paused = result_state["paused_plan"]
    assert paused["route"] == "dynamic"
    assert paused["next_step_index"] == 1  # step 1 (illustrator) is the one pending
    assert paused["completed_results"]["all_metadata"]["step_0_reference_curator"] == step0_result.data
    assert paused["completed_results"]["latest_storage_ref"] == "ref-step0"


@pytest.mark.asyncio
async def test_resume_skips_completed_steps_and_reuses_their_real_result():
    step0_result = _fake_result("reference_curator", "ref-step0")
    illustrator_result = _fake_result("illustrator", "ref-illustrator")
    composition_result = _fake_result("composition_artist", "ref-final")

    calls: list[str] = []

    async def _run_review(specialist, *, context, needs_retry, reminder, brief=None):
        calls.append(specialist)
        if specialist == "illustrator":
            # Confirm the resumed step actually sees the user's real answer.
            joined_text = " ".join(p.get("text", "") for p in context if isinstance(p, dict))
            assert "Nothing Phone 4a" in joined_text
            return illustrator_result
        if specialist == "composition_artist":
            return composition_result
        raise AssertionError("reference_curator already completed before the pause — must not re-run")

    # Simulates session_service.py's resume: `_resume_next_step_index`/`_resume_completed_results`
    # seeded from the persisted `paused_plan`, `clarification_answer` from the user's real reply.
    resume_state = {
        "dynamic_plan": _PLAN,
        "user_message": "the 4a one",
        "brief": {
            "idea": "campaign",
            "clarification_answer": "It's about the Nothing Phone 4a.",
            "_resume_next_step_index": 1,
            "_resume_completed_results": {
                "all_metadata": {"step_0_reference_curator": step0_result.data},
                "latest_storage_ref": "ref-step0",
                "latest_tool": "base_image_generator",
                "last_completed_specialist": "reference_curator",
                "extra_elements": [],
            },
        },
    }
    with patch("src.services.orchestration.graph.run_specialist_with_review", new=AsyncMock(side_effect=_run_review)):
        result_state = await _dynamic_executor_node(resume_state)

    assert calls == ["illustrator", "composition_artist"]  # reference_curator was NOT re-run
    assert result_state["result"]["storage_ref"] == "ref-final"
    assert "step_0_reference_curator" in result_state["result"]["metadata"]
    assert result_state.get("paused_plan") is None
