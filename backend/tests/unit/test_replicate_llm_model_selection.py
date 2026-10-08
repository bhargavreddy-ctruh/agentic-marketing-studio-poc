import pytest
from unittest.mock import patch

from src.providers.llm.base import ModelTier
from src.providers.llm.replicate_llm import ReplicateLLMProvider


def _fake_stream(model, input):
    # Real, live-found fix (2026-10-07): the fallback used to always be google/gemini-2.5-flash,
    # the single biggest source of this codebase's recurring JSON-parsing bugs. These tests
    # assert the real model-selection logic: gpt-oss for text, matched to Groq's own tier sizes,
    # and Gemini kept ONLY for the vision role (the one capability gpt-oss lacks on Replicate).
    yield model  # stash the chosen model name as the "output" so the test can assert on it


@pytest.mark.asyncio
async def test_tier_1_text_request_uses_gpt_oss_20b():
    provider = ReplicateLLMProvider(api_token="fake-token")
    with patch("src.providers.llm.replicate_llm.replicate.stream", side_effect=_fake_stream):
        result = await provider.complete(
            tier=ModelTier.TIER_1, system="sys", messages=[{"role": "user", "content": "hi"}]
        )
        assert result.model == "openai/gpt-oss-20b"
        assert result.text == "openai/gpt-oss-20b"


@pytest.mark.asyncio
async def test_tier_3_text_request_uses_gpt_oss_120b():
    provider = ReplicateLLMProvider(api_token="fake-token")
    with patch("src.providers.llm.replicate_llm.replicate.stream", side_effect=_fake_stream):
        result = await provider.complete(
            tier=ModelTier.TIER_3, system="sys", messages=[{"role": "user", "content": "hi"}]
        )
        assert result.model == "openai/gpt-oss-120b"


@pytest.mark.asyncio
async def test_a_request_with_real_images_still_uses_gemini_regardless_of_tier():
    provider = ReplicateLLMProvider(api_token="fake-token")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what's in this image?"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc123"}},
            ],
        }
    ]
    with patch("src.providers.llm.replicate_llm.replicate.stream", side_effect=_fake_stream):
        result = await provider.complete(tier=ModelTier.TIER_1, system="sys", messages=messages)
        assert result.model == "google/gemini-2.5-flash"
