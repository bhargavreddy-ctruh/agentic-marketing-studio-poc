"""
The LLMProvider contract every reasoning provider must satisfy.

Services depend on this Protocol, never on a concrete vendor class (Dependency Inversion,
Rules.md section 1). "Tier" is how specialists ask for capability level without knowing which
model that resolves to — Architecture.md section 3's Tier 0-3 system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Protocol


class ModelTier(IntEnum):
    TIER_0 = 0  # no AI model, deterministic code — not routed through an LLMProvider at all
    TIER_1 = 1  # small/fast model (Gemma-class)
    TIER_2 = 2  # mid-tier judgment
    TIER_3 = 3  # hardest creative/visual judgment


@dataclass
class LLMResult:
    """Our own result type — never the vendor's raw response object (genai_build's rule)."""

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None


class LLMProvider(Protocol):
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
        """`prefer_local` only means anything to `LLMRouter` (router.py) — whether a TIER_1 call
        should try the self-hosted model first. Real, live-found reason it exists (2026-09-21):
        the self-hosted TIER_1 model handles simple tool-calling specialists fine, but produces
        materially worse judgment on Ideation's own "is this brief ready, and what's the real
        synthesis of it" decision — a side-by-side test on the exact same input ("A red Ferrari")
        showed the local model return `ready: false` and silently drop "red Ferrari" from its own
        brief synthesis entirely, while Groq correctly returned `ready: true` with the detail
        intact. Every other provider ignores this parameter; only the router acts on it.

        `on_delta`, when given, is called with each real streamed text fragment as it arrives from
        the provider (2026-09-21, per the user's explicit ask to show real LLM "thinking" live,
        not just a final result) — the caller (`runner.py`, `ideation_service.py`, etc.) already
        knows which node/specialist is running and is responsible for attributing the fragment
        (e.g. `emit("llm_delta", node=specialist_name, text=fragment)`); this layer only ever
        forwards raw text, never decides what it means. A no-op when the provider or
        `STREAM_LLM_THINKING_ENABLED` doesn't support/allow streaming — the final `LLMResult` is
        always complete and correct either way, streaming only affects when the text becomes
        visible, never what the caller ultimately gets back."""
        ...
