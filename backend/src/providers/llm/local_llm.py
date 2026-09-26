"""
THE ONLY file that talks to a self-hosted Ollama server — Tier 1's new primary (router.py),
replacing the free-tier Groq/OpenRouter APIs for the "small/fast model" tier (base.py's own
ModelTier.TIER_1 docstring). Genuinely $0 and free of any remote rate limit, since it runs on this
machine.

Ollama exposes the same OpenAI-compatible /chat/completions shape Groq/OpenRouter already use, so
this shares the retry/backoff helper in `_openai_compatible.py` rather than duplicating it. No real
API key is needed — Ollama's local server is unauthenticated; `local_llm_api_key` is a placeholder
value only, never checked by the server.

TIER_1 uses this as its preferred primary (router.py's own decision, per-specialist opt-in via
`prefer_local`). TIER_2/TIER_3 don't route here under normal conditions either — this model was
sized for TIER_1's small/fast decisions, not the heavier ones — but router.py does fall back here
as a genuine LAST RESORT for any tier once Groq AND OpenRouter have both failed (2026-09-21, a
real, disclosed trade-off: a live illustrator (TIER_3) run hit free-tier exhaustion on every remote
option at once; running locally at reduced expected quality is preferable to producing nothing).
This file itself no longer refuses a non-TIER_1 call — router.py is where that decision belongs.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...core.config import settings
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
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResult:
        full_messages = [{"role": "system", "content": system}, *messages]
        return await call_openai_compatible_chat(
            provider_name="local_llm",
            base_url=self._base_url,
            api_key=self._api_key,
            model=self._model,
            messages=full_messages,
            tools=tools,
            max_tokens=max_tokens,
            on_delta=on_delta,
            strip_images=True,
        )


_singleton: LocalLLMProvider | None = None


def get_local_llm_provider() -> LocalLLMProvider:
    global _singleton
    if _singleton is None:
        _singleton = LocalLLMProvider()
    return _singleton
