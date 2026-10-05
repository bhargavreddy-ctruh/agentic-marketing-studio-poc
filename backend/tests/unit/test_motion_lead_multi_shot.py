"""Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Fidelity audit (2026-10-05): a planned multi-shot video used to always render only `shots[0]` —
`video_stitcher` existed and worked but was starved to a single clip. Verifies: a 2-shot narrative
renders TWO clips (one scene_lead call for the second shot, two camera_director calls), chains
continuity via the extracted last frame, and passes both raw clips to video_editor_cutter which
stitches them into the final video."""
from unittest.mock import AsyncMock, patch

import pytest

from src.services.leads.base import NarrativePlan, ScenePlan
from src.services.leads.motion_lead import run_motion_lead
from src.services.specialists.runner import AgenticStepResult, ToolCallRecord


def _camera_result(clip_ref: str) -> AgenticStepResult:
    return AgenticStepResult(
        specialist_name="camera_director",
        model="fake-model",
        data={"motion_prompt": "slow dolly in", "camera_motion": "slow dolly in"},
        tool_calls=[ToolCallRecord(
            tool_name="base_video_generator", args={"aspect_ratio": "16:9"}, ok=True,
            data={"storage_ref": clip_ref},
        )],
    )


def _empty_result(name: str) -> AgenticStepResult:
    return AgenticStepResult(specialist_name=name, model="", data={}, tool_calls=[])


@pytest.mark.asyncio
async def test_multi_shot_renders_all_shots_and_stitches() -> None:
    narrative = NarrativePlan(
        shots=("Shot 1: wide establishing shot", "Shot 2: close-up on product"),
        overall_story="unboxing story",
        script_line=None,
        pacing_target="moderate",
        shot_list_storage_ref=None,
    )
    scene_shot_1 = ScenePlan(
        environment_description="studio", prop_description=None, lighting_description="soft",
        scene_image_storage_ref="storage://scene1.png", scene_description_storage_ref=None,
    )
    scene_shot_2 = ScenePlan(
        environment_description="studio close-up", prop_description=None, lighting_description="soft",
        scene_image_storage_ref="storage://scene2.png", scene_description_storage_ref=None,
    )

    camera_calls: list[str] = []

    async def _fake_review(specialist_name: str, *, context: str, **kwargs):
        if specialist_name == "camera_director":
            camera_calls.append(context)
            clip_ref = f"storage://clip{len(camera_calls)}.mp4"
            return _camera_result(clip_ref)
        if specialist_name == "sound_designer":
            return kwargs.get("first_result") or _empty_result("sound_designer")
        raise AssertionError(f"unexpected run_specialist_with_review call: {specialist_name}")

    async def _fake_scene_lead(*, shot_description: str, brief=None):
        assert shot_description == narrative.shots[1]
        return scene_shot_2

    editor_contexts: list[str] = []

    async def _fake_agentic(specialist_name: str, *, context: str, **kwargs):
        if specialist_name == "video_editor_cutter":
            editor_contexts.append(context)
            return AgenticStepResult(
                specialist_name="video_editor_cutter", model="fake-model", data={"pacing_note": "steady"},
                tool_calls=[ToolCallRecord(
                    tool_name="video_stitcher", args={}, ok=True,
                    data={"storage_ref": "storage://stitched.mp4", "clip_count": 2},
                )],
            )
        if specialist_name == "sound_designer":
            return _empty_result("sound_designer")
        if specialist_name == "overlay_artist":
            return _empty_result("overlay_artist")
        raise AssertionError(f"unexpected specialist call: {specialist_name}")

    with (
        patch("src.services.leads.motion_lead.run_specialist_with_review", new=AsyncMock(side_effect=_fake_review)),
        patch("src.services.leads.motion_lead.run_scene_lead", new=AsyncMock(side_effect=_fake_scene_lead)),
        patch("src.services.leads.motion_lead.run_specialist_agentic", new=AsyncMock(side_effect=_fake_agentic)),
        patch("src.services.leads.motion_lead.load_asset", new=AsyncMock(return_value=(b"fake-video-bytes", "video/mp4"))),
        patch("src.services.leads.motion_lead.extract_last_frame", new=AsyncMock(return_value=b"fake-last-frame")),
        patch("src.services.leads.motion_lead.save_asset", new=AsyncMock(return_value="storage://continuity-frame.png")),
    ):
        result = await run_motion_lead(brief={"idea": "unboxing video"}, narrative=narrative, scene=scene_shot_1)

    assert len(camera_calls) == 2
    assert "last_frame_storage_ref" in camera_calls[1]
    assert "storage://continuity-frame.png" in camera_calls[1]
    assert len(editor_contexts) == 1
    assert "storage://clip1.mp4" in editor_contexts[0]
    assert "storage://clip2.mp4" in editor_contexts[0]
    assert result.storage_ref == "storage://stitched.mp4"
    assert result.metadata["shot_count_rendered"] == 2
    assert result.metadata["raw_clip_storage_refs"] == ["storage://clip1.mp4", "storage://clip2.mp4"]
    assert result.metadata["stitched"] is True
