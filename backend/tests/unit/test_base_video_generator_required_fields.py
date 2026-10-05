"""Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Fidelity audit (2026-10-05): `base_video_generator` used to silently default `model`/`resolution`/
`duration_seconds` and never forwarded `camera_motion`/`last_frame_storage_ref` to the provider at
all, even though `camera_director.md` already instructed the LLM to pass them. Verifies: the tool
now rejects a call missing any required field, and forwards camera_motion/last_frame correctly."""
from unittest.mock import AsyncMock, patch

import pytest

from src.providers.video.base import VideoResult
from src.services.tools.base_video_generator import BaseVideoGeneratorTool


@pytest.mark.asyncio
async def test_rejects_call_missing_required_fields() -> None:
    tool = BaseVideoGeneratorTool()
    result = await tool.run({
        "prompt": "animate the product",
        "source_image_storage_ref": "storage://scene.png",
        # model/resolution/duration_seconds/camera_motion deliberately omitted
    })
    assert result.ok is False
    assert "required" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_forwards_camera_motion_and_last_frame_to_provider() -> None:
    tool = BaseVideoGeneratorTool()
    fake_provider = AsyncMock()
    fake_provider.generate.return_value = VideoResult(
        video_bytes=b"fake", mime_type="video/mp4", provider_name="replicate", duration_seconds=5,
    )

    async def _fake_load_asset(ref: str):
        return (b"fake-bytes", "image/png")

    with (
        patch("src.services.tools.base_video_generator.get_video_provider", return_value=fake_provider),
        patch("src.services.tools.base_video_generator.load_asset", new=AsyncMock(side_effect=_fake_load_asset)),
        patch("src.services.tools.base_video_generator.save_asset", new=AsyncMock(return_value="storage://out.mp4")),
    ):
        result = await tool.run({
            "prompt": "animate the product",
            "source_image_storage_ref": "storage://scene.png",
            "last_frame_storage_ref": "storage://prev-last-frame.png",
            "camera_motion": "slow dolly in",
            "model": "bytedance/seedance-2.0-fast",
            "resolution": "720p",
            "duration_seconds": 5,
            "generate_audio": False,
        })

    assert result.ok is True
    fake_provider.generate.assert_awaited_once()
    _, kwargs = fake_provider.generate.call_args
    assert kwargs["camera_motion"] == "slow dolly in"
    assert kwargs["last_frame_bytes"] == b"fake-bytes"
    assert kwargs["generate_audio"] is False
    assert kwargs["model"] == "bytedance/seedance-2.0-fast"
