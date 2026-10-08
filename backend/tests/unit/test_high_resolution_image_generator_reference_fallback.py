"""
Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Real, live-found gap (2026-10-07, reference-image routing complaint): unlike
`base_image_generator` (which already falls back to the shared tool `context`'s
product_photo_storage_ref/reference_storage_ref when the model's own call omits an explicit
reference), `high_resolution_image_generator` had no such backstop — a real, relevant referenced
element could be silently dropped if the model's own tool call simply forgot to attach it.
Verifies: the tool falls back to a context-provided reference when the model omits
`reference_storage_refs` entirely, and never overrides an explicit (even empty) choice the model
did make.
"""
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from src.services.tools.high_resolution_image_generator import HighResolutionImageGeneratorTool


@dataclass
class _FakeResult:
    image_bytes: bytes
    mime_type: str
    provider_name: str


class _FakeProvider:
    def __init__(self):
        self.last_call = None

    async def generate(self, **kwargs):
        self.last_call = kwargs
        return _FakeResult(image_bytes=b"fake", mime_type="image/jpeg", provider_name="fake")


@pytest.mark.asyncio
async def test_falls_back_to_context_reference_when_model_omits_refs_entirely():
    tool = HighResolutionImageGeneratorTool()
    fake_provider = _FakeProvider()
    context = {
        "referenced_elements_context": [{"storage_ref": "ref-from-context", "element_type": "image"}],
    }
    with patch(
        "src.services.tools.high_resolution_image_generator.get_nano_banana_2_provider",
        return_value=fake_provider,
    ), patch(
        "src.services.tools.high_resolution_image_generator.load_asset",
        new=AsyncMock(return_value=(b"bytes", "image/jpeg")),
    ), patch(
        "src.services.tools.high_resolution_image_generator.save_asset",
        new=AsyncMock(return_value="new-storage-ref"),
    ):
        result = await tool.run(
            {"prompt": "a crazy collab scene", "resolution": "2K"},
            context=context,
        )

    assert result.ok is True
    assert fake_provider.last_call["reference_images"] is not None
    assert len(fake_provider.last_call["reference_images"]) == 1


@pytest.mark.asyncio
async def test_never_overrides_an_explicit_model_supplied_refs_list():
    tool = HighResolutionImageGeneratorTool()
    fake_provider = _FakeProvider()
    context = {
        "referenced_elements_context": [{"storage_ref": "ref-from-context", "element_type": "image"}],
    }
    with patch(
        "src.services.tools.high_resolution_image_generator.get_nano_banana_2_provider",
        return_value=fake_provider,
    ), patch(
        "src.services.tools.high_resolution_image_generator.load_asset",
        new=AsyncMock(return_value=(b"bytes", "image/jpeg")),
    ), patch(
        "src.services.tools.high_resolution_image_generator.save_asset",
        new=AsyncMock(return_value="new-storage-ref"),
    ):
        result = await tool.run(
            {"prompt": "a crazy collab scene", "resolution": "2K", "reference_storage_refs": ["explicit-ref"]},
            context=context,
        )

    assert result.ok is True
    assert fake_provider.last_call["reference_images"] is not None
    assert len(fake_provider.last_call["reference_images"]) == 1
