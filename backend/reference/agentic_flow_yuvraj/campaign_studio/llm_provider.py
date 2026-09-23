"""
llm_provider.py — Re-exports shared agent_core LLM client (backward compatible).
"""
from __future__ import annotations

from ..agent_core.llm import (  # noqa: F401
    AnthropicClient,
    CampaignLLMError,
    LLMError,
    LLMResponse,
    get_llm_client,
    image_block,
    require_llm_key,
    tool_result_block,
)

__all__ = [
    "AnthropicClient",
    "CampaignLLMError",
    "LLMError",
    "LLMResponse",
    "get_llm_client",
    "image_block",
    "require_llm_key",
    "tool_result_block",
]
