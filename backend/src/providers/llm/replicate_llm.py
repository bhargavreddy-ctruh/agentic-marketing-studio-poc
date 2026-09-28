"""
Replicate LLM Provider for text generation using google/gemini-2.5-flash.
NOTE: Replicate's wrapper for this model currently does not support native
OpenAI-style tool calling or multi-turn messages array. It only accepts a single
prompt string. This provider will serialize the chat history into the prompt.

Retry policy: retries up to _MAX_RETRIES times with exponential backoff on transient
errors (unlike Groq which tries each key once and moves on).

Real vision support (2026-09-25): gemini-2.5-flash IS genuinely multimodal on Replicate —
confirmed live against the model's own schema (api.replicate.com/v1/models/google/gemini-2.5-flash),
which has an `images` input field (array of URIs, up to 10 images/7MB each), not a text-only
limitation. `image_url` content parts (the same shape `vision.py` builds for Groq) are now
forwarded there as data URIs instead of being silently dropped, so this provider can serve as a
genuine vision fallback, not just a text-only one.

Tool-calling (2026-09-26): this model has no native `tools` input field, so a tool-using
specialist's request is nudged via a system-prompt instruction instead (see the `if tools:` block
below) — the model often replies with a Python-call-style expression rather than perfect JSON
(e.g. Gemini's own `{"tool_code": "print(func(...))"}` habit). That's expected, not a bug to
prompt-engineer away: `runner.py`'s `_recover_pseudo_tool_call` safely recognizes and parses this
pattern (via `ast`, never `eval`) into a REAL, executed tool call — provider-agnostic, so the same
recovery also covers Groq producing this same pattern under a weaker fallback model, not just this
provider.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

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
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResult:
        if not self._api_token:
            raise ProviderUnavailable("replicate_llm", "REPLICATE_API_TOKEN is not set")

        # Map messages to a single prompt string since Replicate's google/gemini-2.5-flash
        # only accepts a `prompt` field (no messages array). `image_url` parts (the shape
        # vision.py builds) are collected separately and passed via the model's real `images`
        # field below, rather than dropped — see this file's module docstring.
        prompt = ""
        images: list[str] = []
        for m in messages:
            role = m.get("role", "user").upper()
            content = m.get("content", "")

            if isinstance(content, list):
                text_parts = []
                for part in content:
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url")
                        if url:
                            images.append(url)
                content_str = "\n".join(text_parts)
            else:
                content_str = str(content)

            prompt += f"{role}:\n{content_str}\n\n"

        if tools:
            # Replicate's google/gemini-2.5-flash doesn't support native tool calling (confirmed
            # via the model's own schema — no `tools`/`functions` input field exists). Appending
            # the schemas to the system instruction is a best-effort nudge, not a guarantee: the
            # model often replies with a Python-call-style expression instead of real JSON (e.g.
            # `{"tool_code": "print(base_image_generator(prompt='...'))"}`). That's expected and
            # handled: `runner.py`'s `_recover_pseudo_tool_call` recognizes and safely parses this
            # exact pattern (via `ast`, never `eval`) into a real, executable tool call — this
            # provider doesn't need to produce perfect JSON for a tool call to actually happen.
            tool_descriptions = "\\n".join(str(t) for t in tools)
            system += (
                f"\n\nYou have access to the following tools. If you want to use one, respond with "
                f"a JSON object describing the call: {{\"tool_call\": {{\"name\": \"<tool_name>\", "
                f"\"arguments\": {{...}}}}}}. If that's not possible, a direct function-call "
                f"expression like tool_name(arg='value', ...) is also understood.\n{tool_descriptions}"
            )

        input_data: dict[str, Any] = {
            "prompt": prompt.strip(),
            "system_instruction": system,
            "max_output_tokens": max_tokens,
            "temperature": 0.7,
        }
        if images:
            input_data["images"] = images[:10]  # real vendor cap, confirmed via the model's own schema

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
                stripped = content.strip()
                if not stripped:
                    # Real, live-found bug (2026-09-28): `replicate.stream(...)` can yield zero/
                    # all-empty chunks without raising — this used to return a "successful"
                    # LLMResult with text="" straight through, since this provider is the LAST
                    # fallback (no ProviderUnavailable = no retry engages, and there's nothing left
                    # for router.py to fall back to). Raising here instead routes into the same
                    # except-block retry loop below, giving it real chances to recover before
                    # actually giving up — matching the same guard `_openai_compatible.py`'s
                    # streaming path already has for this exact failure shape.
                    raise ProviderUnavailable(
                        "replicate_llm", f"{_REPLICATE_LLM_MODEL} returned empty content"
                    )
                return LLMResult(
                    text=stripped,
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
