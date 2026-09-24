"""
Replicate LLM Provider for text generation using google/gemini-2.5-flash.
NOTE: Replicate's wrapper for this model currently does not support native
OpenAI-style tool calling or multi-turn messages array. It only accepts a single
prompt string. This provider will serialize the chat history into the prompt.

Retry policy: retries up to _MAX_RETRIES times with exponential backoff on transient
errors (unlike Groq which tries each key once and moves on).
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Callable

import replicate

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from .base import LLMProvider, LLMResult, ModelTier

log = get_logger(__name__)
_MAX_RETRIES = 3
_REPLICATE_LLM_MODEL = "google/gemini-2.5-flash"


class ReplicateLLMProvider(LLMProvider):
    def __init__(self, api_token: str | None = None):
        self._api_token = api_token or settings.replicate_api_token
        # Replicate SDK relies on the environment variable REPLICATE_API_TOKEN.
        if self._api_token:
            os.environ["REPLICATE_API_TOKEN"] = self._api_token

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
        if not self._api_token:
            raise ProviderUnavailable("replicate_llm", "REPLICATE_API_TOKEN is not set")

        # Map messages to a single prompt string since Replicate's google/gemini-2.5-flash
        # only accepts a `prompt` field (no messages array).
        prompt = ""
        for m in messages:
            role = m.get("role", "user").upper()
            content = m.get("content", "")
            prompt += f"{role}:\n{content}\n\n"

        if tools:
            # Replicate's google/gemini-2.5-flash doesn't support native tool calling yet.
            # We append the tool schemas to the system instruction in hopes it outputs JSON,
            # though it's not a native guarantee.
            tool_descriptions = "\\n".join(str(t) for t in tools)
            system += f"\n\nYou have access to the following tools. If you want to use them, you must respond with a JSON object describing the tool call:\n{tool_descriptions}"

        input_data = {
            "prompt": prompt.strip(),
            "system_instruction": system,
            "max_output_tokens": max_tokens,
            "temperature": 0.7,
        }

        loop = asyncio.get_running_loop()
        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                def _run():
                    full_text = ""
                    for event in replicate.stream(_REPLICATE_LLM_MODEL, input=input_data):
                        chunk = str(event)
                        full_text += chunk
                        if on_delta:
                            on_delta(chunk)
                    return full_text

                content = await loop.run_in_executor(None, _run)
                return LLMResult(
                    text=content.strip(),
                    model=_REPLICATE_LLM_MODEL,
                    tool_calls=[],
                    stop_reason="stop",
                )
            except Exception as e:
                last_error = e
                if attempt < _MAX_RETRIES:
                    wait = 2.0 * (attempt + 1)
                    log.warning(
                        "replicate_llm_retry",
                        extra={"_extra_attempt": attempt + 1, "_extra_wait_s": wait, "_extra_error": str(e)},
                    )
                    await asyncio.sleep(wait)
                    continue

        raise ProviderUnavailable("replicate_llm", str(last_error))


_singleton: ReplicateLLMProvider | None = None

def get_replicate_llm_provider() -> ReplicateLLMProvider:
    global _singleton
    if _singleton is None:
        _singleton = ReplicateLLMProvider()
    return _singleton
