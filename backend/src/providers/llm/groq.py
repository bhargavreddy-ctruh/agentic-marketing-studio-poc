"""
THE ONLY file that talks to Groq's API — a second LLM gateway, used only as a whole-provider
fallback (see router.py) when OpenRouter itself is unavailable.

Added after real testing found OpenRouter's free tier has a hard 50-requests/day cap
(Memory.md, Phase 2) — a whole-provider limit that no per-model retry or in-tier fallback inside
openrouter.py can route around. Groq exposes the same OpenAI-compatible /chat/completions shape,
so it shares the retry/backoff helper in `_openai_compatible.py` rather than duplicating it.

Free tier, no credit card, per Groq's own signup flow. The specific daily-limit numbers this
provider was chosen on are third-party-reported at the time this file was written, not yet
independently confirmed against a real call — verify at console.groq.com/docs/rate-limits or via
a live call before relying on an exact number.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from ._openai_compatible import call_openai_compatible_chat
from .base import LLMProvider, LLMResult, ModelTier

_TIER_TO_MODELS: dict[ModelTier, str | None] = {
    ModelTier.TIER_1: settings.groq_model_tier_1,
    ModelTier.TIER_2: settings.groq_model_tier_2,
    ModelTier.TIER_3: settings.groq_model_tier_3,
}


class GroqProvider(LLMProvider):
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self._api_key = api_key or settings.groq_api_key
        self._base_url = (base_url or settings.groq_base_url).rstrip("/")

    def _resolve_models(self, tier: ModelTier) -> list[str]:
        raw = _TIER_TO_MODELS.get(tier)
        if not raw:
            raise ProviderUnavailable(
                "groq", f"no model configured for {tier.name} — set groq_model_tier_{tier.value} in .env"
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
            raise ProviderUnavailable("groq", "GROQ_API_KEY is not set")
            
        keys = [k.strip() for k in self._api_key.split(",") if k.strip()]
        if not keys:
            raise ProviderUnavailable("groq", "GROQ_API_KEY is empty or invalid")

        models = self._resolve_models(tier)
        full_messages = [{"role": "system", "content": system}, *messages]

        last_error: Exception | None = None
        for model in models:
            for key in keys:
                try:
                    return await call_openai_compatible_chat(
                        provider_name="groq",
                        base_url=self._base_url,
                        api_key=key,
                        model=model,
                        messages=full_messages,
                        tools=tools,
                        max_tokens=max_tokens,
                        retries=0,  # fail fast per user request to shift to fallback rather than retrying multiple times
                        on_delta=on_delta,
                        strip_images=True,
                    )
                except ProviderUnavailable as exc:
                    last_error = exc
                    # If it's a rate limit, the API key is exhausted. We can try the next key.
                    # But if we exhaust all keys, it raises last_error quickly without internal backoff loops.
                    continue

        raise last_error or ProviderUnavailable("groq", "all models and keys failed")


_singleton: GroqProvider | None = None


def get_groq_provider() -> GroqProvider:
    global _singleton
    if _singleton is None:
        _singleton = GroqProvider()
    return _singleton
