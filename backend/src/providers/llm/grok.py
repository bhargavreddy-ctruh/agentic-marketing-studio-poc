"""
Grok (xAI) LLM Provider — OpenAI-compatible client for xAI's Grok API.
Endpoint: https://api.x.ai/v1
Models: grok-2-latest, grok-beta
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from ._openai_compatible import call_openai_compatible_chat
from .base import LLMProvider, LLMResult, ModelTier
from .key_cooldown import is_cooling_down, mark_rate_limited


class GrokProvider(LLMProvider):
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self._api_key = api_key or settings.grok_api_key or settings.xai_api_key
        self._base_url = (base_url or settings.grok_base_url or "https://api.x.ai/v1").rstrip("/")

    def _resolve_models(self, tier: ModelTier) -> list[str]:
        tier_to_models = {
            ModelTier.TIER_1: settings.grok_model_tier_1 or "grok-2-latest",
            ModelTier.TIER_2: settings.grok_model_tier_2 or "grok-2-latest",
            ModelTier.TIER_3: settings.grok_model_tier_3 or "grok-2-latest",
        }
        raw = tier_to_models.get(tier) or "grok-2-latest"
        return [m.strip() for m in raw.split(",") if m.strip()]

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
        key = self._api_key or settings.grok_api_key or settings.xai_api_key
        if not key:
            raise ProviderUnavailable("grok", "GROK_API_KEY or XAI_API_KEY is not set")

        keys = [k.strip() for k in key.split(",") if k.strip()]
        if not keys:
            raise ProviderUnavailable("grok", "GROK_API_KEY is empty")

        models = self._resolve_models(tier)
        full_messages = [{"role": "system", "content": system}, *messages]

        last_error: Exception | None = None
        for model in models:
            ordered_keys = sorted(keys, key=is_cooling_down)
            for k in ordered_keys:
                try:
                    return await call_openai_compatible_chat(
                        provider_name="grok",
                        base_url=self._base_url,
                        api_key=k,
                        model=model,
                        messages=full_messages,
                        tools=tools,
                        max_tokens=max_tokens,
                        on_delta=on_delta,
                    )
                except ProviderUnavailable as exc:
                    last_error = exc
                    if "429" in exc.message:
                        mark_rate_limited(k)
                    continue
                except Exception as exc:
                    last_error = exc
                    continue

        raise last_error or ProviderUnavailable("grok", "all models and keys failed")
