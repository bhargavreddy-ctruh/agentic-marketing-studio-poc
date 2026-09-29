"""
THE ONLY file that talks to a local Ollama instance — scoped narrowly to crawler extraction only
(2026-09-28). Ollama was fully removed for general reasoning on 2026-09-28 (see router.py's own
docstring); this is a separate, deliberate revival that is never wired into `LLMRouter` and is
only ever constructed/called from services/knowledge/{brand,product}_dna_service.py's
crawl_*_from_url methods. Shares the same OpenAI-compatible /chat/completions shape Groq uses, so
it reuses `_openai_compatible.py` rather than duplicating request/retry logic.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from ._openai_compatible import call_openai_compatible_chat
from .base import LLMProvider, LLMResult, ModelTier


class OllamaProvider(LLMProvider):
    def __init__(self, base_url: str | None = None, model: str | None = None):
        self._base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self._model = model or settings.ollama_model

    async def complete(
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResult:
        full_messages = [{"role": "system", "content": system}, *messages]
        try:
            return await call_openai_compatible_chat(
                provider_name="ollama",
                base_url=self._base_url,
                api_key="ollama",  # Ollama's OpenAI-compatible endpoint ignores the key
                model=self._model,
                messages=full_messages,
                tools=tools,
                max_tokens=max_tokens,
                retries=0,
                on_delta=on_delta,
                strip_images=True,
            )
        except ProviderUnavailable:
            raise
        except Exception as exc:
            raise ProviderUnavailable("ollama", str(exc)) from exc


_singleton: OllamaProvider | None = None


def get_ollama_provider() -> OllamaProvider:
    global _singleton
    if _singleton is None:
        _singleton = OllamaProvider()
    return _singleton
