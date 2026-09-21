"""
LLMRouter — combines GroqProvider (primary) and OpenRouterProvider (fallback) behind the single
LLMProvider protocol every service already depends on (Rules.md section 1: Dependency Inversion —
callers never know two vendors exist).

Groq is primary as of this file's latest change: real testing found OpenRouter's free tier has a
hard 50-requests/day cap (Memory.md, Phase 2) that no per-model retry or in-tier fallback inside
openrouter.py can route around, while Groq's free tier's own per-model daily limits are
substantially higher (verified live — Memory.md, Phase 2) — so defaulting to Groq means far fewer
whole-provider fallbacks are needed in normal use. OpenRouter stays wired as the fallback (not
removed) so its own model diversity is still one call away if Groq itself is ever unavailable.

If OPENROUTER_API_KEY isn't set, the fallback attempt still runs and fails with its own clear
ProviderUnavailable("openrouter", "OPENROUTER_API_KEY is not set") — never silently swallowed, so
a caller always learns the real reason the whole call failed.
"""
from __future__ import annotations

from typing import Any

from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from .base import LLMProvider, LLMResult, ModelTier
from .groq import GroqProvider
from .openrouter import OpenRouterProvider

log = get_logger(__name__)


class LLMRouter(LLMProvider):
    def __init__(self):
        self._primary = GroqProvider()
        self._fallback = OpenRouterProvider()

    async def complete(
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
    ) -> LLMResult:
        try:
            return await self._primary.complete(
                tier=tier, system=system, messages=messages, tools=tools, max_tokens=max_tokens
            )
        except ProviderUnavailable as exc:
            log.warning(
                "llm_router_falling_back_to_openrouter",
                extra={"_extra_tier": tier.name, "_extra_groq_error": exc.message},
            )
            return await self._fallback.complete(
                tier=tier, system=system, messages=messages, tools=tools, max_tokens=max_tokens
            )


_singleton: LLMRouter | None = None


def get_llm_provider() -> LLMProvider:
    global _singleton
    if _singleton is None:
        _singleton = LLMRouter()
    return _singleton
