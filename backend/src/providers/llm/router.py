"""
LLMRouter — combines GroqProvider (primary) and ReplicateLLMProvider (final fallback) behind the
single LLMProvider protocol.

Every tier: tries Groq first (one attempt per key, never retries — fail fast and move on). On
failure, falls back to ReplicateLLM, which retries with exponential backoff.

Local Ollama was removed entirely (2026-09-28, explicit user decision) — it was already unused in
practice (every one of this app's 9 TIER_1 specialists had opted out via `prefer_local=False`
following a real, measured 106.5s/524-timeout incident; none of the others were ever individually
quality-verified on it either), so there was no reason to keep the dead code path around.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ...core.config import settings
from ...core.events import emit
from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from .base import LLMProvider, LLMResult, ModelTier
from .gemini import GeminiProvider
from .grok import GrokProvider
from .groq import GroqProvider

log = get_logger(__name__)

_GEMINI_COOLDOWN_SECONDS = 30.0


class LLMRouter(LLMProvider):
    def __init__(self):
        self._primary = GeminiProvider()
        self._fallback_grok = GrokProvider()
        self._fallback_groq = GroqProvider()
        self._gemini_cooldown_until: float = 0.0

    async def complete(
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResult:
        now = time.monotonic()
        last_error: Exception | None = None

        # 1. Main Provider: Gemini
        if now >= self._gemini_cooldown_until:
            try:
                result = await self._primary.complete(
                    tier=tier, system=system, messages=messages, tools=tools, max_tokens=max_tokens,
                    on_delta=on_delta,
                )
                self._gemini_cooldown_until = 0.0
                return result
            except ProviderUnavailable as exc:
                self._gemini_cooldown_until = now + _GEMINI_COOLDOWN_SECONDS
                last_error = exc
                log.warning(
                    "llm_router_gemini_failed",
                    extra={"_extra_tier": tier.name, "_extra_gemini_error": exc.message},
                )

        # 2. Fallback: Grok (xAI)
        grok_key = self._fallback_grok._api_key or settings.grok_api_key or settings.xai_api_key
        if grok_key:
            try:
                emit("llm_provider_fallback", tier=tier.name, from_provider="gemini", to_provider="grok")
                return await self._fallback_grok.complete(
                    tier=tier, system=system, messages=messages, tools=tools, max_tokens=max_tokens,
                    on_delta=on_delta,
                )
            except ProviderUnavailable as exc:
                last_error = exc
                log.warning(
                    "llm_router_grok_failed",
                    extra={"_extra_tier": tier.name, "_extra_grok_error": exc.message},
                )

        # 3. Tertiary fallback: Groq
        if settings.groq_api_key:
            try:
                emit("llm_provider_fallback", tier=tier.name, from_provider="gemini", to_provider="groq")
                return await self._fallback_groq.complete(
                    tier=tier, system=system, messages=messages, tools=tools, max_tokens=max_tokens,
                    on_delta=on_delta,
                )
            except ProviderUnavailable as exc:
                last_error = exc

        raise last_error or ProviderUnavailable("llm_router", "All configured LLM providers failed")


_singleton: LLMRouter | None = None


def get_llm_provider() -> LLMProvider:
    global _singleton
    if _singleton is None:
        _singleton = LLMRouter()
    return _singleton


def reset_llm_provider() -> None:
    """Drops the cached router (and, with it, its Groq/Replicate-LLM sub-providers, both
    constructed inside LLMRouter.__init__) so the next get_llm_provider() call rebuilds them off
    the current `settings` values — used by services/settings/settings_service.py after a
    DB-driven override changes any of the keys those providers baked in at construction."""
    global _singleton
    _singleton = None
