"""
THE ONLY file that talks to OpenRouter's API.

OpenRouter exposes an OpenAI-compatible /chat/completions endpoint, so a plain httpx client is
used rather than pulling in a vendor SDK for it. Two resilience layers:

1. Retry-with-backoff on a single model (429/5xx) — shared with Groq via
   `_openai_compatible.call_openai_compatible_chat`, since both vendors expose the same request
   shape (Rules.md section 1: DRY).
2. A fallback chain across multiple models per tier (Architecture.md's model-tiering system) —
   the same "try the next provider" pattern already proven in image_providers/video_providers.
   Added after real testing (Memory.md, Phase 1) showed free-tier OpenRouter models do get
   genuinely, transiently congested — a single pinned model per tier isn't resilient enough.

A THIRD resilience layer lives one level up, in `router.py`: OpenRouter's free tier turned out to
have a hard 50-requests/day cap (Memory.md, Phase 2) that no amount of per-model retry or in-tier
fallback can route around — that's a whole-provider outage, handled by falling back to Groq
entirely, not by anything in this file.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from ._openai_compatible import call_openai_compatible_chat
from .base import LLMProvider, LLMResult, ModelTier

log = get_logger(__name__)

_TIER_TO_MODELS: dict[ModelTier, str | None] = {
    ModelTier.TIER_1: settings.model_tier_1,
    ModelTier.TIER_2: settings.model_tier_2,
    ModelTier.TIER_3: settings.model_tier_3,
}


class OpenRouterProvider(LLMProvider):
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self._api_key = api_key or settings.openrouter_api_key
        self._base_url = (base_url or settings.openrouter_base_url).rstrip("/")

    def _resolve_models(self, tier: ModelTier) -> list[str]:
        raw = _TIER_TO_MODELS.get(tier)
        if not raw:
            raise ProviderUnavailable(
                "openrouter",
                f"no model configured for {tier.name} — set model_tier_{tier.value} in .env",
            )
        return [m.strip() for m in raw.split(",") if m.strip()]

    async def complete(
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
        prefer_local: bool = True,  # unused — only LLMRouter acts on this, see base.py's docstring
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResult:
        if not self._api_key:
            raise ProviderUnavailable("openrouter", "OPENROUTER_API_KEY is not set")

        models = self._resolve_models(tier)
        full_messages = [{"role": "system", "content": system}, *messages]

        last_error: Exception | None = None
        for model in models:
            try:
                return await call_openai_compatible_chat(
                    provider_name="openrouter",
                    base_url=self._base_url,
                    api_key=self._api_key,
                    model=model,
                    messages=full_messages,
                    tools=tools,
                    max_tokens=max_tokens,
                    on_delta=on_delta,
                    strip_images=True,
                )
            except ProviderUnavailable as exc:
                last_error = exc
                log.warning(
                    "openrouter_model_failed_falling_back",
                    extra={"_extra_model": model, "_extra_tier": tier.name, "_extra_error": exc.message},
                )
                continue

        raise last_error or ProviderUnavailable("openrouter", "all models in tier failed")


_singleton: OpenRouterProvider | None = None


def get_openrouter_provider() -> OpenRouterProvider:
    global _singleton
    if _singleton is None:
        _singleton = OpenRouterProvider()
    return _singleton
