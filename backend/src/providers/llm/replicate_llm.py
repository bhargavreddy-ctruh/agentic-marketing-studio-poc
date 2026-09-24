"""
Replicate LLM Provider for text generation using google/gemini-2.5-flash.
NOTE: Replicate's wrapper for this model currently does not support native
OpenAI-style tool calling or multi-turn messages array. It only accepts a single
prompt string. This provider will serialize the chat history into the prompt.
"""
from __future__ import annotations

import os
from typing import Any, Callable

import replicate

from ...core.config import settings
from ...core.exceptions import ProviderUnavailable
from .base import LLMProvider, LLMResult, ModelTier


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

        try:
            # We run this in a thread since replicate.stream is synchronous
            import asyncio
            loop = asyncio.get_running_loop()
            
            def _run():
                full_text = ""
                for event in replicate.stream("google/gemini-2.5-flash", input=input_data):
                    chunk = str(event)
                    full_text += chunk
                    if on_delta:
                        on_delta(chunk)
                return full_text

            content = await loop.run_in_executor(None, _run)
            return LLMResult(
                content=content.strip(),
                tool_calls=[],
                provider_name="replicate_llm",
                model_name="google/gemini-2.5-flash"
            )
        except Exception as e:
            raise ProviderUnavailable("replicate_llm", str(e))


_singleton: ReplicateLLMProvider | None = None

def get_replicate_llm_provider() -> ReplicateLLMProvider:
    global _singleton
    if _singleton is None:
        _singleton = ReplicateLLMProvider()
    return _singleton
