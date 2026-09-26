"""
LLMRouter — combines LocalLLMProvider (TIER_1 primary with prefer_local) and
ReplicateLLMProvider (final fallback) behind the single LLMProvider protocol.

For TIER_1 + prefer_local=True: tries the self-hosted local Ollama model first ($0,
no remote rate limit). On failure, falls through to Groq → ReplicateLLM.

For all other tiers (and TIER_1 with prefer_local=False): tries Groq first (one
attempt per key, never retries — fail fast and move on). On failure, falls back to
ReplicateLLM which retries with exponential backoff.

The local model is NO LONGER the final fallback — it was producing low-quality
output and failing on multimodal requests. ReplicateLLM (google/gemini-2.5-flash)
is the correct fallback.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...core.events import emit
from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from .base import LLMProvider, LLMResult, ModelTier
from .groq import GroqProvider
from .local_llm import LocalLLMProvider
from .replicate_llm import ReplicateLLMProvider

log = get_logger(__name__)


class LLMRouter(LLMProvider):
    def __init__(self):
        self._local = LocalLLMProvider()
        self._primary = GroqProvider()
        self._fallback = ReplicateLLMProvider()

    async def complete(
        self,
        *,
        tier: ModelTier,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
        prefer_local: bool = True,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResult:
        if tier == ModelTier.TIER_1 and prefer_local:
            try:
                return await self._local.complete(
                    tier=tier, system=system, messages=messages, tools=tools, max_tokens=max_tokens,
                    on_delta=on_delta,
                )
            except ProviderUnavailable as exc:
                log.warning(
                    "llm_router_falling_back_to_groq_from_local",
                    extra={"_extra_tier": tier.name, "_extra_local_error": exc.message},
                )
                # Real, live-found bug (2026-09-24, per an explicit user report: "the node model
                # has latency... 30 seconds lag sometimes"): switching providers here is itself a
                # real, silent gap in the SAME class `_openai_compatible.py`'s own retry loop had —
                # the frontend never knew a provider was being abandoned entirely, only that
                # nothing was happening. `emit()` is a safe no-op with no turn active.
                emit("llm_provider_fallback", tier=tier.name, from_provider="local", to_provider="groq")

        try:
            return await self._primary.complete(
                tier=tier, system=system, messages=messages, tools=tools, max_tokens=max_tokens,
                on_delta=on_delta,
            )
        except ProviderUnavailable as exc:
            log.warning(
                "llm_router_falling_back_to_replicate",
                extra={"_extra_tier": tier.name, "_extra_groq_error": exc.message},
            )
            emit("llm_provider_fallback", tier=tier.name, from_provider="groq", to_provider="replicate_llm")

        return await self._fallback.complete(
            tier=tier, system=system, messages=messages, tools=tools, max_tokens=max_tokens,
            on_delta=on_delta,
        )


_singleton: LLMRouter | None = None


def get_llm_provider() -> LLMProvider:
    global _singleton
    if _singleton is None:
        _singleton = LLMRouter()
    return _singleton
