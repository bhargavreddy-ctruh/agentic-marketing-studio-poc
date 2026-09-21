"""
THE ONLY file that talks to a self-hosted Ollama server — Tier 1's new primary (router.py),
replacing the free-tier Groq/OpenRouter APIs for the "small/fast model" tier (base.py's own
ModelTier.TIER_1 docstring). Genuinely $0 and free of any remote rate limit, since it runs on this
machine.

Ollama exposes the same OpenAI-compatible /chat/completions shape Groq/OpenRouter already use, so
this shares the retry/backoff helper in `_openai_compatible.py` rather than duplicating it. No real
API key is needed — Ollama's local server is unauthenticated; `local_llm_api_key` is a placeholder
value only, never checked by the server.

Only ever attempted for TIER_1 (router.py's own decision) — raises ProviderUnavailable immediately
for TIER_2/TIER_3 rather than silently running a model it was never sized or asked to run.
"""
from __future__ import annotations

from typing import Any

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from ._openai_compatible import call_openai_compatible_chat
from .base import LLMProvider, LLMResult, ModelTier


class LocalLLMProvider(LLMProvider):
    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str | None = None):
        self._api_key = api_key or settings.local_llm_api_key
        self._base_url = (base_url or settings.local_llm_base_url).rstrip("/")
        self._model = model or settings.local_llm_model_tier_1

    async def complete(
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
        prefer_local: bool = True,  # unused here — LLMRouter already decided to call this provider
    ) -> LLMResult:
        if tier != ModelTier.TIER_1:
            raise ProviderUnavailable("local_llm", f"only configured for TIER_1, not {tier.name}")

        full_messages = [{"role": "system", "content": system}, *messages]
        return await call_openai_compatible_chat(
            provider_name="local_llm",
            base_url=self._base_url,
            api_key=self._api_key,
            model=self._model,
            messages=full_messages,
            tools=tools,
            max_tokens=max_tokens,
        )


_singleton: LocalLLMProvider | None = None


def get_local_llm_provider() -> LocalLLMProvider:
    global _singleton
    if _singleton is None:
        _singleton = LocalLLMProvider()
    return _singleton
