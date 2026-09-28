"""
Unit test — no DB, no real network, per Architecture.md's tests/unit/ scope.

Verifies the Groq cooldown added to `LLMRouter` (2026-09-26, per an explicit user report: a
screenshot showing "groq unavailable — switching to replicate_llm" repeated 4 times in a row for
one specialist's run). Once Groq fails, subsequent calls within the cooldown window must skip it
entirely — never re-attempt it, never re-emit the fallback event — and go straight to the
fallback provider.
"""
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import ProviderUnavailable
from src.providers.llm.base import LLMResult, ModelTier
from src.providers.llm.router import LLMRouter


@pytest.mark.asyncio
async def test_groq_only_tried_once_then_cooldown_skips_it():
    router = LLMRouter()
    router._primary = AsyncMock()
    router._primary.complete.side_effect = ProviderUnavailable("groq", "rate limited")
    router._fallback = AsyncMock()
    router._fallback.complete.return_value = LLMResult(text="{}", model="fallback-model")

    with patch("src.providers.llm.router.emit") as mock_emit:
        for _ in range(4):
            result = await router.complete(
                tier=ModelTier.TIER_1, system="sys", messages=[{"role": "user", "content": "hi"}],
            )
            assert result.model == "fallback-model"

    # Groq only genuinely attempted ONCE across all 4 calls — the rest were skipped via cooldown.
    assert router._primary.complete.await_count == 1
    # The fallback provider WAS used all 4 times (that's correct — every call still needs an answer).
    assert router._fallback.complete.await_count == 4
    # The "switching to fallback" event only fired once, not once per call.
    fallback_emits = [c for c in mock_emit.call_args_list if c.args[0] == "llm_provider_fallback"]
    assert len(fallback_emits) == 1


@pytest.mark.asyncio
async def test_groq_success_clears_cooldown():
    router = LLMRouter()
    router._primary = AsyncMock()
    router._primary.complete.side_effect = [
        ProviderUnavailable("groq", "rate limited"),
    ]
    router._fallback = AsyncMock()
    router._fallback.complete.return_value = LLMResult(text="{}", model="fallback-model")

    await router.complete(
        tier=ModelTier.TIER_1, system="sys", messages=[{"role": "user", "content": "hi"}],
    )
    # Manually expire the cooldown (as if enough real time had passed) and confirm Groq is tried
    # again rather than staying permanently skipped.
    router._groq_cooldown_until = 0.0
    router._primary.complete.side_effect = None
    router._primary.complete.return_value = LLMResult(text="{}", model="groq-model")

    result = await router.complete(
        tier=ModelTier.TIER_1, system="sys", messages=[{"role": "user", "content": "hi"}],
    )
    assert result.model == "groq-model"
    # 2 total across both calls in this test (1 failed attempt + 1 successful retry after the
    # cooldown was cleared) — proves Groq was genuinely tried again, not left permanently skipped.
    assert router._primary.complete.await_count == 2
