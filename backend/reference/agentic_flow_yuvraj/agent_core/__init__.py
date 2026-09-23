"""
agent_core — Shared multi-agent runtime for Commverse aiservices.

Extracted from campaign_studio so Assist, Creative Director, Try-on Stylist,
and Store Composer can reuse the same Claude tool loop, LLM client, and SSE helpers.
"""
from .config import (
    AGENT_MAX_TURNS,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_PROVIDER,
    llm_configured,
    llm_health_status,
)
from .events import AgentEvent
from .llm import (
    AnthropicClient,
    LLMError,
    LLMResponse,
    get_llm_client,
    image_block,
    require_llm_key,
    resolve_image_mime,
    tool_result_block,
)
from .runtime import (
    EmitFn,
    TerminalToolResult,
    ToolHandler,
    run_agent,
    terminal,
)
from .sse import format_sse, iter_queue_events, sse_headers
from .logger import get_logger

__all__ = [
    "AGENT_MAX_TURNS",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_PROVIDER",
    "llm_configured",
    "llm_health_status",
    "AgentEvent",
    "AnthropicClient",
    "LLMError",
    "LLMResponse",
    "get_llm_client",
    "image_block",
    "resolve_image_mime",
    "require_llm_key",
    "tool_result_block",
    "EmitFn",
    "TerminalToolResult",
    "ToolHandler",
    "run_agent",
    "terminal",
    "format_sse",
    "iter_queue_events",
    "sse_headers",
    "get_logger",
]
