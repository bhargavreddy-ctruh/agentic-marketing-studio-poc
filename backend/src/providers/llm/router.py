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

from ...core.events import emit
from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from .base import LLMProvider, LLMResult, ModelTier
from .groq import GroqProvider
from .replicate_llm import ReplicateLLMProvider

log = get_logger(__name__)

# Real, live-found noise/waste (2026-09-26, explicit user report — a screenshot showing the SAME
# "groq unavailable — switching to replicate_llm" line repeated 4 times in a row for one
# specialist's run): Groq's own per-call retry already tries every configured key once and gives
# up fast (`groq.py`'s `retries=0`) — correct for a single call. But `LLMRouter` had no memory
# ACROSS calls, so every iteration of the same specialist's multi-tool-call agentic loop
# re-attempted Groq from scratch, hit the exact same rate limit again, and re-logged/re-emitted
# the identical fallback message — wasted requests and repeated noise, not a retry that could ever
# plausibly succeed sooner than the rate limit actually resets. A rate-limit window is normally on
# the order of a minute, not milliseconds, so "try again next call" was never a real recovery
# chance anyway.
_GROQ_COOLDOWN_SECONDS = 60.0


class LLMRouter(LLMProvider):
    def __init__(self):
        self._primary = GroqProvider()
        self._fallback = ReplicateLLMProvider()
        # Monotonic timestamp until which Groq is skipped entirely (0 = never tripped / already
        # expired). Instance-level, not per-call — `get_llm_provider()` is a singleton, so this
        # state is naturally shared across every specialist/tool-calling iteration in the process,
        # which is exactly the scope a rate limit actually applies at.
        self._groq_cooldown_until: float = 0.0

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
        now = time.monotonic()
        if now < self._groq_cooldown_until:
            # Already tripped recently — go straight to the fallback, no repeat attempt, no
            # repeat log/event (that already happened once, when the cooldown started below).
            return await self._fallback.complete(
                tier=tier, system=system, messages=messages, tools=tools, max_tokens=max_tokens,
                on_delta=on_delta,
            )

        try:
            result = await self._primary.complete(
                tier=tier, system=system, messages=messages, tools=tools, max_tokens=max_tokens,
                on_delta=on_delta,
            )
            self._groq_cooldown_until = 0.0  # a real success clears any earlier trip
            return result
        except ProviderUnavailable as exc:
            self._groq_cooldown_until = now + _GROQ_COOLDOWN_SECONDS
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


def reset_llm_provider() -> None:
    """Drops the cached router (and, with it, its Groq/Replicate-LLM sub-providers, both
    constructed inside LLMRouter.__init__) so the next get_llm_provider() call rebuilds them off
    the current `settings` values — used by services/settings/settings_service.py after a
    DB-driven override changes any of the keys those providers baked in at construction."""
    global _singleton
    _singleton = None
