"""
Shared retry-with-backoff HTTP call logic for OpenAI-compatible /chat/completions APIs —
OpenRouter and Groq both expose this exact request/response shape. Extracted once so both
provider files share it rather than duplicating the same ~60-line retry block (Rules.md section 1:
DRY, and the genai_build guide's explicit "copying a block that already exists elsewhere" mistake).

Not a vendor SDK itself — each provider file still owns its own base URL, API key, and model-tier
resolution, and is still the only place that decides which vendor it's really talking to.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from ...core.exceptions import ProviderUnavailable
from ...core.middleware.logging import get_logger
from .base import LLMResult

log = get_logger(__name__)


def _try_recover_fake_final_tool_call(error_body: str) -> str | None:
    """
    Some free tool-calling models occasionally hallucinate a fake tool call (named "json", "JSON",
    "final_answer", etc.) to represent their final answer instead of just returning plain content
    with no tool_calls — a real quirk observed live on Groq's gpt-oss-120b under real tool-calling
    load (Memory.md, Phase 2), with the exact fake name varying between calls (case differed
    between two separate live occurrences), so a prompt instruction alone can't reliably prevent
    it — a stochastic model won't always obey a prompt rule.

    The vendor's own rejection still carries the model's real intended answer in
    `error.failed_generation` (Groq's shape) — recovering it here turns a hard failure into the
    exact same successful outcome a well-behaved final answer would have produced, without
    fabricating anything: the JSON returned is verbatim what the model itself generated.
    """
    try:
        err = (json.loads(error_body).get("error")) or {}
    except json.JSONDecodeError:
        return None

    if "tool call validation failed" not in str(err.get("message", "")).lower():
        return None

    failed_generation = err.get("failed_generation")
    if not failed_generation:
        return None

    try:
        parsed = json.loads(failed_generation)
    except json.JSONDecodeError:
        return None

    args = parsed.get("arguments")
    if isinstance(args, dict):
        return json.dumps(args)
    if isinstance(args, str):
        return args
    return None


async def call_openai_compatible_chat(
    *,
    provider_name: str,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    max_tokens: int,
    retries: int = 2,
) -> LLMResult:
    """Retry-with-backoff on ONE model against an OpenAI-compatible /chat/completions endpoint.
    Raises ProviderUnavailable if this model can't complete the call after its retry budget —
    the caller decides whether to fall back to another model or another provider entirely."""
    body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if tools:
        body["tools"] = tools

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"{base_url.rstrip('/')}/chat/completions"

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, headers=headers, json=body)

            if resp.status_code == 429:
                wait = 1.5 * (attempt + 1)
                log.warning(
                    "llm_http_rate_limited",
                    extra={"_extra_provider": provider_name, "_extra_model": model, "_extra_wait_s": wait},
                )
                await asyncio.sleep(wait)
                last_error = ProviderUnavailable(provider_name, f"{model} rate limited")
                continue

            if resp.status_code >= 500:
                wait = 1.0 * (attempt + 1)
                log.warning(
                    "llm_http_5xx",
                    extra={"_extra_provider": provider_name, "_extra_model": model, "_extra_status": resp.status_code},
                )
                await asyncio.sleep(wait)
                last_error = ProviderUnavailable(provider_name, f"{model} HTTP {resp.status_code}")
                continue

            if resp.status_code >= 400:
                recovered = _try_recover_fake_final_tool_call(resp.text)
                if recovered is not None:
                    log.warning(
                        "llm_http_fake_tool_call_recovered",
                        extra={"_extra_provider": provider_name, "_extra_model": model},
                    )
                    return LLMResult(text=recovered, model=model, stop_reason="stop")
                raise ProviderUnavailable(
                    provider_name, f"{model} HTTP {resp.status_code}: {resp.text[:300]}"
                )

            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            usage = data.get("usage") or {}
            log.info(
                "llm_http_call",
                extra={
                    "_extra_provider": provider_name,
                    "_extra_model": model,
                    "_extra_tokens_in": usage.get("prompt_tokens"),
                    "_extra_tokens_out": usage.get("completion_tokens"),
                    "_extra_cost": usage.get("cost"),
                    "_extra_ms": round((time.monotonic() - start) * 1000, 1),
                },
            )
            return LLMResult(
                text=msg.get("content") or "",
                model=model,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                tool_calls=msg.get("tool_calls") or [],
                stop_reason=choice.get("finish_reason"),
            )
        except httpx.TimeoutException as exc:
            last_error = ProviderUnavailable(provider_name, f"{model} timeout: {exc}")
            log.warning(
                "llm_http_timeout",
                extra={"_extra_provider": provider_name, "_extra_model": model, "_extra_attempt": attempt + 1},
            )

    raise last_error or ProviderUnavailable(provider_name, f"{model} failed after retries")
