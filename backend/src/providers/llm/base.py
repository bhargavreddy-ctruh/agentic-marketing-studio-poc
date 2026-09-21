"""
The LLMProvider contract every reasoning provider must satisfy.

Services depend on this Protocol, never on a concrete vendor class (Dependency Inversion,
Rules.md section 1). "Tier" is how specialists ask for capability level without knowing which
model that resolves to — Architecture.md section 3's Tier 0-3 system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Protocol


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
    ) -> LLMResult: ...
