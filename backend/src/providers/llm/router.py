"""
LLMRouter — combines LocalLLMProvider (TIER_1 primary), GroqProvider (primary otherwise, and
TIER_1's own fallback), and OpenRouterProvider (final fallback) behind the single LLMProvider
protocol every service already depends on (Rules.md section 1: Dependency Inversion — callers
never know which vendor answered).

TIER_1 tries the self-hosted Ollama model first (genuinely $0, no remote rate limit, runs on this
machine) — matching ModelTier.TIER_1's own "small/fast model" docstring in base.py. If the local
server is down, or `prefer_local=False` was passed, TIER_1 falls through to the same
Groq -> OpenRouter chain every other tier already uses. TIER_2/TIER_3 are unaffected — local
inference was never sized or asked to run those.

`prefer_local=False` exists because a real, live comparison (2026-09-21) found the local model's
judgment measurably worse specifically for Ideation's "is this brief ready, and what does it
actually mean" decision (base.py's own docstring on the parameter has the exact test) — so
`ideation_service.py` passes it explicitly, while the tool-calling Tier 1 specialists (which tested
fine locally) keep the default and still go local-first.

Groq is primary for TIER_2/TIER_3 (and TIER_1's own fallback) as of an earlier change: real testing
found OpenRouter's free tier has a hard 50-requests/day cap (Memory.md, Phase 2) that no per-model
retry or in-tier fallback inside openrouter.py can route around, while Groq's free tier's own
per-model daily limits are substantially higher (verified live — Memory.md, Phase 2) — so
defaulting to Groq means far fewer whole-provider fallbacks are needed in normal use. OpenRouter
stays wired as the final fallback (not removed) so its own model diversity is still one call away
if Groq itself is ever unavailable.

If OPENROUTER_API_KEY isn't set, the fallback attempt still runs and fails with its own clear
ProviderUnavailable("openrouter", "OPENROUTER_API_KEY is not set") — never silently swallowed, so
a caller always learns the real reason the whole call failed.

LAST-RESORT LOCAL FALLBACK, any tier (2026-09-21): a real live outage found Groq AND every free
OpenRouter model for a tier rate-limited at once (illustrator, TIER_3) — the user explicitly asked
to use the local model rather than fail outright. Rather than flip TIER_2/TIER_3 to prefer local
(which would trade away quality on every normal call, not just outages), this only reaches for
local_llm as the very last thing tried, after Groq AND OpenRouter have both already failed —
quality priority is unchanged in the normal case, and a real request only degrades to the smaller
local model instead of failing outright during a genuine free-tier outage.
"""
from __future__ import annotations

from typing import Any, Callable

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
            groq_error = exc
            log.warning(
                "llm_router_falling_back_to_replicate",
                extra={"_extra_tier": tier.name, "_extra_groq_error": exc.message},
            )
            emit("llm_provider_fallback", tier=tier.name, from_provider="groq", to_provider="replicate_llm")

        try:
            return await self._fallback.complete(
                tier=tier, system=system, messages=messages, tools=tools, max_tokens=max_tokens,
                on_delta=on_delta,
            )
        except ProviderUnavailable as exc:
            log.warning(
                "llm_router_falling_back_to_local_last_resort",
                extra={"_extra_tier": tier.name, "_extra_groq_error": groq_error.message, "_extra_replicate_error": exc.message},
            )
            emit("llm_provider_fallback", tier=tier.name, from_provider="replicate_llm", to_provider="local")
            return await self._local.complete(
                tier=tier, system=system, messages=messages, tools=tools, max_tokens=max_tokens,
                on_delta=on_delta,
            )


_singleton: LLMRouter | None = None


def get_llm_provider() -> LLMProvider:
    global _singleton
    if _singleton is None:
        _singleton = LLMRouter()
    return _singleton
