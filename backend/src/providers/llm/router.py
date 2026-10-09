"""
LLMRouter — combines GeminiProvider (primary) and GroqProvider (fallback) behind the single
LLMProvider protocol.

Every tier: tries Gemini first. On failure, falls back to Groq (one attempt per key, never
retries — fail fast and move on).

Grok (xAI) was removed entirely (2026-10-09, explicit user decision) — it sat as a middle fallback
between Gemini and Groq with no evidence it was ever actually needed; removing it also removes one
more provider's worth of surface area (its own API key, its own failure mode) for no observed
benefit.

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
from .groq import GroqProvider

log = get_logger(__name__)

_GEMINI_COOLDOWN_SECONDS = 30.0


class LLMRouter(LLMProvider):
    def __init__(self):
        self._primary = GeminiProvider()
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
        # Real, live-found bug (2026-10-09): this used to keep only the LAST provider's error and
        # `raise last_error` when every provider failed — so a turn where Gemini also failed
        # surfaced only Groq's error to the user, with zero indication Gemini was even tried, let
        # alone why IT failed. Misleading for anyone debugging a failure from the specialist card
        # alone. Now collects every attempted provider's own failure reason and raises one
        # combined, accurate message instead.
        failures: list[str] = []

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
                failures.append(f"gemini: {exc.message}")
                log.warning(
                    "llm_router_gemini_failed",
                    extra={"_extra_tier": tier.name, "_extra_gemini_error": exc.message},
                )
        else:
            failures.append("gemini: skipped (still in cooldown from a recent failure)")

        # 2. Fallback: Groq
        if settings.groq_api_key:
            try:
                emit("llm_provider_fallback", tier=tier.name, from_provider="gemini", to_provider="groq")
                return await self._fallback_groq.complete(
                    tier=tier, system=system, messages=messages, tools=tools, max_tokens=max_tokens,
                    on_delta=on_delta,
                )
            except ProviderUnavailable as exc:
                failures.append(f"groq: {exc.message}")
        else:
            failures.append("groq: skipped (no API key configured)")

        raise ProviderUnavailable("llm_router", "all providers failed — " + "; ".join(failures))


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
